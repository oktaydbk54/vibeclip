"""Headless automated repurposing (Faz 3.2). All heavy collaborators (download,
session, REGISTRY tools) are mocked — asserts the orchestration: ingest →
generate_clips → per-clip optional steps → export → manifest, with per-clip
failures recorded but never sinking the batch.
"""

from pathlib import Path

import pytest

from chat import repurpose, tools
from chat.session import Session


class _FakeSession:
    def __init__(self, n=2):
        self.data = {"clips": [{"id": i, "title": f"c{i}", "status": "pending",
                                "current": f"/c{i}.mp4"} for i in range(1, n + 1)]}
        self.path = Path("/sessions/proj/project.json")

    def clip(self, cid):
        return next(c for c in self.data["clips"] if c["id"] == cid)


@pytest.fixture
def wired(monkeypatch):
    sess = _FakeSession(2)
    monkeypatch.setattr(repurpose, "_resolve_source",
                        lambda src: Path("/video.mp4"))
    monkeypatch.setattr(Session, "load_or_create",
                        classmethod(lambda cls, p, **k: sess))
    monkeypatch.setattr(tools, "generate_clips",
                        lambda s, **k: {"ok": True})
    calls = []
    for name in ("apply_style", "add_broll", "set_caption_language", "set_dub"):
        monkeypatch.setattr(tools, name,
                            (lambda nm: (lambda s, cid, *a, **k:
                             calls.append((nm, cid)) or {"ok": True}))(name))
    monkeypatch.setattr(tools, "export_clip",
                        lambda s, cid, **k: calls.append(("export", cid))
                        or {"ok": True, "file": f"/out{cid}.mp4"})
    return sess, calls


def test_repurpose_minimal_export_only(wired):
    sess, calls = wired
    res = repurpose.auto_repurpose("/video.mp4")
    assert res["ok"] is True
    assert res["count"] == 2 and res["project"] == "proj"
    assert [c["file"] for c in res["clips"]] == ["/out1.mp4", "/out2.mp4"]
    # only export ran (no style/broll/caption/dub requested).
    assert calls == [("export", 1), ("export", 2)]


def test_repurpose_full_pipeline_order(wired):
    sess, calls = wired
    res = repurpose.auto_repurpose(
        "/video.mp4", style="hormozi", generate_broll=True,
        caption_language="Spanish", dub_language="French")
    assert res["ok"] is True
    # per clip: style, broll, caption, dub, export — in that order.
    assert calls[:5] == [("apply_style", 1), ("add_broll", 1),
                         ("set_caption_language", 1), ("set_dub", 1),
                         ("export", 1)]


def test_repurpose_records_step_errors_without_aborting(monkeypatch, wired):
    sess, calls = wired
    monkeypatch.setattr(tools, "apply_style",
                        lambda s, cid, *a, **k: {"ok": False, "error": "bad style"})
    res = repurpose.auto_repurpose("/video.mp4", style="nope")
    assert res["ok"] is True            # batch still completes
    assert any("bad style" in e for e in res["errors"])
    assert ("export", 1) in calls       # later steps still ran


def test_repurpose_generate_clips_failure_aborts(monkeypatch, wired):
    sess, calls = wired
    monkeypatch.setattr(tools, "generate_clips",
                        lambda s, **k: {"ok": False, "error": "no highlights"})
    res = repurpose.auto_repurpose("/video.mp4")
    assert res["ok"] is False
    assert "no highlights" in res["error"]


def test_repurpose_bad_source(monkeypatch):
    def boom(src):
        raise FileNotFoundError("missing.mp4")
    monkeypatch.setattr(repurpose, "_resolve_source", boom)
    res = repurpose.auto_repurpose("missing.mp4")
    assert res["ok"] is False and "missing.mp4" in res["error"]


def test_is_url():
    assert repurpose._is_url("https://youtu.be/x")
    assert repurpose._is_url("http://x")
    assert not repurpose._is_url("/local/path.mp4")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
