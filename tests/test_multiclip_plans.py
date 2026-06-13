"""multiclip_plans: project-scope plans that span several clips under ONE undo.

pending_plan now stores EITHER today's single plan dict OR a project-scope
composite ({'scope':'project','plans':[...]}); Session.pending_plans() normalizes
both to a list so every reader stays uniform. apply_plan iterates the list under
a SINGLE tagged snapshot so one undo / one revert_plan reverts the whole
multi-clip op atomically. Single-clip behaviour is unchanged (a 1-element list).

The render engine is sidestepped by registering a fake action into tools.REGISTRY
so apply_plan's atomicity/record logic is exercised in isolation.
"""

from pathlib import Path

import pytest

from chat import tools
from chat.session import Session


def _session(tmp_path: Path) -> Session:
    data = {
        "version": 1, "name": "t",
        "source": {"path": str(tmp_path / "src.mp4"), "width": 1920,
                   "height": 1080, "duration": 30.0, "fps": 30},
        "platform": "youtube_shorts",
        "clips": [
            {"id": 1, "title": "c1", "start": 0.0, "end": 5.0,
             "status": "pending", "stages": [{"name": "cut", "params": {}}],
             "current": None, "marks": []},
            {"id": 2, "title": "c2", "start": 5.0, "end": 10.0,
             "status": "pending", "stages": [{"name": "cut", "params": {}}],
             "current": None, "marks": []},
        ],
        "history": [],
    }
    return Session(data, tmp_path / "project.json")


# --------------------------------------------------------------------------
# pending_plans() normalization
# --------------------------------------------------------------------------

def test_pending_plans_normalizes_single_and_composite(tmp_path):
    sess = _session(tmp_path)
    # Nothing pending -> empty list.
    assert sess.pending_plans() == []
    assert sess.pending_plan_is_project() is False

    # Single plan dict -> [plan], NOT flagged project.
    single = {"clip_id": 1, "instruction": "x", "steps": [{"action": "a"}]}
    sess.data["pending_plan"] = single
    assert sess.pending_plans() == [single]
    assert sess.pending_plan_is_project() is False

    # Project composite -> its plan list, flagged project.
    p1 = {"clip_id": 1, "instruction": "tighten", "steps": [{"action": "a"}]}
    p2 = {"clip_id": 2, "instruction": "tighten", "steps": [{"action": "a"}]}
    sess.data["pending_plan"] = {"scope": "project", "instruction": "tighten",
                                 "plans": [p1, p2]}
    assert sess.pending_plans() == [p1, p2]
    assert sess.pending_plan_is_project() is True


# --------------------------------------------------------------------------
# apply_plan atomicity across a multi-clip composite
# --------------------------------------------------------------------------

@pytest.fixture
def _fake_action(monkeypatch):
    """Register a no-op success action that records (clip_id, value) calls."""
    calls: list[tuple] = []

    def _touch(session, clip_id, value=0):
        calls.append((clip_id, value))
        return {"ok": True, "clip_id": clip_id}

    monkeypatch.setitem(tools.REGISTRY, "_touch", _touch)
    return calls


def test_apply_composite_is_one_snapshot_and_one_record(tmp_path, _fake_action):
    sess = _session(tmp_path)
    p1 = {"clip_id": 1, "instruction": "tighten every clip",
          "steps": [{"action": "_touch", "args": {"clip_id": 1, "value": 1}}]}
    p2 = {"clip_id": 2, "instruction": "tighten every clip",
          "steps": [{"action": "_touch", "args": {"clip_id": 2, "value": 2}},
                    {"action": "_touch", "args": {"clip_id": 2, "value": 3}}]}
    sess.data["pending_plan"] = {"scope": "project",
                                 "instruction": "tighten every clip",
                                 "summary": "s", "plans": [p1, p2]}

    n_history = len(sess.data["history"])
    res = tools.apply_plan(sess)

    assert res["ok"] is True
    # Every step across BOTH clips ran.
    assert _fake_action == [(1, 1), (2, 2), (2, 3)]
    assert len(res["applied"]) == 3
    assert res["failed_at"] is None
    # Per-step clip attribution for the carousel/per-action revert.
    assert [e["clip_id"] for e in res["applied"]] == [1, 2, 2]
    # ONE undo snapshot for the whole composite (atomic multi-clip undo).
    assert len(sess.data["history"]) == n_history + 1
    # ONE applied-plan record covering all clips, tagged with the checkpoint.
    records = sess.data["applied_plans"]
    assert len(records) == 1
    rec = records[0]
    assert rec["scope"] == "project"
    assert rec["clip_ids"] == [1, 2]
    assert rec["checkpoint"] == res["checkpoint"]
    # The snapshot carries the same checkpoint tag for revert_plan.
    assert sess.data["history"][-1]["tag"] == res["checkpoint"]
    # Pending plan cleared after apply.
    assert sess.data["pending_plan"] is None


def test_apply_composite_failure_still_one_undo_entry(tmp_path, monkeypatch):
    """A failure mid-clip stops the batch but still leaves exactly ONE undo
    entry and ONE record (atomicity invariant)."""
    sess = _session(tmp_path)

    def _boom(session, clip_id):
        return {"ok": False, "error": "nope"}

    monkeypatch.setitem(tools.REGISTRY, "_boom", _boom)
    sess.data["pending_plan"] = {
        "scope": "project", "instruction": "x", "plans": [
            {"clip_id": 1, "steps": [{"action": "_boom",
                                      "args": {"clip_id": 1}}]},
            {"clip_id": 2, "steps": [{"action": "_boom",
                                      "args": {"clip_id": 2}}]},
        ]}
    n_history = len(sess.data["history"])
    res = tools.apply_plan(sess)
    assert res["failed_at"] == 1  # stopped at the first failing step
    assert len(sess.data["history"]) == n_history + 1
    assert len(sess.data["applied_plans"]) == 1


# --------------------------------------------------------------------------
# single-clip regression: apply_plan over a bare dict is unchanged
# --------------------------------------------------------------------------

def test_apply_single_plan_back_compat(tmp_path, _fake_action):
    sess = _session(tmp_path)
    sess.data["pending_plan"] = {
        "clip_id": 1, "instruction": "punchier", "summary": "s",
        "steps": [{"action": "_touch", "args": {"clip_id": 1, "value": 9}}]}

    n_history = len(sess.data["history"])
    res = tools.apply_plan(sess)

    assert res["ok"] is True
    assert _fake_action == [(1, 9)]
    assert len(res["applied"]) == 1
    # No per-clip attribution / scope on a single plan (byte-identical shape).
    assert "clip_id" not in res["applied"][0]
    assert len(sess.data["history"]) == n_history + 1
    rec = sess.data["applied_plans"][0]
    assert "scope" not in rec
    assert rec["clip_id"] == 1
    assert sess.data["pending_plan"] is None


# --------------------------------------------------------------------------
# planner.propose_project assembles a composite by looping the single-clip path
# --------------------------------------------------------------------------

def test_propose_project_assembles_composite(tmp_path, monkeypatch):
    from chat import planner

    sess = _session(tmp_path)

    def _fake_propose(session, clip_id, instruction, extra_note=""):
        return {"clip_id": clip_id, "instruction": instruction,
                "summary": "", "steps": [{"action": "_touch",
                                          "args": {"clip_id": clip_id}}]}

    monkeypatch.setattr(planner, "propose", _fake_propose)
    composite = planner.propose_project(sess, "tighten every clip")
    assert composite["scope"] == "project"
    assert [p["clip_id"] for p in composite["plans"]] == [1, 2]
    assert composite["instruction"] == "tighten every clip"


def test_propose_project_skips_failing_clips_but_keeps_others(tmp_path,
                                                              monkeypatch):
    from chat import planner

    sess = _session(tmp_path)

    def _fake_propose(session, clip_id, instruction, extra_note=""):
        if clip_id == 1:
            raise ValueError("picture-locked")
        return {"clip_id": clip_id, "instruction": instruction,
                "summary": "", "steps": [{"action": "_touch",
                                          "args": {"clip_id": clip_id}}]}

    monkeypatch.setattr(planner, "propose", _fake_propose)
    composite = planner.propose_project(sess, "tighten")
    assert [p["clip_id"] for p in composite["plans"]] == [2]
    assert composite["skipped"] and "#1" in composite["skipped"][0]


def test_propose_project_raises_when_no_clip_succeeds(tmp_path, monkeypatch):
    from chat import planner

    sess = _session(tmp_path)
    monkeypatch.setattr(
        planner, "propose",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("nope")))
    with pytest.raises(ValueError):
        planner.propose_project(sess, "tighten")
