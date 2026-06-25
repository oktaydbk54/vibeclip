"""Local web UI for the chat editor.

Usage:
    uv run python -m chat.app <video.mp4> [port]

Serves a single-page chat + preview app that reuses chat/session.py,
chat/agent.py and chat/tools.py verbatim. Endpoints:
    GET  /            -> the app (chat/static/index.html)
    GET  /api/state   -> session summary + clip list
    POST /api/chat    -> {message} -> run one agent turn
    GET  /media/{id}  -> stream a clip's current artifact
"""

from __future__ import annotations

import copy
import json
import os
import queue
import re
import sys
import threading
from pathlib import Path
from urllib.parse import quote

from fastapi import (Depends, FastAPI, File, Form, Query, Request, UploadFile,
                     HTTPException)
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               PlainTextResponse, RedirectResponse, Response,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from chat import auth
from chat import blog_render
from chat import webutil
from chat.agent import run_turn
from chat.jobs import MANAGER
from chat.session import SESSIONS_DIR, Session
from chat.tools import REGISTRY
from pipeline import config
from pipeline import progress as pg

# Serializes all SESSION access. The job worker holds it for the duration of a
# render; read endpoints acquire it so they never observe torn mid-replay state.
SESSION_LOCK = threading.RLock()

# Tools the pro UI may invoke directly (bypassing the chat LLM). Grows per phase;
# everything here goes through the same REGISTRY impl the agent would call.
TOOL_WHITELIST = {"export_captions", "undo", "redo", "remove_section",
                  "remove_fillers", "set_speed",
                  "nudge_edit", "add_marker", "remove_marker", "set_cut",
                  "edit_event", "delete_event", "lock_clip", "unlock_clip",
                  "set_autonomy", "export_timeline", "restore_section",
                  "set_denoise", "set_music", "add_sound_effect", "add_zoom",
                  "set_clip_status", "render_clip", "export_clip",
                  "generate_metadata", "find_moment",
                  "add_broll", "rerun_broll", "assemble_reel",
                  "generate_storyboard",
                  "generate_asset", "generate_variations",
                  "generate_video_from_asset",
                  "move_asset_to_folder", "organize_assets",
                  "apply_style", "set_subtitles",
                  "revert_plan", "regenerate_plan"}

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="shorts-mcp chat editor")
app.include_router(auth.router)
from chat import social  # noqa: E402 — after `app` so its lazy import of app works
app.include_router(social.router)
from chat import automation  # noqa: E402 — lazy-imports chat.app in its job closures
app.include_router(automation.router)
SESSION: Session | None = None
HISTORY: list[dict] = []
# studio2 is project-keyed, so chat history can't live in the single global
# HISTORY (which belongs to the legacy global SESSION). Keep one rolling history
# per project id, in memory only (the edits themselves persist to project.json
# via the tools). Capped per turn so a long session can't bloat memory.
_V2_HISTORY: dict[str, list[dict]] = {}


class ChatIn(BaseModel):
    message: str
    mode: str = "pro"   # "basit" -> guide persona in the agent
    tier: str = "fast"  # "pro" -> stronger model for intent + planning


def _require_session() -> None:
    """Guard for SESSION-reading endpoints. Raises a 409 HTTPException telling
    the client to bounce to /projects when no project is active. The single
    global SESSION can be None now that the server boots with zero projects (the
    (B) switcher design); every active-project endpoint funnels through here so
    they fail cleanly instead of asserting."""
    if SESSION is None:
        raise HTTPException(
            status_code=409,
            detail={"error": "no_active_project", "next": "/projects"})


class ToolIn(BaseModel):
    name: str
    args: dict = {}


def _clips_payload() -> list[dict]:
    assert SESSION is not None
    out = []
    for c in SESSION.data["clips"]:
        out.append({
            "id": c["id"],
            "title": c["title"],
            "start": c["start"],
            "end": c["end"],
            "score": c.get("score", 0),
            "hook": c.get("hook", ""),
            "reason": c.get("reason", ""),
            "scores": c.get("scores"),
            "status": Session.clip_status(c),
            "style": c.get("style"),
            "variant_of": c.get("variant_of"),
            "stages": [st["name"] for st in c["stages"]],
            "url": f"/media/{c['id']}" if c.get("current") else None,
            # Progressive open: a clip can be playable (url set to a captionless
            # preview) before every stage is rendered. 'complete' is true only
            # once all stages have an 'output' — the UI uses it to background-
            # finish captions when re-opening a preview-only clip.
            "complete": bool(c.get("current"))
            and all("output" in st for st in c["stages"]),
            # Phase 5 — full-res deliverable, once export_clip has run.
            "export_url": f"/media/export/{c['id']}" if c.get("export") else None,
        })
    return out


def _comps_payload() -> list[dict]:
    assert SESSION is not None
    return [{"id": cp["id"], "title": cp["title"], "clips": cp["clips"],
             "duration": cp["duration"], "url": f"/media/comp/{cp['id']}"}
            for cp in SESSION.data.get("compilations", [])]


def _serve_html(filename: str) -> str:
    """Read a static HTML page and stamp every local /static/*.js|css ref with
    a ?v=<file-mtime> cache-buster. Static assets are served with no cache
    headers, so browsers heuristically cache them — without this, a user (or an
    automation tab) keeps running yesterday's JS/CSS after a change ships. The
    token is each file's own mtime, so only edited assets re-download."""
    html = webutil.inject_head((STATIC / filename).read_text())

    def stamp(m: "re.Match") -> str:
        url = m.group(1)
        f = STATIC / url[len("/static/"):]
        try:
            ver = int(f.stat().st_mtime)
        except OSError:
            return m.group(0)
        sep = "&" if "?" in url else "?"
        return m.group(0).replace(url, f"{url}{sep}v={ver}")

    return re.sub(r'(?:src|href)="(/static/[^"]+\.(?:js|css))"', stamp, html)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    # Public landing page (built by the parallel front-end agent). Transitional
    # fallback to the studio app if landing.html doesn't exist yet.
    if (STATIC / "landing.html").exists():
        return _serve_html("landing.html")
    return _serve_html("index.html")


# Page routes that need the SESSION global live here (not auth.py) so the
# no-active-project redirect can read it; auth.py must not import app.py.
@app.get("/studio", response_class=HTMLResponse)
def studio_page(request: Request, project: str | None = None):
    global SESSION, HISTORY
    user = auth.get_user(request)
    if user is None:
        nxt = "/studio" + (f"?project={quote(project)}" if project else "")
        return RedirectResponse(f"/login?next={quote(nxt, safe='')}", 302)
    if not user["onboarded"]:
        return RedirectResponse("/onboarding", 302)
    # A project id in the URL is the source of truth: a deep-link / refresh to
    # /studio?project=<name> swaps the active SESSION to it (when the worker is
    # idle), so each project is shareable by URL — even from a cold start.
    if project and (SESSIONS_DIR / project / "project.json").exists():
        if SESSION is None or SESSION.data.get("name") != project:
            if MANAGER.current is None and MANAGER.q.empty():
                with SESSION_LOCK:
                    if MANAGER.current is None and MANAGER.q.empty():
                        SESSION = Session.open_existing(project)
                        HISTORY = []
    if SESSION is None:                       # (B) switcher: no active project
        return RedirectResponse("/projects", 302)
    # Always reflect the ACTIVE project's id in the URL (shareable + visible):
    # bare /studio, a stale id, or a busy/unknown id all normalize here.
    active = SESSION.data.get("name")
    if active and project != active:
        return RedirectResponse(f"/studio?project={quote(active)}", 302)
    return HTMLResponse(_serve_html("index.html"))


@app.get("/projects", response_class=HTMLResponse)
def projects_page(request: Request):
    user = auth.get_user(request)
    if user is None:
        return RedirectResponse("/login?next=/projects", 302)
    if not user["onboarded"]:
        return RedirectResponse("/onboarding", 302)
    return HTMLResponse(_serve_html("projects.html"))


# ------------------------------------------------------------------- blog
# Public, server-rendered, SEO-first. No auth — these pages exist to be crawled
# and indexed. Content lives in blog_content.py; HTML is built in blog_render.py.
@app.get("/blog", response_class=HTMLResponse, include_in_schema=False)
def blog_index() -> str:
    return blog_render.render_index()


@app.get("/blog/{slug}", response_class=HTMLResponse, include_in_schema=False)
def blog_post(slug: str):
    page = blog_render.render_article(slug)
    if page is None:
        return RedirectResponse("/blog", 302)
    return HTMLResponse(page)


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap_xml():
    return Response(blog_render.render_sitemap(), media_type="application/xml")


@app.get("/robots.txt", include_in_schema=False)
def robots_txt():
    return PlainTextResponse(blog_render.render_robots())


@app.get("/llms.txt", include_in_schema=False)
def llms_txt():
    # Machine-readable product card for AI answer engines (GEO). JS-free, citable.
    return PlainTextResponse(blog_render.render_llms())


# ------------------------------------------------------------------- admin
@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    """Admin dashboard. Logged-out → /login; logged-in non-admins → /projects
    (no leak that the page exists). Admin status comes from the ADMIN_EMAILS
    allowlist, applied on signup/verify/login."""
    user = auth.get_user(request)
    if user is None:
        return RedirectResponse("/login?next=/admin", 302)
    if not user.get("is_admin"):
        return RedirectResponse("/projects", 302)
    return HTMLResponse(_serve_html("admin.html"))


def _content_counts() -> dict:
    """Global project + clip totals scanned off SESSIONS_DIR (projects are not
    per-user in this app, so these are studio-wide numbers)."""
    projects = clips = 0
    if SESSIONS_DIR.exists():
        for sdir in SESSIONS_DIR.iterdir():
            pfile = sdir / "project.json"
            if not pfile.exists():
                continue
            projects += 1
            try:
                clips += len(json.loads(pfile.read_text()).get("clips") or [])
            except Exception:  # noqa: BLE001 — skip unparseable projects
                continue
    return {"projects": projects, "clips": clips}


@app.get("/api/admin/stats")
def admin_stats(_admin: dict = Depends(auth.require_admin)) -> dict:
    return {
        "users": auth.list_users(),
        "counts": auth.user_counts(),
        "content": _content_counts(),
    }


class AdminDeleteIn(BaseModel):
    id: int


@app.post("/api/admin/users/delete")
def admin_delete_user(body: AdminDeleteIn,
                      admin: dict = Depends(auth.require_admin)):
    if int(body.id) == int(admin["id"]):
        return JSONResponse(
            {"error": "You can't delete your own admin account."},
            status_code=400)
    if not auth.delete_user(int(body.id)):
        return JSONResponse({"error": "No such user."}, status_code=404)
    return {"ok": True}


@app.get("/api/state")
def state():
    if SESSION is None:
        return JSONResponse(
            {"error": "no_active_project", "next": "/projects"},
            status_code=409)
    with SESSION_LOCK:
        return {"source": SESSION.data["source"], "clips": _clips_payload(),
                "compilations": _comps_payload(),
                # project id — drives the appbar id badge + URL ?project= sync.
                "name": SESSION.data.get("name"),
                "pending_plan": SESSION.data.get("pending_plan"),
                # Phase 4 — queue cursor + batch progress (additive). Old UIs
                # that don't read these are unaffected.
                "active_clip_id": SESSION.active_clip_id(),
                "queue": SESSION.queue_summary()}


class ActiveClipIn(BaseModel):
    clip_id: int


@app.post("/api/active-clip")
def active_clip(body: ActiveClipIn,
                _user: dict = Depends(auth.require_user)) -> dict:
    """Phase 4 — move the sequential-editing-queue focus cursor. Gated like the
    other mutating UI calls (require_user). This is NOT a render and NOT A/B-
    gated: it only changes WHICH clip is in focus, never the clip's edits. The
    UI then loads that clip via the existing playClip() path. Returns the new
    cursor + a fresh batch summary so the appbar can update in one round-trip."""
    _require_session()
    with SESSION_LOCK:
        try:
            SESSION.set_active_clip(body.clip_id)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=404)
        return {"ok": True, "active_clip_id": SESSION.active_clip_id(),
                "queue": SESSION.queue_summary()}


@app.get("/api/clips")
def clips_queue() -> dict:
    """Phase 3 — the ranked candidate QUEUE. Read-only (matches /api/state's
    no-auth read pattern). Returns clips in the existing ranked order (id asc =
    score desc) with each candidate's headline virality score, sub-axis
    breakdown, hook, reason and review status."""
    _require_session()
    with SESSION_LOCK:
        clips = []
        for c in SESSION.data["clips"]:
            clips.append({
                "id": c["id"],
                "title": c["title"],
                "start": c["start"],
                "end": c["end"],
                "duration": round(c["end"] - c["start"], 2),
                "score": c.get("score", 0),
                "scores": c.get("scores"),
                "hook": c.get("hook", ""),
                "reason": c.get("reason", ""),
                "status": Session.clip_status(c),
            })
        # Phase 4 — surface the focus cursor + batch summary alongside the queue
        # so the UI can render "clip N / M · X approved" without another fetch.
        return {"clips": clips, "active_clip_id": SESSION.active_clip_id(),
                "queue": SESSION.queue_summary()}


@app.get("/api/assets")
def assets_list() -> dict:
    from pipeline import assets as alib
    rows = []
    for r in alib.load_catalog():
        thumb = alib.THUMBS_DIR / f"{r['id']}.jpg"
        rows.append({"id": r["id"], "kind": r["kind"],
                     "description": r.get("description", ""),
                     "tags": r.get("tags", []),
                     "name": r.get("filename_original", ""),
                     "path": r.get("path", ""),
                     "folder": (r.get("folder") or "").strip(),
                     "thumb": f"/asset_thumb/{r['id']}"
                              if thumb.exists() else None})
    return {"assets": rows}


@app.post("/api/assets/upload")
async def upload_asset(file: UploadFile = File(...),
                       _user: dict = Depends(auth.require_user)):
    from pipeline import assets as alib
    incoming = alib.ASSETS_DIR / "_incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    dest = incoming / (file.filename or "upload.bin")
    dest.write_bytes(await file.read())
    try:
        row = alib.ingest_file(str(dest), original_name=file.filename or "")
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    finally:
        dest.unlink(missing_ok=True)
    return {"ok": True, "asset": {"id": row["id"], "kind": row["kind"],
                                  "description": row["description"]}}


@app.post("/api/upload-video")
async def upload_video(file: UploadFile = File(...),
                       process: str = Form("0"),
                       user: dict = Depends(auth.require_user)):
    """Phase 0 — ingest a new full-res source and make it the active session.

    Streams the upload to outputs/sessions/<name>/source.<ext>, ffprobe-validates
    it's a real video, then creates/swaps the global SESSION via
    Session.load_or_create (which kicks off the background 540p-proxy + keyframe
    build). Refuses with 409 while a render/chat job is queued or running, since
    swapping the source out from under an in-flight job would corrupt it.

    process="1" (the /projects long-video flow) ADDITIONALLY stamps an
    intake={mode:long_video,...} block and submits the chained auto-clipping job
    (generate_clips), recording its id in intake.processing_job. The studio's
    existing call sends no `process` field -> behavior is identical to before.

    Returns {ok, name, duration, proxy: "building"[, processing_job]}.
    """
    global SESSION, HISTORY
    # 409 guard: never swap the source while the single worker is busy or has
    # work queued — the running job holds SESSION_LOCK and edits SESSION.data.
    if MANAGER.current is not None or not MANAGER.q.empty():
        return JSONResponse(
            {"error": "A job is running. Wait for it to finish, then retry."},
            status_code=409)

    # Per-account storage quota — refuse early (before streaming the upload).
    qb = _quota_block(user["id"])
    if qb is not None:
        return qb

    ext = Path(file.filename or "").suffix.lower() or ".mp4"
    if ext not in (".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi"):
        return JSONResponse(
            {"error": f"Unsupported video type '{ext}'."}, status_code=400)
    name = Path(file.filename or "video").stem or "video"
    from chat.session import SESSIONS_DIR
    sdir = SESSIONS_DIR / name
    sdir.mkdir(parents=True, exist_ok=True)
    # Save under the ORIGINAL stem (not a literal "source"): load_or_create keys
    # the session dir on the video file's stem, so naming it source.<ext> would
    # spawn a session called "source" while the file sits in <name>/ — every
    # upload would then collide on "source". Keeping the stem makes the session
    # name, dir, and source file agree.
    dest = sdir / f"{name}{ext}"
    dest.write_bytes(await file.read())

    from pipeline.media import ffprobe_info
    try:
        info = ffprobe_info(str(dest))
    except Exception as e:  # noqa: BLE001 — surface a clean 400 to the client
        dest.unlink(missing_ok=True)
        return JSONResponse(
            {"error": f"Not a readable video: {type(e).__name__}: {e}"},
            status_code=400)
    if not info.get("width") or not info.get("duration"):
        dest.unlink(missing_ok=True)
        return JSONResponse(
            {"error": "File has no decodable video stream."}, status_code=400)
    limit = config.MAX_UPLOAD_SECONDS
    if limit and info["duration"] > limit:
        dest.unlink(missing_ok=True)
        return JSONResponse(
            {"error": f"Videos are limited to {limit // 60} minutes on this "
                      f"instance (yours is {info['duration'] / 60:.1f} min). "
                      "Trim it shorter and try again.", "too_long": True},
            status_code=400)

    # Re-check the guard under the lock right before swapping, then create/swap
    # the session. load_or_create submits the background proxy build.
    with SESSION_LOCK:
        if MANAGER.current is not None or not MANAGER.q.empty():
            return JSONResponse(
                {"error": "A job started. Retry once it finishes."},
                status_code=409)
        SESSION = Session.load_or_create(str(dest))
        SESSION.data["owner_uid"] = user["id"]   # for the per-user quota + list
        SESSION.save()
        HISTORY = []
        proc_id = None
        if process == "1":
            SESSION.data["intake"] = {"mode": "long_video",
                                      "processing_job": None,
                                      "error": None, "processed_at": None}
            SESSION.save()
            job = _submit_processing_job(SESSION, auth.user_llm_override(user))
            proc_id = job.id
            SESSION.data["intake"]["processing_job"] = proc_id
            SESSION.save()
    resp = {"ok": True, "name": name, "duration": info["duration"],
            "proxy": "building"}
    if proc_id is not None:
        resp["processing_job"] = proc_id
    return resp


def _owned_project_count(uid: int) -> int:
    """How many stored projects belong to this user. Used for the per-account
    storage quota. Legacy projects (no owner_uid, pre-multi-user) don't count."""
    n = 0
    if SESSIONS_DIR.exists():
        for sdir in SESSIONS_DIR.iterdir():
            pfile = sdir / "project.json"
            if not pfile.exists():
                continue
            try:
                if json.loads(pfile.read_text()).get("owner_uid") == uid:
                    n += 1
            except Exception:  # noqa: BLE001 — skip unparseable projects
                continue
    return n


def _quota_block(uid: int) -> JSONResponse | None:
    """403 if the user is already at their project cap, else None.
    MAX_PROJECTS_PER_USER=0 disables the cap (self-host with room)."""
    cap = config.MAX_PROJECTS_PER_USER
    if cap and _owned_project_count(uid) >= cap:
        plural = "project" if cap == 1 else "projects"
        return JSONResponse(
            {"error": f"You've reached your limit of {cap} {plural} on this "
                      "instance. Delete your existing project to upload a new one.",
             "quota": True}, status_code=403)
    return None


def _submit_processing_job(sess: Session, llm_override=None):
    """Submit the auto-clipping job for a long-video project. The closure
    CAPTURES the given Session instance (never the global SESSION) — mirroring
    the _submit_proxy_job pattern — so a later project swap can't redirect this
    job's writes. Runs generate_clips directly (off the /api/tool whitelist, no
    chat turn) under SESSION_LOCK, then records the outcome on intake: error
    (None on success), cleared processing_job, processed_at timestamp. The
    proxy job that load_or_create queued runs FIRST (FIFO single worker), so
    generate_clips' proxy_or_source() picks up the freshly-built proxy."""
    import datetime
    from chat.tools import generate_clips

    def _run(job=None) -> dict:
        with SESSION_LOCK:
            try:
                result = generate_clips(sess)
                err = None if result.get("ok") else result.get("error")
            except Exception as e:  # noqa: BLE001 — surface to intake.error
                result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
                err = result["error"]
            intake = sess.data.setdefault(
                "intake", {"mode": "long_video"})
            intake["error"] = err
            intake["processing_job"] = None
            intake["processed_at"] = \
                datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat() + "Z"
            sess.save()
        return result

    return MANAGER.submit("tool", "auto-clip", _run, llm_override=llm_override)


# --------------------------------------------------------------- projects
def _live_job_public() -> dict | None:
    """The active project's current OR head-of-queue job as a public() dict, so
    derive_status can tell whether THIS project is the one processing. Only the
    running/next job matters for status; deeper queue items don't change it."""
    cur = MANAGER.current
    if cur is not None:
        return cur.public()
    return None


def _clip_counts(clips: list[dict]) -> dict:
    counts = {"total": len(clips), "pending": 0, "approved": 0,
              "skipped": 0, "exported": 0}
    for c in clips:
        counts[Session.clip_status(c)] += 1
    return counts


def _can_see_project(data: dict, user: dict) -> bool:
    """A user sees their own projects. Legacy/unowned (owner_uid=None) projects
    are visible to admins, and — on a single-tenant self-host box
    (CLAIM_ORPHAN_PROJECTS, the default) — to any logged-in user, who claims them
    on open. A public instance sets the flag False so orphans never leak."""
    owner = data.get("owner_uid")
    if owner == user["id"]:
        return True
    if owner is None:
        return bool(user.get("is_admin")) or config.CLAIM_ORPHAN_PROJECTS
    return False


@app.get("/api/projects")
def list_projects(user: dict = Depends(auth.require_user)) -> dict:
    """Scan SESSIONS_DIR for */project.json and return each project's derived
    pipeline status + clip counts for the /projects switcher, restricted to the
    caller's own projects. The ACTIVE project is read live from SESSION under the
    lock; the rest are parsed tolerantly off disk (unparseable ones skipped)."""
    active_name = SESSION.data["name"] if SESSION is not None else None
    busy = MANAGER.current is not None or not MANAGER.q.empty()
    live = _live_job_public()
    rows = []
    if SESSIONS_DIR.exists():
        for sdir in SESSIONS_DIR.iterdir():
            pfile = sdir / "project.json"
            if not pfile.exists():
                continue
            is_active = sdir.name == active_name
            if is_active:
                with SESSION_LOCK:
                    data = SESSION.data
                    if not _can_see_project(data, user):
                        continue
                    rows.append(_project_row(data, sdir, pfile, True, live))
            else:
                try:
                    data = json.loads(pfile.read_text())
                except Exception:  # noqa: BLE001 — skip unparseable projects
                    continue
                if not _can_see_project(data, user):
                    continue
                rows.append(_project_row(data, sdir, pfile, False, None))
    # active first, then project.json mtime desc.
    rows.sort(key=lambda r: (not r["active"], -r["_mtime"]))
    for r in rows:
        r.pop("_mtime", None)
    owned = _owned_project_count(user["id"])
    cap = config.MAX_PROJECTS_PER_USER
    return {"projects": rows, "active": active_name, "busy": busy,
            "limits": {"max_seconds": config.MAX_UPLOAD_SECONDS,
                       "max_projects": cap},
            "owned": owned,
            "at_quota": bool(cap and owned >= cap)}


def _project_row(data: dict, sdir: Path, pfile: Path,
                 active: bool, live: dict | None) -> dict:
    clips = data.get("clips") or []
    intake = data.get("intake") or {}
    src = data.get("source") or {}
    try:
        mtime = pfile.stat().st_mtime
    except OSError:
        mtime = 0.0
    import datetime
    return {
        "name": data.get("name", sdir.name),
        "display_name": data.get("display_name") or data.get("name", sdir.name),
        "mode": intake.get("mode", "long_video"),
        "status": Session.derive_status(data, live),
        "created": data.get("created"),
        "modified": datetime.datetime.utcfromtimestamp(mtime).isoformat() + "Z"
        if mtime else None,
        "duration": src.get("duration"),
        "clips": _clip_counts(clips),
        "best_score": max((int(c.get("score") or 0) for c in clips), default=0),
        "folder": data.get("folder") or "",
        "source": intake.get("source") or "",
        "youtube_url": intake.get("youtube_url") or "",
        "thumb": f"/api/projects/{sdir.name}/thumb",
        "active": active,
        "error": intake.get("error"),
        "_mtime": mtime,
    }


class ProjectOpenIn(BaseModel):
    name: str


@app.post("/api/projects/open")
def open_project(body: ProjectOpenIn,
                 user: dict = Depends(auth.require_user)):
    """Swap the global SESSION to an existing project (the (B) switcher). 404 if
    it has no project.json; 409 if the single worker is busy (double-checked
    under the lock, like upload_video). Routes through Session.open_existing
    (NOT load_or_create, which keys on the video stem and would re-create the
    session if the source moved)."""
    global SESSION, HISTORY
    name = body.name
    pfile = SESSIONS_DIR / name / "project.json"
    if not pfile.exists():
        return JSONResponse({"error": f"No project '{name}'."},
                            status_code=404)
    try:
        if not _can_see_project(json.loads(pfile.read_text()), user):
            return JSONResponse({"error": "Not your project."}, status_code=403)
    except Exception:  # noqa: BLE001 — unreadable project.json
        return JSONResponse({"error": f"No project '{name}'."}, status_code=404)
    if MANAGER.current is not None or not MANAGER.q.empty():
        return JSONResponse(
            {"error": "Another project is rendering."}, status_code=409)
    with SESSION_LOCK:
        if MANAGER.current is not None or not MANAGER.q.empty():
            return JSONResponse(
                {"error": "Another project is rendering."}, status_code=409)
        SESSION = Session.open_existing(name)
        # Claim a legacy orphan (owner_uid=None) for the opener so ownership is
        # deterministic from here on (matches _can_see_project's self-host path).
        if SESSION.data.get("owner_uid") is None:
            SESSION.data["owner_uid"] = user["id"]
            SESSION.save()
        HISTORY = []
    return {"ok": True, "name": name, "next": f"/studio?project={quote(name)}"}


@app.post("/api/projects/{name}/process")
def process_project(name: str, user: dict = Depends(auth.require_user)):
    """Retry/trigger auto-clipping for a long-video project (no chat turn). 409
    if busy. If the project isn't the active one, open-swap to it first (safe:
    we just verified not-busy, all under the lock). Then submit a job whose
    closure CAPTURES sess (never the global SESSION)."""
    global SESSION, HISTORY
    pfile = SESSIONS_DIR / name / "project.json"
    if not pfile.exists():
        return JSONResponse({"error": f"No project '{name}'."},
                            status_code=404)
    try:
        if not _can_see_project(json.loads(pfile.read_text()), user):
            return JSONResponse({"error": "Not your project."}, status_code=403)
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": f"No project '{name}'."}, status_code=404)
    if MANAGER.current is not None or not MANAGER.q.empty():
        return JSONResponse(
            {"error": "Another project is rendering."}, status_code=409)
    with SESSION_LOCK:
        if MANAGER.current is not None or not MANAGER.q.empty():
            return JSONResponse(
                {"error": "Another project is rendering."}, status_code=409)
        if SESSION is None or SESSION.data["name"] != name:
            SESSION = Session.open_existing(name)
            HISTORY = []
        sess = SESSION
        sess.data.setdefault("intake", {"mode": "long_video",
                                        "processing_job": None,
                                        "error": None, "processed_at": None})
        sess.data["intake"]["error"] = None
        sess.save()
        job = _submit_processing_job(sess, auth.user_llm_override(user))
        sess.data["intake"]["processing_job"] = job.id
        sess.save()
    return {"ok": True, "job_id": job.id}


@app.post("/api/projects/upload-clips")
async def upload_clips(name: str = Form(...),
                       files: list[UploadFile] = File(...),
                       user: dict = Depends(auth.require_user)):
    """Own-clips project: the user uploads already-finished clips, skipping
    auto-clipping. 409 if busy. Streams each upload into the new session dir,
    builds the project via Session.create_from_clips, swaps it active, then
    submits a 'prepare' job that does a trivial full-span precise cut per clip
    (the cut artifact is the timing origin words_for/timing_chain need)."""
    global SESSION, HISTORY
    if MANAGER.current is not None or not MANAGER.q.empty():
        return JSONResponse(
            {"error": "Another project is rendering."}, status_code=409)
    qb = _quota_block(user["id"])
    if qb is not None:
        return qb
    name = (name or "").strip() or "clips"
    safe = "".join(ch for ch in name if ch.isalnum() or ch in (" ", "_", "-")
                   ).strip().replace(" ", "_") or "clips"
    sdir = SESSIONS_DIR / safe
    if sdir.exists() and (sdir / "project.json").exists():
        return JSONResponse(
            {"error": f"A project named '{safe}' already exists."},
            status_code=409)
    sdir.mkdir(parents=True, exist_ok=True)
    from pipeline.media import ffprobe_info
    saved: list[str] = []
    for i, f in enumerate(files, start=1):
        ext = Path(f.filename or "").suffix.lower() or ".mp4"
        if ext not in (".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi"):
            continue
        dest = sdir / f"user_clip{i:02d}{ext}"
        dest.write_bytes(await f.read())
        try:
            info = ffprobe_info(str(dest))
        except Exception:  # noqa: BLE001
            dest.unlink(missing_ok=True)
            continue
        if not info.get("width") or not info.get("duration"):
            dest.unlink(missing_ok=True)
            continue
        saved.append(str(dest.resolve()))
    if not saved:
        return JSONResponse(
            {"error": "No readable video clips uploaded."}, status_code=400)
    with SESSION_LOCK:
        if MANAGER.current is not None or not MANAGER.q.empty():
            return JSONResponse(
                {"error": "Another project is rendering."}, status_code=409)
        SESSION = Session.create_from_clips(safe, saved)
        SESSION.data["owner_uid"] = user["id"]   # per-user quota + list
        SESSION.save()
        HISTORY = []
        sess = SESSION
    job = _submit_prepare_job(sess)
    return {"ok": True, "name": safe, "clips": len(saved),
            "preparing_job": job.id, "next": "/studio"}


def _submit_prepare_job(sess: Session):
    """Own-clips 'prepare' job (captures sess). Per clip, a trivial full-span
    precise cut (start=0..clip dur) — the timing origin every downstream stage
    needs. NO DEFAULT_STAGES: these are finished clips, we must not mangle them.
    Stamps intake.processed_at and clears processing_job on completion."""
    import datetime

    def _run(job=None) -> dict:
        with SESSION_LOCK:
            try:
                for c in list(sess.data["clips"]):
                    sess.set_stage(c["id"], "cut",
                                   {"start": 0.0, "end": c["end"]})
                err = None
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
            intake = sess.data.setdefault("intake", {"mode": "own_clips"})
            intake["error"] = err
            intake["processing_job"] = None
            intake["processed_at"] = \
                datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat() + "Z"
            sess.save()
        return {"ok": err is None, "error": err}

    job = MANAGER.submit("tool", "prepare clips", _run)
    with SESSION_LOCK:
        sess.data.setdefault("intake", {"mode": "own_clips"})
        sess.data["intake"]["processing_job"] = job.id
        sess.save()
    return job


class ProjectRenameIn(BaseModel):
    display_name: str


@app.post("/api/projects/{name}/rename")
def rename_project(name: str, body: ProjectRenameIn,
                   _user: dict = Depends(auth.require_user)):
    """Write a display_name into that project.json ONLY — never rename the dir
    (artifact paths inside are absolute and keyed on the dir name)."""
    pfile = SESSIONS_DIR / name / "project.json"
    if not pfile.exists():
        return JSONResponse({"error": f"No project '{name}'."},
                            status_code=404)
    new_name = (body.display_name or "").strip()
    if not new_name:
        return JSONResponse({"error": "Name required."}, status_code=400)
    with SESSION_LOCK:
        if SESSION is not None and SESSION.data["name"] == name:
            SESSION.data["display_name"] = new_name
            SESSION.save()
        else:
            data = json.loads(pfile.read_text())
            data["display_name"] = new_name
            pfile.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    return {"ok": True, "display_name": new_name}


class ProjectFolderIn(BaseModel):
    folder: str = ""


@app.post("/api/projects/{name}/folder")
def set_project_folder(name: str, body: ProjectFolderIn,
                       _user: dict = Depends(auth.require_user)):
    """Tag a project with a folder name (blank clears it). Stored in
    project.json only — like rename, it never touches the dir."""
    pfile = SESSIONS_DIR / name / "project.json"
    if not pfile.exists():
        return JSONResponse({"error": f"No project '{name}'."},
                            status_code=404)
    folder = (body.folder or "").strip()[:60]
    with SESSION_LOCK:
        if SESSION is not None and SESSION.data["name"] == name:
            SESSION.data["folder"] = folder
            SESSION.save()
        else:
            data = json.loads(pfile.read_text())
            data["folder"] = folder
            pfile.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    return {"ok": True, "folder": folder}


@app.post("/api/projects/{name}/delete")
def delete_project(name: str, user: dict = Depends(auth.require_user)):
    """Remove a project's session dir. 409 only if it's the active project AND
    the worker is busy (can't yank a dir out from under a running render). If
    active, clears SESSION/HISTORY under the lock. Users may only delete their
    own projects (admins may delete any)."""
    global SESSION, HISTORY
    import shutil
    sdir = SESSIONS_DIR / name
    if not (sdir / "project.json").exists():
        return JSONResponse({"error": f"No project '{name}'."},
                            status_code=404)
    try:
        pdata = json.loads((sdir / "project.json").read_text())
    except Exception:  # noqa: BLE001
        pdata = {}
    if not _can_see_project(pdata, user):
        return JSONResponse({"error": "Not your project."}, status_code=403)
    is_active = SESSION is not None and SESSION.data["name"] == name
    busy = MANAGER.current is not None or not MANAGER.q.empty()
    if is_active and busy:
        return JSONResponse(
            {"error": "Project is rendering — stop it first."},
            status_code=409)
    with SESSION_LOCK:
        if is_active:
            SESSION = None
            HISTORY = []
        shutil.rmtree(sdir, ignore_errors=True)
    return {"ok": True}


@app.get("/api/projects/{name}/thumb")
def project_thumb(name: str):
    """Lazy ffmpeg poster (1s) for a project card: prefer the first clip's
    current render -> source.proxy_path -> source.path. Cached under
    CACHE_DIR/thumbs keyed by sha1(path+mtime). 404 if nothing decodable."""
    import hashlib
    import subprocess
    from pipeline import config as cfg
    pfile = SESSIONS_DIR / name / "project.json"
    if not pfile.exists():
        return JSONResponse({"error": "no such project"}, status_code=404)
    try:
        data = json.loads(pfile.read_text())
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "unreadable project"}, status_code=404)
    src = None
    clips = data.get("clips") or []
    if clips and clips[0].get("current") and Path(clips[0]["current"]).exists():
        src = clips[0]["current"]
    elif data.get("source", {}).get("proxy_path") \
            and Path(data["source"]["proxy_path"]).exists():
        src = data["source"]["proxy_path"]
    elif data.get("source", {}).get("path") \
            and Path(data["source"]["path"]).exists():
        src = data["source"]["path"]
    if not src:
        return JSONResponse({"error": "nothing decodable"}, status_code=404)
    tdir = cfg.CACHE_DIR / "thumbs"
    tdir.mkdir(exist_ok=True)
    key = hashlib.sha1(
        f"{src}:{Path(src).stat().st_mtime}".encode()).hexdigest()[:12]
    out = tdir / f"{key}.jpg"
    if not out.exists():
        subprocess.run(["ffmpeg", "-y", "-ss", "1", "-i", src, "-frames:v", "1",
                        "-vf", "scale=320:-2", str(out)],
                       capture_output=True, check=False)
    if not out.exists():
        return JSONResponse({"error": "thumb failed"}, status_code=404)
    return FileResponse(str(out), media_type="image/jpeg")


@app.get("/asset_thumb/{asset_id}")
def asset_thumb(asset_id: str):
    from pipeline import assets as alib
    p = alib.THUMBS_DIR / f"{asset_id}.jpg"
    if not p.exists():
        return JSONResponse({"error": "no thumb"}, status_code=404)
    return FileResponse(str(p), media_type="image/jpeg")


@app.get("/api/sounds")
def sounds() -> dict:
    """Sound palette for the pro UI: music tracks (drag onto the timeline ->
    set_music) and sfx kinds (drop at a time -> add_sound_effect)."""
    from pipeline import config as cfg
    from pipeline.orchestrate import SFX_LIBRARY
    music = []
    root = cfg.ROOT / "assets" / "music"
    if root.exists():
        for bucket in sorted(d for d in root.iterdir() if d.is_dir()):
            for f in sorted(bucket.iterdir()):
                if f.suffix.lower() in (".m4a", ".mp3", ".wav", ".aac",
                                        ".flac", ".ogg"):
                    music.append({"mood": bucket.name, "name": f.stem,
                                  "path": str(f)})
    return {"music": music, "sfx": sorted(SFX_LIBRARY)}


@app.get("/api/styles")
def styles() -> dict:
    from pipeline.styles import load_styles
    return {"styles": [{"name": k, "label": v.get("label", "")}
                       for k, v in load_styles().items()]}


def _chat_payload(reply: str, tools: list[str]) -> dict:
    assert SESSION is not None
    return {"reply": reply, "tools": tools, "clips": _clips_payload(),
            "compilations": _comps_payload(),
            "pending_plan": SESSION.data.get("pending_plan"),
            "clarify": getattr(SESSION, "last_clarify", None),
            "applied": getattr(SESSION, "last_applied", None),
            "history": _history_payload()}


def _run_chat(message: str, job=None, mode: str = "pro",
              profile_prompt: str = "", tier: str = "fast") -> dict:
    """Run one agent turn under the session lock. On cancel/crash mid-render the
    session is rolled back to its pre-turn state so clips stay consistent.
    profile_prompt is resolved in the request handler (no request context here)."""
    assert SESSION is not None
    tools_used: list[str] = []

    def on_tool(name: str, args: dict) -> None:
        pretty = ", ".join(f"{k}={v}" for k, v in args.items())
        tools_used.append(f"{name}({pretty})")
        # Narrate the tool call live into the job's SSE stream so the chat
        # bubble can show it AS IT HAPPENS (not only at turn end). The message
        # carries the bare tool name so the UI can map it to a friendly line;
        # full args follow in parens for the appbar chip / tooltip.
        pg.note(f"{name}|{pretty}" if pretty else name)

    with SESSION_LOCK:
        backup = copy.deepcopy(SESSION.data)
        try:
            reply = run_turn(SESSION, HISTORY, message, on_tool=on_tool,
                             mode=mode, profile_prompt=profile_prompt,
                             tier=tier)
        except pg.CancelledError:
            SESSION.data = backup
            SESSION.save()
            raise
        except Exception as e:
            SESSION.data = backup
            SESSION.save()
            reply = f"Error: {type(e).__name__}: {e}"
        if job is not None and job.cancel_event.is_set():
            SESSION.data = backup
            SESSION.save()
            return _chat_payload("Cancelled.", tools_used)
        return _chat_payload(reply, tools_used)


@app.post("/api/chat")
def chat(body: ChatIn, request: Request, sync: bool = False,
         _user: dict = Depends(auth.require_user)):
    _require_session()
    # Personalize from the logged-in user's profile. OPTIONAL by design: the
    # worker thread has no request context, so resolve the user here and pass
    # the pre-built string into the closure. If auth misbehaves (no/invalid
    # cookie) we fall back to an empty prompt so the studio keeps working.
    profile_prompt = ""
    user = auth.get_user(request)
    if user is not None:
        profile = json.loads(user["profile_json"] or "{}")
        profile_prompt = auth.build_profile_prompt(profile,
                                                   user["display_name"])
    # BYOK: run this turn on the user's own key if they set one (else env key).
    ov = auth.user_llm_override(user)
    if sync:
        token = config.set_llm_override(ov)
        try:
            return _run_chat(body.message, mode=body.mode,
                             profile_prompt=profile_prompt, tier=body.tier)
        finally:
            config.reset_llm_override(token)
    job = MANAGER.submit("chat", body.message[:60] or "chat",
                         lambda j: _run_chat(body.message, j, mode=body.mode,
                                             profile_prompt=profile_prompt,
                                             tier=body.tier),
                         llm_override=ov)
    return {"job_id": job.id}


def _history_payload() -> list[dict]:
    assert SESSION is not None
    out = []
    for i, h in enumerate(SESSION.data.get("history", [])):
        label = h.get("label", "") if isinstance(h, dict) else ""
        source = h.get("source", "chat") if isinstance(h, dict) else "chat"
        out.append({"index": i, "label": label or f"edit {i + 1}",
                    "source": source})
    return out


def _run_tool(name: str, args: dict, job=None) -> dict:
    assert SESSION is not None
    fn = REGISTRY[name]
    with SESSION_LOCK:
        backup = copy.deepcopy(SESSION.data)
        try:
            result = fn(SESSION, **(args or {}))
        except pg.CancelledError:
            SESSION.data = backup
            SESSION.save()
            raise
        except Exception as e:
            SESSION.data = backup
            SESSION.save()
            return {"ok": False, "error": f"{type(e).__name__}: {e}",
                    "clips": _clips_payload(),
                    "compilations": _comps_payload(),
                    "history": _history_payload()}
        if job is not None and job.cancel_event.is_set():
            SESSION.data = backup
            SESSION.save()
            return {"ok": False, "cancelled": True,
                    "clips": _clips_payload(),
                    "compilations": _comps_payload(),
                    "history": _history_payload()}
        return {"ok": result.get("ok", True), "result": result,
                "clips": _clips_payload(), "compilations": _comps_payload(),
                "history": _history_payload()}


@app.post("/api/tool")
def call_tool(body: ToolIn, request: Request, sync: bool = False,
              user: dict = Depends(auth.require_user)):
    """Backbone endpoint: pro-UI controls call a whitelisted REGISTRY tool
    directly, no chat agent in the loop. Same impls the chat agent dispatches.
    Some tools (b-roll search, music pick) still call the LLM internally, so the
    user's BYOK key rides along. Returns a job_id (async, stream progress on
    /api/events); ?sync=1 runs inline."""
    _require_session()
    if body.name not in TOOL_WHITELIST:
        return JSONResponse({"error": f"tool '{body.name}' not allowed"},
                            status_code=403)
    if body.name not in REGISTRY:
        return JSONResponse({"error": f"unknown tool '{body.name}'"},
                            status_code=404)
    ov = auth.user_llm_override(user)
    if sync:
        token = config.set_llm_override(ov)
        try:
            return _run_tool(body.name, body.args or {})
        finally:
            config.reset_llm_override(token)
    job = MANAGER.submit("tool", body.name,
                         lambda j: _run_tool(body.name, body.args or {}, j),
                         llm_override=ov)
    return {"job_id": job.id}


@app.get("/api/events")
def events():
    """Server-Sent Events: job_queued / job_progress / job_done. The UI keeps
    one EventSource open and reconciles state when a job finishes."""
    sub = MANAGER.subscribe()

    def stream():
        try:
            yield "retry: 3000\n\n"
            cur = MANAGER.current
            if cur is not None:
                hello = {"type": "job_progress", "job": cur.public()}
                yield f"data: {json.dumps(hello)}\n\n"
            while True:
                try:
                    evt = sub.get(timeout=15)
                    yield f"data: {json.dumps(evt)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            MANAGER.unsubscribe(sub)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/jobs/{jid}/cancel")
def cancel_job(jid: str, _user: dict = Depends(auth.require_user)) -> dict:
    return {"ok": MANAGER.cancel(jid)}


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    job = MANAGER.get(jid)
    if job is None:
        return JSONResponse({"error": "no such job"}, status_code=404)
    return job.public(with_result=True)


def _transcript_impl(sess: Session, clip_id: int, cuts: bool):
    """Session-keyed body shared by the legacy /api/transcript (global SESSION)
    and the project-keyed /api/v2/transcript (a freshly-resolved Session)."""
    from pipeline.captions import build_caption_segments
    from pipeline.jumpcut import FILLER_WORDS, _norm_word
    try:
        clip = sess.clip(clip_id)
        words = sess.words_for(clip)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    cut_spans = _cut_spans(sess, clip) if cuts else None
    out_words = [{
        "i": i,
        "start": round(w["start"], 3),
        "end": round(w["end"], 3),
        "word": w["word"],
        "is_filler": _norm_word(w["word"]) in FILLER_WORDS,
    } for i, w in enumerate(words)]
    segs = build_caption_segments(words)
    resp = {
        "clip_id": clip_id,
        "words": out_words,
        "segments": [{"start": round(s["start"], 3), "end": round(s["end"], 3)}
                     for s in segs],
        "fps": sess.data["source"].get("fps") or 30,
    }
    if cut_spans is not None:
        resp["cuts"] = cut_spans
    return resp


@app.get("/api/transcript/{clip_id}")
def transcript(clip_id: int, cuts: bool = False):
    """Clip-local word timings for the text-based editor. Times match the
    player exactly (words_for reads the clip's current cut artifact).
    ?cuts=1 adds the removed spans (source seconds + their words + the output
    time where each gap sits) so the UI can show/restore them."""
    assert SESSION is not None
    with SESSION_LOCK:
        return _transcript_impl(SESSION, clip_id, cuts)


def _cut_spans(sess: Session, clip: dict) -> list[dict]:
    """Removed spans from the timing chain, with source words + out anchor."""
    from pipeline.transcribe import transcribe
    try:
        tmap = sess.timemap_for(clip)
    except ValueError:
        return []
    # Own-clips: transcribe the clip's OWN footage, not the nominal shared
    # source. Legacy/long-video clips have no source_path -> shared source.
    src_words = transcribe(
        clip.get("source_path") or sess.data["source"]["path"])["words"]
    spans = []
    for gs, ge in tmap.removed_spans():
        inside = [w["word"] for w in src_words
                  if w["start"] >= gs - 0.05 and w["end"] <= ge + 0.05]
        # output time just before the gap = where the seam sits in the player
        anchor = tmap.to_output(max(tmap.in_span[0], gs - 0.001))
        spans.append({
            "start": round(gs, 3), "end": round(ge, 3),
            "out_anchor": round(anchor, 3) if anchor is not None else 0.0,
            "text": " ".join(inside),
            "duration": round(ge - gs, 2),
        })
    return spans


@app.get("/api/timeline/{clip_id}")
def timeline(clip_id: int):
    """Multi-track timeline (events in player-time) — pure state derivative."""
    assert SESSION is not None
    from chat.timeline_view import serialize
    from pipeline.media import ffprobe_info
    with SESSION_LOCK:
        try:
            clip = SESSION.clip(clip_id)
            words = SESSION.words_for(clip)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=404)
        cur = clip.get("current")
        dur = ffprobe_info(cur)["duration"] if cur and Path(cur).exists() \
            else max(0.0, clip.get("end", 0.0) - clip.get("start", 0.0))
        fps = SESSION.data["source"].get("fps") or 30
        speed = SESSION.speed_factor(clip)
        payload = serialize(clip, words, dur, fps, speed=speed)
        payload["speed"] = round(speed, 4)
        # ---- additive fields for the CapCut-style main video track ----
        # (old consumers ignore unknown keys; new clients use them for the
        #  filmstrip, the source<->output time map, and trim handles)
        payload["media_url"] = f"/media/{clip_id}"
        cut = next((st["params"] for st in clip["stages"]
                    if st["name"] == "cut"), None)
        if cut:
            payload["cut"] = {"start": round(float(cut["start"]), 3),
                              "end": round(float(cut["end"]), 3)}
        try:
            tmap = SESSION.timemap_for(clip)
            payload["kept"] = [[round(a, 3), round(b, 3)]
                               for a, b in tmap.kept_spans()]
            seams = []
            for gs, _ge in tmap.removed_spans():
                a = tmap.to_output(max(tmap.in_span[0], gs - 1e-3))
                # to_output is PRE-speed output time; map into the sped timeline.
                a = (a if a is not None else 0.0) / (speed or 1.0)
                seams.append(round(a, 3))
            payload["seams"] = seams
        except ValueError:
            pass
        if cur and Path(cur).exists():
            import hashlib
            p = Path(cur)
            payload["artifact_key"] = hashlib.sha1(
                f"{p}:{p.stat().st_mtime}".encode()).hexdigest()[:12]
        return payload


@app.get("/api/captions/{clip_id}.{ext}")
def captions(clip_id: int, ext: str):
    assert SESSION is not None
    from chat.tools import export_captions
    ext = ext.lower()
    if ext not in ("srt", "vtt"):
        return JSONResponse({"error": "format must be srt or vtt"},
                            status_code=400)
    r = export_captions(SESSION, clip_id, format=ext)
    if not r.get("ok"):
        return JSONResponse({"error": r.get("error")}, status_code=404)
    media = "application/x-subrip" if ext == "srt" else "text/vtt"
    return FileResponse(r["path"], media_type=media,
                        filename=f"clip{clip_id:02d}.{ext}")


@app.get("/api/export/{clip_id}.{fmt}")
def export_nle(clip_id: int, fmt: str):
    """NLE timeline download: xml = FCP7 xmeml (Resolve/Premiere), edl."""
    assert SESSION is not None
    from chat.export_nle import export_timeline as _export
    try:
        with SESSION_LOCK:
            out = _export(SESSION, clip_id, fmt)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    media = "application/xml" if fmt == "xml" else "text/plain"
    return FileResponse(str(out), media_type=media, filename=out.name)


@app.get("/api/qc/{clip_id}")
def qc(clip_id: int):
    """Quality-control card: measured loudness/true-peak vs the platform
    target + container basics. Measurement runs OUTSIDE the session lock
    (read-only ffmpeg null pass on the current render)."""
    assert SESSION is not None
    from pipeline.effects import PLATFORM_LOUDNESS, _loudnorm_measure
    from pipeline.media import ffprobe_info
    with SESSION_LOCK:
        try:
            clip = SESSION.clip(clip_id)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=404)
        cur = clip.get("current")
        fade = dict(next((st["params"] for st in clip["stages"]
                          if st["name"] == "fade"), {}))
        stages = [st["name"] for st in clip["stages"]]
    if not cur or not Path(cur).exists():
        return JSONResponse({"error": "clip has no render yet"},
                            status_code=404)
    platform = fade.get("platform") or "youtube_shorts"
    t_lufs, t_tp = PLATFORM_LOUDNESS.get(platform, (-14.0, -1.5))
    t_lufs = float(fade.get("lufs", t_lufs))
    t_tp = float(fade.get("tp", t_tp))
    info = ffprobe_info(cur)
    m = _loudnorm_measure(cur, t_lufs, t_tp)

    checks: list[dict] = []

    def chk(key: str, label: str, status: str, detail: str) -> None:
        checks.append({"key": key, "label": label, "status": status,
                       "detail": detail})

    if m:
        lufs, tp = float(m["input_i"]), float(m["input_tp"])
        diff = lufs - t_lufs
        chk("loudness", "Loudness",
            "ok" if abs(diff) <= 1.0 else "warn" if abs(diff) <= 2.5
            else "fail",
            f"{lufs:.1f} LUFS · target {t_lufs:g} ({diff:+.1f} LU)")
        chk("true_peak", "True peak",
            "ok" if tp <= t_tp + 0.3 else "warn" if tp <= 0.0 else "fail",
            f"{tp:.1f} dBTP · limit {t_tp:g}")
        chk("lra", "Dynamic range", "ok",
            f"{float(m['input_lra']):.1f} LU")
    else:
        chk("loudness", "Loudness", "warn", "could not measure")
    ratio = info["height"] / max(info["width"], 1)
    chk("aspect", "Aspect ratio", "ok" if ratio >= 1.7 else "warn",
        f"{info['width']}×{info['height']} ({'9:16 vertical' if ratio >= 1.7 else 'not vertical'})")
    chk("duration", "Duration", "ok", f"{info['duration']:.1f} s")
    chk("captions", "Captions",
        "ok" if "subtitles" in stages else "warn",
        "yes (burned in)" if "subtitles" in stages else "none")
    chk("loudnorm", "Mastering",
        "ok" if "fade" in stages else "warn",
        f"{platform}" if "fade" in stages else "no fade/loudnorm stage")

    worst = ("fail" if any(c["status"] == "fail" for c in checks)
             else "warn" if any(c["status"] == "warn" for c in checks)
             else "ok")
    return {"clip_id": clip_id, "platform": platform, "overall": worst,
            "checks": checks}


@app.post("/api/gc")
def gc(force: bool = False, max_age_days: float = 7.0,
       _user: dict = Depends(auth.require_user)) -> dict:
    assert SESSION is not None
    from chat.gc import collect
    return collect(SESSION, dry_run=not force, max_age_days=max_age_days)


@app.get("/api/history")
def history() -> dict:
    with SESSION_LOCK:
        return {"history": _history_payload(),
                "redo_depth": len(SESSION.data.get("redo", []))}


@app.post("/api/restore/{index}")
def restore(index: int, _user: dict = Depends(auth.require_user)) -> dict:
    assert SESSION is not None
    with SESSION_LOCK:
        hist = SESSION.data.get("history", [])
        if not 0 <= index < len(hist):
            return JSONResponse({"error": "no such version"}, status_code=404)
        entry = hist[index]
        clips = entry.get("clips") if isinstance(entry, dict) else entry
        if clips is None:
            return JSONResponse({"error": "version has no clips to restore"},
                                status_code=422)
        SESSION.snapshot("before restore")
        SESSION.data["clips"] = copy.deepcopy(clips)
        SESSION.save()
        return {"ok": True, "clips": _clips_payload(),
                "history": _history_payload()}


@app.get("/thumb/{clip_id}")
def thumb(clip_id: int):
    _require_session()
    import hashlib
    import subprocess
    from pipeline import config as cfg
    try:
        clip = SESSION.clip(clip_id)
    except ValueError:
        return JSONResponse({"error": "no such clip"}, status_code=404)
    src = clip.get("current")
    if not src or not Path(src).exists():
        return JSONResponse({"error": "not rendered"}, status_code=404)
    tdir = cfg.CACHE_DIR / "thumbs"
    tdir.mkdir(exist_ok=True)
    key = hashlib.sha1(f"{src}:{Path(src).stat().st_mtime}".encode()).hexdigest()[:12]
    out = tdir / f"{key}.jpg"
    if not out.exists():
        subprocess.run(["ffmpeg", "-y", "-ss", "1", "-i", src, "-frames:v", "1",
                        "-vf", "scale=180:-2", str(out)],
                       capture_output=True, check=False)
    if not out.exists():
        return JSONResponse({"error": "thumb failed"}, status_code=500)
    return FileResponse(str(out), media_type="image/jpeg")


def _filmstrip_impl(sess: Session, clip_id: int, n: int, h: int):
    """Session-keyed body shared by the legacy /api/filmstrip (global SESSION)
    and the project-keyed /api/v2/filmstrip (a freshly-resolved Session)."""
    import hashlib
    import subprocess
    from pipeline import config as cfg
    from pipeline.media import ffprobe_info
    try:
        clip = sess.clip(clip_id)
    except ValueError:
        return JSONResponse({"error": "no such clip"}, status_code=404)
    src = clip.get("current")
    if not src or not Path(src).exists():
        return JSONResponse({"error": "not rendered"}, status_code=404)
    n = max(16, min(240, int(n)))
    h = max(30, min(90, int(h)))
    try:
        dur = float(ffprobe_info(src)["duration"]) or 0.0
    except Exception:
        dur = 0.0
    if dur <= 0:
        return JSONResponse({"error": "no duration"}, status_code=404)
    fdir = cfg.CACHE_DIR / "filmstrips"
    fdir.mkdir(exist_ok=True)
    key = hashlib.sha1(
        f"{src}:{Path(src).stat().st_mtime}:{n}:{h}".encode()).hexdigest()[:12]
    out = fdir / f"{key}.jpg"
    if not out.exists():
        # fps slightly under n/dur so we never overshoot the tile count; tile
        # into a single 1-row sprite. -q:v 5 keeps it small but legible.
        rate = max(0.1, (n - 0.5) / dur)
        subprocess.run(
            ["ffmpeg", "-y", "-i", src, "-frames:v", "1", "-q:v", "5",
             "-vf", f"fps={rate:.6f},scale=-2:{h},crop=iw:{h},tile={n}x1",
             str(out)],
            capture_output=True, check=False)
    if not out.exists():
        return JSONResponse({"error": "filmstrip failed"}, status_code=500)
    return FileResponse(str(out), media_type="image/jpeg",
                        headers={"Cache-Control": "max-age=31536000, immutable"})


@app.get("/api/filmstrip/{clip_id}")
def filmstrip(clip_id: int, n: int = 80, h: int = 54):
    """Horizontal thumbnail sprite (n tiles in one row) for the main video
    track. Cached by source path+mtime+n+h; never goes through the render
    worker (same precedent as /thumb), so it can't block edits."""
    _require_session()
    return _filmstrip_impl(SESSION, clip_id, n, h)


@app.get("/media/comp/{comp_id}")
def media_comp(comp_id: int):
    assert SESSION is not None
    comp = next((c for c in SESSION.data.get("compilations", [])
                 if c["id"] == comp_id), None)
    if not comp or not Path(comp["file"]).exists():
        return JSONResponse({"error": "no such compilation"}, status_code=404)
    return FileResponse(comp["file"], media_type="video/mp4")


@app.get("/media/plan_preview")
def plan_preview():
    assert SESSION is not None
    plan = SESSION.data.get("pending_plan") or {}
    path = (plan.get("preview") or {}).get("file")
    if not path or not Path(path).exists():
        return JSONResponse({"error": "no preview"}, status_code=404)
    return FileResponse(path, media_type="video/mp4")


@app.get("/media/export/{clip_id}")
def media_export(clip_id: int):
    """Phase 5 — download the full-res deliverable produced by export_clip.
    Served as an attachment so the browser saves it rather than streaming."""
    _require_session()
    try:
        clip = SESSION.clip(clip_id)
    except ValueError:
        return JSONResponse({"error": "no such clip"}, status_code=404)
    path = clip.get("export")
    if not path or not Path(path).exists():
        return JSONResponse({"error": "not exported"}, status_code=404)
    title = "".join(ch if ch.isalnum() or ch in "-_" else "_"
                    for ch in (clip.get("title") or f"clip{clip_id}"))
    return FileResponse(path, media_type="video/mp4",
                        filename=f"{title}_full.mp4")


@app.get("/media/{clip_id}")
def media(clip_id: int):
    _require_session()
    try:
        clip = SESSION.clip(clip_id)
    except ValueError:
        return JSONResponse({"error": "no such clip"}, status_code=404)
    path = clip.get("current")
    if not path or not Path(path).exists():
        return JSONResponse({"error": "not rendered"}, status_code=404)
    return FileResponse(path, media_type="video/mp4")


# ---------------------------------------------------------------------------
# studio2 — the greenfield React studio (Faz C, first PR). A project-keyed,
# disk-backed API that mirrors chat/mcp_bridge.py: each call loads the project
# fresh from disk (Session.open_existing) instead of touching the single global
# SESSION the legacy /studio uses. So studio2 is multi-project from day one, and
# the backend truth (session.py + pipeline/) is unchanged — every mutation still
# routes through one REGISTRY tool via mcp_bridge.run_tool.
# ---------------------------------------------------------------------------
STUDIO2 = STATIC / "studio2"


def _v2_state_payload(sess: Session) -> dict:
    from chat import mcp_bridge
    src = sess.data.get("source", {})
    return {
        "project": sess.data.get("name"),
        "display_name": sess.data.get("display_name") or sess.data.get("name"),
        "source": {"width": src.get("width"), "height": src.get("height"),
                   "fps": src.get("fps"), "duration": src.get("duration")},
        "clips": mcp_bridge._clip_rows(sess),
        "active_clip": sess.active_clip_id(),
    }


def _v2_timeline_payload(sess: Session, clip_id: int) -> dict:
    """Same multi-track payload the MCP bridge serves, plus the main-track
    fields studio2 needs (proxy media URL + the source-time cut for trim)."""
    from chat import mcp_bridge
    clip = sess.clip(clip_id)                       # raises ValueError
    payload = mcp_bridge._timeline(sess, clip) or {}
    # Version the media URL by the current artifact (path + mtime) so the <video>
    # reloads after any re-render (trim, rerun) instead of showing a stale cache.
    name = quote(sess.data.get("name", ""))
    cur = clip.get("current")
    ver = ""
    if cur and Path(cur).exists():
        import hashlib
        p = Path(cur)
        ver = hashlib.sha1(f"{p}:{p.stat().st_mtime}".encode()).hexdigest()[:12]
    payload["media_url"] = (f"/api/v2/media?project={name}&clip={clip_id}"
                            + (f"&v={ver}" if ver else ""))
    payload["clip"] = clip_id   # so async results reconcile only onto this clip
    cut = next((st["params"] for st in clip["stages"]
                if st["name"] == "cut"), None)
    if cut:
        payload["cut"] = {"start": round(float(cut["start"]), 3),
                          "end": round(float(cut["end"]), 3)}
    return payload


def _v2_session(project: str) -> Session:
    from chat import mcp_bridge
    return mcp_bridge._resolve(project)             # raises FileNotFoundError


@app.get("/studio2", response_class=HTMLResponse)
def studio2_page() -> HTMLResponse:
    index = STUDIO2 / "index.html"
    if not index.exists():
        return HTMLResponse(
            "<h1>studio2 isn’t built yet</h1><p>Run "
            "<code>cd studio &amp;&amp; npm install &amp;&amp; npm run build</code> "
            "to produce <code>chat/static/studio2/</code>, then reload.</p>",
            status_code=503)
    return HTMLResponse(index.read_text(encoding="utf-8"))


@app.get("/api/v2/state")
def v2_state(project: str, _user: dict = Depends(auth.require_user)):
    try:
        sess = _v2_session(project)
    except FileNotFoundError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    return _v2_state_payload(sess)


@app.get("/api/v2/timeline")
def v2_timeline(project: str, clip: int,
                _user: dict = Depends(auth.require_user)):
    try:
        sess = _v2_session(project)
    except FileNotFoundError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    try:
        return _v2_timeline_payload(sess, clip)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)


@app.get("/api/v2/genmedia/models")
def v2_genmedia_models(_user: dict = Depends(auth.require_user)):
    """Selectable generation models for the studio Generate panel, per kind,
    plus whether generation is configured at all (GENMEDIA_API_KEY present)."""
    from pipeline import genmedia
    return {
        "available": genmedia.available(),
        "video": genmedia.models("video"),
        "image": genmedia.models("image"),
        "i2v": genmedia.models("i2v"),
    }


@app.get("/api/v2/media")
def v2_media(project: str, clip: int):
    try:
        sess = _v2_session(project)
    except FileNotFoundError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    try:
        c = sess.clip(clip)
    except ValueError:
        return JSONResponse({"error": "no such clip"}, status_code=404)
    path = c.get("current")
    if not path or not Path(path).exists():
        return JSONResponse({"error": "not rendered"}, status_code=404)
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/v2/filmstrip")
def v2_filmstrip(project: str, clip: int, n: int = 80, h: int = 54):
    """Project-keyed filmstrip sprite for the studio2 main video track. Loaded
    by an <img>/CSS background, so (like /api/v2/media) it carries no auth dep
    and relies on the session cookie."""
    try:
        sess = _v2_session(project)
    except FileNotFoundError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    return _filmstrip_impl(sess, clip, n, h)


@app.get("/api/v2/transcript")
def v2_transcript(project: str, clip: int, cuts: bool = False):
    """Project-keyed word timings for the studio2 caption track."""
    try:
        sess = _v2_session(project)
    except FileNotFoundError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    return _transcript_impl(sess, clip, cuts)


class V2ToolIn(BaseModel):
    project: str
    name: str
    args: dict = {}
    clip: int | None = None


def _run_v2_tool(project: str, name: str, args: dict, clip: int | None,
                 job=None) -> dict:
    """Run one whitelisted REGISTRY tool against a project on disk and return
    {result, state, timeline}. Shared by the sync and async (job) paths. The
    LLM override is set by the caller (request handler / job closure)."""
    from chat import mcp_bridge
    result = mcp_bridge.run_tool(project, name, args or {})
    state = timeline = None
    try:
        sess = _v2_session(project)
        state = _v2_state_payload(sess)
        clip_id = clip or sess.active_clip_id()
        if clip_id:
            timeline = _v2_timeline_payload(sess, clip_id)
    except (FileNotFoundError, ValueError):
        pass
    return {"result": result, "state": state, "timeline": timeline}


@app.post("/api/v2/tool")
def v2_tool(body: V2ToolIn, async_: bool = Query(False, alias="async"),
            user: dict = Depends(auth.require_user)):
    """One mutation channel for studio2: run a whitelisted REGISTRY tool against
    the project on disk (mcp_bridge.run_tool snapshots + restores on error), then
    return fresh state + the active clip's timeline so the UI reconciles in one
    round-trip. Default is synchronous; ?async=1 submits to MANAGER and returns
    {job_id} (stream progress on /api/events, fetch result via /api/jobs/{id})
    so long generations don't block the request — the lane shows a pending
    'Generating…' block meanwhile."""
    if body.name not in TOOL_WHITELIST:
        return JSONResponse({"error": f"tool '{body.name}' not allowed"},
                            status_code=403)
    ov = auth.user_llm_override(user)
    if async_:
        job = MANAGER.submit(
            "tool", body.name,
            lambda j: _run_v2_tool(body.project, body.name, body.args or {},
                                   body.clip, j),
            llm_override=ov)
        return {"job_id": job.id}
    token = config.set_llm_override(ov)
    try:
        return _run_v2_tool(body.project, body.name, body.args or {}, body.clip)
    finally:
        config.reset_llm_override(token)


def _run_v2_chat(project: str, message: str, job=None, *,
                 profile_prompt: str = "", tier: str = "fast",
                 mode: str = "pro") -> dict:
    """Run one agent turn against a project-keyed session (studio2's chat panel),
    mirroring the legacy _run_chat but resolving the session fresh from disk and
    keeping a per-project in-memory history. Tool calls are narrated live via
    pg.note → /api/events (the same SSE the chat chips read). Snapshots/restores
    the session on cancel/crash so a failed turn never leaves disk half-edited."""
    from chat import mcp_bridge
    hist = _V2_HISTORY.setdefault(project, [])
    tools_used: list[str] = []

    def on_tool(name: str, args: dict) -> None:
        pretty = ", ".join(f"{k}={v}" for k, v in (args or {}).items())
        tools_used.append(name)
        pg.note(f"{name}|{pretty}" if pretty else name)

    # mcp_bridge._LOCK serializes against /api/v2/tool's run_tool so disk state
    # never tears. run_turn dispatches REGISTRY tools DIRECTLY (not via run_tool),
    # so there's no re-entrancy on this non-reentrant lock.
    sess = None
    with mcp_bridge._LOCK:
        try:
            sess = mcp_bridge._resolve(project)
        except FileNotFoundError as e:
            return {"reply": f"Error: {e}", "tools": [], "clarify": None,
                    "pending_plan": None, "state": None, "timeline": None}
        backup = copy.deepcopy(sess.data)
        try:
            reply = run_turn(sess, hist, message, on_tool=on_tool,
                             mode=mode, profile_prompt=profile_prompt, tier=tier)
        except pg.CancelledError:
            sess.data = backup
            sess.save()
            raise
        except Exception as e:  # noqa: BLE001 — surface as a chat error
            sess.data = backup
            sess.save()
            reply = f"Error: {type(e).__name__}: {e}"
        if job is not None and job.cancel_event.is_set():
            sess.data = backup
            sess.save()
            reply = "Cancelled."
        hist[:] = hist[-40:]              # cap rolling history
        state = _v2_state_payload(sess)
        clip_id = sess.active_clip_id()
        timeline = None
        if clip_id:
            try:
                timeline = _v2_timeline_payload(sess, clip_id)
            except ValueError:
                timeline = None
    return {"reply": reply, "tools": tools_used,
            "clarify": getattr(sess, "last_clarify", None),
            "pending_plan": sess.data.get("pending_plan"),
            "state": state, "timeline": timeline}


class V2ChatIn(BaseModel):
    project: str
    message: str
    mode: str = "pro"
    tier: str = "fast"


@app.post("/api/v2/chat")
def v2_chat(body: V2ChatIn, request: Request, sync: bool = False,
            _user: dict = Depends(auth.require_user)):
    """studio2's agentic chat: NL → real edits via the same run_turn agent the
    legacy UI uses, but project-keyed. Returns {job_id} (stream tool-call chips
    on /api/events, fetch the reply+state+timeline via /api/jobs/{id}); ?sync=1
    runs inline. BYOK + profile personalization mirror /api/chat exactly."""
    profile_prompt = ""
    user = auth.get_user(request)
    if user is not None:
        profile = json.loads(user["profile_json"] or "{}")
        profile_prompt = auth.build_profile_prompt(profile,
                                                   user["display_name"])
    ov = auth.user_llm_override(user)
    if sync:
        token = config.set_llm_override(ov)
        try:
            return _run_v2_chat(body.project, body.message,
                                profile_prompt=profile_prompt,
                                tier=body.tier, mode=body.mode)
        finally:
            config.reset_llm_override(token)
    job = MANAGER.submit("chat", body.message[:60] or "chat",
                         lambda j: _run_v2_chat(body.project, body.message, j,
                                                profile_prompt=profile_prompt,
                                                tier=body.tier, mode=body.mode),
                         llm_override=ov)
    return {"job_id": job.id}


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(str(STATIC / "favicon.ico"), media_type="image/x-icon")


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


def main() -> None:
    global SESSION
    # The video arg is OPTIONAL now (the (B) /projects switcher): the server can
    # boot with zero projects and the user opens/creates one from /projects.
    if len(sys.argv) > 1:
        SESSION = Session.load_or_create(sys.argv[1])
    port = int(sys.argv[2]) if len(sys.argv) > 2 else int(os.getenv("PORT", "8765"))

    def _auto_gc() -> None:
        """Sweep unreferenced aged artifacts whenever the queue drains.
        No-op when no project is active (nothing to sweep)."""
        if SESSION is None:
            return
        from chat.gc import collect
        with SESSION_LOCK:
            r = collect(SESSION, dry_run=False, max_age_days=7.0)
        if r.get("removed"):
            print(f"[gc] {len(r['removed'])} stale artifacts removed")

    MANAGER.on_idle = _auto_gc

    # Start the YouTube auto-clip watcher (opt-in per user; no-op if disabled).
    automation.start_poller()

    import uvicorn
    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=port, log_level="warning")


if __name__ == "__main__":
    main()
