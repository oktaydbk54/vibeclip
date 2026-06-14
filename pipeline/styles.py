"""V2.1 — Named editing styles ("Hormozi tarzı yap").

A style is a declarative taste bundle that maps onto the chat session's stage
params: subtitle look, pacing (jumpcut + zoom planning knobs), and audio
(music mood/volume, sfx density, fade). Built-ins below; users can drop their
own JSON files into assets/styles/<name>.json (same schema) and they merge in.

Consumed by chat/tools.apply_style, which turns a style into ONE batched
set_stages replay.
"""

from __future__ import annotations

import json

from pipeline import config

_FONT_DIR = "/System/Library/Fonts/Supplemental"

# Bundled, OFL-licensed display faces (assets/fonts/) — portable across the dev
# Mac and the Linux/Docker server, unlike the proprietary system fonts the
# legacy styles below reference (real Impact/Arial Black are NOT free; those
# paths degrade gracefully via subtitle._resolve_font). Meme styles and the
# add_meme_text tool reference these by a stable LOGICAL key instead of a path.
_BUNDLED_FONT_DIR = config.ROOT / "assets" / "fonts"
MEME_FONTS = {
    "impact": "Anton-Regular.ttf",       # free Impact-alike — the meme face
    "block": "ArchivoBlack-Regular.ttf",  # heavy Arial-Black-alike
    "condensed": "BebasNeue-Regular.ttf",  # tall all-caps headline
}
_ANTON = str(_BUNDLED_FONT_DIR / MEME_FONTS["impact"])


def resolve_font(key: str) -> str:
    """Map a logical meme-font key to an absolute bundled-font path.

    A known key ('impact'/'block'/'condensed') resolves to the OFL font under
    assets/fonts/; anything else (e.g. an explicit path) is returned unchanged.
    """
    fname = MEME_FONTS.get((key or "").strip().lower())
    return str(_BUNDLED_FONT_DIR / fname) if fname else key


SFX_DENSITY_CAP = {"off": 0, "low": 1, "medium": 2, "high": 3}

# Caption-template library — a curated gallery of viral short-form looks.
#
# Each entry is PURE CONFIG JSON in the existing schema (subtitle/pacing/audio
# blocks) plus an optional human-readable "description". Templates carry NO
# per-template code. Newer subtitle keys ('animation', 'pill', 'emphasis',
# 'auto_emoji') describe the caption look for the forthcoming caption engine;
# today's renderer ignores unknown keys harmlessly (see subtitle_params /
# SubStyle safe defaults), so they are fully backward-compatible.
#
#   animation : "none" | "pop" | "slide" | "spring"  — per-word entrance
#   pill       : bool | "#rrggbb"  — rounded pill/box behind the active word
#   emphasis   : "llm" | "none"     — let the LLM pick words to emphasize
#   auto_emoji : bool               — auto-place a contextual emoji per caption
STYLES: dict[str, dict] = {
    "hormozi": {
        "label": "Hormozi — bold yellow karaoke, tight pacing, punchy zooms",
        "description": "Center-stacked Arial Black in white with a yellow active "
                       "word that pops; aggressive jumpcuts and frequent zooms.",
        "subtitle": {
            "scale": 1.2, "y_ratio": 0.55, "karaoke": True,
            "text_color": "#ffffff", "highlight_color": "#ffd60a",
            "font": f"{_FONT_DIR}/Arial Black.ttf",
            "stroke": 9, "hilite_pop": 1.18, "uppercase": True,
            "animation": "pop", "pill": False, "emphasis": "none",
            "auto_emoji": False,
        },
        "pacing": {
            "max_pause": 0.35, "remove_fillers": True,
            "zoom_density": 0.25, "zoom_strength": 1.22,
        },
        "audio": {
            "music_mood": "energetic", "music_volume": 0.16,
            "sfx_density": "high", "fade": 0.2,
        },
    },
    "mrbeast": {
        "label": "MrBeast — huge green-pop captions, max energy, dense sfx",
        "description": "Oversized Impact captions with a neon-green active word, "
                       "maximum energy, dense sound effects and big zooms.",
        "subtitle": {
            "scale": 1.3, "y_ratio": 0.5, "karaoke": True,
            "text_color": "#ffffff", "highlight_color": "#39e75f",
            "font": f"{_FONT_DIR}/Impact.ttf",
            "stroke": 10, "hilite_pop": 1.22, "uppercase": True,
            "animation": "pop", "pill": False, "emphasis": "none",
            "auto_emoji": True,
        },
        "pacing": {
            "max_pause": 0.3, "remove_fillers": True,
            "zoom_density": 0.3, "zoom_strength": 1.26,
        },
        "audio": {
            "music_mood": "energetic", "music_volume": 0.2,
            "sfx_density": "high", "fade": 0.15,
        },
    },
    "podcast_minimal": {
        "label": "Podcast minimal — clean lower captions, calm bed, no zooms",
        "description": "Understated white lower-third captions in mixed case, "
                       "calm music bed and no zooms — interview / talking-head.",
        "subtitle": {
            "scale": 0.9, "y_ratio": 0.74, "karaoke": False,
            "text_color": "#ffffff", "highlight_color": "#ffd60a",
            "font": f"{_FONT_DIR}/Arial Bold.ttf",
            "stroke": 6, "hilite_pop": 1.0, "uppercase": False,
            "animation": "none", "pill": False, "emphasis": "none",
            "auto_emoji": False,
        },
        "pacing": {
            "max_pause": 0.6, "remove_fillers": True,
            "zoom_density": 0.0, "zoom_strength": 1.1,
        },
        "audio": {
            "music_mood": "calm", "music_volume": 0.1,
            "sfx_density": "off", "fade": 0.5,
        },
    },
    "kinetic": {
        "label": "Kinetic — rounded pop captions, upbeat, frequent motion",
        "description": "Rounded uppercase captions with a hot-pink active word "
                       "that springs in; upbeat, motion-heavy energy.",
        "subtitle": {
            "scale": 1.1, "y_ratio": 0.62, "karaoke": True,
            "text_color": "#fdfdfd", "highlight_color": "#ff5c8a",
            "font": f"{_FONT_DIR}/Arial Rounded Bold.ttf",
            "stroke": 8, "hilite_pop": 1.16, "uppercase": True,
            "animation": "spring", "pill": False, "emphasis": "none",
            "auto_emoji": False,
        },
        "pacing": {
            "max_pause": 0.4, "remove_fillers": True,
            "zoom_density": 0.28, "zoom_strength": 1.2,
        },
        "audio": {
            "music_mood": "neutral", "music_volume": 0.15,
            "sfx_density": "medium", "fade": 0.25,
        },
    },
    "beasty": {
        "label": "Beasty — chunky white captions with a red pill highlight",
        "description": "Thick Arial Black in white with a rounded red pill behind "
                       "the spoken word; loud, hype, challenge-video energy.",
        "subtitle": {
            "scale": 1.28, "y_ratio": 0.52, "karaoke": True,
            "text_color": "#ffffff", "highlight_color": "#ffffff",
            "font": f"{_FONT_DIR}/Arial Black.ttf",
            "stroke": 9, "hilite_pop": 1.0, "uppercase": True,
            "animation": "pop", "pill": "#ff2d2d", "emphasis": "none",
            "auto_emoji": True,
        },
        "pacing": {
            "max_pause": 0.32, "remove_fillers": True,
            "zoom_density": 0.3, "zoom_strength": 1.24,
        },
        "audio": {
            "music_mood": "energetic", "music_volume": 0.18,
            "sfx_density": "high", "fade": 0.15,
        },
    },
    "clean_white": {
        "label": "Clean white — simple bold white captions, no frills",
        "description": "Plain bold white captions, centered, no color highlight "
                       "or animation — neutral, brand-safe, works anywhere.",
        "subtitle": {
            "scale": 1.0, "y_ratio": 0.68, "karaoke": False,
            "text_color": "#ffffff", "highlight_color": "#ffffff",
            "font": f"{_FONT_DIR}/Arial Bold.ttf",
            "stroke": 7, "hilite_pop": 1.0, "uppercase": False,
            "animation": "none", "pill": False, "emphasis": "none",
            "auto_emoji": False,
        },
        "pacing": {
            "max_pause": 0.5, "remove_fillers": True,
            "zoom_density": 0.1, "zoom_strength": 1.12,
        },
        "audio": {
            "music_mood": "neutral", "music_volume": 0.12,
            "sfx_density": "low", "fade": 0.3,
        },
    },
    "tiktok_bold": {
        "label": "TikTok bold — uppercase white-on-black pill captions",
        "description": "Uppercase white words inside a solid black pill, mid-frame, "
                       "with a pop entrance — the classic TikTok auto-caption look.",
        "subtitle": {
            "scale": 1.08, "y_ratio": 0.6, "karaoke": True,
            "text_color": "#ffffff", "highlight_color": "#ffe14d",
            "font": f"{_FONT_DIR}/Arial Black.ttf",
            "stroke": 4, "hilite_pop": 1.1, "uppercase": True,
            "animation": "pop", "pill": "#000000", "emphasis": "none",
            "auto_emoji": False,
        },
        "pacing": {
            "max_pause": 0.4, "remove_fillers": True,
            "zoom_density": 0.2, "zoom_strength": 1.18,
        },
        "audio": {
            "music_mood": "neutral", "music_volume": 0.15,
            "sfx_density": "medium", "fade": 0.25,
        },
    },
    "neon_pop": {
        "label": "Neon pop — cyan captions with a magenta glow highlight",
        "description": "High-contrast cyan captions with a magenta active word that "
                       "springs in; vivid, late-night, hyped aesthetic.",
        "subtitle": {
            "scale": 1.15, "y_ratio": 0.58, "karaoke": True,
            "text_color": "#27e3ff", "highlight_color": "#ff2bd6",
            "font": f"{_FONT_DIR}/Arial Rounded Bold.ttf",
            "stroke": 8, "hilite_pop": 1.2, "uppercase": True,
            "animation": "spring", "pill": False, "emphasis": "none",
            "auto_emoji": False,
        },
        "pacing": {
            "max_pause": 0.38, "remove_fillers": True,
            "zoom_density": 0.26, "zoom_strength": 1.22,
        },
        "audio": {
            "music_mood": "energetic", "music_volume": 0.17,
            "sfx_density": "high", "fade": 0.2,
        },
    },
    "subtle_lower": {
        "label": "Subtle lower-third — small mixed-case captions, low key",
        "description": "Small mixed-case captions parked near the bottom safe area, "
                       "soft yellow accent, minimal motion — vlog / b-roll.",
        "subtitle": {
            "scale": 0.85, "y_ratio": 0.76, "karaoke": True,
            "text_color": "#ffffff", "highlight_color": "#ffd60a",
            "font": f"{_FONT_DIR}/Arial Bold.ttf",
            "stroke": 5, "hilite_pop": 1.04, "uppercase": False,
            "animation": "none", "pill": False, "emphasis": "none",
            "auto_emoji": False,
        },
        "pacing": {
            "max_pause": 0.55, "remove_fillers": True,
            "zoom_density": 0.05, "zoom_strength": 1.1,
        },
        "audio": {
            "music_mood": "calm", "music_volume": 0.1,
            "sfx_density": "low", "fade": 0.4,
        },
    },
    "bold_center": {
        "label": "Bold center — large centered Impact captions, orange pop",
        "description": "Large centered Impact captions with an orange active word "
                       "that pops; punchy explainer / hot-take look.",
        "subtitle": {
            "scale": 1.22, "y_ratio": 0.5, "karaoke": True,
            "text_color": "#ffffff", "highlight_color": "#ff8c1a",
            "font": f"{_FONT_DIR}/Impact.ttf",
            "stroke": 9, "hilite_pop": 1.18, "uppercase": True,
            "animation": "pop", "pill": False, "emphasis": "llm",
            "auto_emoji": False,
        },
        "pacing": {
            "max_pause": 0.36, "remove_fillers": True,
            "zoom_density": 0.24, "zoom_strength": 1.2,
        },
        "audio": {
            "music_mood": "energetic", "music_volume": 0.16,
            "sfx_density": "medium", "fade": 0.2,
        },
    },
    "gradient_word": {
        "label": "Gradient word — white captions, gold spoken-word emphasis",
        "description": "Clean white captions where the spoken word turns gold and "
                       "pops; premium, polished talking-head feel.",
        "subtitle": {
            "scale": 1.1, "y_ratio": 0.6, "karaoke": True,
            "text_color": "#f6f6f6", "highlight_color": "#ffc24b",
            "font": f"{_FONT_DIR}/Arial Black.ttf",
            "stroke": 7, "hilite_pop": 1.16, "uppercase": True,
            "animation": "pop", "pill": False, "emphasis": "llm",
            "auto_emoji": False,
        },
        "pacing": {
            "max_pause": 0.42, "remove_fillers": True,
            "zoom_density": 0.2, "zoom_strength": 1.18,
        },
        "audio": {
            "music_mood": "neutral", "music_volume": 0.14,
            "sfx_density": "medium", "fade": 0.25,
        },
    },
    "minimal_serif": {
        "label": "Minimal serif — elegant Georgia lower-thirds, mixed case",
        "description": "Elegant mixed-case Georgia captions, thin stroke, low in "
                       "frame, no animation — documentary / editorial mood.",
        "subtitle": {
            "scale": 0.92, "y_ratio": 0.74, "karaoke": False,
            "text_color": "#ffffff", "highlight_color": "#e8d9a0",
            "font": f"{_FONT_DIR}/Georgia Bold.ttf",
            "stroke": 4, "hilite_pop": 1.0, "uppercase": False,
            "animation": "none", "pill": False, "emphasis": "none",
            "auto_emoji": False,
        },
        "pacing": {
            "max_pause": 0.6, "remove_fillers": True,
            "zoom_density": 0.0, "zoom_strength": 1.08,
        },
        "audio": {
            "music_mood": "calm", "music_volume": 0.09,
            "sfx_density": "off", "fade": 0.5,
        },
    },
    "comic": {
        "label": "Comic — playful Comic Sans captions, blue pop",
        "description": "Playful uppercase Comic Sans captions with a blue active "
                       "word that springs in; fun, casual, meme-friendly.",
        "subtitle": {
            "scale": 1.08, "y_ratio": 0.6, "karaoke": True,
            "text_color": "#ffffff", "highlight_color": "#2f8bff",
            "font": f"{_FONT_DIR}/Comic Sans MS Bold.ttf",
            "stroke": 7, "hilite_pop": 1.16, "uppercase": True,
            "animation": "spring", "pill": False, "emphasis": "none",
            "auto_emoji": True,
        },
        "pacing": {
            "max_pause": 0.42, "remove_fillers": True,
            "zoom_density": 0.24, "zoom_strength": 1.2,
        },
        "audio": {
            "music_mood": "neutral", "music_volume": 0.15,
            "sfx_density": "high", "fade": 0.2,
        },
    },
    "news_ticker": {
        "label": "News ticker — monospace captions in a black bar, low frame",
        "description": "Monospace Courier captions in a solid black bar near the "
                       "bottom; broadcast / breaking-news headline style.",
        "subtitle": {
            "scale": 0.9, "y_ratio": 0.78, "karaoke": False,
            "text_color": "#ffffff", "highlight_color": "#ff3b3b",
            "font": f"{_FONT_DIR}/Courier New Bold.ttf",
            "stroke": 3, "hilite_pop": 1.0, "uppercase": True,
            "animation": "slide", "pill": "#000000", "emphasis": "none",
            "auto_emoji": False,
        },
        "pacing": {
            "max_pause": 0.55, "remove_fillers": True,
            "zoom_density": 0.0, "zoom_strength": 1.06,
        },
        "audio": {
            "music_mood": "neutral", "music_volume": 0.08,
            "sfx_density": "off", "fade": 0.3,
        },
    },
    "story_caption": {
        "label": "Story caption — soft white centered captions, gentle pop",
        "description": "Soft white centered captions in mixed case with a gentle "
                       "pop and warm accent; narration / storytelling pacing.",
        "subtitle": {
            "scale": 1.0, "y_ratio": 0.64, "karaoke": True,
            "text_color": "#fafafa", "highlight_color": "#ffb86b",
            "font": f"{_FONT_DIR}/Arial Bold.ttf",
            "stroke": 6, "hilite_pop": 1.1, "uppercase": False,
            "animation": "pop", "pill": False, "emphasis": "llm",
            "auto_emoji": False,
        },
        "pacing": {
            "max_pause": 0.5, "remove_fillers": True,
            "zoom_density": 0.12, "zoom_strength": 1.14,
        },
        "audio": {
            "music_mood": "calm", "music_volume": 0.12,
            "sfx_density": "low", "fade": 0.35,
        },
    },
    # ── Meme / Instagram-style looks ──────────────────────────────────────
    # These reference the BUNDLED Anton face (free Impact-alike) so they render
    # the same on the server as on the dev Mac. They are content-agnostic looks
    # (caption styling + pacing + color + sfx); the actual top/bottom meme
    # headline text is content, added per-clip with the add_meme_text tool.
    "meme_impact": {
        "label": "Meme Impact — top white Impact-caps with heavy black stroke",
        "description": "Classic Instagram/Reddit meme captions: bold uppercase "
                       "Anton (free Impact) high in frame, white with a thick "
                       "black outline, punchy zooms and dense sfx.",
        "subtitle": {
            "scale": 1.2, "y_ratio": 0.18, "karaoke": True,
            "text_color": "#ffffff", "highlight_color": "#ffffff",
            "font": _ANTON,
            "stroke": 11, "hilite_pop": 1.12, "uppercase": True,
            "animation": "pop", "pill": False, "emphasis": "none",
            "auto_emoji": True,
        },
        "pacing": {
            "max_pause": 0.32, "remove_fillers": True,
            "zoom_density": 0.28, "zoom_strength": 1.24,
        },
        "audio": {
            "music_mood": "energetic", "music_volume": 0.16,
            "sfx_density": "high", "fade": 0.18,
        },
    },
    "meme_caption": {
        "label": "Meme caption — black pill caption bar, IG-reels look",
        "description": "Uppercase Anton words inside a solid black pill parked "
                       "just below center — the ubiquitous Instagram Reels / "
                       "TikTok auto-caption meme bar.",
        "subtitle": {
            "scale": 1.05, "y_ratio": 0.6, "karaoke": True,
            "text_color": "#ffffff", "highlight_color": "#ffe14d",
            "font": _ANTON,
            "stroke": 4, "hilite_pop": 1.1, "uppercase": True,
            "animation": "pop", "pill": "#000000", "emphasis": "none",
            "auto_emoji": True,
        },
        "pacing": {
            "max_pause": 0.38, "remove_fillers": True,
            "zoom_density": 0.2, "zoom_strength": 1.18,
        },
        "audio": {
            "music_mood": "neutral", "music_volume": 0.15,
            "sfx_density": "medium", "fade": 0.2,
        },
    },
    "deep_fried": {
        "label": "Deep fried — blown-out saturation, loud Impact, max chaos",
        "description": "The 'deep-fried meme' aesthetic: crushed high-saturation "
                       "color grade, oversized Anton caps and wall-to-wall sound "
                       "effects.",
        "subtitle": {
            "scale": 1.32, "y_ratio": 0.5, "karaoke": True,
            "text_color": "#ffffff", "highlight_color": "#ff2d2d",
            "font": _ANTON,
            "stroke": 12, "hilite_pop": 1.2, "uppercase": True,
            "animation": "pop", "pill": False, "emphasis": "none",
            "auto_emoji": True,
        },
        "pacing": {
            "max_pause": 0.3, "remove_fillers": True,
            "zoom_density": 0.32, "zoom_strength": 1.28,
        },
        "look": {"look": "deepfried", "strength": 0.8},
        "audio": {
            "music_mood": "energetic", "music_volume": 0.2,
            "sfx_density": "high", "fade": 0.12,
        },
    },
    "reaction_zoom": {
        "label": "Reaction zoom — punch-in heavy, vivid, reaction-video energy",
        "description": "Aggressive frequent punch-in zooms and a vivid grade with "
                       "bold Anton caps — the reaction-channel / commentary look "
                       "built to pair with reaction overlays and flash hits.",
        "subtitle": {
            "scale": 1.22, "y_ratio": 0.55, "karaoke": True,
            "text_color": "#ffffff", "highlight_color": "#39e75f",
            "font": _ANTON,
            "stroke": 10, "hilite_pop": 1.18, "uppercase": True,
            "animation": "pop", "pill": False, "emphasis": "none",
            "auto_emoji": False,
        },
        "pacing": {
            "max_pause": 0.34, "remove_fillers": True,
            "zoom_density": 0.4, "zoom_strength": 1.3,
        },
        "look": {"look": "vivid", "strength": 0.5},
        "audio": {
            "music_mood": "energetic", "music_volume": 0.17,
            "sfx_density": "high", "fade": 0.15,
        },
    },
}


def load_styles() -> dict[str, dict]:
    """Built-ins merged with user JSON files from assets/styles/*.json."""
    styles = {k: json.loads(json.dumps(v)) for k, v in STYLES.items()}
    user_dir = config.ROOT / "assets" / "styles"
    if user_dir.exists():
        for f in sorted(user_dir.glob("*.json")):
            try:
                styles[f.stem] = json.loads(f.read_text())
            except (json.JSONDecodeError, OSError):
                continue
    return styles


def get_style(name: str) -> dict | None:
    return load_styles().get(name.strip().lower().replace(" ", "_"))


def subtitle_params(style: dict) -> dict:
    """Style's subtitle block -> 'subtitles' stage params."""
    s = style.get("subtitle", {})
    p = {
        "karaoke": s.get("karaoke", True),
        "scale": s.get("scale", 1.0),
        "y_ratio": s.get("y_ratio", 0.68),
    }
    for src_key, dst_key in (
        ("text_color", "text_color"), ("highlight_color", "highlight_color"),
        ("font", "font"), ("stroke", "stroke"),
        ("hilite_pop", "hilite_pop"), ("uppercase", "uppercase"),
        # Forward-compatible caption-engine knobs (ignored by today's renderer):
        ("animation", "animation"), ("pill", "pill"),
        ("emphasis", "emphasis"), ("auto_emoji", "auto_emoji"),
    ):
        if src_key in s:
            p[dst_key] = s[src_key]
    return p


def jumpcut_params(style: dict) -> dict:
    pc = style.get("pacing", {})
    return {
        "max_pause": pc.get("max_pause", 0.5),
        "remove_fillers": pc.get("remove_fillers", False),
    }


def look_params(style: dict) -> dict | None:
    """Style's optional color-grade block -> 'lut' stage params.

    Returns None when the style declares no `look` (the common case), so
    apply_style leaves the lut stage untouched for the legacy caption styles.
    """
    lk = style.get("look")
    if not lk or not lk.get("look"):
        return None
    return {"look": str(lk["look"]),
            "strength": max(0.1, min(1.0, float(lk.get("strength", 0.5))))}
