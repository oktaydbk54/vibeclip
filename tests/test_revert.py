"""Per-action revert + regenerate in the chat thread.

Named checkpoints on top of the existing LIFO history: apply_plan tags its
pre-plan snapshot, revert_plan pops to that tag, regenerate_plan re-proposes
the same instruction with a 'do it differently' nudge. No LLM in these tests
(planner.propose is monkeypatched for the regenerate path).
"""

import pytest

from chat import tools
from chat.session import Session


def _make_session(tmp_path):
    data = {
        "source": {"path": "src.mp4", "duration": 30, "width": 1080,
                   "height": 1920, "fps": 30},
        "platform": "youtube_shorts",
        "clips": [{"id": 1, "title": "T", "start": 0.0, "end": 20.0,
                   "stages": [], "marker": "v0"}],
        "history": [],
    }
    return Session(data, tmp_path / "project.json")


# --------------------------------------------------------------- session core
def test_snapshot_tag_and_revert(tmp_path):
    sess = _make_session(tmp_path)
    sess.snapshot("plan: x", tag="abc")
    assert sess.data["history"][-1]["tag"] == "abc"
    # Mutate then revert: clips return to the pre-snapshot state.
    sess.data["clips"][0]["marker"] = "v1"
    sess.data["redo"] = [{"label": "stale", "clips": []}]
    msg = sess.revert_to_tag("abc")
    assert "Reverted" in msg
    assert sess.data["clips"][0]["marker"] == "v0"
    # The revert itself is recorded (undoable) as a fresh 'before revert' entry.
    assert sess.data["history"][-1]["label"] == "before revert"


def test_revert_to_missing_tag(tmp_path):
    sess = _make_session(tmp_path)
    msg = sess.revert_to_tag("nope")
    assert "not found" in msg


def test_revert_tolerates_legacy_entries(tmp_path):
    sess = _make_session(tmp_path)
    sess.data["history"] = [["bare", "legacy", "list"]]  # pre-tag format
    assert "not found" in sess.revert_to_tag("abc")


# --------------------------------------------------------------- apply_plan
def test_apply_plan_tags_and_records(tmp_path, monkeypatch):
    sess = _make_session(tmp_path)

    def _step(session, clip_id):
        session.clip(clip_id)["marker"] = "edited"
        return {"ok": True}

    monkeypatch.setitem(tools.REGISTRY, "_t", _step)
    sess.data["pending_plan"] = {
        "clip_id": 1, "instruction": "make it punchy", "summary": "s",
        "steps": [{"action": "_t", "args": {"clip_id": 1}}]}
    res = tools.apply_plan(sess)
    assert res["ok"] and res["checkpoint"]
    cp = res["checkpoint"]
    assert sess.data["clips"][0]["marker"] == "edited"
    # checkpoint recorded both on the history tag and in applied_plans.
    assert sess.data["history"][-1]["tag"] == cp
    assert sess.data["applied_plans"][-1]["checkpoint"] == cp
    assert sess.last_applied["checkpoint"] == cp


def test_revert_plan_empty_checkpoint_targets_last(tmp_path, monkeypatch):
    sess = _make_session(tmp_path)

    def _step(session, clip_id):
        session.clip(clip_id)["marker"] = "edited"
        return {"ok": True}

    monkeypatch.setitem(tools.REGISTRY, "_t", _step)
    sess.data["pending_plan"] = {
        "clip_id": 1, "instruction": "x", "summary": "",
        "steps": [{"action": "_t", "args": {"clip_id": 1}}]}
    tools.apply_plan(sess)
    # A stale pending plan over the reverted state must be cleared.
    sess.data["pending_plan"] = {"clip_id": 1, "instruction": "y", "steps": []}
    res = tools.revert_plan(sess)  # empty checkpoint -> most recent
    assert res["ok"]
    assert sess.data["clips"][0]["marker"] == "v0"
    assert sess.data["pending_plan"] is None


def test_revert_plan_no_history(tmp_path):
    sess = _make_session(tmp_path)
    assert not tools.revert_plan(sess)["ok"]  # nothing applied


# --------------------------------------------------------------- regenerate
def test_regenerate_plan_nudge_and_replace(tmp_path, monkeypatch):
    sess = _make_session(tmp_path)

    def _step(session, clip_id):
        session.clip(clip_id)["marker"] = "edited"
        return {"ok": True}

    monkeypatch.setitem(tools.REGISTRY, "_t", _step)
    sess.data["pending_plan"] = {
        "clip_id": 1, "instruction": "make it punchy", "summary": "",
        "steps": [{"action": "_t", "args": {"clip_id": 1, "k": 1}}]}
    tools.apply_plan(sess)

    captured = {}

    def _fake_propose(session, clip_id, instruction, extra_note=""):
        captured["instruction"] = instruction
        captured["extra_note"] = extra_note
        return {"clip_id": clip_id, "instruction": instruction,
                "summary": "different", "steps": []}

    monkeypatch.setattr("chat.planner.propose", _fake_propose)
    # Avoid an actual preview render path.
    monkeypatch.setattr(tools, "_render_plan_preview", lambda s, p: None)

    res = tools.regenerate_plan(sess)
    assert res["ok"]
    # Clean instruction preserved; nudge carries the previous steps.
    assert captured["instruction"] == "make it punchy"
    assert "different approach" in captured["extra_note"]
    assert "_t" in captured["extra_note"]
    # Reverted before re-proposing, and the new plan is now pending.
    assert sess.data["clips"][0]["marker"] == "v0"
    assert sess.data["pending_plan"]["summary"] == "different"


# --------------------------------------------------------------- wiring parity
def test_tool_specs_registry_parity():
    names = {s["function"]["name"] for s in tools.TOOL_SPECS}
    assert "revert_plan" in names and "regenerate_plan" in names
    assert "revert_plan" in tools.REGISTRY
    assert "regenerate_plan" in tools.REGISTRY
    # Must NOT be gated behind the A/B propose_edit flow.
    assert "revert_plan" not in tools.MUTATING_TOOLS
    assert "regenerate_plan" not in tools.MUTATING_TOOLS


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
