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

SFX_DENSITY_CAP = {"off": 0, "low": 1, "medium": 2, "high": 3}

STYLES: dict[str, dict] = {
    "hormozi": {
        "label": "Hormozi — bold yellow karaoke, tight pacing, punchy zooms",
        "subtitle": {
            "scale": 1.2, "y_ratio": 0.55, "karaoke": True,
            "text_color": "#ffffff", "highlight_color": "#ffd60a",
            "font": f"{_FONT_DIR}/Arial Black.ttf",
            "stroke": 9, "hilite_pop": 1.18, "uppercase": True,
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
        "subtitle": {
            "scale": 1.3, "y_ratio": 0.5, "karaoke": True,
            "text_color": "#ffffff", "highlight_color": "#39e75f",
            "font": f"{_FONT_DIR}/Impact.ttf",
            "stroke": 10, "hilite_pop": 1.22, "uppercase": True,
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
        "subtitle": {
            "scale": 0.9, "y_ratio": 0.74, "karaoke": False,
            "text_color": "#ffffff", "highlight_color": "#ffd60a",
            "font": f"{_FONT_DIR}/Arial Bold.ttf",
            "stroke": 6, "hilite_pop": 1.0, "uppercase": False,
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
        "subtitle": {
            "scale": 1.1, "y_ratio": 0.62, "karaoke": True,
            "text_color": "#fdfdfd", "highlight_color": "#ff5c8a",
            "font": f"{_FONT_DIR}/Arial Rounded Bold.ttf",
            "stroke": 8, "hilite_pop": 1.16, "uppercase": True,
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
