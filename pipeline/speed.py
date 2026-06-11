"""Per-clip constant speed (retime) stage.

setpts=PTS/F speeds the VIDEO up by F (F>1 → faster/shorter, F<1 → slower).
atempo=F speeds the AUDIO up by the same factor while preserving pitch — but
atempo only accepts 0.5–2.0, so factors outside that range are realized as a
CHAIN of atempo links whose product equals F (e.g. 4× → atempo=2,2).

This stage sits right after `trim` in the canonical order, so every later stage
(zoom/subtitles/overlay/fx/music/…) operates in the already-sped timeline — the
only thing that must be rescaled is caption word-timing (handled by the caller
dividing word times by the factor). The cut/jumpcut/trim TimeMap is untouched
because speed lives outside TIMING_STAGES.
"""

from __future__ import annotations

from pathlib import Path

from pipeline import config
from pipeline.media import run_ffmpeg


def _atempo_chain(factor: float) -> str:
    """Express `factor` as a comma-chain of atempo links each within [0.5, 2.0]."""
    links: list[float] = []
    rem = factor
    while rem > 2.0 + 1e-9:
        links.append(2.0)
        rem /= 2.0
    while rem < 0.5 - 1e-9:
        links.append(0.5)
        rem /= 0.5
    links.append(rem)
    return ",".join(f"atempo={x:.6f}" for x in links)


def retime(clip_path: str, factor: float, out_path: str | None = None) -> str:
    """Change a clip's constant playback speed by `factor`. Returns the path.

    factor > 1 → faster (shorter); factor < 1 → slower (longer). Pitch is
    preserved on the audio. factor == 1 is a no-op (returns the input).
    """
    f = max(0.25, min(4.0, float(factor)))
    if abs(f - 1.0) < 1e-3:
        return clip_path
    src = Path(clip_path)
    out = out_path or str(src.with_name(src.stem + "_spd.mp4"))
    run_ffmpeg([
        "-i", str(src.resolve()),
        "-vf", f"setpts=PTS/{f:.6f}",
        "-af", _atempo_chain(f),
        "-c:v", config.VIDEO_ENCODER,
        "-c:a", "aac", "-b:a", "192k",
        str(Path(out).resolve()),
    ])
    return out
