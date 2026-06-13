"""YouTube auto-clip automation — the "cronjob" share flow.

Opt-in, per-user, keyless. When a creator enables it for their channel, a
background poller watches the channel's public RSS feed (chat/youtube.py); each
NEW upload is enqueued as an "ingest" job that:

    download (yt-dlp) → generate_clips → apply the chosen style → pre-render

so the clips land fully edited and instantly viewable. Publishing is NEVER
automated — the human still hits PUBLISH in the studio's share flow.

Design notes that keep this safe:
- Config lives in the user's profile_json["automation"] (auth.update_profile),
  so it survives restarts and never touches the BYOK "llm" block.
- The poller only ENQUEUES; the single JobManager worker serialises downloads
  and renders, so the global SESSION invariant is never violated. Each ingest
  job captures its OWN Session instance (like _submit_processing_job), so a
  project swap can't redirect its writes.
- On enable we seed last_video_id to the channel's CURRENT latest upload, so
  turning it on never backfills the entire channel — only future uploads ingest.
- Network egress is limited to the user-configured channel (RSS + yt-dlp).
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from chat import auth, youtube
from chat.jobs import MANAGER
from pipeline import config

router = APIRouter()

# yt-dlp resolved once; the dev box has it at ~/.local/bin. download_youtube
# re-checks existence so a missing binary surfaces as a clean job error.
YT_DLP = shutil.which("yt-dlp") or str(Path.home() / ".local" / "bin" / "yt-dlp")

DOWNLOAD_DIR = config.OUTPUTS_DIR / "youtube_dl"

# Global poll cadence (minutes). Per-user enable/disable is in the config; the
# loop simply sweeps all enabled users each tick.
POLL_MIN = int(os.getenv("AUTOMATION_POLL_MIN", "15"))
POLL_STARTUP_DELAY = int(os.getenv("AUTOMATION_STARTUP_DELAY", "30"))
DEFAULT_STYLE = "hormozi"

DEFAULT_CFG = {
    "enabled": False,
    "channel_handle": "",
    "channel_id": "",
    "channel_title": "",
    "auto_edit_style": DEFAULT_STYLE,
    "last_video_id": "",
    "last_checked": "",
    "last_error": "",
}


# ----------------------------------------------------------------- config i/o
def get_config(uid: int) -> dict:
    """The user's automation config, merged onto DEFAULT_CFG so missing keys
    always read sane defaults."""
    cfg = dict(DEFAULT_CFG)
    stored = auth.get_profile(uid).get("automation") or {}
    cfg.update({k: stored[k] for k in stored if k in DEFAULT_CFG})
    return cfg


def save_config(uid: int, cfg: dict) -> dict:
    """Persist only the known keys (re-reads profile_json so the llm/onboarding
    blocks survive)."""
    clean = {k: cfg.get(k, DEFAULT_CFG[k]) for k in DEFAULT_CFG}
    auth.update_profile(uid, {"automation": clean})
    return clean


def _now_iso() -> str:
    return (datetime.datetime.now(datetime.timezone.utc)
            .replace(tzinfo=None).isoformat() + "Z")


def _llm_override_for(profile: dict):
    """Build the BYOK override from a profile dict (the poller has no request
    context). user_llm_override only reads the profile_json field."""
    try:
        return auth.user_llm_override({"profile_json": json.dumps(profile)})
    except Exception:  # noqa: BLE001 — fall back to the env key
        return None


# ------------------------------------------------------------------ download
def download_youtube(url: str, video_id: str) -> Path:
    """Download a single video to DOWNLOAD_DIR/<video_id>.mp4 (deterministic, so
    re-ingesting the same id reuses the file + project). Raises on failure."""
    if not Path(YT_DLP).exists():
        raise RuntimeError(
            "yt-dlp is not installed. `pip install yt-dlp` (or `uv sync`).")
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    out = DOWNLOAD_DIR / f"{video_id}.mp4"
    if out.exists() and out.stat().st_size > 0:
        return out
    cmd = [
        YT_DLP, "--no-playlist", "--no-warnings",
        "-f", "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b",
        "--merge-output-format", "mp4",
        "-o", str(DOWNLOAD_DIR / f"{video_id}.%(ext)s"),
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise RuntimeError("yt-dlp failed: " + (tail[-1] if tail else "unknown error"))
    if not out.exists():
        # merge produced a non-.mp4 container — adopt whatever landed.
        cands = sorted(DOWNLOAD_DIR.glob(f"{video_id}.*"))
        if not cands:
            raise RuntimeError("yt-dlp produced no output file.")
        out = cands[0]
    return out


# -------------------------------------------------------------- ingest job
def submit_ingest_job(user_id: int, url: str, video_id: str, title: str,
                      style: str, llm_override=None):
    """Enqueue the full auto-edit chain for one video. Mirrors
    _submit_processing_job: the closure captures its OWN Session, and each
    discrete mutation runs under SESSION_LOCK (released between clips so the UI's
    read endpoints never block for the whole render)."""
    style = (style or DEFAULT_STYLE).strip()

    def _run(job=None) -> dict:
        import chat.app as appmod
        from chat.session import Session
        from chat.tools import apply_style, generate_clips, render_clip

        try:
            path = download_youtube(url, video_id)
        except Exception as e:  # noqa: BLE001 — surface as a job error
            return {"ok": False, "error": f"download failed: {e}"}

        # Build the project (skip the QUEUED proxy job — we'd deadlock waiting on
        # it behind ourselves on the single worker — and build the proxy inline).
        sess = Session.load_or_create(str(path), build_proxy=False)
        with appmod.SESSION_LOCK:
            sess.data["owner_uid"] = user_id
            sess.data["display_name"] = title or sess.data.get("name")
            sess.data["intake"] = {
                "mode": "long_video", "source": "youtube",
                "youtube_video_id": video_id, "youtube_url": url,
                "auto": True, "auto_style": style,
                "error": None,
                "processing_job": job.id if job else None,
                "processed_at": None,
            }
            sess.save()

        try:
            from pipeline.proxy import build_proxy, keyframe_index
            with appmod.SESSION_LOCK:
                src_path = sess.data["source"]["path"]
                sdir = sess.path.parent
            proxy = build_proxy(src_path, sdir)
            keys = keyframe_index(src_path)
            with appmod.SESSION_LOCK:
                sess.data.setdefault("source", {})["proxy_path"] = proxy
                sess.data["source"]["keyframes"] = keys
                sess.save()
        except Exception:  # noqa: BLE001 — proxy is an optimization, not a gate
            pass

        with appmod.SESSION_LOCK:
            try:
                result = generate_clips(sess)
                err = None if result.get("ok") else result.get("error")
            except Exception as e:  # noqa: BLE001
                result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
                err = result["error"]
            sess.save()

        # Pre-render: style + materialize every clip so it's publish-ready. Done
        # per-clip with the lock released between, like the share job.
        rendered = 0
        if result.get("ok"):
            with appmod.SESSION_LOCK:
                clip_ids = [c["id"] for c in (sess.data.get("clips") or [])]
            for cid in clip_ids:
                try:
                    # render_clip FIRST materializes the lazy clip (clips from
                    # generate_clips carry only stage params, no rendered output);
                    # apply_style then needs that baseline to replay the style onto.
                    with appmod.SESSION_LOCK:
                        render_clip(sess, cid)
                    with appmod.SESSION_LOCK:
                        apply_style(sess, cid, style)
                    rendered += 1
                except Exception:  # noqa: BLE001 — one bad clip mustn't abort the rest
                    continue

        with appmod.SESSION_LOCK:
            intake = sess.data.setdefault("intake", {"mode": "long_video"})
            intake["error"] = err
            intake["processing_job"] = None
            intake["processed_at"] = _now_iso()
            intake["auto_rendered"] = rendered
            sess.save()
        return {"ok": result.get("ok", False), "error": err,
                "project": sess.data.get("name"), "rendered": rendered}

    label = f"YouTube · {title or video_id}"
    return MANAGER.submit("tool", label, _run, llm_override=llm_override)


# ------------------------------------------------------------------ polling
def poll_user(uid: int, profile: dict, cfg: dict, force: bool = False) -> dict:
    """Check one user's channel and enqueue ingest jobs for new uploads.
    Advances last_video_id to the newest upload. `force` bypasses the enabled
    gate (the manual "Check now" button). Never raises — records last_error."""
    cid = cfg.get("channel_id") or ""
    if not cid or (not cfg.get("enabled") and not force):
        return {"new": 0, "skipped": True}

    ups = youtube.fetch_uploads(cid)
    cfg["last_checked"] = _now_iso()
    if not ups:
        cfg["last_error"] = "Couldn't fetch the channel feed (network or no uploads)."
        save_config(uid, cfg)
        return {"new": 0, "error": cfg["last_error"]}

    last_seen = cfg.get("last_video_id") or ""
    new_videos: list[dict] = []
    if last_seen:
        for v in ups:                      # newest-first; stop at the last seen
            if v["video_id"] == last_seen:
                break
            new_videos.append(v)
    # If last_seen is empty we only SEED (no backfill of the whole channel).

    override = _llm_override_for(profile)
    style = cfg.get("auto_edit_style") or DEFAULT_STYLE
    for v in reversed(new_videos):         # oldest-first → chronological projects
        submit_ingest_job(uid, v["url"], v["video_id"], v["title"], style,
                          llm_override=override)

    cfg["last_video_id"] = ups[0]["video_id"]
    cfg["last_error"] = ""
    save_config(uid, cfg)
    return {"new": len(new_videos)}


def poll_all() -> None:
    """Sweep every user with automation enabled. Per-user errors are isolated."""
    for uid, _email, profile in auth.all_profiles():
        cfg = dict(DEFAULT_CFG)
        stored = profile.get("automation") or {}
        cfg.update({k: stored[k] for k in stored if k in DEFAULT_CFG})
        if not cfg.get("enabled") or not cfg.get("channel_id"):
            continue
        try:
            poll_user(uid, profile, cfg)
        except Exception:  # noqa: BLE001 — one user mustn't stall the sweep
            continue


_started = False


def start_poller() -> None:
    """Start the background watcher thread (idempotent). No-op when disabled via
    AUTOMATION_DISABLE=1."""
    global _started
    if _started:
        return
    if os.getenv("AUTOMATION_DISABLE", "").strip().lower() in ("1", "true", "yes", "on"):
        return
    _started = True

    def _loop() -> None:
        time.sleep(POLL_STARTUP_DELAY)
        while True:
            try:
                poll_all()
            except Exception:  # noqa: BLE001 — the loop must never die
                pass
            time.sleep(max(60, POLL_MIN * 60))

    threading.Thread(target=_loop, daemon=True, name="yt-automation").start()


# ------------------------------------------------------------------ endpoints
def _public_cfg(cfg: dict) -> dict:
    return {k: cfg.get(k, DEFAULT_CFG[k]) for k in DEFAULT_CFG}


def _styles() -> list[dict]:
    from pipeline.styles import load_styles
    return [{"name": k, "label": v.get("label", "")}
            for k, v in load_styles().items()]


@router.get("/api/automation")
def get_automation(user: dict = Depends(auth.require_user)):
    cfg = get_config(user["id"])
    return {"automation": _public_cfg(cfg), "styles": _styles(),
            "yt_dlp_available": Path(YT_DLP).exists(),
            "poll_interval_min": POLL_MIN}


class AutomationIn(BaseModel):
    enabled: bool = False
    channel_handle: str = ""
    auto_edit_style: str = DEFAULT_STYLE


@router.post("/api/automation")
def set_automation(body: AutomationIn, user: dict = Depends(auth.require_user)):
    from pipeline.styles import load_styles

    cfg = get_config(user["id"])
    style = (body.auto_edit_style or DEFAULT_STYLE).strip()
    if style not in load_styles():
        return JSONResponse({"error": f"Unknown style '{style}'."}, status_code=400)

    handle = (body.channel_handle or "").strip()
    if handle:
        # Resolve when the handle changed or we don't yet have an id for it.
        if handle != cfg.get("channel_handle") or not cfg.get("channel_id"):
            resolved = youtube.resolve_channel(handle)
            if not resolved or not resolved.get("channel_id"):
                return JSONResponse(
                    {"error": "Couldn't find that channel. Use your @handle "
                              "(e.g. @codewithbod) or the channel URL."},
                    status_code=400)
            cfg["channel_handle"] = handle
            cfg["channel_id"] = resolved["channel_id"]
            cfg["channel_title"] = resolved.get("title") or handle
            # Seed to the current latest so enabling never backfills the channel.
            ups = youtube.fetch_uploads(resolved["channel_id"])
            cfg["last_video_id"] = ups[0]["video_id"] if ups else ""
    else:
        # Clearing the handle turns the feature off and forgets the channel.
        cfg.update({"channel_handle": "", "channel_id": "", "channel_title": "",
                    "last_video_id": ""})

    cfg["auto_edit_style"] = style
    cfg["enabled"] = bool(body.enabled) and bool(cfg.get("channel_id"))
    cfg["last_error"] = ""
    save_config(user["id"], cfg)
    return {"ok": True, "automation": _public_cfg(cfg)}


@router.post("/api/automation/check")
def check_now(request: Request, user: dict = Depends(auth.require_user)):
    """Run one poll cycle for the caller right now (the 'Check for new videos'
    button). Works even when disabled, so the user can test the connection."""
    cfg = get_config(user["id"])
    if not cfg.get("channel_id"):
        return JSONResponse(
            {"error": "Set and save your channel first."}, status_code=400)
    profile = auth.get_profile(user["id"])
    res = poll_user(user["id"], profile, cfg, force=True)
    if res.get("error"):
        return JSONResponse({"error": res["error"]}, status_code=502)
    return {"ok": True, "queued": res.get("new", 0),
            "automation": _public_cfg(get_config(user["id"]))}
