"""edit_event / delete_event are exposed to the LLM and gated.

The impls already lived in REGISTRY; this guards that they now have TOOL_SPECs,
that they sit behind the A/B gate (MUTATING_TOOLS), that mutating one event by
index works, and that Session.summary() surfaces per-stage 0-based indices so
the model can address "the second zoom".
"""

import pytest

from chat import tools
from chat.session import Session


class _StubSession:
    """Session stand-in for edit_event/delete_event: one clip with a zoom
    stage of two windows; set_stage records the params it was handed."""

    def __init__(self):
        self._clip = {
            "id": 1,
            "stages": [
                {"name": "zoom", "params": {"windows": [
                    [3.1, 4.6, 1.18, "center"],
                    [12.0, 13.5, 1.3, "left"],
                ]}},
                {"name": "sfx", "params": {"events": [
                    {"time": 8.0, "kind": "ding", "volume": 0.5},
                ]}},
            ],
        }
        self.data = {"source": {"fps": 30}}
        self.last_notes = None
        self.set_stage_calls = []
        self.snapshots = []

    def clip(self, clip_id):
        if clip_id != 1:
            raise ValueError(f"No clip #{clip_id}.")
        return self._clip

    def snapshot(self, label=""):
        self.snapshots.append(label)

    def set_stage(self, clip_id, stage, params):
        self.set_stage_calls.append((clip_id, stage, params))
        st = next(s for s in self._clip["stages"] if s["name"] == stage)
        st["params"] = params
        return f"{stage}.mp4"


def test_event_tools_wired_and_gated():
    names = {s["function"]["name"] for s in tools.TOOL_SPECS}
    for name in ("edit_event", "delete_event"):
        assert name in names, f"{name} missing a TOOL_SPEC"
        assert name in tools.REGISTRY
        assert name in tools.MUTATING_TOOLS


def test_event_specs_use_stage_enum():
    spec = next(s["function"] for s in tools.TOOL_SPECS
                if s["function"]["name"] == "edit_event")
    stage = spec["parameters"]["properties"]["stage"]
    assert set(stage["enum"]) == set(tools._EDITABLE_EVENTS)
    assert spec["parameters"]["required"] == ["clip_id", "stage", "index"]


def test_edit_event_mutates_one_window_by_index():
    sess = _StubSession()
    res = tools.REGISTRY["edit_event"](
        sess, clip_id=1, stage="zoom", index=1, start=20.0, end=22.0,
        value=1.5)
    assert res["ok"] is True
    assert sess.snapshots  # took an undo point
    windows = sess.set_stage_calls[-1][2]["windows"]
    assert windows[0] == [3.1, 4.6, 1.18, "center"]  # untouched
    assert windows[1][0] == pytest.approx(20.0, abs=0.05)  # start moved (snap)
    assert windows[1][2] == 1.5  # strength retuned


def test_delete_event_removes_one_window_by_index():
    sess = _StubSession()
    res = tools.REGISTRY["delete_event"](sess, clip_id=1, stage="zoom", index=0)
    assert res["ok"] is True
    assert res["remaining"] == 1
    windows = sess.set_stage_calls[-1][2]["windows"]
    assert len(windows) == 1
    assert windows[0][0] == 12.0  # the survivor is the former index 1


def test_edit_event_index_out_of_range():
    sess = _StubSession()
    res = tools.REGISTRY["edit_event"](sess, clip_id=1, stage="zoom", index=9)
    assert res["ok"] is False
    assert not sess.set_stage_calls


def test_summary_surfaces_event_indices(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sess = Session.__new__(Session)
    sess.data = {
        "source": {"path": "src.mp4", "duration": 30, "width": 1080,
                   "height": 1920, "fps": 30},
        "platform": "youtube_shorts",
        "clips": [{
            "id": 1, "title": "T", "start": 0.0, "end": 20.0,
            "stages": [
                {"name": "zoom", "params": {"windows": [
                    [3.1, 4.6, 1.18, "center"],
                    [12.0, 13.5, 1.3, "left"],
                ]}},
                {"name": "sfx", "params": {"events": [
                    {"time": 8.0, "kind": "ding"},
                ]}},
            ],
        }],
    }
    monkeypatch.setattr(Session, "active_clip_id", lambda self: None)
    text = sess.summary()
    assert "[0]" in text and "[1]" in text
    assert "3.1-4.6s" in text


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
