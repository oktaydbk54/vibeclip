"""V4.2 — Color grading: built-in looks + user .cube LUTs with strength.

Pro practice (research): apply a creative grade at 30-70% strength, never
100%. Strength is implemented the editor way: split the frame, grade one
copy, blend it back over the original at `strength` opacity.
"""

from __future__ import annotations

from pathlib import Path

from pipeline import config
from pipeline.media import run_ffmpeg

# Built-in looks (no assets needed). Kept subtle; strength scales them.
LOOKS: dict[str, str] = {
    "warm": "colortemperature=temperature=4800,eq=saturation=1.08",
    "cold": "colortemperature=temperature=7800,eq=saturation=0.96",
    "bw": "hue=s=0,eq=contrast=1.06",
    "cinematic": ("curves=blue='0/0.05 0.5/0.5 1/0.95':"
                  "red='0/0 0.5/0.52 1/1',eq=saturation=1.06:contrast=1.05"),
    "vintage": "curves=all='0/0.06 1/0.93',eq=saturation=0.85:gamma=1.04",
    # Meme grades. "vivid" = punchy reaction-video pop; "deepfried" = the
    # intentionally blown-out crushed-saturation meme look.
    "vivid": "eq=saturation=1.35:contrast=1.12:gamma=0.97",
    "deepfried": ("eq=saturation=2.2:contrast=1.45:brightness=0.04,"
                  "noise=alls=8:allf=t,unsharp=5:5:1.4"),
}


def apply_look(clip_path: str, look: str = "", cube: str = "",
               strength: float = 0.5, out_path: str | None = None) -> str:
    """Grade a clip with a named look OR a .cube LUT, mixed at `strength`."""
    src = Path(clip_path)
    strength = max(0.1, min(1.0, float(strength)))
    if cube:
        if not Path(cube).exists():
            raise ValueError(f"LUT not found: {cube}")
        grade = f"lut3d=file='{cube}':interp=tetrahedral"
    elif look in LOOKS:
        grade = LOOKS[look]
    else:
        raise ValueError(f"Unknown look '{look}'. Built-ins: {sorted(LOOKS)}")

    if strength >= 0.999:
        vf = grade
    else:
        vf = (f"split[orig][g];[g]{grade}[graded];"
              f"[orig][graded]blend=all_mode=normal:all_opacity={strength:.2f}")

    out = out_path or str(src.with_name(src.stem + "_look.mp4"))
    run_ffmpeg([
        "-i", str(src.resolve()),
        "-filter_complex", f"[0:v]{vf}[v]",
        "-map", "[v]", "-map", "0:a?",
        "-c:v", config.VIDEO_ENCODER, "-c:a", "copy",
        str(Path(out).resolve()),
    ])
    return out
