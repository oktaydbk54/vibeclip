"""generate_metadata: LLM writes platform copy from a clip transcript.

Read-only tool — must persist clip["metadata"], take NO undo snapshot, and stay
out of the A/B gate (not in MUTATING_TOOLS). Also guards spec<->registry parity.
"""

import json

import pytest

from chat import tools


class _FakeSession:
    """Minimal Session-like stand-in: one clip + word list, save() flag."""

    def __init__(self, words, platform="youtube_shorts"):
        self._clip = {"id": 1, "title": "Working title", "hook": "A hook"}
        self._words = words
        self.data = {"platform": platform}
        self.saved = 0
        self.snapshots = []  # would record undo points if any were taken

    def clip(self, clip_id):
        if clip_id != 1:
            raise ValueError(f"No clip #{clip_id}.")
        return self._clip

    def words_for(self, clip):
        return self._words

    def save(self):
        self.saved += 1

    def snapshot(self, label=""):  # pragma: no cover - must NOT be called
        self.snapshots.append(label)


def _patch_openai(monkeypatch, payload):
    """Make openai.OpenAI(...).chat.completions.create() return `payload`."""
    class _Msg:
        def __init__(self, content):
            self.message = type("M", (), {"content": content})()

    class _Resp:
        def __init__(self, content):
            self.choices = [_Msg(content)]

    class _Completions:
        def create(self, **kwargs):
            return _Resp(json.dumps(payload))

    class _Chat:
        completions = _Completions()

    class _Client:
        def __init__(self, *a, **k):
            self.chat = _Chat()

    import openai
    monkeypatch.setattr(openai, "OpenAI", _Client)
    monkeypatch.setattr(
        tools_config(), "llm_settings",
        lambda tier="fast", override=None: ("k", None, "m"))


def tools_config():
    from pipeline import config
    return config


def test_generate_metadata_validates_and_persists(monkeypatch):
    payload = {"platforms": {
        "youtube_shorts": {"title": "  My Title  ",
                           "description": " A desc ",
                           "hashtags": ["#ai", "vibe coding", "x" + "y" * 40]},
        "tiktok": {"title": "T", "description": "D", "hashtags": []},
        "instagram_reels": {"title": "I", "description": "D2",
                            "hashtags": [str(i) for i in range(20)]},
    }}
    _patch_openai(monkeypatch, payload)
    sess = _FakeSession([{"word": "hello"}, {"word": "world"}])

    res = tools.generate_metadata(sess, 1)
    assert res["ok"] is True
    meta = res["metadata"]
    # all three platforms present (default = primary + the rest)
    assert set(meta) == {"youtube_shorts", "tiktok", "instagram_reels"}
    yt = meta["youtube_shorts"]
    assert yt["title"] == "My Title"          # stripped
    assert yt["description"] == "A desc"      # stripped
    assert yt["hashtags"][0] == "#ai"         # already prefixed
    assert yt["hashtags"][1] == "#vibecoding"  # spaces removed, # added
    # hashtags capped at 8
    assert len(meta["instagram_reels"]["hashtags"]) == 8
    # persisted additively + saved, with NO undo snapshot taken
    assert sess._clip["metadata"] == meta
    assert sess.saved == 1
    assert sess.snapshots == []


def test_generate_metadata_subset(monkeypatch):
    payload = {"platforms": {
        "tiktok": {"title": "T", "description": "D", "hashtags": ["#a"]}}}
    _patch_openai(monkeypatch, payload)
    sess = _FakeSession([{"word": "hi"}])
    res = tools.generate_metadata(sess, 1, platforms=["tiktok"])
    assert set(res["metadata"]) == {"tiktok"}


def test_generate_metadata_bad_platforms(monkeypatch):
    sess = _FakeSession([{"word": "hi"}])
    res = tools.generate_metadata(sess, 1, platforms=["facebook"])
    assert res["ok"] is False


def test_generate_metadata_no_transcript(monkeypatch):
    sess = _FakeSession([])
    res = tools.generate_metadata(sess, 1)
    assert res["ok"] is False


def test_generate_metadata_unknown_clip(monkeypatch):
    sess = _FakeSession([{"word": "hi"}])
    res = tools.generate_metadata(sess, 99)
    assert res["ok"] is False


def test_generate_metadata_json_failure(monkeypatch):
    class _Bad:
        def create(self, **kwargs):
            r = type("R", (), {})()
            r.choices = [type("C", (), {"message": type(
                "M", (), {"content": "not json at all"})()})()]
            return r

    import openai
    monkeypatch.setattr(openai, "OpenAI",
                        lambda *a, **k: type("X", (), {
                            "chat": type("Y", (), {"completions": _Bad()})()})())
    monkeypatch.setattr(tools_config(), "llm_settings",
                        lambda tier="fast", override=None: ("k", None, "m"))
    sess = _FakeSession([{"word": "hi"}])
    res = tools.generate_metadata(sess, 1)
    assert res["ok"] is False
    assert "failed" in res["error"].lower()


def test_metadata_is_read_only_and_wired():
    assert "generate_metadata" not in tools.MUTATING_TOOLS
    assert "generate_metadata" in tools.REGISTRY
    names = {s["function"]["name"] for s in tools.TOOL_SPECS}
    assert "generate_metadata" in names


def test_spec_registry_parity():
    names = {s["function"]["name"] for s in tools.TOOL_SPECS}
    for name in names:
        assert name in tools.REGISTRY, f"{name} has a spec but no registry impl"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
