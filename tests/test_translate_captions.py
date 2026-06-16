"""Caption translation: timing-preserving redistribution, graceful fallback,
and the set_caption_language tool wiring. The LLM call is monkeypatched so the
tests are offline and deterministic."""

import pipeline.translate as tr


def _words():
    # Two caption lines worth of source words (clip-local seconds).
    return [
        {"start": 0.0, "end": 0.4, "word": "the"},
        {"start": 0.4, "end": 0.9, "word": "quick"},
        {"start": 0.9, "end": 1.3, "word": "brown"},
        {"start": 1.3, "end": 1.8, "word": "fox"},
        {"start": 2.0, "end": 2.5, "word": "jumps"},
        {"start": 2.5, "end": 3.0, "word": "high"},
    ]


def test_redistribute_keeps_span_and_is_monotonic():
    chunk = {"start": 2.0, "end": 4.0, "word": "", "words": [
        {"start": 2.0, "end": 3.0, "word": "jumps"},
        {"start": 3.0, "end": 4.0, "word": "high"},
    ]}
    out = tr._redistribute(chunk, "salta muy alto")
    assert [w["word"] for w in out] == ["salta", "muy", "alto"]
    # Span is preserved exactly: first word starts at chunk start, last ends at end.
    assert out[0]["start"] == 2.0
    assert out[-1]["end"] == 4.0
    # Words are contiguous (each ends where the next begins) and inside the span.
    for w in out:
        assert 2.0 <= w["start"] <= w["end"] <= 4.0
    for a, b in zip(out, out[1:]):
        assert a["end"] == b["start"]


def test_redistribute_empty_translation_falls_back_to_original():
    chunk = {"start": 0.0, "end": 1.0, "word": "", "words": [
        {"start": 0.0, "end": 1.0, "word": "hi"}]}
    out = tr._redistribute(chunk, "   ")
    assert out == [{"start": 0.0, "end": 1.0, "word": "hi"}]


def test_translate_captions_no_target_returns_input_unchanged():
    w = _words()
    assert tr.translate_captions(w, "") is w
    assert tr.translate_captions(w, "   ") is w


def test_translate_captions_llm_failure_keeps_source_words(monkeypatch):
    monkeypatch.setattr(tr, "_llm_translate", lambda texts, lang: None)
    # Force a cache miss so it hits the (mocked) LLM path.
    monkeypatch.setattr(tr, "_cache_path",
                        lambda texts, lang: tr.config.CACHE_DIR / "nope_xyz.json")
    w = _words()
    assert tr.translate_captions(w, "Spanish") is w


def test_translate_captions_happy_path(monkeypatch, tmp_path):
    # Two source chunks (max_words=4 -> "the quick brown fox" | "jumps high").
    calls = {}

    def fake_llm(texts, lang):
        calls["texts"] = texts
        calls["lang"] = lang
        return [f"<{lang}:{t}>" for t in texts]

    monkeypatch.setattr(tr, "_llm_translate", fake_llm)
    monkeypatch.setattr(tr, "_cache_path",
                        lambda texts, lang: tmp_path / "c.json")
    out = tr.translate_captions(_words(), "es")
    assert calls["lang"] == "es"
    assert len(calls["texts"]) == 2  # two caption lines
    joined = " ".join(w["word"] for w in out)
    assert "<es:the quick brown fox>" in joined
    # Full source span [0.0, 3.0] is still covered by the translated words.
    assert out[0]["start"] == 0.0
    assert out[-1]["end"] == 3.0


def test_translate_captions_uses_disk_cache(monkeypatch, tmp_path):
    cache = tmp_path / "c.json"
    monkeypatch.setattr(tr, "_cache_path", lambda texts, lang: cache)
    n = {"calls": 0}

    def fake_llm(texts, lang):
        n["calls"] += 1
        return [t.upper() for t in texts]

    monkeypatch.setattr(tr, "_llm_translate", fake_llm)
    tr.translate_captions(_words(), "es")
    tr.translate_captions(_words(), "es")  # second call must hit the cache
    assert n["calls"] == 1


def _install_fake_chat(monkeypatch, content, calls=None):
    """Fake openai.OpenAI whose chat.completions.create returns `content`."""
    import sys
    import types

    def create(**kw):
        if calls is not None:
            calls.append(kw)
        msg = types.SimpleNamespace(content=content)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])
    client = types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=create)))
    fake = types.ModuleType("openai")
    fake.OpenAI = lambda *a, **k: client
    monkeypatch.setitem(sys.modules, "openai", fake)
    monkeypatch.setattr(tr.config, "llm_settings",
                        lambda tier="fast": ("k", None, "m"))


def test_translate_lines_fitted_happy(monkeypatch, tmp_path):
    monkeypatch.setattr(tr, "_cache_path_fitted",
                        lambda texts, lang, budgets: tmp_path / "f.json")
    _install_fake_chat(monkeypatch,
                       '{"lines":[{"text":"hola","alt_short":"hi"},'
                       '{"text":"adios","alt_short":"chau"}]}')
    out = tr.translate_lines_fitted(["hello", "bye"], "es", [20, 20])
    assert out == [{"text": "hola", "alt_short": "hi"},
                   {"text": "adios", "alt_short": "chau"}]


def test_translate_lines_fitted_count_mismatch_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(tr, "_cache_path_fitted",
                        lambda texts, lang, budgets: tmp_path / "f.json")
    _install_fake_chat(monkeypatch, '{"lines":[{"text":"hola"}]}')
    assert tr.translate_lines_fitted(["a", "b"], "es", [10, 10]) is None


def test_translate_lines_fitted_client_error_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(tr, "_cache_path_fitted",
                        lambda texts, lang, budgets: tmp_path / "f.json")
    import sys
    import types
    fake = types.ModuleType("openai")

    def boom(*a, **k):
        raise RuntimeError("network")
    fake.OpenAI = boom
    monkeypatch.setitem(sys.modules, "openai", fake)
    monkeypatch.setattr(tr.config, "llm_settings", lambda tier="fast": ("k", None, "m"))
    assert tr.translate_lines_fitted(["a"], "es", [10]) is None


def test_translate_lines_fitted_cache_key_depends_on_budgets():
    p1 = tr._cache_path_fitted(["hello"], "es", [10])
    p2 = tr._cache_path_fitted(["hello"], "es", [20])
    assert p1 != p2  # same text, different window -> no cache collision


def test_translate_lines_fitted_uses_cache(monkeypatch, tmp_path):
    import json
    cache = tmp_path / "f.json"
    cache.write_text(json.dumps([{"text": "cached", "alt_short": "c"}]))
    monkeypatch.setattr(tr, "_cache_path_fitted",
                        lambda texts, lang, budgets: cache)
    calls = []
    _install_fake_chat(monkeypatch, "{}", calls=calls)
    out = tr.translate_lines_fitted(["x"], "es", [10])
    assert out == [{"text": "cached", "alt_short": "c"}]
    assert calls == []  # served from disk, client never called


def test_lang_slug():
    import chat.tools as tools
    assert tools._lang_slug("Spanish") == "spanish"
    assert tools._lang_slug("pt-BR") == "pt-br"
    assert tools._lang_slug("  ") == "xx"


class _CapSession:
    """Minimal session for export_captions: fixed words + a subtitles stage."""

    def __init__(self, tmp_path, sub_lang=None):
        self.workdir = tmp_path
        params = {}
        if sub_lang:
            params["lang"] = sub_lang
        self._clip = {"id": 3, "stages": [{"name": "subtitles",
                                           "params": params}]}

    def clip(self, cid):
        return self._clip

    def words_for(self, clip):
        return _words()


def test_export_captions_translates_explicit_language(monkeypatch, tmp_path):
    import chat.tools as tools
    # Stub the translator so the export is offline + deterministic.
    monkeypatch.setattr("pipeline.translate.translate_lines",
                        lambda texts, lang: [f"[{lang}]{t}" for t in texts])
    s = _CapSession(tmp_path)
    r = tools.export_captions(s, 3, format="srt", language="Spanish")
    assert r["ok"] and r["language"] == "Spanish"
    out = tmp_path / "clip03.spanish.srt"  # language-suffixed, no clobber
    assert out.exists()
    assert "[Spanish]" in out.read_text(encoding="utf-8")


def test_export_captions_defaults_to_burned_caption_language(monkeypatch, tmp_path):
    import chat.tools as tools
    monkeypatch.setattr("pipeline.translate.translate_lines",
                        lambda texts, lang: [f"[{lang}]{t}" for t in texts])
    s = _CapSession(tmp_path, sub_lang="fr")  # captions burned in French
    r = tools.export_captions(s, 3, format="vtt")  # no explicit language
    assert r["language"] == "fr"
    assert (tmp_path / "clip03.fr.vtt").exists()


def test_export_captions_original_forces_spoken_language(tmp_path):
    import chat.tools as tools
    s = _CapSession(tmp_path, sub_lang="fr")
    r = tools.export_captions(s, 3, format="srt", language="original")
    assert r["language"] is None
    assert (tmp_path / "clip03.srt").exists()  # no suffix -> the original
    assert "the quick" in (tmp_path / "clip03.srt").read_text(encoding="utf-8")


def test_set_caption_language_tool_sets_and_clears(monkeypatch):
    """set_caption_language stores/removes the `lang` param on the subtitles
    stage and re-renders through set_stage."""
    import chat.tools as tools

    rendered = {}

    class FakeSession:
        last_notes = "ok"

        def __init__(self):
            self._clip = {"id": 1, "stages": [{"name": "subtitles",
                                               "params": {"karaoke": True}}]}

        def clip(self, cid):
            return self._clip

        def snapshot(self, *a, **k):
            pass

        def set_stage(self, cid, name, params):
            rendered["name"] = name
            rendered["params"] = params
            # mirror set_stage's persistence so a follow-up read sees the param
            self._clip["stages"] = [{"name": name, "params": params}]
            return "/tmp/out.mp4"

    s = FakeSession()
    r = tools.set_caption_language(s, 1, "Spanish")
    assert r["ok"] and r["language"] == "Spanish"
    assert rendered["name"] == "subtitles"
    assert rendered["params"]["lang"] == "Spanish"

    r2 = tools.set_caption_language(s, 1, "original")
    assert r2["ok"] and r2["language"] is None
    assert "lang" not in rendered["params"]
