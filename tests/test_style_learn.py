"""Style-learning core: a reference video must distil into a VALID STYLES preset
that round-trips through the existing apply_style param helpers, aggregation must
be median/mode, and the vision pass must degrade gracefully to a complete
measurable-only fingerprint when no vision model is available. All fixture-based
— the local repo sample video is the input; NO network, NO Instagram."""

from pathlib import Path

import pytest

from pipeline import style_learn
from pipeline.styles import jumpcut_params, look_params, subtitle_params

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "long_test.mp4"


def _fp(**over):
    """A complete signal fingerprint with overridable fields."""
    base = dict(style_learn._BASE_SUBTITLE)
    base.update({
        "font_key": "impact", "max_pause": 0.45, "zoom_density": 0.2,
        "zoom_strength": 1.2, "fade": 0.2, "music_volume": 0.15,
        "sfx_density": "medium", "music_mood": "neutral",
        "look_name": None, "look_strength": 0.5,
    })
    base.update(over)
    return base


def test_aggregate_produces_valid_style_schema():
    style = style_learn.aggregate_fingerprints([_fp(), _fp(), _fp()])
    assert set(style) >= {"subtitle", "pacing", "audio"}
    # Must feed the existing helpers without error.
    sp = subtitle_params(style)
    assert "font" in sp and Path(sp["font"]).exists()  # resolved bundled font
    jp = jumpcut_params(style)
    assert 0.0 <= jp["max_pause"] <= 1.0
    assert 0.0 <= style["pacing"]["zoom_density"] <= 0.5


def test_aggregate_is_median_and_mode():
    fps = [_fp(max_pause=0.3, sfx_density="high"),
           _fp(max_pause=0.5, sfx_density="high"),
           _fp(max_pause=0.7, sfx_density="low")]
    style = style_learn.aggregate_fingerprints(fps)
    assert style["pacing"]["max_pause"] == 0.5          # median of .3/.5/.7
    assert style["audio"]["sfx_density"] == "high"      # mode (2 of 3)


def test_look_emitted_only_on_majority():
    none_majority = [_fp(look_name=None), _fp(look_name=None),
                     _fp(look_name="vivid")]
    assert "look" not in style_learn.aggregate_fingerprints(none_majority)
    fried = [_fp(look_name="deepfried"), _fp(look_name="deepfried"),
             _fp(look_name=None)]
    out = style_learn.aggregate_fingerprints(fried)
    assert out["look"]["look"] == "deepfried"
    assert look_params(out) is not None


def test_look_from_color_thresholds():
    assert style_learn._look_from_color(None)[0] is None
    assert style_learn._look_from_color(40)[0] is None
    assert style_learn._look_from_color(100)[0] == "vivid"
    assert style_learn._look_from_color(140)[0] == "deepfried"


def test_apply_vision_validates_and_maps():
    sig = _fp()
    style_learn._apply_vision(sig, {
        "font_feel": "block", "caption_color": "#ff0000",
        "highlight_color": "BAD", "uppercase": False,
        "pill": "#000000", "animation": "spring", "auto_emoji": True,
        "caption_position": "top", "music_mood": "energetic"})
    assert sig["font_key"] == "block"
    assert sig["text_color"] == "#ff0000"
    assert sig["highlight_color"] == _fp()["highlight_color"]  # bad hex ignored
    assert sig["uppercase"] is False
    assert sig["pill"] == "#000000"
    assert sig["animation"] == "spring"
    assert sig["auto_emoji"] is True
    assert sig["y_ratio"] == 0.18          # top
    assert sig["music_mood"] == "energetic"
    # Garbage enum values are ignored, not stored.
    sig2 = _fp()
    style_learn._apply_vision(sig2, {"font_feel": "wingdings",
                                     "animation": "explode"})
    assert sig2["font_key"] == "impact" and sig2["animation"] == "pop"


@pytest.mark.skipif(not SAMPLE.exists(), reason="sample video absent")
def test_analyze_reel_on_local_sample_no_vision():
    sig = style_learn.analyze_reel(str(SAMPLE), use_vision=False)
    # Complete, well-ranged fingerprint without any network/LLM.
    for k in ("max_pause", "zoom_density", "zoom_strength", "sfx_density",
              "music_mood", "font_key"):
        assert k in sig
    assert 0.3 <= sig["max_pause"] <= 0.6
    assert 0.0 <= sig["zoom_density"] <= 0.4
    assert sig["sfx_density"] in ("low", "medium", "high")


@pytest.mark.skipif(not SAMPLE.exists(), reason="sample video absent")
def test_vision_degrades_when_no_key(monkeypatch):
    # No LLM key configured → vision returns {} and analyze_reel still completes.
    def _boom(*a, **k):
        raise RuntimeError("no key")
    monkeypatch.setattr(style_learn.config, "llm_settings", _boom)
    assert style_learn.vision_style_descriptor(["/nope.jpg"], "cap") == {}
    sig = style_learn.analyze_reel(str(SAMPLE), use_vision=True)
    assert "max_pause" in sig and "font_key" in sig    # measurable-only fallback


def test_learned_style_roundtrips_through_writer(tmp_path, monkeypatch):
    # Writing the aggregated style must produce a file load_styles picks up.
    import pipeline.styles as styles
    style = style_learn.aggregate_fingerprints([_fp(look_name="vivid"),
                                                _fp(look_name="vivid")])
    sdir = tmp_path / "assets" / "styles"
    sdir.mkdir(parents=True)
    monkeypatch.setattr(styles.config, "ROOT", tmp_path)
    (sdir / "learned_me.json").write_text(__import__("json").dumps(style))
    loaded = styles.load_styles()
    assert "learned_me" in loaded
    assert styles.get_style("learned_me")["audio"]["sfx_density"] in (
        "low", "medium", "high")
