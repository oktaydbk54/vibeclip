"""Thread-local render-progress + cancellation context.

The job worker (chat/jobs.py) installs a context for its thread before running a
render; the pipeline reports stage transitions and ffmpeg sub-progress into it,
and checks for cancellation. When no context is installed (CLI, tests, the sync
API path) every call here is a cheap no-op — so pipeline function signatures
never change.
"""

from __future__ import annotations

import threading
import time


class CancelledError(Exception):
    """Raised inside a render when its job was cancelled."""


_ctx = threading.local()


def set_context(cancel_event, emit) -> None:
    """Install this thread's render context. emit(dict) pushes a progress
    update (keys: progress 0..1, message). cancel_event is a threading.Event."""
    _ctx.cancel = cancel_event
    _ctx.emit = emit
    _ctx.n = 1
    _ctx.idx = 0
    _ctx.name = ""
    _ctx.dur = 0.0
    _ctx.last = 0.0


def clear_context() -> None:
    for a in ("cancel", "emit", "n", "idx", "name", "dur", "last"):
        if hasattr(_ctx, a):
            delattr(_ctx, a)


def _active() -> bool:
    return getattr(_ctx, "emit", None) is not None


def should_cancel() -> bool:
    ev = getattr(_ctx, "cancel", None)
    return bool(ev is not None and ev.is_set())


def begin_stages(n: int) -> None:
    """Declare how many render stages this job will run (for the % bar)."""
    if not _active():
        return
    _ctx.n = max(1, int(n))
    _ctx.idx = 0


def report_stage(idx: int, name: str, dur: float = 0.0) -> None:
    """Mark the start of stage `idx` (0-based) named `name`. dur = expected
    seconds of footage (drives within-stage sub-progress)."""
    if not _active():
        return
    _ctx.idx = idx
    _ctx.name = name
    _ctx.dur = float(dur or 0.0)
    _emit(idx / _ctx.n, force=True)


def note(message: str) -> None:
    """Emit a one-off status line (e.g. a tool call about to run) WITHOUT
    advancing the progress bar. Forced through the throttle so the UI learns
    about tool calls as they happen. No-op when no context is installed."""
    if not _active() or not message:
        return
    try:
        _ctx.emit({"message": message})
    except Exception:
        pass


def report_ffmpeg(out_time_us) -> None:
    """Called by run_ffmpeg per progress line — advances the within-stage bar
    and is the cancel-polling heartbeat."""
    if not _active():
        return
    try:
        t = float(out_time_us) / 1e6
    except (ValueError, TypeError):
        return
    sub = min(0.999, t / _ctx.dur) if _ctx.dur > 0 else 0.0
    _emit((_ctx.idx + sub) / _ctx.n)


def _emit(frac: float, force: bool = False) -> None:
    frac = max(0.0, min(0.999, frac))
    now = time.monotonic()
    if not force and (now - getattr(_ctx, "last", 0.0)) < 0.2:
        return  # throttle to ~5 updates/sec so SSE isn't flooded
    _ctx.last = now
    try:
        _ctx.emit({"progress": frac, "message": _ctx.name})
    except Exception:
        pass
