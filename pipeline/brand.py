"""V2.3 — Branding overlays: watermark + title card.

Both are OVERLAYS on the existing frames (a prepended title segment would
shift the timeline and desync every downstream timestamp). Title card text is
Pillow-rendered to a PNG (no drawtext on this ffmpeg build) and shown for the
first N seconds; the watermark is a corner image for the whole clip.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from pipeline import config
from pipeline.media import ffprobe_info, run_ffmpeg

TITLE_FONT = "/System/Library/Fonts/Supplemental/Arial Black.ttf"
# Corner anchors sit inside the platform safe zone (top 11%, bottom 79%,
# right 87.5%) so the watermark never hides under TikTok/Shorts UI.
CORNERS = {"tl": (0.06, 0.115), "tr": (0.875, 0.115),
           "bl": (0.06, 0.7), "br": (0.875, 0.7)}


def _render_title_png(text: str, width: int, height: int, out_path: str,
                      font_path: str = TITLE_FONT) -> None:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    size = int(height * 0.045)
    font = ImageFont.truetype(font_path, size)

    words, lines, cur = text.upper().split(), [], []
    for w in words:
        trial = " ".join(cur + [w])
        if draw.textlength(trial, font=font) <= width * 0.84 or not cur:
            cur.append(w)
        else:
            lines.append(" ".join(cur))
            cur = [w]
    if cur:
        lines.append(" ".join(cur))

    line_h = int(size * 1.25)
    pad = int(size * 0.55)
    block_w = max(int(draw.textlength(ln, font=font)) for ln in lines)
    block_h = line_h * len(lines)
    x0 = (width - block_w) // 2 - pad
    y0 = int(height * 0.12) - pad
    draw.rounded_rectangle(
        [x0, y0, x0 + block_w + 2 * pad, y0 + block_h + 2 * pad],
        radius=int(size * 0.4), fill=(10, 10, 10, 215))
    y = int(height * 0.12)
    for ln in lines:
        x = (width - draw.textlength(ln, font=font)) / 2
        draw.text((x, y), ln, font=font, fill=(255, 255, 255, 255))
        y += line_h
    img.save(out_path)


def apply_brand(clip_path: str, watermark: dict | None = None,
                title: dict | None = None, out_path: str | None = None) -> str:
    """One encode adding watermark and/or title-card overlays.

    watermark: {"path", "corner": tl|tr|bl|br, "opacity": 0..1, "scale": 0..1}
    title:     {"text", "duration": s}
    """
    src = Path(clip_path)
    if not watermark and not title:
        return clip_path
    info = ffprobe_info(clip_path)
    w, h = info["width"], info["height"]

    inputs: list[str] = ["-i", str(src.resolve())]
    steps: list[str] = []
    label = "0:v"
    idx = 1

    if watermark and Path(watermark.get("path", "")).exists():
        inputs += ["-i", str(Path(watermark["path"]).resolve())]
        opacity = float(watermark.get("opacity", 0.85))
        scale = float(watermark.get("scale", 0.14))
        fx, fy = CORNERS.get(watermark.get("corner", "tr"), CORNERS["tr"])
        ww = int(w * scale)
        steps.append(
            f"[{idx}:v]scale={ww}:-1,format=rgba,"
            f"colorchannelmixer=aa={opacity:.2f}[wm]")
        x = f"{int(w * fx)}-overlay_w" if fx > 0.5 else f"{int(w * fx)}"
        y = f"{int(h * fy)}"
        steps.append(f"[{label}][wm]overlay={x}:{y}[vw]")
        label = "vw"
        idx += 1

    if title and title.get("text"):
        key = hashlib.sha1(
            f"{title['text']}:{w}x{h}".encode()).hexdigest()[:10]
        png = str(config.CACHE_DIR / f"title_{key}.png")
        _render_title_png(title["text"], w, h, png)
        inputs += ["-i", png]
        dur = float(title.get("duration", 2.5))
        steps.append(
            f"[{label}][{idx}:v]overlay=0:0:"
            f"enable='between(t,0,{dur:.2f})'[vt]")
        label = "vt"
        idx += 1

    out = out_path or str(src.with_name(src.stem + "_brand.mp4"))
    run_ffmpeg([
        *inputs,
        "-filter_complex", ";".join(steps),
        "-map", f"[{label}]", "-map", "0:a?",
        "-c:v", config.VIDEO_ENCODER,
        "-c:a", "copy",
        str(Path(out).resolve()),
    ])
    return out
