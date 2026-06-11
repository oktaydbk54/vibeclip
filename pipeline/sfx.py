"""Faz 5d (part 1) — Timed sound effects.

add_sfx mixes one or more sound-effect files into the clip's audio at chosen
timestamps (e.g. a whoosh on a punch-in, a ding on a key point). Each effect is
delayed to its time with adelay, then amix-ed over the original audio.
"""

from __future__ import annotations

from pathlib import Path

from pipeline import config
from pipeline.media import ffprobe_info, run_ffmpeg


def add_sfx(
    clip_path: str,
    events: list[dict],
    out_path: str | None = None,
) -> str:
    """Overlay sound effects on a clip.

    events: [{"time": seconds, "path": sfx_file, "volume": 0..1}]
    Returns output path. Video is copied; only audio is rebuilt.
    """
    src = Path(clip_path)
    events = [e for e in events if Path(e["path"]).exists()]
    if not events:
        return clip_path

    dur = ffprobe_info(clip_path)["duration"]

    inputs: list[str] = ["-i", str(src.resolve())]
    for e in events:
        inputs += ["-i", str(Path(e["path"]).resolve())]

    # Each effect gets a short envelope (60ms attack fade) so it doesn't click,
    # and the combined SFX bus is gently ducked under the voice so speech stays
    # intelligible. No loudnorm here — normalization happens once at chain end.
    parts = []
    sfx_labels = []
    for i, e in enumerate(events, start=1):
        ms = int(max(0.0, e["time"]) * 1000)
        vol = e.get("volume", 0.8)
        parts.append(
            f"[{i}:a]afade=t=in:st=0:d=0.06,volume={vol},adelay={ms}|{ms}[s{i}]"
        )
        sfx_labels.append(f"s{i}")

    if len(sfx_labels) == 1:
        sfx_bus = sfx_labels[0]
    else:
        sfx_in = "".join(f"[{l}]" for l in sfx_labels)
        parts.append(
            f"{sfx_in}amix=inputs={len(sfx_labels)}:duration=longest:"
            f"dropout_transition=0,volume={len(sfx_labels)}[sbus]"
        )
        sfx_bus = "sbus"

    parts.append("[0:a]asplit=2[sc][voice]")
    parts.append(
        f"[{sfx_bus}][sc]sidechaincompress="
        f"threshold=0.05:ratio=4:attack=20:release=250[sduck]"
    )
    parts.append(
        "[voice][sduck]amix=inputs=2:duration=first:dropout_transition=0[aout]"
    )
    filtergraph = ";".join(parts)

    out = out_path or str(src.with_name(src.stem + "_sfx.mp4"))
    run_ffmpeg([
        *inputs,
        "-filter_complex", filtergraph,
        "-map", "0:v", "-map", "[aout]",
        "-t", f"{dur:.3f}",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        str(Path(out).resolve()),
    ])
    return out
