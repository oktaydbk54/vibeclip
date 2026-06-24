"""MCP bridge: the session-aware editing REGISTRY exposed over MCP.

Guards the contract external agents (Claude Code / Cursor / Codex) rely on:
every non-interactive REGISTRY tool is registered with a `project`-first schema,
the human-in-the-loop plan tools stay OUT of the surface, dispatch routes args
through to the real impls, and a failing edit restores project.json verbatim.
"""

import copy
import inspect

import pytest

from chat import mcp_bridge as B
from chat import tools


# --------------------------------------------------------------- registration

def _registered_names():
    """Names the bridge would register (excludes the 3 project helpers)."""
    return {s["function"]["name"] for s in tools.TOOL_SPECS
            if s["function"]["name"] not in B._EXCLUDE
            and s["function"]["name"] in tools.REGISTRY}


def test_excluded_plan_tools_are_not_exposed():
    names = _registered_names()
    # The interactive approval flow must not leak onto the agent surface.
    for blocked in ("propose_edit", "apply_plan", "ask_user", "set_autonomy"):
        assert blocked not in names
    # ...but the direct editing/export tools must be present.
    for present in ("generate_clips", "set_cut", "add_zoom", "add_broll",
                    "set_dub", "export_clip", "undo"):
        assert present in names


def test_every_registered_tool_has_an_impl():
    for name in _registered_names():
        assert name in tools.REGISTRY, f"{name} registered with no impl"


def test_signature_puts_project_first_and_marks_required():
    spec = next(s for s in tools.TOOL_SPECS
                if s["function"]["name"] == "add_zoom")
    sig = B._signature(spec)
    params = list(sig.parameters)
    assert params[0] == "project"
    assert sig.parameters["project"].default is inspect.Parameter.empty
    # add_zoom requires clip_id + time; strength/motion are optional (-> None).
    assert sig.parameters["clip_id"].default is inspect.Parameter.empty
    assert sig.parameters["time"].default is inspect.Parameter.empty
    assert sig.parameters["motion"].default is None


def test_wrapper_strips_omitted_optionals(monkeypatch):
    seen = {}

    def fake_run(project, name, args):
        seen.update(project=project, name=name, args=args)
        return {"ok": True}

    monkeypatch.setattr(B, "run_tool", fake_run)
    spec = next(s for s in tools.TOOL_SPECS
                if s["function"]["name"] == "add_zoom")
    wrapper = B._make_wrapper("add_zoom", spec)
    # FastMCP passes every model field, defaulting omitted optionals to None.
    wrapper(project="p1", clip_id=2, time=1.5, strength=None, motion=None,
            duration=None)
    assert seen["name"] == "add_zoom"
    assert seen["project"] == "p1"
    # None-valued optionals dropped so the impl keeps its own defaults.
    assert seen["args"] == {"clip_id": 2, "time": 1.5}


# ------------------------------------------------------------------- dispatch

class _FakeSession:
    def __init__(self):
        self.data = {"clips": [{"id": 1}], "n": 0}
        self.saved = 0

    def save(self):
        self.saved += 1


def test_run_tool_unknown_tool():
    assert B.run_tool("p", "no_such_tool", {})["ok"] is False


def test_run_tool_missing_project(monkeypatch):
    res = B.run_tool("ghost_project_xyz", "list_clips", {})
    assert res["ok"] is False
    assert "ghost_project_xyz" in res["error"]


def test_run_tool_dispatches_and_passes_args(monkeypatch):
    sess = _FakeSession()
    monkeypatch.setattr(B, "_resolve", lambda project: sess)

    def impl(session, clip_id, factor):
        session.data["n"] = (clip_id, factor)
        return {"ok": True, "applied": True}

    monkeypatch.setitem(tools.REGISTRY, "_probe_ok", impl)
    res = B.run_tool("p", "_probe_ok", {"clip_id": 1, "factor": 2.0})
    assert res == {"ok": True, "applied": True}
    assert sess.data["n"] == (1, 2.0)


def test_run_tool_restores_backup_on_error(monkeypatch):
    sess = _FakeSession()
    original = copy.deepcopy(sess.data)
    monkeypatch.setattr(B, "_resolve", lambda project: sess)

    def boom(session, **kw):
        session.data["n"] = "mutated"  # partial mutation before failing
        raise ValueError("kaboom")

    monkeypatch.setitem(tools.REGISTRY, "_probe_boom", boom)
    res = B.run_tool("p", "_probe_boom", {})
    assert res["ok"] is False
    assert "kaboom" in res["error"]
    assert sess.data == original          # rolled back
    assert sess.saved == 1                # persisted the rollback


def test_non_dict_result_is_wrapped(monkeypatch):
    sess = _FakeSession()
    monkeypatch.setattr(B, "_resolve", lambda project: sess)
    monkeypatch.setitem(tools.REGISTRY, "_probe_str", lambda session: "hello")
    res = B.run_tool("p", "_probe_str", {})
    assert res == {"ok": True, "result": "hello"}


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
