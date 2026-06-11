"""V4.3 — Platform safe areas for 9:16 (1080x1920).

Union of TikTok / Reels / Shorts UI zones (research): top ~200px, bottom
~400px, left ~60px, right ~130px. Anything outside gets covered by platform
buttons/captions — the #1 silent failure of auto-editors. All overlay
placement (stickers, reactions, watermark, captions) clamps to this.
Ratios of frame size, so any resolution works.
"""

from __future__ import annotations

SAFE = {"top": 0.11, "bottom": 0.79, "left": 0.06, "right": 0.875}


def clamp_center(x_ratio: float, y_ratio: float,
                 width_ratio: float = 0.25,
                 height_guess: float = 0.08) -> tuple[float, float]:
    """Clamp an overlay CENTER so the element stays inside the safe zone."""
    hx = min(0.4, width_ratio / 2)
    hy = min(0.3, height_guess)
    x = min(max(float(x_ratio), SAFE["left"] + hx), SAFE["right"] - hx)
    y = min(max(float(y_ratio), SAFE["top"] + hy), SAFE["bottom"] - hy)
    return round(x, 3), round(y, 3)
