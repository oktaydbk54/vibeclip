"""Faz 4b / 5d — Burn captions onto a clip (plain or word-highlight karaoke).

This build's ffmpeg lacks libass/freetype, so captions are rendered to transparent
PNGs with Pillow and composited via the core `overlay` filter, timed with
enable='between(t,start,end)'.

- Plain mode: one PNG per caption chunk.
- Karaoke mode: one PNG per word, where the currently-spoken word is highlighted
  (yellow), timed to that word's range — the modern short-form caption look.

Caption engine (additive, opt-in): when a SubStyle requests it, the karaoke look
gains per-word entrance animations (pop/slide/spring), a rounded pill behind the
active word, an LLM keyword-emphasis pass and an auto-emoji map. ffmpeg's overlay
can't animate alpha/scale on its own without per-frame PNGs, so entrances are a
BOUNDED K-step approximation: each animated word renders K small PNGs (scale or
offset steps) switched across a short entrance window via enable= ranges. With
the defaults (animation='none', pill=None) the render is byte-for-byte identical
to the historical full-frame karaoke path.

Word timestamps are in SOURCE time; we subtract `clip_start`.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
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

# Caption-engine entrance tuning. The K-step approximation stays bounded so we
# never re-introduce the per-frame PNG explosion: a word's entrance is split into
# up to ANIM_STEPS sub-PNGs shown across ANIM_DURATION seconds, then the settled
# PNG holds for the rest of the word. The step COUNT is adaptive — long enough
# entrances get more sub-PNGs (smoother motion), short ones fewer (less work) —
# capped at ANIM_STEPS so the worst case is bounded.
ANIM_STEPS = 6        # max sub-PNGs per entrance (was a flat 4)
ANIM_STEPS_MIN = 3    # floor so even a brief entrance still eases, not jumps
ANIM_STEP_DT = 0.03   # target seconds per sub-PNG (~1 frame at 33fps)
ANIM_DURATION = 0.18  # seconds for the entrance to settle


def _adaptive_steps(dur: float) -> int:
    """How many entrance sub-PNGs to render for an entrance of `dur` seconds:
    one per ~ANIM_STEP_DT, clamped to [ANIM_STEPS_MIN, ANIM_STEPS]. More steps on
    longer entrances = smoother motion; bounded so the PNG count never explodes."""
    return max(ANIM_STEPS_MIN, min(ANIM_STEPS, round(dur / ANIM_STEP_DT)))


def _ease_out_cubic(t: float) -> float:
    """Decelerating ease — fast then settling. ease(0)=0, ease(1)=1."""
    return 1.0 - (1.0 - t) ** 3


def _ease_out_back(t: float, overshoot: float = 1.70158) -> float:
    """Ease that overshoots past 1.0 then settles back to it (bounce). At t==1
    returns exactly 1.0, so an animation built on it ends perfectly settled."""
    c1 = overshoot
    c3 = c1 + 1.0
    return 1.0 + c3 * (t - 1.0) ** 3 + c1 * (t - 1.0) ** 2


@functools.lru_cache(maxsize=64)
def _resolve_font(font_path: str, size: int):
    """Load a TrueType font with a portable fallback chain.

    Tries (a) the requested path, (b) any font bundled under assets/fonts/,
    (c) PIL's built-in default. Never raises: an unavailable/offline font must
    degrade to an ugly-but-working caption rather than crashing the render.
    """
    from PIL import ImageFont

    candidates: list[str] = []
    if font_path:
        candidates.append(font_path)
    fonts_dir = config.ROOT / "assets" / "fonts"
    if fonts_dir.exists():
        candidates += [str(p) for p in sorted(fonts_dir.glob("*.tt[fc]"))]
    for cand in candidates:
        try:
            if Path(cand).exists():
                return ImageFont.truetype(cand, size)
        except OSError:
            continue
    # Last resort — bitmap default. Honors `size` on modern Pillow, ignores it
    # on very old ones, but never crashes.
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10
        return ImageFont.load_default()


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
    # Forward-compatible caption-engine knobs. Today's _render_png ignores these
    # entirely (default animation='none'/pill=None reproduces the current look);
    # the later caption engine consumes them. Kept here so apply_style/styles.py
    # can populate the full param surface before the engine lands.
    animation: str = "none"  # none | pop | slide | spring
    pill: "str | bool | None" = None  # rounded box color behind the active word
    emphasis_keywords: "list[str] | None" = None  # words to emphasize
    auto_emoji: bool = False  # auto-place a contextual emoji per caption
    # keyword (lowercased, punctuation-stripped) -> emoji, appended after the
    # word in the caption. Populated by _plan_emphasis when auto_emoji is on.
    emoji_map: dict = field(default_factory=dict)
    # Color emphasized keywords get instead of color_base (defaults to hilite).
    color_emphasis: "tuple | None" = None


def _norm(word: str) -> str:
    """Lowercase + strip surrounding punctuation for keyword/emoji matching."""
    return word.strip().strip(".,!?;:'\"()[]{}…-").lower()


def _pill_color(pill, fallback: tuple = (0, 0, 0, 220)) -> "tuple | None":
    """Resolve a SubStyle.pill value to an RGBA tuple, or None if disabled."""
    if not pill:
        return None
    if pill is True:
        return fallback
    if isinstance(pill, str):
        h = pill.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        try:
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 235)
        except ValueError:
            return fallback
    if isinstance(pill, (tuple, list)) and len(pill) >= 3:
        t = tuple(int(c) for c in pill[:4])
        return t if len(t) == 4 else (*t, 235)
    return fallback


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


def _caption_band(chunks: list[dict], width: int, height: int,
                  st: "SubStyle") -> "tuple[int, int | None]":
    """Compute a tight (band_top, band_h) covering every caption block, so the
    karaoke path can render small band PNGs instead of full-frame ones.

    Returns (0, None) — i.e. "render full-frame" — when the band would cover
    almost the whole frame anyway (many-line captions), so the optimization is
    never a pessimization. Mirrors the y-placement math in _render_png.
    """
    from PIL import Image, ImageDraw

    draw = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    font = _resolve_font(st.font_path, st.font_size)
    line_h = int(st.font_size * 1.18)
    top_min, bot_max = height, 0
    for c in chunks:
        raw = [wd["word"] for wd in c["words"]]
        words = [w.upper() for w in raw] if st.uppercase else raw
        groups = _wrap_indices(draw, words, font, int(width * 0.86))
        total_h = line_h * max(1, len(groups))
        y = int(height * st.caption_y_ratio) - total_h // 2
        y = min(y, int(height * st.safe_bottom_ratio) - total_h)
        y = max(y, int(height * 0.08))
        top_min = min(top_min, y)
        bot_max = max(bot_max, y + total_h)
    if bot_max <= top_min:
        return 0, None
    # Margin absorbs stroke, the highlight's upward lift and the pill padding.
    margin = int(st.font_size * 1.2) + st.stroke_width
    top = max(0, top_min - margin)
    bot = min(height, bot_max + margin)
    band_h = bot - top
    if band_h <= 0 or band_h >= int(height * 0.92):
        return 0, None
    return top, band_h


def _render_png(words: list[str], width: int, height: int, out_path: str,
                highlight: int = -1, style: "SubStyle | None" = None,
                band_top: int = 0, png_h: "int | None" = None) -> None:
    """Render a caption PNG (the historical chokepoint).

    Default style (animation='none', pill=None, no emphasis/emoji) reproduces
    the original look. The caption-engine knobs add a pill behind the active
    word, color emphasized keywords and append auto-emoji — all additive.

    Perf: captions only occupy a thin horizontal band, so callers may pass
    `png_h` (band height) + `band_top` (the band's y on the full frame). The
    image is then `width x png_h` (not full-frame) and the text is drawn shifted
    up by band_top; the caller overlays it at y=band_top. `height` stays the FULL
    frame height so the vertical-placement math is unchanged. png_h=None keeps
    the historical full-frame PNG (used by the animated path and the unit tests).
    """
    from PIL import Image, ImageDraw

    st = style or SubStyle()
    img_h = png_h if png_h is not None else height
    raw = list(words)  # casing-preserved originals for keyword/emoji matching
    words = [w.upper() for w in raw] if st.uppercase else list(raw)
    # Append auto-emoji to the display word (matched on the normalized original).
    if st.emoji_map:
        words = [
            f"{w} {st.emoji_map[_norm(o)]}" if _norm(o) in st.emoji_map else w
            for w, o in zip(words, raw)
        ]
    emphasis = {_norm(k) for k in (st.emphasis_keywords or [])}

    img = Image.new("RGBA", (width, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _resolve_font(st.font_path, st.font_size)
    hi_font = _resolve_font(st.font_path, int(st.font_size * st.hilite_scale))
    pill_rgba = _pill_color(st.pill)

    def _font(i: int):
        return hi_font if i == highlight else font

    def _color(i: int) -> tuple:
        if i == highlight:
            return st.color_hilite
        if _norm(raw[i]) in emphasis:
            return st.color_emphasis or st.color_hilite
        return st.color_base

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
            color = _color(i)
            # Lift the larger highlighted word so baselines roughly align.
            dy = -int((st.hilite_scale - 1) * st.font_size * 0.8) \
                if i == highlight else 0
            # Draw y, shifted into the band's local coordinate space.
            yd = y + dy - band_top
            # Rounded pill behind the active word (drawn under the glyphs).
            if pill_rgba is not None and i == highlight:
                wlen = draw.textlength(words[i], font=_font(i))
                pad_x = int(st.font_size * 0.22)
                pad_y = int(st.font_size * 0.12)
                radius = int(st.font_size * 0.28)
                draw.rounded_rectangle(
                    [x - pad_x, yd - pad_y,
                     x + wlen + pad_x, yd + line_h - pad_y],
                    radius=radius, fill=pill_rgba,
                )
            draw.text((x, yd), words[i], font=_font(i), fill=color,
                      stroke_width=st.stroke_width, stroke_fill=(0, 0, 0, 255))
            x += draw.textlength(words[i], font=_font(i)) + space_w
        y += line_h
    img.save(out_path)


def _ease_steps(animation: str, steps: int) -> list[tuple[float, float, float]]:
    """Per-step (scale, dy_ratio, alpha) for an entrance, ending settled at
    (1.0, 0.0, 1.0). dy_ratio is a fraction of the caption block height.

    Curves are properly eased (not linear) so entrances decelerate into place
    the way pro motion captions do, instead of marching in at constant speed:

    pop    : grows from small to full size on an ease-out (fast, then settle).
    spring : overshoots past full size then settles back (ease-out-back bounce).
    slide  : rises from below on an ease-out, fading in; no scale change.
    """
    if steps < 1:
        steps = 1
    out: list[tuple[float, float, float]] = []
    for k in range(steps):
        t = (k + 1) / steps  # 0<t<=1, t==1 is settled
        e = _ease_out_cubic(t)
        if animation == "pop":
            out.append((0.55 + 0.45 * e, 0.0, min(1.0, 0.2 + 0.8 * e)))
        elif animation == "spring":
            # Scale overshoots above 1.0 mid-entrance, converging to 1.0 at t==1.
            scale = 0.5 + 0.5 * _ease_out_back(t, overshoot=2.0)
            out.append((scale, 0.0, min(1.0, 0.3 + 0.7 * e)))
        elif animation == "slide":
            out.append((1.0, 0.55 * (1.0 - e), min(1.0, 0.3 + 0.7 * e)))
        else:  # none / unknown — single settled frame
            out.append((1.0, 0.0, 1.0))
    return out


def _anim_variant(settled_png: str, out_png: str, width: int, height: int,
                  cy: float, scale: float, dy: float, alpha: float) -> None:
    """Write a transformed copy of a settled caption PNG for one entrance step.

    Scales the whole transparent canvas about the caption-block center (cy),
    shifts it vertically by dy px and multiplies its alpha. Output stays the
    same WxH so it still composites at overlay=0:0 (no x/y math in ffmpeg).
    """
    from PIL import Image

    with Image.open(settled_png) as im:
        base = im.convert("RGBA")
    if scale != 1.0:
        sw, sh = max(1, int(width * scale)), max(1, int(height * scale))
        scaled = base.resize((sw, sh), Image.BILINEAR)
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        # Keep the caption block centered on cy after scaling about that point.
        ox = int((width - sw) / 2)
        oy = int(cy - cy * scale)
        # paste tolerates off-canvas / negative offsets (overshoot scale>1).
        canvas.paste(scaled, (ox, oy), scaled)
        base = canvas
    if dy:
        shifted = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        shifted.paste(base, (0, int(dy)), base)
        base = shifted
    if alpha < 1.0:
        r, g, b, a = base.split()
        a = a.point(lambda v: int(v * alpha))
        base = Image.merge("RGBA", (r, g, b, a))
    base.save(out_png)


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
    # Optional libass hero-caption path (Faz 2.3): true per-frame motion on a
    # libass-enabled ffmpeg. Gated by CAPTIONS_ENGINE; only fires when the burn
    # is a simple caption pass (no pre_vf fused crop/zoom — that stays PNG so the
    # single-encode fusion is preserved). Any miss falls through to PNG below.
    engine = config.CAPTIONS_ENGINE
    if engine in ("libass", "auto") and not pre_vf:
        from pipeline.captions_ass import burn_ass, libass_available
        if engine == "libass" or libass_available():
            res = burn_ass(clip_path, words, clip_start, style=st,
                           out_path=out_path, canvas=canvas)
            if res:
                return res

    if canvas:
        w, h = canvas
    else:
        info = ffprobe_info(clip_path)
        w, h = info["width"], info["height"]
    chunks = [c for c in build_caption_segments(words, clip_start) if c["text"]]
    if not chunks:
        return clip_path

    animated = bool(karaoke and st.animation and st.animation != "none")
    cy = h * st.caption_y_ratio  # caption-block center for scale-about-center
    # Band-crop the non-animated path: render captions only onto the thin band
    # they occupy and overlay it at y=band_top — far less Pillow + ffmpeg pixel
    # work than a full-frame PNG per word. The animated path keeps full-frame
    # PNGs (its _anim_variant scales the whole canvas about cy, which needs the
    # full-frame slack), so band_top=0/png_h=None preserves it byte-for-byte.
    band_top, png_h = (0, None) if animated else _caption_band(chunks, w, h, st)

    # Build (png_path, start, end) overlay events.
    events: list[tuple[str, float, float]] = []
    n = 0
    for ci, c in enumerate(chunks):
        line_words = [wd["word"] for wd in c["words"]]
        if karaoke:
            for wi, wd in enumerate(c["words"]):
                p = str(config.CACHE_DIR / f"{src.stem}_k{n:04d}.png")
                _render_png(line_words, w, h, p, highlight=wi, style=st,
                            band_top=band_top, png_h=png_h)
                ws, we = wd["start"], wd["end"]
                if animated and we - ws > 0.02:
                    # Split the word window: a short K-step entrance, then the
                    # settled PNG holds the remainder. Bounded K — no per-frame
                    # explosion (ANIM_STEPS sub-PNGs per word, capped).
                    dur = min(ANIM_DURATION, (we - ws) * 0.6)
                    frames = _ease_steps(st.animation, _adaptive_steps(dur))
                    step_dt = dur / len(frames)
                    # Slide offset is a fraction of one caption line's height.
                    block_h = int(st.font_size * 1.18)
                    for si, (sc, dyr, al) in enumerate(frames[:-1]):
                        ap = str(config.CACHE_DIR / f"{src.stem}_k{n:04d}a{si}.png")
                        _anim_variant(p, ap, w, h, cy, sc, dyr * block_h, al)
                        fs = ws + si * step_dt
                        fe = ws + (si + 1) * step_dt
                        events.append((ap, fs, fe))
                    events.append((p, ws + (len(frames) - 1) * step_dt, we))
                else:
                    events.append((p, ws, we))
                n += 1
        else:
            p = str(config.CACHE_DIR / f"{src.stem}_c{ci:03d}.png")
            _render_png(line_words, w, h, p, highlight=-1, style=st,
                        band_top=band_top, png_h=png_h)
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
            f"[{label}][{i + 1}:v]overlay=0:{band_top}:enable='between(t,{s:.3f},{e:.3f})'[{nxt}]"
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


_EMPHASIS_SYSTEM = """You style short-form video captions. Given a clip's
word-timestamped transcript, pick the few highest-impact words to EMPHASIZE
(color them) and, when asked, map a small number of concrete nouns/concepts to a
single fitting emoji.

Rules:
- emphasis: at most {max_emph} words. Choose punchy/important words (numbers,
  power verbs, surprising nouns) — NOT filler/stopwords. Return the words exactly
  as they appear (lowercase, no punctuation).
- emoji: at most {max_emoji} entries, only when the word clearly evokes one
  obvious emoji (e.g. money->💰, fire->🔥, time->⏰). Skip if unsure.
Return ONLY JSON:
{{
  "emphasis": ["word", ...],
  "emoji": {{"word": "🔥", ...}}
}}
"""


def _plan_emphasis(words: list[dict], clip_start: float = 0.0,
                   want_emphasis: bool = True, want_emoji: bool = False,
                   max_emph: int = 6, max_emoji: int = 4
                   ) -> "tuple[list[str], dict]":
    """LLM keyword-emphasis + auto-emoji pass over a clip's words.

    Mirrors editplan.plan_clip_edits: reuses config.llm_settings / BYOK,
    json_response_format and extract_json. Returns (emphasis_keywords,
    emoji_map). Degrades to ([], {}) with NO LLM key or on any error, so an
    offline render simply burns plain karaoke.
    """
    if not (want_emphasis or want_emoji) or not words:
        return [], {}
    try:
        api_key, base_url, model = config.llm_settings()
    except RuntimeError:
        return [], {}

    from openai import OpenAI

    client = (OpenAI(api_key=api_key, base_url=base_url)
              if base_url else OpenAI(api_key=api_key))
    text = " ".join(w["word"] for w in words).strip()
    system = _EMPHASIS_SYSTEM.format(
        max_emph=max_emph if want_emphasis else 0,
        max_emoji=max_emoji if want_emoji else 0)
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"Transcript:\n{text}"},
            ],
            temperature=0.2,
            **config.json_response_format(base_url),
        )
        data = config.extract_json(resp.choices[0].message.content)
    except Exception:
        return [], {}

    emphasis: list[str] = []
    if want_emphasis:
        for w in data.get("emphasis", []) or []:
            if isinstance(w, str) and w.strip():
                emphasis.append(_norm(w))
        emphasis = list(dict.fromkeys(emphasis))[:max_emph]

    emoji_map: dict = {}
    if want_emoji:
        raw = data.get("emoji", {}) or {}
        if isinstance(raw, dict):
            for k, v in list(raw.items())[:max_emoji]:
                if isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip():
                    emoji_map[_norm(k)] = v.strip()
    return emphasis, emoji_map
