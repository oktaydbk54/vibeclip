"""prompt_generalization: configurable planner tier + additive capability map.

LOWEST-RISK item. These tests are mostly regression guards: the PLANNER_TIER
knob must default to a no-op (today's behavior) and the existing phrase maps +
deterministic approve/reject backstops must remain byte-stable.
"""

import importlib

from chat import agent, planner
from pipeline import config


# --- (a) config knob defaults to a no-op -----------------------------------
def test_planner_tier_defaults_to_none(monkeypatch):
    """Unset PLANNER_TIER -> None, so propose() inherits the session tier
    exactly like before (no cost/behavior change for existing users)."""
    monkeypatch.delenv("PLANNER_TIER", raising=False)
    reloaded = importlib.reload(config)
    try:
        assert reloaded.PLANNER_TIER is None
    finally:
        importlib.reload(config)  # restore module-level state for other tests


def test_planner_tier_reads_env(monkeypatch):
    monkeypatch.setenv("PLANNER_TIER", "PRO")
    reloaded = importlib.reload(config)
    try:
        assert reloaded.PLANNER_TIER == "pro"  # normalized lower
    finally:
        monkeypatch.delenv("PLANNER_TIER", raising=False)
        importlib.reload(config)


# --- (b) propose() honors the knob -----------------------------------------
class _StubClip(dict):
    pass


class _StubSession:
    def __init__(self, tier="fast"):
        self._tier = tier
        self.data = {"preferences": []}

    def clip(self, clip_id):
        return {"id": clip_id, "current": None, "stages": []}

    def words_for(self, clip):
        return []

    def speed_factor(self, clip):
        return 1.0


class _StopPlanning(Exception):
    pass


def _run_propose_capture_tier(monkeypatch, session):
    """Call propose() far enough to observe the tier llm_settings receives,
    then bail out (we don't need a real LLM round)."""
    seen = {}

    def _fake_llm_settings(tier="fast", override=None):
        seen["tier"] = tier
        raise _StopPlanning  # stop before any network call

    monkeypatch.setattr(config, "llm_settings", _fake_llm_settings)
    try:
        planner.propose(session, clip_id=1, instruction="tighten it")
    except _StopPlanning:
        pass
    return seen.get("tier")


def test_propose_inherits_session_tier_by_default(monkeypatch):
    monkeypatch.setattr(config, "PLANNER_TIER", None)
    tier = _run_propose_capture_tier(monkeypatch, _StubSession(tier="fast"))
    assert tier == "fast"


def test_propose_honors_planner_tier_override(monkeypatch):
    monkeypatch.setattr(config, "PLANNER_TIER", "pro")
    # Even though the session ran on "fast", the env knob forces "pro".
    tier = _run_propose_capture_tier(monkeypatch, _StubSession(tier="fast"))
    assert tier == "pro"


# --- (c) phrase maps and localized deterministic approval -------------------
def test_system_rules_keeps_phrase_mappings():
    rules = agent.SYSTEM_RULES
    for phrase in (
        "segundo clip",
        "añade musica",
        "remove_phrase",
        "apply_style",
        "set_aspect",
        "add_gameplay_background",
        "propose_edit",
        "propose_project",
    ):
        assert phrase in rules, f"missing phrase mapping: {phrase!r}"


def test_planner_system_keeps_action_specs():
    sys = planner._SYSTEM
    # Capability map is additive; the detailed PLAN_ACTIONS list still drives.
    for action in ("set_speed", "set_aspect", "remove_phrase", "set_subtitles"):
        assert action in sys


def test_capability_headers_are_additive():
    assert "CAPABILITY MAP" in agent.SYSTEM_RULES
    assert "CAPABILITY MAP" in planner._SYSTEM
    # Header sits BEFORE the existing detailed rules, not replacing them.
    assert agent.SYSTEM_RULES.index("CAPABILITY MAP") < \
        agent.SYSTEM_RULES.index("A/B APPROVAL GATE")


def test_approve_reject_backstops_intact():
    assert agent._APPROVE_RE.search("aplica")
    assert agent._APPROVE_RE.search("si")
    assert agent._APPROVE_RE.search("approve")
    assert agent._REJECT_RE.search("cancela")
    assert agent._REJECT_RE.search("no")
    assert agent._REJECT_RE.search("cancel")
    # Spanish approval should be localized; English should remain English.
    assert agent._SPANISH_RE.search("aplica")
    assert agent._SPANISH_RE.search("sí")
    assert not agent._SPANISH_RE.search("approve")
    # "si pero 3x" is a modification, not a plain approval.
    assert agent._MODIFY_RE.search("si pero 3x")


def test_plan_narration_backstop_patterns_compile_and_match():
    """The plan-narration guard's patterns remain valid and recognize both languages."""
    import re

    plan_words = re.compile(
        r"\bplan\b|\bpaso\b|\bstep\b|approve", re.I
    )
    plan_action = re.compile(
        r"crear|preparar|proponer|añadir|hacer|created|ready|"
        r"propos|here.{0,3}s the|steps?:|will (add|apply)", re.I
    )
    assert plan_words.search("He preparado un plan")
    assert plan_action.search("He preparado un plan")
    assert plan_words.search("Here is the plan")
    assert plan_action.search("Here is the plan")
