"""Optional libass caption path (Faz 2.3) — true per-frame hero captions.

The default caption engine rasterizes PNGs and composites them, because the
shipped ffmpeg has no libass (so it can't burn .ass subtitles). When a user
brings a libass-enabled ffmpeg, this module offers a higher-fidelity path: an
.ass document with smooth per-frame entrance animation (ASS `\\fad`/`\\t`
transforms give continuous fade+scale that the PNG path only approximates in K
steps), burned in one `subtitles` filter pass.

Everything is gated by `libass_available()` — on the default build burn_ass()
returns None and the caller keeps the PNG path. The ASS document generation is
pure string logic (no ffmpeg), so it is fully unit-tested even where libass
itself cannot run.
"""

from __future__ import annotations

import functools
from pathlib import Path

from pipeline import config
from pipeline.captions import build_caption_segments
from pipeline.media import run_ffmpeg


@functools.lru_cache(maxsize=1)
def libass_available() -> bool:
    """True when the active ffmpeg exposes the `subtitles`/`ass` filter."""
    import subprocess
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:  # noqa: BLE001
        return False
    return any(f" {name} " in out for name in ("ass", "subtitles"))


def _ass_ts(t: float) -> str:
    """Seconds -> ASS H:MM:SS.cc (centisecond) timestamp."""
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _ass_color(rgba: tuple) -> str:
    """RGBA tuple -> ASS &HAABBGGRR (alpha is 00=opaque..FF=transparent)."""
    r, g, b = int(rgba[0]), int(rgba[1]), int(rgba[2])
    a = 255 - int(rgba[3]) if len(rgba) > 3 else 0   # ASS alpha is inverted
    return f"&H{a:02X}{b:02X}{g:02X}{r:02X}"


def _font_name(font_path: str) -> str:
    """Best-effort family name from a font file path (libass matches by name)."""
    stem = Path(font_path).stem if font_path else ""
    return stem.replace("-", " ").replace("_", " ").strip() or "Arial"


_ANIM_TAGS = {
    # entrance override tags prepended to each line's text.
    "pop": r"{\fad(120,0)\fscx70\fscy70\t(0,160,\fscx100\fscy100)}",
    "spring": r"{\fad(120,0)\fscx60\fscy60\t(0,90,\fscx108\fscy108)"
              r"\t(90,200,\fscx100\fscy100)}",
    "slide": r"{\fad(120,0)\move(0,40,0,0,0,160)}",  # placeholder offset; see note
    "none": r"",
}


def build_ass(words: list[dict], width: int, height: int, style,
              clip_start: float = 0.0) -> str:
    """Render caption segments to a full .ass document string.

    `style` is a subtitle.SubStyle. Each on-screen line gets the style's
    entrance animation as ASS transform tags, so libass produces continuous
    motion. Returns the .ass text (caller writes + burns it).
    """
    segments = [s for s in build_caption_segments(words, clip_start)
                if s.get("text")]
    fontname = _font_name(getattr(style, "font_path", ""))
    size = int(getattr(style, "font_size", 84))
    outline = int(getattr(style, "stroke_width", 7))
    primary = _ass_color(getattr(style, "color_base", (255, 255, 255, 255)))
    outline_c = "&H00000000"
    # Alignment 2 = bottom-center; MarginV = distance from the frame bottom.
    y_ratio = float(getattr(style, "caption_y_ratio", 0.68))
    margin_v = max(0, int(height * (1.0 - y_ratio)))
    bold = -1
    anim = _ANIM_TAGS.get(getattr(style, "animation", "none"), "")
    upper = bool(getattr(style, "uppercase", True))

    head = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\n"
        "WrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Hero,{fontname},{size},{primary},&H000000FF,{outline_c},"
        f"&H00000000,{bold},0,0,0,100,100,0,0,1,{outline},0,2,60,60,"
        f"{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )
    lines = []
    for seg in segments:
        text = seg["text"]
        if upper:
            text = text.upper()
        text = text.replace("\n", " ").strip()
        lines.append(
            f"Dialogue: 0,{_ass_ts(seg['start'])},{_ass_ts(seg['end'])},"
            f"Hero,,0,0,0,,{anim}{text}")
    return head + "\n".join(lines) + "\n"


def burn_ass(clip_path: str, words: list[dict], clip_start: float = 0.0,
             style=None, out_path: str | None = None,
             canvas: "tuple[int, int] | None" = None) -> str | None:
    """Burn captions via libass. Returns the output path, or None when libass is
    unavailable / on any failure (caller falls back to the PNG path)."""
    if not words or not libass_available():
        return None
    from pipeline.subtitle import SubStyle
    from pipeline.media import ffprobe_info
    st = style or SubStyle()
    try:
        if canvas:
            w, h = canvas
        else:
            info = ffprobe_info(clip_path)
            w, h = info["width"], info["height"]
        ass_text = build_ass(words, w, h, st, clip_start)
        src = Path(clip_path)
        ass_file = config.CACHE_DIR / f"{src.stem}_cap.ass"
        ass_file.write_text(ass_text, encoding="utf-8")
        out = out_path or str(src.with_name(src.stem + "_sub.mp4"))
        # Escape the path for the filter (colons/backslashes inside filtergraph).
        esc = str(ass_file.resolve()).replace("\\", "\\\\").replace(":", "\\:")
        run_ffmpeg([
            "-i", str(src.resolve()),
            "-vf", f"subtitles={esc}",
            "-c:v", config.VIDEO_ENCODER, "-c:a", "copy",
            str(Path(out).resolve()),
        ])
        return out if Path(out).exists() else None
    except Exception:  # noqa: BLE001 — any failure -> caller uses PNG path
        return None
