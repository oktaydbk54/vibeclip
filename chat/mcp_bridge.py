"""MCP bridge — expose the session-aware editing toolset over MCP.

The chat agent and the pro web UI both drive a project through the same
`chat.tools.REGISTRY` (60+ tools: generate_clips, set_cut, add_zoom,
set_subtitles, add_broll, set_dub, export_clip, ...). `server.py` historically
only exposed a dozen *stateless* pipeline primitives, so an external agent
(Claude Code / Cursor / Codex) connected over MCP could not actually edit a
project. This module closes that gap: every REGISTRY tool becomes a first-class
MCP tool that takes a `project` id plus the tool's own args.

Design notes:
- The project is loaded FRESH from disk per call (`Session.open_existing`) so an
  MCP edit and a concurrent web edit never share stale in-memory state — they
  serialize through project.json on disk.
- A deep-copy backup is restored on error, mirroring `chat.app._run_tool`, so a
  failed tool never leaves project.json half-mutated. (Mutating tools also
  snapshot themselves internally, which is what powers `undo`.)
- MCP tool schemas are generated from `TOOL_SPECS` (the same specs the chat
  agent's function-calling sees), so descriptions and arg types stay in one
  place. Optional args default to None here and are stripped before dispatch so
  each tool keeps its own native default instead of being overridden with None.
- Plan-approval / interactive tools are excluded: an external agent IS the
  planner and calls editing tools directly, so the human-in-the-loop web flow
  (propose_edit -> apply_plan, ask_user, ...) does not belong on this surface.
"""

from __future__ import annotations

import copy
import inspect
import json
import threading
from pathlib import Path

from chat.session import SESSIONS_DIR, Session
from chat.tools import REGISTRY, TOOL_SPECS

# Tools that only make sense inside the web UI's human-in-the-loop approval flow.
_EXCLUDE = frozenset({
    "ask_user",          # asks the web user a question; no UI here
    "propose_edit",      # stages a plan for A/B approval in the web app
    "propose_project",   # multi-clip plan staging
    "apply_plan",        # commits a staged plan
    "discard_plan",      # drops a staged plan
    "revert_plan",       # reverts an applied plan checkpoint
    "regenerate_plan",   # re-plans a staged edit
    "propose_assets",    # asset-suggestion UI flow
    "set_autonomy",      # web autonomy-mode toggle
})

_JSON_PY = {"string": str, "number": float, "integer": int,
            "boolean": bool, "array": list, "object": dict}

# Serialize MCP tool calls. FastMCP's stdio transport is single-threaded, but a
# lock keeps us correct under any concurrent transport and matches the web app's
# SESSION_LOCK discipline.
_LOCK = threading.Lock()


def _resolve(project: str) -> Session:
    pfile = SESSIONS_DIR / project / "project.json"
    if not pfile.exists():
        raise FileNotFoundError(
            f"No project '{project}'. Call list_projects, or open_project to "
            f"create one from a source video.")
    return Session.open_existing(project)


def run_tool(project: str, name: str, args: dict) -> dict:
    """Dispatch a REGISTRY tool against a fresh-from-disk session.

    Restores a deep-copy backup (and persists it) on error so a partial edit
    never corrupts project.json. Returns the tool's own dict result, or a
    `{ok: False, error}` envelope on failure / unknown tool.
    """
    fn = REGISTRY.get(name)
    if fn is None:
        return {"ok": False, "error": f"unknown tool '{name}'"}
    with _LOCK:
        try:
            sess = _resolve(project)
        except FileNotFoundError as e:
            return {"ok": False, "error": str(e)}
        backup = copy.deepcopy(sess.data)
        try:
            result = fn(sess, **(args or {}))
        except Exception as e:  # noqa: BLE001 — surface as a tool error, not a crash
            sess.data = backup
            sess.save()
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if isinstance(result, dict):
        return result
    return {"ok": True, "result": result}


def _signature(spec: dict) -> inspect.Signature:
    """Build an inspect.Signature (project + the tool's args) that FastMCP reads
    via inspect.signature() to derive the MCP input schema."""
    params = [inspect.Parameter(
        "project", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=str)]
    fn_spec = spec["function"]["parameters"]
    props = fn_spec.get("properties", {})
    required = set(fn_spec.get("required", []))
    for pname, schema in props.items():
        ann = _JSON_PY.get(schema.get("type"), str)
        if pname in required:
            params.append(inspect.Parameter(
                pname, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=ann))
        else:
            params.append(inspect.Parameter(
                pname, inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=ann, default=None))
    return inspect.Signature(params)


def _make_wrapper(name: str, spec: dict):
    sig = _signature(spec)
    desc = spec["function"]["description"]

    def wrapper(project, **kwargs):
        # Strip omitted optionals (defaulted to None) so each tool keeps its own
        # native default rather than being force-overridden with None.
        args = {k: v for k, v in kwargs.items() if v is not None}
        return run_tool(project, name, args)

    wrapper.__name__ = name
    wrapper.__doc__ = (
        f"{desc}\n\n`project` is the project id from list_projects / "
        f"open_project.")
    wrapper.__signature__ = sig
    return wrapper


def _project_rows() -> list[dict]:
    rows: list[dict] = []
    if not SESSIONS_DIR.exists():
        return rows
    for sdir in sorted(SESSIONS_DIR.iterdir()):
        pfile = sdir / "project.json"
        if not pfile.exists():
            continue
        try:
            data = json.loads(pfile.read_text())
        except Exception:  # noqa: BLE001 — skip unreadable projects
            continue
        rows.append({
            "project": sdir.name,
            "name": data.get("display_name") or data.get("name") or sdir.name,
            "status": Session.derive_status(data, None),
            "clips": len(data.get("clips") or []),
        })
    return rows


def _clip_rows(sess: Session) -> list[dict]:
    out = []
    for c in sess.data.get("clips") or []:
        cur = c.get("current")
        out.append({
            "id": c.get("id"),
            "title": c.get("title", ""),
            "status": Session.clip_status(c),
            "score": c.get("score", 0),
            "rendered": bool(cur and Path(cur).exists()),
        })
    return out


def _timeline(sess: Session, clip: dict) -> dict | None:
    """Multi-track timeline for one clip (same payload the web UI renders)."""
    from chat.timeline_view import serialize
    from pipeline.media import ffprobe_info
    cur = clip.get("current")
    if cur and Path(cur).exists():
        dur = ffprobe_info(cur)["duration"]
    else:
        dur = max(0.0, clip.get("end", 0.0) - clip.get("start", 0.0))
    fps = sess.data["source"].get("fps") or 30
    speed = sess.speed_factor(clip)
    payload = serialize(clip, sess.words_for(clip), dur, fps, speed=speed)
    payload["speed"] = round(speed, 4)
    return payload


def register_session_tools(mcp) -> int:
    """Register project-management + all editing tools onto a FastMCP instance.

    Returns the number of editing tools registered (excludes the 3 project
    helpers below).
    """

    @mcp.tool()
    def list_projects() -> list:
        """List all VibeClip projects: {project, name, status, clips}. Use a
        project's `project` id with the editing tools and project_state."""
        return _project_rows()

    @mcp.tool()
    def open_project(video_path: str) -> dict:
        """Open or create a project from a long source video (builds the 540p
        proxy in the background). Returns {project, summary}. Next call
        generate_clips to produce short clips, then edit and export_clip."""
        sess = Session.load_or_create(video_path)
        return {"ok": True, "project": sess.path.parent.name,
                "summary": sess.summary()}

    @mcp.tool()
    def auto_repurpose(source: str, count: int = 0, style: str = "",
                       aspect: str = "", generate_broll: bool = False,
                       caption_language: str = "", dub_language: str = "",
                       export: bool = True) -> dict:
        """Headless: turn a long video (local path OR URL) into finished shorts
        in one call — ingest → auto-clip → optional style/AI-b-roll/translated
        captions/dub → export. Returns a manifest of deliverables. This is the
        cross-platform/server/CI path a desktop editor can't offer. count=0 lets
        the clip count adapt to the source length."""
        from chat.repurpose import auto_repurpose as _run
        return _run(source, count=count or None, style=style, aspect=aspect,
                    generate_broll=generate_broll,
                    caption_language=caption_language,
                    dub_language=dub_language, export=export)

    @mcp.tool()
    def project_state(project: str, clip_id: int = 0) -> dict:
        """Full state of a project: status, clips, and the multi-track timeline
        of one clip (clip_id, or the active clip when 0/omitted)."""
        try:
            sess = _resolve(project)
        except FileNotFoundError as e:
            return {"ok": False, "error": str(e)}
        clips = _clip_rows(sess)
        target = clip_id or sess.active_clip_id()
        timeline = None
        if target:
            try:
                timeline = _timeline(sess, sess.clip(target))
            except (ValueError, KeyError):
                timeline = None
        return {"ok": True, "project": project,
                "status": Session.derive_status(sess.data, None),
                "clips": clips, "active_clip": target, "timeline": timeline}

    count = 0
    for spec in TOOL_SPECS:
        name = spec["function"]["name"]
        if name in _EXCLUDE or name not in REGISTRY:
            continue
        mcp.add_tool(_make_wrapper(name, spec), name=name,
                     description=spec["function"]["description"])
        count += 1
    return count
