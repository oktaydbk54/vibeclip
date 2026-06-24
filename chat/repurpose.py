"""Headless automated repurposing (Faz 3.2) — the cross-platform moat.

One call turns a long video (a local path OR a URL) into finished shorts with no
web UI and no human in the loop: download (if a URL) → generate_clips → optional
style → optional AI-generated b-roll → optional translated captions / dub →
export. It reuses the EXACT REGISTRY tools the web app and chat agent drive, so
behavior is identical; only the orchestration is headless.

This is precisely what a native-desktop editor structurally cannot do — run on a
server, in CI, in a cron job, or behind an external agent over MCP, across
platforms. An agent can call this once and get a manifest of deliverables back.

Robustness: a per-clip step that fails is recorded and skipped, never sinking the
batch; generation/translation are best-effort (a missing key just omits that
enhancement). Returns {ok, project, clips:[...], errors:[...]}.
"""

from __future__ import annotations

from pathlib import Path


def _is_url(s: str) -> bool:
    return s.lower().startswith(("http://", "https://"))


def _resolve_source(source: str) -> Path:
    """A local path is used as-is; a URL is downloaded via the shared yt-dlp
    wrapper (the same one YouTube auto-ingest and style-learning use)."""
    if not _is_url(source):
        p = Path(source).expanduser()
        if not p.exists():
            raise FileNotFoundError(f"Video not found: {source}")
        return p
    from chat.automation import DOWNLOAD_DIR, download_video
    import hashlib
    basename = hashlib.sha1(source.encode()).hexdigest()[:12]
    return download_video(source, DOWNLOAD_DIR, basename)


def auto_repurpose(source: str, *, count: int | None = None,
                   max_duration: float | None = None, style: str = "",
                   aspect: str = "", generate_broll: bool = False,
                   caption_language: str = "", dub_language: str = "",
                   export: bool = True,
                   progress=None) -> dict:
    """Long video (path or URL) → finished shorts, fully headless.

    count/max_duration: passed to generate_clips. style: a style preset applied
    to every clip. generate_broll: add AI-generated b-roll per clip (needs
    GENMEDIA_API_KEY). caption_language/dub_language: translate captions / re-
    voice every clip. aspect/export: export ratio + whether to render finals.
    """
    from chat import tools
    from chat.session import Session

    def _say(msg: str) -> None:
        if progress:
            progress(msg)

    try:
        path = _resolve_source(source)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"source: {e}"}

    _say(f"Ingesting {path.name}")
    sess = Session.load_or_create(str(path))

    gen = tools.generate_clips(sess, count=count, max_duration=max_duration)
    if not gen.get("ok", True):
        return {"ok": False, "error": gen.get("error", "generate_clips failed")}
    clip_ids = [c["id"] for c in sess.data.get("clips", [])]
    if not clip_ids:
        return {"ok": False, "error": "No clips were generated."}
    _say(f"{len(clip_ids)} clips found")

    errors: list[str] = []

    def _step(cid: int, name: str, fn) -> None:
        try:
            res = fn()
            if isinstance(res, dict) and res.get("ok") is False:
                errors.append(f"clip {cid} {name}: {res.get('error')}")
        except Exception as e:  # noqa: BLE001 — one step never sinks the batch
            errors.append(f"clip {cid} {name}: {type(e).__name__}: {e}")

    out_clips: list[dict] = []
    for cid in clip_ids:
        if style:
            _step(cid, "style", lambda: tools.apply_style(sess, cid, style))
        if generate_broll:
            _step(cid, "broll",
                  lambda: tools.add_broll(sess, cid, auto=True, generate=True))
        if caption_language:
            _step(cid, "captions",
                  lambda: tools.set_caption_language(sess, cid, caption_language))
        if dub_language:
            _step(cid, "dub", lambda: tools.set_dub(sess, cid, dub_language))

        file = None
        if export:
            try:
                ex = tools.export_clip(sess, cid, aspect=aspect)
                file = ex.get("file") if ex.get("ok", True) else None
                if not file:
                    errors.append(f"clip {cid} export: {ex.get('error')}")
            except Exception as e:  # noqa: BLE001
                errors.append(f"clip {cid} export: {type(e).__name__}: {e}")
        clip = sess.clip(cid)
        out_clips.append({"id": cid, "title": clip.get("title", ""),
                          "status": clip.get("status", "pending"),
                          "file": file or clip.get("current")})
        _say(f"clip {cid} done")

    return {"ok": True, "project": sess.path.parent.name,
            "count": len(out_clips), "clips": out_clips,
            "errors": errors or None}
