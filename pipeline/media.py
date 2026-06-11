"""Small ffmpeg/ffprobe helpers shared across the pipeline."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path

from pipeline import progress as _pg


def ffprobe_info(video_path: str | Path) -> dict:
    """Return basic media info: duration, width, height, fps, codec."""
    path = Path(video_path)
    if not path.exists():
        raise FileNotFoundError(f"Video not found: {path}")

    cmd = [
        "ffprobe", "-v", "error",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(path),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    data = json.loads(out)

    video_stream = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "video"), {}
    )
    fps = 0.0
    rate = video_stream.get("avg_frame_rate", "0/0")
    if "/" in rate:
        num, den = rate.split("/")
        fps = float(num) / float(den) if float(den) else 0.0

    return {
        "duration": float(data.get("format", {}).get("duration", 0.0)),
        "width": int(video_stream.get("width", 0)),
        "height": int(video_stream.get("height", 0)),
        "fps": round(fps, 3),
        "codec": video_stream.get("codec_name", ""),
    }


def _looks_like_output(arg: str) -> bool:
    """ffmpeg's grammar puts the single output file last. Treat the final arg
    as the output unless it's a flag or has no extension (e.g. 'pipe:1')."""
    return (isinstance(arg, str) and not arg.startswith("-")
            and "." in Path(arg).name and ":" not in Path(arg).name)


def run_ffmpeg(args: list[str], cwd: str | Path | None = None) -> None:
    """Run ffmpeg with -y, raising with stderr on failure.

    When a render context is installed (pipeline/progress.py) this also streams
    ffmpeg's -progress output for the live %-bar and polls for cancellation,
    killing the process and raising CancelledError if the job was cancelled.

    Output is written to a hidden temp sibling and atomically os.replace'd into
    place, so a killed or failed render never leaves a half-written file where
    the hash-cache would mistake it for a finished artifact.
    """
    out_path = args[-1] if args else None
    atomic = _looks_like_output(out_path) if out_path else False
    tmp = None
    if atomic:
        p = Path(out_path)
        tmp = str(p.with_name(f".{p.stem}.tmp{p.suffix}"))
        args = [*args[:-1], tmp]

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-progress", "pipe:1", "-nostats", *args]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, cwd=str(cwd) if cwd else None)

    # Drain stderr on a side thread so a chatty error log can't deadlock us
    # while we're blocked reading the progress stream.
    err_chunks: list[str] = []

    def _drain():
        if proc.stderr:
            err_chunks.append(proc.stderr.read())

    et = threading.Thread(target=_drain, daemon=True)
    et.start()

    cancelled = False
    try:
        for line in proc.stdout or []:
            line = line.strip()
            if line.startswith("out_time_us="):
                _pg.report_ffmpeg(line.split("=", 1)[1])
            if _pg.should_cancel():
                cancelled = True
                proc.terminate()
                break
    finally:
        if cancelled:
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        proc.wait()
        et.join(timeout=1)

    if cancelled:
        if tmp:
            Path(tmp).unlink(missing_ok=True)
        raise _pg.CancelledError()

    if proc.returncode != 0:
        if tmp:
            Path(tmp).unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg failed:\n{''.join(err_chunks)}")

    if tmp:
        os.replace(tmp, out_path)
