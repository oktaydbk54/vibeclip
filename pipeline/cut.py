"""Faz 3 — Cut clips out of the source video.

Two paths:
- fast_cut: stream-copy (`-c copy`), no re-encode, near-instant. Cut points may
  snap to the nearest keyframe, so it can be a few frames off — fine for drafts.
- precise_cut: re-encodes around exact timestamps (frame-accurate). Used when the
  clip will be reframed/subtitled anyway, so re-encoding is free.
"""

from __future__ import annotations

import re
from pathlib import Path

from pipeline import config
from pipeline.media import run_ffmpeg


def _safe_name(title: str, fallback: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_-]+", "_", title).strip("_").lower()
    return name[:48] or fallback


def fast_cut(video_path: str, start: float, end: float, out_path: str) -> str:
    """Stream-copy cut — instant, keyframe-aligned (good enough for raw clips)."""
    run_ffmpeg([
        "-ss", f"{start:.3f}",
        "-to", f"{end:.3f}",
        "-i", video_path,
        "-c", "copy",
        out_path,
    ])
    return out_path


def _snap_back(start: float, keyframes: list[float] | None) -> float:
    """Nearest I-frame PTS at or before `start` (so a stream copy keeps GOP
    integrity). Returns `start` unchanged when there's no index or no earlier
    keyframe."""
    if not keyframes:
        return start
    prev = 0.0
    for k in keyframes:
        if k <= start + 1e-6:
            prev = k
        else:
            break
    return prev


def fast_cut_snapped(video_path: str, start: float, end: float, out_path: str,
                     keyframes: list[float] | None = None) -> str:
    """Phase 5 — near-instant PREVIEW cut. Stream-copy (no re-encode); when a
    Phase-0 keyframe index is supplied, snap `-ss` back to the nearest preceding
    I-frame so the copy starts on a clean GOP boundary. With no index, ffmpeg's
    own copy already lands on a nearby keyframe (e.g. the proxy's dense GOP).

    The few-frames-early start is fine for editing/preview: the clip's timeline
    and word timings are reconstructed from the artifact itself, and the
    frame-exact boundary is re-established at export via precise_cut."""
    s = _snap_back(start, keyframes)
    run_ffmpeg([
        "-ss", f"{s:.3f}",
        "-to", f"{end:.3f}",
        "-i", video_path,
        "-c", "copy",
        out_path,
    ])
    return out_path


def precise_cut(video_path: str, start: float, end: float, out_path: str) -> str:
    """Frame-accurate cut via re-encode (GPU encoder when available).

    Short ~1s GOP (-g 30, no scene-cut keyframes) so the player can seek/scrub
    to almost any frame cheaply — pros expect step-accurate scrubbing.
    """
    run_ffmpeg([
        "-ss", f"{start:.3f}",
        "-to", f"{end:.3f}",
        "-i", video_path,
        "-c:v", config.VIDEO_ENCODER,
        "-g", "30", "-sc_threshold", "0",
        "-c:a", "aac",
        "-b:a", "160k",
        out_path,
    ])
    return out_path


def cut_clip(
    video_path: str,
    start: float,
    end: float,
    title: str = "",
    precise: bool = False,
    index: int = 0,
    out_path: str | None = None,
) -> str:
    """Cut [start, end] from video. Returns the output path.

    out_path: the session passes a content-hash-named artifact path so the cut
    participates in the same cache/undo discipline as every other stage (an
    unchanged cut replays as a free cache hit; two variants with the same bounds
    can share one artifact). When omitted, falls back to a title-based name in
    outputs/ (used by the standalone MCP `make_short` path).
    """
    src = Path(video_path)
    if not src.exists():
        raise FileNotFoundError(f"Video not found: {src}")

    if out_path is None:
        name = _safe_name(title, f"clip_{index:02d}")
        out_path = str(config.OUTPUTS_DIR / f"{index:02d}_{name}.mp4")

    if precise:
        return precise_cut(video_path, start, end, out_path)
    return fast_cut(video_path, start, end, out_path)
