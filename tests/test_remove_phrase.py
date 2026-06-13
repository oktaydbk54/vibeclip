"""remove_phrase: content-described deletion through the trim pipeline.

Mocks _find_moment_core (span resolution) and the session's snapshot/set_stage
so the test exercises remove_phrase's own logic: range accumulation, atomic
single-snapshot multi-occurrence removal, the confidence floor, and the
combined >50%-of-clip guard. Also checks TOOL_SPECS/REGISTRY/MUTATING parity.
"""

from chat import tools


def _make_words():
    spec = [
        ("bugün", 0.0, 0.4),
        ("fiyat", 0.4, 0.9),
        ("politikası", 0.9, 1.6),
        ("önemli", 1.6, 2.2),
        ("tekrar", 4.0, 4.5),
        ("fiyat", 4.5, 5.0),
        ("politikası", 5.0, 5.7),
        ("son", 8.0, 8.4),
    ]
    return [{"start": s, "end": e, "word": w} for w, s, e in spec]


class _StubSession:
    """Records snapshot calls and the final set_stage params."""

    def __init__(self, words, factor=1.0, current="clip.mp4"):
        self._words = words
        self._factor = factor
        self.last_notes = None
        self._clip = {"id": 1, "current": current,
                      "stages": [{"name": "jumpcut"}]}
        self.snapshots = []
        self.set_stage_calls = []

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

    def snapshot(self, label):
        self.snapshots.append(label)

    def set_stage(self, clip_id, name, params):
        self.set_stage_calls.append((name, params))
        return "out.mp4"


def _patch_no_guard(monkeypatch):
    """Make ffprobe report a long clip so the >50% guard never trips."""
    import pipeline.media as media
    monkeypatch.setattr(media, "ffprobe_info", lambda p: {"duration": 100.0})


def test_registry_spec_mutating_parity():
    names = {s["function"]["name"] for s in tools.TOOL_SPECS}
    assert "remove_phrase" in names
    assert "remove_phrase" in tools.REGISTRY
    assert tools.REGISTRY["remove_phrase"] is tools.remove_phrase
    # Mutating: must be A/B gated (blocked outside a plan).
    assert "remove_phrase" in tools.MUTATING_TOOLS


def test_single_span_appends_one_anchored_range(monkeypatch):
    _patch_no_guard(monkeypatch)
    monkeypatch.setattr(
        tools, "_find_moment_core",
        lambda s, c, d, limit=5: [
            {"start": 0.4, "end": 1.6, "quote": "fiyat politikası",
             "confidence": 0.9},
        ])
    sess = _StubSession(_make_words())
    res = tools.remove_phrase(sess, 1, "fiyat politikasından bahsettiği yer")
    assert res["ok"] is True
    assert res["count"] == 1
    assert len(sess.snapshots) == 1            # one undo entry
    assert len(sess.set_stage_calls) == 1
    name, params = sess.set_stage_calls[0]
    assert name == "trim"
    assert len(params["ranges"]) == 1
    rng = params["ranges"][0]
    assert rng["start"] == 0.4 and rng["end"] == 1.6
    assert "fiyat" in rng["anchor_text"]


def test_occurrence_all_one_atomic_trim_with_two_ranges(monkeypatch):
    _patch_no_guard(monkeypatch)
    monkeypatch.setattr(
        tools, "_find_moment_core",
        lambda s, c, d, limit=5: [
            {"start": 0.4, "end": 1.6, "quote": "fiyat politikası",
             "confidence": 0.9},
            {"start": 4.5, "end": 5.7, "quote": "fiyat politikası",
             "confidence": 0.7},
        ])
    sess = _StubSession(_make_words())
    res = tools.remove_phrase(sess, 1, "fiyat politikası", occurrence="all")
    assert res["ok"] is True
    assert res["count"] == 2
    # Atomic: exactly ONE snapshot and ONE set_stage for both spans.
    assert len(sess.snapshots) == 1
    assert len(sess.set_stage_calls) == 1
    _, params = sess.set_stage_calls[0]
    assert len(params["ranges"]) == 2


def test_confidence_floor_filters_weak_matches(monkeypatch):
    _patch_no_guard(monkeypatch)
    monkeypatch.setattr(
        tools, "_find_moment_core",
        lambda s, c, d, limit=5: [
            {"start": 0.4, "end": 1.6, "quote": "x", "confidence": 0.2},
        ])
    sess = _StubSession(_make_words())
    res = tools.remove_phrase(sess, 1, "something vague")
    assert res["ok"] is False
    assert not sess.snapshots          # nothing mutated


def test_no_match_returns_err(monkeypatch):
    _patch_no_guard(monkeypatch)
    monkeypatch.setattr(tools, "_find_moment_core",
                        lambda s, c, d, limit=5: [])
    sess = _StubSession(_make_words())
    res = tools.remove_phrase(sess, 1, "zzz qqq")
    assert res["ok"] is False
    assert not sess.snapshots


def test_combined_removal_over_half_is_refused(monkeypatch):
    import pipeline.media as media
    # Short clip: the two spans together exceed 50% of 10s.
    monkeypatch.setattr(media, "ffprobe_info", lambda p: {"duration": 10.0})
    monkeypatch.setattr(
        tools, "_find_moment_core",
        lambda s, c, d, limit=5: [
            {"start": 0.0, "end": 3.0, "quote": "a", "confidence": 0.9},
            {"start": 4.0, "end": 7.0, "quote": "b", "confidence": 0.8},
        ])
    sess = _StubSession(_make_words())
    res = tools.remove_phrase(sess, 1, "everything", occurrence="all")
    assert res["ok"] is False
    assert "half" in res["error"].lower()
    assert not sess.snapshots          # guard fires BEFORE snapshot


def test_unrendered_clip_errors(monkeypatch):
    _patch_no_guard(monkeypatch)
    sess = _StubSession(None)          # words_for raises ValueError
    res = tools.remove_phrase(sess, 1, "anything")
    assert res["ok"] is False
    assert "render" in res["error"].lower() or "open" in res["error"].lower()


def test_bad_clip_id_errors():
    sess = _StubSession(_make_words())
    res = tools.remove_phrase(sess, 99, "anything")
    assert res["ok"] is False
