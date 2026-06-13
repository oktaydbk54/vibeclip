"""find_moment: semantic in-clip moment lookup by description.

Covers the non-LLM keyword fallback, the LLM path (monkeypatched), graceful
degradation on LLM failure, and TOOL_SPECS/REGISTRY parity for the new tool.
"""

import pytest

from chat import tools


def _make_words():
    """A fabricated pre-speed word list: [{start,end,word}]."""
    spec = [
        ("bugün", 0.0, 0.4),
        ("size", 0.4, 0.8),
        ("fiyat", 0.8, 1.3),
        ("politikamızdan", 1.3, 2.2),
        ("bahsedeceğim", 2.2, 3.0),
        ("önce", 4.0, 4.4),
        ("bir", 4.4, 4.6),
        ("merhaba", 4.6, 5.2),
        ("diyelim", 5.2, 5.9),
        ("sonra", 8.0, 8.4),
        ("tekrar", 8.4, 9.0),
        ("görüşürüz", 9.0, 9.8),
    ]
    return [{"start": s, "end": e, "word": w} for w, s, e in spec]


class _StubSession:
    def __init__(self, words, factor=1.0, tier="fast"):
        self._words = words
        self._factor = factor
        self._tier = tier
        self._clip = {"id": 1, "stages": [{"name": "jumpcut"}]}

    def clip(self, clip_id):
        if clip_id != 1:
            raise ValueError(f"No clip {clip_id}.")
        return self._clip

    def words_for(self, clip):
        if self._words is None:
            raise ValueError("Clip has no cut artifact yet.")
        return self._words

    def speed_factor(self, clip):
        return self._factor


def test_registry_spec_parity():
    """Every TOOL_SPECS function name resolves in REGISTRY (and vice-checks
    find_moment specifically)."""
    names = {s["function"]["name"] for s in tools.TOOL_SPECS}
    for n in names:
        assert n in tools.REGISTRY, f"{n} missing from REGISTRY"
    assert "find_moment" in names
    assert "find_moment" in tools.REGISTRY


def test_find_moment_not_mutating():
    """Read-only: must NOT be A/B gated."""
    assert "find_moment" not in tools.MUTATING_TOOLS


def test_keyword_fallback_finds_span(monkeypatch):
    """No LLM key -> keyword fallback returns the span containing the words."""
    import pipeline.config as config

    monkeypatch.setattr(config, "llm_settings",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    sess = _StubSession(_make_words())
    res = tools.find_moment(sess, 1, "fiyat politikası", limit=3)
    assert res["ok"] is True
    assert res["timeline"] == "player"
    cands = res["candidates"]
    assert cands
    # The best window should cover the "fiyat politikamızdan" region (~0.8s).
    top = cands[0]
    assert top["start"] <= 1.3 <= top["end"]
    assert 0.0 <= top["confidence"] <= 1.0
    assert len(cands) <= 3


def test_keyword_fallback_respects_limit(monkeypatch):
    import pipeline.config as config

    monkeypatch.setattr(config, "llm_settings",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    sess = _StubSession(_make_words())
    res = tools.find_moment(sess, 1, "fiyat merhaba tekrar", limit=2)
    assert res["ok"] is True
    assert len(res["candidates"]) <= 2


def test_llm_path_snaps_clamps_and_divides_by_factor(monkeypatch):
    """LLM candidates are word-snapped, clamped, and player-time = pre/factor."""
    import pipeline.config as config

    monkeypatch.setattr(config, "llm_settings",
                        lambda *a, **k: ("k", None, "m"))
    monkeypatch.setattr(config, "json_response_format", lambda b: {})
    monkeypatch.setattr(
        config, "extract_json",
        lambda c: {"candidates": [
            # deliberately ragged + out-of-range end to test snap+clamp
            {"start": 0.9, "end": 999.0, "quote": "fiyat", "confidence": 0.9},
        ]})

    class _FakeMsg:
        content = "{}"

    class _FakeChoice:
        message = _FakeMsg()

    class _FakeResp:
        choices = [_FakeChoice()]

    class _FakeCompletions:
        def create(self, **kw):
            return _FakeResp()

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        def __init__(self, *a, **k):
            self.chat = _FakeChat()

    import openai

    monkeypatch.setattr(openai, "OpenAI", _FakeClient)

    sess = _StubSession(_make_words(), factor=2.0)
    res = tools.find_moment(sess, 1, "fiyattan bahsettiği yer")
    assert res["ok"] is True
    top = res["candidates"][0]
    # pre-speed span snaps to 0.8..9.8 (clamped to last word end), /2.0.
    assert top["start"] == pytest.approx(0.4, abs=0.01)  # 0.8/2.0
    assert top["end"] == pytest.approx(4.9, abs=0.01)    # 9.8/2.0
    assert top["confidence"] == 0.9


def test_llm_failure_falls_back(monkeypatch):
    """LLM raising must not raise: fall through to keyword fallback."""
    import pipeline.config as config

    monkeypatch.setattr(config, "llm_settings",
                        lambda *a, **k: ("k", None, "m"))
    monkeypatch.setattr(config, "json_response_format", lambda b: {})

    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("network down")

    import openai

    monkeypatch.setattr(openai, "OpenAI", _Boom)
    sess = _StubSession(_make_words())
    res = tools.find_moment(sess, 1, "tekrar görüşürüz")
    assert res["ok"] is True
    assert res["candidates"]


def test_no_match_returns_err(monkeypatch):
    import pipeline.config as config

    monkeypatch.setattr(config, "llm_settings",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    sess = _StubSession(_make_words())
    res = tools.find_moment(sess, 1, "zzz qqq vvv")
    assert res["ok"] is False


def test_unrendered_clip_errors(monkeypatch):
    import pipeline.config as config

    monkeypatch.setattr(config, "llm_settings",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    sess = _StubSession(None)  # words_for raises ValueError
    res = tools.find_moment(sess, 1, "anything")
    assert res["ok"] is False
    assert "render" in res["error"].lower() or "open" in res["error"].lower()


def test_bad_clip_id_errors():
    sess = _StubSession(_make_words())
    res = tools.find_moment(sess, 99, "anything")
    assert res["ok"] is False
