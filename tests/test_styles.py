"""Caption-template gallery: the curated STYLES library must stay well-formed
(every entry carries subtitle/pacing/audio blocks), the new forward-compatible
caption-engine keys must round-trip through subtitle_params, the SubStyle
defaults must keep producing an unchanged caption PNG, and list_styles must
surface a description for every style. If any of these regress, the gallery /
apply_style param surface breaks for the upcoming caption engine."""

from pathlib import Path

from chat.tools import list_styles
from pipeline import subtitle as sub
from pipeline.styles import (MEME_FONTS, load_styles, look_params,
                             resolve_font, subtitle_params)


def test_gallery_has_curated_entries():
    styles = load_styles()
    # 15 built-ins + at least the seeded user JSON example.
    assert len(styles) >= 14
    for name, sty in styles.items():
        assert "subtitle" in sty, f"{name} missing subtitle block"
        assert "pacing" in sty, f"{name} missing pacing block"
        assert "audio" in sty, f"{name} missing audio block"


def test_subtitle_params_roundtrips_new_keys():
    # Keys present -> forwarded.
    sty = {"subtitle": {"animation": "pop", "pill": "#000000",
                        "emphasis": "llm", "auto_emoji": True}}
    p = subtitle_params(sty)
    assert p["animation"] == "pop"
    assert p["pill"] == "#000000"
    assert p["emphasis"] == "llm"
    assert p["auto_emoji"] is True

    # Keys absent -> omitted entirely (renderer falls back to SubStyle defaults).
    p2 = subtitle_params({"subtitle": {"scale": 1.0}})
    for k in ("animation", "pill", "emphasis", "auto_emoji"):
        assert k not in p2


def test_substyle_defaults_are_backward_compatible():
    st = sub.SubStyle()
    assert st.animation == "none"
    assert st.pill is None
    assert st.emphasis_keywords is None
    assert st.auto_emoji is False


def test_render_png_unchanged_with_default_style(tmp_path):
    out = tmp_path / "cap.png"
    sub._render_png(["hello", "world"], 1080, 1920, str(out), highlight=0)
    assert out.exists()
    from PIL import Image
    with Image.open(out) as im:
        assert im.size == (1080, 1920)
        assert im.mode == "RGBA"


def test_list_styles_returns_descriptions_for_every_style():
    res = list_styles(None)
    assert res["ok"] is True
    gallery = res["styles"]
    builtin = set(load_styles())
    assert builtin <= set(gallery)
    for name, info in gallery.items():
        assert info["label"], f"{name} missing label"
        assert info["description"], f"{name} missing description"
        assert "caption" in info


def test_seeded_example_json_exists():
    # The user-extensible taste layer keeps working alongside the built-ins.
    assert (Path(__file__).resolve().parents[1]
            / "assets" / "styles" / "boran_v1.json").exists()


# ── Meme styles + look + bundled fonts ────────────────────────────────────

def test_meme_styles_present_and_use_bundled_font():
    styles = load_styles()
    root = Path(__file__).resolve().parents[1]
    for name in ("meme_impact", "meme_caption", "deep_fried", "reaction_zoom"):
        assert name in styles, f"missing meme style {name}"
        font = styles[name]["subtitle"]["font"]
        # Meme styles must reference a BUNDLED font (portable to the server),
        # never a proprietary system path like /System/Library/...Impact.ttf.
        assert "assets/fonts" in font.replace("\\", "/")
        assert Path(font).exists(), f"{name} font not bundled: {font}"
    assert (root / "assets" / "fonts" / "Anton-Regular.ttf").exists()


def test_bundled_fonts_load_as_truetype():
    from PIL import ImageFont
    for fname in MEME_FONTS.values():
        p = Path(__file__).resolve().parents[1] / "assets" / "fonts" / fname
        assert p.exists(), f"bundled font missing: {fname}"
        # Must be a real TrueType (not the PIL bitmap default).
        ImageFont.truetype(str(p), 48)


def test_resolve_font_maps_keys_and_passes_paths():
    impact = resolve_font("impact")
    assert impact.endswith("Anton-Regular.ttf")
    assert Path(impact).exists()
    # Unknown key / explicit path is returned unchanged.
    assert resolve_font("/abs/path/Foo.ttf") == "/abs/path/Foo.ttf"
    assert resolve_font("") == ""


def test_look_params_only_for_styles_with_a_look():
    styles = load_styles()
    # deep_fried declares a grade; legacy styles do not.
    df = look_params(styles["deep_fried"])
    assert df and df["look"] == "deepfried" and 0.1 <= df["strength"] <= 1.0
    assert look_params(styles["hormozi"]) is None
    assert look_params({}) is None


def test_deepfried_and_vivid_looks_registered():
    from pipeline.colorfx import LOOKS
    assert "deepfried" in LOOKS
    assert "vivid" in LOOKS
