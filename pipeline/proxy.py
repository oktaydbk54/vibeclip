"""Phase 0 — the proxy spine.

A 30-minute source is expensive to decode/analyze at full resolution, so we
build a cheap 540p H.264 *proxy* of the FULL source once, up front, and run all
analysis/preview against it. Final export still uses the full-res source.

Two artifacts, both content-cached by the source's content hash so re-ingesting
the same file is free:

  build_proxy(src)    -> a 540p H.264 mirror under the session outputs dir.
  keyframe_index(src) -> sorted I-frame PTS (seconds), cached as JSON.

CRITICAL — timestamp fidelity: the proxy must keep the source's frame rate and
timebase EXACTLY so a timestamp on the proxy maps 1:1 onto the source (analysis
times computed on the proxy are later applied to the full-res source at export).
We read the source's exact r_frame_rate (a rational, e.g. 30000/1001) and
time_base from ffprobe and pin the proxy to them with -r / -vsync passthrough /
-video_track_timescale — never letting ffmpeg resample to a rounded fps.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from pipeline import config
from pipeline.media import run_ffmpeg


def _content_key(src: Path) -> str:
    """Mirror transcribe._cache_key: hash (resolved path, size, mtime). Cheap
    and stable — re-ingesting the same untouched file hits the cache."""
    stat = src.stat()
    raw = f"{src.resolve()}:{stat.st_size}:{int(stat.st_mtime)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _source_rate_timebase(src: Path) -> tuple[str | None, int | None]:
    """Exact (r_frame_rate, timescale) of the source video stream.

    r_frame_rate is the precise capture rate as a rational string ("30000/1001")
    — we feed it back verbatim to -r so the proxy doesn't drift to a rounded fps.
    timescale is the denominator of the stream time_base ("1/30000" -> 30000),
    pinned on the proxy track so PTS land on the same grid as the source.
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate,time_base",
        "-print_format", "json",
        str(src),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    streams = json.loads(out).get("streams", [])
    if not streams:
        return None, None
    s = streams[0]
    rfr = s.get("r_frame_rate") or None
    if rfr in ("0/0", "0/1"):
        rfr = None
    timescale: int | None = None
    tb = s.get("time_base")  # e.g. "1/30000"
    if tb and "/" in tb:
        try:
            den = int(tb.split("/", 1)[1])
            timescale = den if den > 0 else None
        except ValueError:
            timescale = None
    return rfr, timescale


def build_proxy(src_path: str, out_dir: str | Path) -> str:
    """Build (or reuse) a 540p H.264 proxy of the full source.

    out_dir: the session's outputs dir (the proxy lives next to its clips).
    Returns the proxy path. Content-cached by source hash, so a second call on
    the same untouched source is a no-op.
    """
    src = Path(src_path)
    if not src.exists():
        raise FileNotFoundError(f"Video not found: {src}")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    proxy = out / f"proxy_{_content_key(src)}_{config.PROXY_HEIGHT}p.mp4"
    if proxy.exists():
        return str(proxy)

    _rfr, timescale = _source_rate_timebase(src)

    args = ["-i", str(src)]
    # passthrough vsync keeps every frame's ORIGINAL pts (no drop/dup, no CFR
    # resampling) -> proxy timestamps map 1:1 to the source. Do NOT also pass
    # -r: pinning a rate together with a non-CFR -vsync is contradictory and
    # ffmpeg errors out. Passthrough alone is the correct 1:1 preservation.
    args += [
        "-vf", f"scale=-2:{config.PROXY_HEIGHT}",
        "-vsync", "passthrough",
        "-c:v", config.PROXY_ENCODER,
        "-g", "30", "-sc_threshold", "0",
    ]
    if timescale:
        # Same PTS grid as the source so proxy seconds == source seconds.
        args += ["-video_track_timescale", str(timescale)]
    args += ["-c:a", "aac", "-b:a", "128k", str(proxy)]

    run_ffmpeg(args)
    return str(proxy)


def keyframe_index(src_path: str) -> list[float]:
    """Sorted I-frame presentation times (seconds) of the source.

    Used by later phases to snap cuts/seeks to real keyframes cheaply. Cached as
    JSON keyed by the source content hash (lives in config.CACHE_DIR, mirroring
    the transcript cache convention)."""
    src = Path(src_path)
    if not src.exists():
        raise FileNotFoundError(f"Video not found: {src}")
    cache = config.CACHE_DIR / f"keyframes_{_content_key(src)}.json"
    if cache.exists():
        return json.loads(cache.read_text())

    # -skip_frame nokey decodes only keyframes -> fast even on a 30-min source.
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-skip_frame", "nokey",
        "-show_entries", "frame=pts_time,best_effort_timestamp_time",
        "-print_format", "json",
        str(src),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    frames = json.loads(out).get("frames", [])
    times: list[float] = []
    for f in frames:
        t = f.get("pts_time") or f.get("best_effort_timestamp_time")
        if t in (None, "N/A"):
            continue
        try:
            times.append(float(t))
        except (TypeError, ValueError):
            continue
    times = sorted(set(times))
    cache.write_text(json.dumps(times))
    return times
