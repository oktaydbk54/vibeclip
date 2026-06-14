"""Meme-text rendering: the brand stage must draw a top/bottom meme headline
to a transparent full-frame PNG — an opaque white BAR with black text in bar
mode, and stroked white text over a transparent frame in Impact mode. These are
pixel-level guards so the IG-meme look can't silently regress to the ugly PIL
default or lose its bar."""

from PIL import Image

from pipeline.brand import MEME_FONT, _render_meme_text_png


def _open(path):
    return Image.open(path).convert("RGBA")


def test_bundled_meme_font_exists():
    from pathlib import Path
    assert Path(MEME_FONT).exists() and MEME_FONT.endswith("Anton-Regular.ttf")


def test_bar_mode_paints_opaque_white_band(tmp_path):
    out = tmp_path / "bar.png"
    _render_meme_text_png("WHEN THE CODE FINALLY COMPILES", 1080, 1920,
                          str(out), position="top", bar=True)
    im = _open(out)
    assert im.size == (1080, 1920)
    px = im.load()
    # Somewhere in the top region there must be a fully-opaque white bar pixel...
    band = [px[x, y] for x in range(0, 1080, 60) for y in range(20, 240, 20)]
    assert any(a == 255 and (r, g, b) == (255, 255, 255)
               for (r, g, b, a) in band), "no opaque white bar drawn"
    # ...and black text pixels inside that band.
    assert any(a == 255 and r < 40 and g < 40 and b < 40
               for (r, g, b, a) in band), "no black meme text drawn"


def test_impact_mode_is_transparent_with_stroked_text(tmp_path):
    out = tmp_path / "impact.png"
    _render_meme_text_png("TOP TEXT", 1080, 1920, str(out),
                          position="top", bar=False)
    im = _open(out)
    px = im.load()
    # Corner stays fully transparent (no bar) — text floats over the video.
    assert px[5, 5][3] == 0
    scan = [px[x, y] for x in range(0, 1080, 30) for y in range(20, 300, 20)]
    assert any(a == 255 and r > 230 and g > 230 and b > 230
               for (r, g, b, a) in scan), "no white impact text"
    assert any(a == 255 and r < 40 and g < 40 and b < 40
               for (r, g, b, a) in scan), "no black outline stroke"


def test_bottom_position_keeps_clear_of_safe_zone(tmp_path):
    out = tmp_path / "bottom.png"
    _render_meme_text_png("BOTTOM MEME LINE", 1080, 1920, str(out),
                          position="bottom", bar=True)
    im = _open(out)
    px = im.load()
    # The very bottom strip (platform UI safe zone, last ~18%) must stay clear.
    bottom = [px[x, y] for x in range(0, 1080, 60)
              for y in range(1920 - 100, 1920, 20)]
    assert all(a == 0 for (_, _, _, a) in bottom), "meme text intruded on UI zone"
