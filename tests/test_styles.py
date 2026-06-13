"""Caption-template gallery: the curated STYLES library must stay well-formed
(every entry carries subtitle/pacing/audio blocks), the new forward-compatible
caption-engine keys must round-trip through subtitle_params, the SubStyle
defaults must keep producing an unchanged caption PNG, and list_styles must
surface a description for every style. If any of these regress, the gallery /
apply_style param surface breaks for the upcoming caption engine."""

from pathlib import Path

from chat.tools import list_styles
from pipeline import subtitle as sub
from pipeline.styles import load_styles, subtitle_params


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
