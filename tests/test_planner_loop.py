"""agentic_replan_loop: planner.propose() as a bounded tool-using re-plan loop.

Covers the happy path (exactly one LLM call), validator-feedback refinement,
moment-lookup resolution, the round cap / termination guarantee, and the
pure-function _validate_steps checks. The LLM is monkeypatched with a fake
OpenAI client whose create() pops scripted responses; config.extract_json is
patched to JSON-decode the scripted content.
"""

import json

import pytest

from chat import planner


def _make_words():
    spec = [
        ("bugün", 0.0, 0.4),
        ("fiyat", 0.4, 1.0),
        ("politikası", 1.0, 1.8),
        ("merhaba", 4.0, 4.6),
        ("diyelim", 4.6, 5.2),
        ("görüşürüz", 8.0, 8.8),
    ]
    return [{"start": s, "end": e, "word": w} for w, s, e in spec]


class _StubSession:
    def __init__(self, words, factor=1.0, tier="fast", stages=None):
        self._words = words
        self._factor = factor
        self._tier = tier
        self.data = {"preferences": []}
        self._clip = {"id": 1, "current": None,
                      "stages": stages or [{"name": "jumpcut"}]}

    def clip(self, clip_id):
        if clip_id != 1:
            raise ValueError(f"No clip {clip_id}.")
        return self._clip

    def words_for(self, clip):
        return self._words

    def speed_factor(self, clip):
        return self._factor

    def summary(self):
        return "CLIP #1"


class _ScriptedClient:
    """Pops dict responses from a shared script list; records call count."""

    script: list
    calls: list

    def __init__(self, *a, **k):
        pass

    class _Chat:
        def __init__(self, outer):
            self.completions = _ScriptedClient._Completions(outer)

    class _Completions:
        def __init__(self, outer):
            self.outer = outer

        def create(self, **kw):
            _ScriptedClient.calls.append(kw["messages"])
            payload = _ScriptedClient.script.pop(0)

            class _Msg:
                content = json.dumps(payload)

            class _Choice:
                message = _Msg()

            class _Resp:
                choices = [_Choice()]

            return _Resp()

    @property
    def chat(self):
        return _ScriptedClient._Chat(self)


def _patch_llm(monkeypatch, script):
    import pipeline.config as config
    import openai

    _ScriptedClient.script = list(script)
    _ScriptedClient.calls = []
    monkeypatch.setattr(config, "llm_settings", lambda *a, **k: ("k", None, "m"))
    monkeypatch.setattr(config, "json_response_format", lambda b: {})
    monkeypatch.setattr(config, "extract_json", lambda c: json.loads(c))
    monkeypatch.setattr(openai, "OpenAI", _ScriptedClient)


# --------------------------------------------------------------------------
# propose() loop behaviour
# --------------------------------------------------------------------------

def test_clean_first_plan_is_one_call(monkeypatch):
    """A valid first plan -> exactly ONE LLM call; no refinement."""
    _patch_llm(monkeypatch, [
        {"summary": "punchier", "steps": [
            {"action": "auto_zoom", "args": {"density": 0.3}, "why": "punch"},
        ]},
    ])
    sess = _StubSession(_make_words())
    plan = planner.propose(sess, 1, "daha punchy yap")
    assert len(_ScriptedClient.calls) == 1
    assert plan["refinements"] == 0
    assert plan["resolved_moments"] == 0
    assert plan["steps"][0]["action"] == "auto_zoom"
    assert plan["steps"][0]["args"]["clip_id"] == 1


def test_named_speed_regex_still_enforced(monkeypatch):
    """The deterministic named-factor override survives the loop refactor."""
    _patch_llm(monkeypatch, [
        {"summary": "faster", "steps": [
            {"action": "set_speed", "args": {"factor": 1.5}, "why": "safer"},
        ]},
    ])
    sess = _StubSession(_make_words())
    plan = planner.propose(sess, 1, "2x hızlandır")
    assert plan["steps"][0]["args"]["factor"] == 2.0


def test_validator_feedback_triggers_refine(monkeypatch, tmp_path):
    """Unknown action + missing file -> a 2nd call carrying VALIDATOR FEEDBACK;
    the corrected 2nd response wins."""
    ghost = str(tmp_path / "nope.png")
    _patch_llm(monkeypatch, [
        {"summary": "v1", "steps": [
            {"action": "bogus_action", "args": {}, "why": "?"},
            {"action": "add_sticker",
             "args": {"file": ghost, "start": 4, "duration": 2}, "why": "x"},
        ]},
        {"summary": "v2", "steps": [
            {"action": "auto_zoom", "args": {"density": 0.3}, "why": "ok"},
        ]},
    ])
    sess = _StubSession(_make_words())
    plan = planner.propose(sess, 1, "bir şeyler ekle")
    assert len(_ScriptedClient.calls) == 2
    # The 2nd call's last user message is the validator feedback.
    fb = _ScriptedClient.calls[1][-1]["content"]
    assert "VALIDATOR FEEDBACK" in fb
    assert "missing_file" in fb
    assert "unknown_action" in fb
    assert plan["refinements"] == 1
    assert plan["steps"][0]["action"] == "auto_zoom"


def test_lookup_resolves_moment(monkeypatch):
    """A first-round lookup invokes _find_moment_core and feeds LOOKUP RESULTS;
    the final plan uses the resolved span."""
    import chat.tools as tools

    captured = {}

    def _fake_find(session, clip, description, limit=3):
        captured["desc"] = description
        return [{"start": 4.0, "end": 5.2, "quote": "merhaba",
                 "confidence": 0.9}]

    monkeypatch.setattr(tools, "_find_moment_core", _fake_find)
    _patch_llm(monkeypatch, [
        {"lookup": [{"id": "m1", "description": "selamladığı yer"}]},
        {"summary": "zoom at greeting", "steps": [
            {"action": "add_zoom", "args": {"time": 4.0, "duration": 1.0},
             "why": "emphasis"},
        ]},
    ])
    sess = _StubSession(_make_words())
    plan = planner.propose(sess, 1, "selamladığı yerde zoom yap")
    assert captured["desc"] == "selamladığı yer"
    assert len(_ScriptedClient.calls) == 2
    results_msg = _ScriptedClient.calls[1][-1]["content"]
    assert "LOOKUP RESULTS" in results_msg
    assert plan["resolved_moments"] == 1
    assert plan["steps"][0]["args"]["time"] == 4.0


def test_all_rounds_invalid_raises(monkeypatch):
    """Every round invalid -> ValueError mentioning the issues; never exceeds
    MAX_PLAN_ROUNDS calls."""
    bad = {"summary": "bad", "steps": [
        {"action": "bogus", "args": {}, "why": "?"},
    ]}
    _patch_llm(monkeypatch, [bad, bad, bad])
    sess = _StubSession(_make_words())
    with pytest.raises(ValueError) as exc:
        planner.propose(sess, 1, "yap bir şey")
    assert "unknown_action" in str(exc.value)
    assert len(_ScriptedClient.calls) == planner.MAX_PLAN_ROUNDS


def test_loop_never_exceeds_cap(monkeypatch):
    """Persistent lookups also terminate at MAX_PLAN_ROUNDS calls."""
    import chat.tools as tools
    monkeypatch.setattr(tools, "_find_moment_core",
                        lambda *a, **k: [{"start": 1.0, "end": 2.0,
                                          "quote": "", "confidence": 0.5}])
    lk = {"lookup": [{"id": "m1", "description": "x"}]}
    _patch_llm(monkeypatch, [lk, lk, lk])
    sess = _StubSession(_make_words())
    # Last round still has only a lookup -> no steps -> ValueError, but bounded.
    with pytest.raises(ValueError):
        planner.propose(sess, 1, "x")
    assert len(_ScriptedClient.calls) == planner.MAX_PLAN_ROUNDS


# --------------------------------------------------------------------------
# _validate_steps pure-function checks
# --------------------------------------------------------------------------

def test_validate_unknown_and_bad_args():
    data = {"steps": [
        {"action": "nope", "args": {}},
        {"action": "auto_zoom", "args": "not a dict"},
    ]}
    steps, issues = planner._validate_steps(1, data, 10.0)
    assert steps == []
    codes = {i["problem"] for i in issues}
    assert codes == {"unknown_action", "bad_args"}


def test_validate_injects_clip_id():
    data = {"steps": [{"action": "cut_silences", "args": {"max_pause": 0.5}}]}
    steps, issues = planner._validate_steps(7, data, 10.0)
    assert not issues
    assert steps[0]["args"]["clip_id"] == 7


def test_validate_time_out_of_range():
    data = {"steps": [
        {"action": "add_zoom", "args": {"time": 99.0, "duration": 1.0}},
    ]}
    steps, issues = planner._validate_steps(1, data, 10.0)
    assert steps == []
    assert issues[0]["problem"] == "time_out_of_range"


def test_validate_set_cut_time_exempt():
    """set_cut speaks SOURCE seconds -> not bounded by clip duration."""
    data = {"steps": [
        {"action": "set_cut", "args": {"start": 120.0, "end": 130.0}},
    ]}
    steps, issues = planner._validate_steps(1, data, 10.0)
    assert not issues
    assert steps[0]["action"] == "set_cut"


def test_validate_empty_range():
    data = {"steps": [
        {"action": "remove_section", "args": {"start": 5.0, "end": 3.0}},
    ]}
    steps, issues = planner._validate_steps(1, data, 10.0)
    assert steps == []
    assert issues[0]["problem"] == "empty_range"


def test_validate_event_index_out_of_range():
    clip = {"stages": [{"name": "zoom",
                        "params": {"windows": [[1.0, 2.0, 1.2]]}}]}
    data = {"steps": [
        {"action": "delete_event", "args": {"stage": "zoom", "index": 5}},
    ]}
    steps, issues = planner._validate_steps(1, data, 10.0, clip)
    assert steps == []
    assert issues[0]["problem"] == "event_index_out_of_range"

    ok = {"steps": [
        {"action": "delete_event", "args": {"stage": "zoom", "index": 0}},
    ]}
    steps2, issues2 = planner._validate_steps(1, ok, 10.0, clip)
    assert not issues2
    assert steps2[0]["args"]["index"] == 0


def test_validate_too_many_steps():
    data = {"steps": [{"action": "cut_silences", "args": {}} for _ in range(9)]}
    steps, issues = planner._validate_steps(1, data, 10.0)
    assert len(steps) == 9  # all valid; the cap is enforced post-loop
    assert any(i["problem"] == "too_many_steps" for i in issues)
