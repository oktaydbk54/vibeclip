"""Storyboard-first generation (Faz 2.2): script -> scenes -> generated short.

LLM + generation are mocked. Asserts scene planning (LLM + offline fallback),
even caption spreading, graceful degradation without a gen key, and that
assembly generates/captions/concats only the scenes that succeed.
"""

import json

import pytest

from pipeline import broll, config, genmedia, storyboard, subtitle


def _patch_llm(monkeypatch, payload):
    class _Resp:
        def __init__(self, c):
            self.choices = [type("M", (), {"message": type(
                "X", (), {"content": c})()})()]

    class _Client:
        def __init__(self, *a, **k):
            self.chat = type("C", (), {"completions": type(
                "Co", (), {"create": lambda self, **kw: _Resp(
                    json.dumps(payload))})()})()

    import openai
    monkeypatch.setattr(openai, "OpenAI", _Client)
    monkeypatch.setattr(config, "llm_settings",
                        lambda tier="fast", override=None: ("k", None, "m"))


# ------------------------------------------------------------------- planning

def test_plan_storyboard_uses_llm_scenes(monkeypatch):
    _patch_llm(monkeypatch, {"scenes": [
        {"narration": "A lone hiker", "visual": "hiker on a misty ridge"},
        {"narration": "", "visual": "close-up boots on rock"},
        {"narration": "skip me", "visual": ""}]})   # no visual -> dropped
    scenes = storyboard.plan_storyboard("a hiking story", max_scenes=6)
    assert len(scenes) == 2
    assert scenes[0]["visual"] == "hiker on a misty ridge"


def test_plan_storyboard_offline_fallback(monkeypatch):
    # No LLM key -> sentence split, still usable.
    monkeypatch.setattr(config, "llm_settings",
                        lambda tier="fast", override=None: ("", None, "m"))
    scenes = storyboard.plan_storyboard("First scene. Second scene! Third?",
                                        max_scenes=6)
    assert [s["narration"] for s in scenes] == \
        ["First scene.", "Second scene!", "Third?"]
    assert scenes[0]["visual"] == "First scene."


def test_plan_storyboard_empty():
    assert storyboard.plan_storyboard("   ") == []


def test_scene_words_spread_evenly():
    words = storyboard._scene_words("one two three four", 4.0)
    assert [w["word"] for w in words] == ["one", "two", "three", "four"]
    assert words[0]["start"] == 0.0 and words[0]["end"] == 1.0
    assert words[-1]["end"] == 4.0
    assert storyboard._scene_words("", 4.0) == []


# ------------------------------------------------------------------- assembly

def test_assemble_none_without_genmedia(monkeypatch, tmp_path):
    monkeypatch.setattr(genmedia, "available", lambda: False)
    out = storyboard.assemble_storyboard(
        [{"narration": "x", "visual": "y"}], str(tmp_path / "o.mp4"))
    assert out is None


def test_assemble_generates_captions_concats(monkeypatch, tmp_path):
    monkeypatch.setattr(genmedia, "available", lambda: True)
    gen_calls, concat_calls = [], []
    monkeypatch.setattr(genmedia, "generate_video",
                        lambda visual, **kw: gen_calls.append(visual) or "/raw.mp4")
    monkeypatch.setattr(broll, "normalize_media", lambda path, **kw: "/norm.mp4")
    monkeypatch.setattr(subtitle, "burn_subtitles",
                        lambda src, words, **kw: kw["out_path"])

    def fake_concat(paths, out_path):
        concat_calls.append(list(paths))
        return out_path

    monkeypatch.setattr(storyboard, "_concat", fake_concat)

    scenes = [{"narration": "scene one", "visual": "a forest"},
              {"narration": "scene two", "visual": "a river"}]
    res = storyboard.assemble_storyboard(
        scenes, str(tmp_path / "out.mp4"), style="cinematic", seed=5)
    assert res["path"] == str(tmp_path / "out.mp4")
    assert [s["ok"] for s in res["scenes"]] == [True, True]
    # style suffix applied to every visual prompt.
    assert gen_calls == ["a forest, cinematic", "a river, cinematic"]
    # both captioned pieces concatenated.
    assert len(concat_calls[0]) == 2


def test_assemble_none_when_all_generations_fail(monkeypatch, tmp_path):
    monkeypatch.setattr(genmedia, "available", lambda: True)
    monkeypatch.setattr(genmedia, "generate_video", lambda visual, **kw: None)
    res = storyboard.assemble_storyboard(
        [{"narration": "a", "visual": "b"}], str(tmp_path / "o.mp4"))
    assert res is None


def test_assemble_single_scene_skips_concat(monkeypatch, tmp_path):
    monkeypatch.setattr(genmedia, "available", lambda: True)
    monkeypatch.setattr(genmedia, "generate_video", lambda visual, **kw: "/raw.mp4")
    monkeypatch.setattr(broll, "normalize_media", lambda path, **kw: "/norm.mp4")
    monkeypatch.setattr(subtitle, "burn_subtitles",
                        lambda src, words, **kw: kw["out_path"])
    monkeypatch.setattr(storyboard, "_single", lambda src, out: out)
    monkeypatch.setattr(storyboard, "_concat",
                        lambda paths, out: pytest.fail("should not concat one"))
    res = storyboard.assemble_storyboard(
        [{"narration": "only", "visual": "one"}], str(tmp_path / "o.mp4"))
    assert res["path"] == str(tmp_path / "o.mp4")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
