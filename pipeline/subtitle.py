"""Faz 4b / 5d — Burn captions onto a clip (plain or word-highlight karaoke).

This build's ffmpeg lacks libass/freetype, so captions are rendered to transparent
PNGs with Pillow and composited via the core `overlay` filter, timed with
enable='between(t,start,end)'.

- Plain mode: one PNG per caption chunk.
- Karaoke mode: one PNG per word, where the currently-spoken word is highlighted
  (yellow), timed to that word's range — the modern short-form caption look.

Word timestamps are in SOURCE time; we subtract `clip_start`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pipeline import config
from pipeline.captions import build_caption_segments
from pipeline.media import ffprobe_info, run_ffmpeg

FONT_PATH = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
FONT_SIZE = 84
STROKE_WIDTH = 7
CAPTION_Y_RATIO = 0.68
# Caption block must stay above this — TikTok/IG/Shorts bottom UI (progress bar,
# buttons, comments) occupies roughly the last 18-20% of a 9:16 frame.
SAFE_BOTTOM_RATIO = 0.80
COLOR_BASE = (255, 255, 255, 255)
COLOR_HILITE = (255, 214, 10, 255)  # warm yellow
HILITE_SCALE = 1.14  # spoken word "pops" slightly larger than its neighbors
UPPERCASE = True


@dataclass
class SubStyle:
    """Caption styling for ONE render — replaces the old global monkeypatch so
    parallel renders can't clobber each other's style. Defaults mirror the
    module constants (the standard short-form look)."""
    font_path: str = FONT_PATH
    font_size: int = FONT_SIZE
    stroke_width: int = STROKE_WIDTH
    caption_y_ratio: float = CAPTION_Y_RATIO
    safe_bottom_ratio: float = SAFE_BOTTOM_RATIO
    color_base: tuple = COLOR_BASE
    color_hilite: tuple = COLOR_HILITE
    hilite_scale: float = HILITE_SCALE
    uppercase: bool = UPPERCASE


def _wrap_indices(draw, words: list[str], font, max_width: int) -> list[list[int]]:
    """Wrap a list of words into lines, returning word-index groups per line."""
    lines: list[list[int]] = []
    cur: list[int] = []
    for i, w in enumerate(words):
        trial = " ".join(words[j] for j in cur + [i])
        if draw.textlength(trial, font=font) <= max_width or not cur:
            cur.append(i)
        else:
            lines.append(cur)
            cur = [i]
    if cur:
        lines.append(cur)
    return lines


def _render_png(words: list[str], width: int, height: int, out_path: str,
                highlight: int = -1, style: "SubStyle | None" = None) -> None:
    from PIL import Image, ImageDraw, ImageFont

    st = style or SubStyle()
    words = [w.upper() for w in words] if st.uppercase else list(words)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(st.font_path, st.font_size)
    hi_font = ImageFont.truetype(st.font_path, int(st.font_size * st.hilite_scale))

    def _font(i: int):
        return hi_font if i == highlight else font

    line_groups = _wrap_indices(draw, words, font, int(width * 0.86))
    line_h = int(st.font_size * 1.18)
    space_w = draw.textlength(" ", font=font)

    # Center the block at caption_y_ratio but clamp it above the platform UI
    # safe area (and below the top 8%).
    total_h = line_h * len(line_groups)
    y = int(height * st.caption_y_ratio) - total_h // 2
    y = min(y, int(height * st.safe_bottom_ratio) - total_h)
    y = max(y, int(height * 0.08))

    for group in line_groups:
        line_w = sum(draw.textlength(words[i], font=_font(i)) for i in group) \
            + space_w * (len(group) - 1)
        x = (width - line_w) / 2
        for i in group:
            color = st.color_hilite if i == highlight else st.color_base
            # Lift the larger highlighted word so baselines roughly align.
            dy = -int((st.hilite_scale - 1) * st.font_size * 0.8) \
                if i == highlight else 0
            draw.text((x, y + dy), words[i], font=_font(i), fill=color,
                      stroke_width=st.stroke_width, stroke_fill=(0, 0, 0, 255))
            x += draw.textlength(words[i], font=_font(i)) + space_w
        y += line_h
    img.save(out_path)


def burn_subtitles(clip_path: str, words: list[dict], clip_start: float = 0.0,
                   karaoke: bool = False, out_path: str | None = None,
                   pre_vf: str = "", canvas: tuple[int, int] | None = None,
                   style: "SubStyle | None" = None) -> str:
    """Burn word-synced captions onto a clip. Returns output path.

    karaoke=True highlights each word as it is spoken (one overlay per word);
    karaoke=False shows one static caption per chunk.

    pre_vf: optional video filter chain (e.g. reframe crop + zoompan) applied
    BEFORE the caption overlays in the SAME encode — fuses what used to be 2-3
    re-encodes into one pass. canvas=(w,h) gives the frame size pre_vf produces
    (PNG captions are rendered at that size, not the input's).

    style: a SubStyle for this render (defaults to the standard look). Passed
    explicitly so concurrent renders don't share mutable module state.
    """
    src = Path(clip_path)
    if not words:
        return clip_path

    st = style or SubStyle()
    if canvas:
        w, h = canvas
    else:
        info = ffprobe_info(clip_path)
        w, h = info["width"], info["height"]
    chunks = [c for c in build_caption_segments(words, clip_start) if c["text"]]
    if not chunks:
        return clip_path

    # Build (png_path, start, end) overlay events.
    events: list[tuple[str, float, float]] = []
    n = 0
    for ci, c in enumerate(chunks):
        line_words = [wd["word"] for wd in c["words"]]
        if karaoke:
            for wi, wd in enumerate(c["words"]):
                p = str(config.CACHE_DIR / f"{src.stem}_k{n:04d}.png")
                _render_png(line_words, w, h, p, highlight=wi, style=st)
                events.append((p, wd["start"], wd["end"]))
                n += 1
        else:
            p = str(config.CACHE_DIR / f"{src.stem}_c{ci:03d}.png")
            _render_png(line_words, w, h, p, highlight=-1, style=st)
            events.append((p, c["start"], c["end"]))

    inputs: list[str] = ["-i", str(src.resolve())]
    for p, _, _ in events:
        inputs += ["-i", p]

    steps = []
    label = "0:v"
    if pre_vf:
        steps.append(f"[0:v]{pre_vf}[vbase]")
        label = "vbase"
    for i, (_, s, e) in enumerate(events):
        nxt = f"v{i}"
        steps.append(
            f"[{label}][{i + 1}:v]overlay=0:0:enable='between(t,{s:.3f},{e:.3f})'[{nxt}]"
        )
        label = nxt
    filtergraph = ";".join(steps)

    out = out_path or str(src.with_name(src.stem + "_sub.mp4"))
    run_ffmpeg([
        *inputs,
        "-filter_complex", filtergraph,
        "-map", f"[{label}]", "-map", "0:a?",
        "-c:v", config.VIDEO_ENCODER,
        "-c:a", "copy",
        str(Path(out).resolve()),
    ])
    return out
