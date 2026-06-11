"""Faz 5b — Audio layer: background music with auto-ducking + loudness norm.

add_background_music mixes a music bed under the clip. With duck=True the music
is side-chain compressed by the speech, so it automatically dips whenever someone
is talking and swells in the gaps — the standard podcast/short music feel.

normalize_loudness applies EBU R128 (loudnorm), the loudness target platforms
expect, so clips don't come out too quiet or too hot.
"""

from __future__ import annotations

from pathlib import Path

from pipeline import config
from pipeline.media import ffprobe_info, run_ffmpeg


def add_background_music(
    clip_path: str,
    music_path: str,
    music_volume: float = 0.18,
    duck: bool = True,
    out_path: str | None = None,
) -> str:
    """Mix a (looping) music bed under the clip's audio. Returns output path."""
    src = Path(clip_path)
    music = Path(music_path)
    if not music.exists():
        raise FileNotFoundError(f"Music not found: {music}")

    dur = ffprobe_info(clip_path)["duration"]

    # NOTE: no loudnorm here — normalization happens ONCE at the end of the edit
    # chain (fade_in_out(normalize=True) / normalize_loudness). Stacking loudnorm
    # per-stage crushed dynamics ("squashed" sound).
    if duck:
        # main = music, sidechain = speech -> music ducks under speech.
        filtergraph = (
            f"[1:a]volume={music_volume}[m];"
            f"[0:a]asplit=2[sc][voice];"
            f"[m][sc]sidechaincompress=threshold=0.03:ratio=12:attack=20:release=300[mduck];"
            f"[voice][mduck]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        )
    else:
        filtergraph = (
            f"[1:a]volume={music_volume}[m];"
            f"[0:a][m]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        )

    out = out_path or str(src.with_name(src.stem + "_music.mp4"))
    run_ffmpeg([
        "-i", str(src.resolve()),
        "-stream_loop", "-1", "-i", str(music.resolve()),
        "-filter_complex", filtergraph,
        "-map", "0:v", "-map", "[aout]",
        "-t", f"{dur:.3f}",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        str(Path(out).resolve()),
    ])
    return out


def normalize_loudness(clip_path: str, out_path: str | None = None) -> str:
    """Apply EBU R128 loudness normalization to a clip's audio."""
    src = Path(clip_path)
    out = out_path or str(src.with_name(src.stem + "_norm.mp4"))
    run_ffmpeg([
        "-i", str(src.resolve()),
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        str(Path(out).resolve()),
    ])
    return out
