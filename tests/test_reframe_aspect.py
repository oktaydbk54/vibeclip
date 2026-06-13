"""Chatbot-controllable reframe + multi-aspect-ratio export.

build_reframe_vf must keep the historical 1080x1920 9:16 output byte-for-byte
and emit the right canvas for 1:1 / 16:9; the set_aspect tool must snapshot,
flip the reframe param and yield a DISTINCT param-keyed artifact; and
export_clip(aspect=...) must replay per-ratio WITHOUT mutating the stored
recipe. ffprobe/ffmpeg are mocked exactly as the engine boundary allows."""

from pathlib import Path

from chat.session import Session
from chat.tools import export_clip, set_aspect
from pipeline import reframe as reframe_mod
from pipeline import tracking


def _mk_clip(tmp_path: Path) -> Path:
    out = tmp_path / "clip01_reframe_seed.mp4"
    out.write_bytes(b"seed")
    return out


def test_build_reframe_vf_canvas_per_aspect(monkeypatch):
    # Portrait-ish source so crop_w >= w -> the simple center-crop path runs
    # (no cv2 face detection / scene classification needed).
    monkeypatch.setattr(tracking, "ffprobe_info",
                        lambda p: {"width": 1080, "height": 1920,
                                   "duration": 5.0})

    vf_916 = tracking.build_reframe_vf("x.mp4")  # default 9:16
    assert "scale=1080:1920" in vf_916
    assert "pad=1080:1920" in vf_916

    vf_11 = tracking.build_reframe_vf("x.mp4", aspect="1:1")
    assert "scale=1080:1080" in vf_11
    assert "pad=1080:1080" in vf_11

    vf_169 = tracking.build_reframe_vf("x.mp4", aspect="16:9")
    assert "scale=1920:1080" in vf_169
    assert "pad=1920:1080" in vf_169


def test_aspects_table_default_unchanged():
    # 9:16 must stay the historical 1080x1920 contract.
    assert tracking.ASPECTS["9:16"] == (9 / 16, 1080, 1920)
    assert reframe_mod.ASPECTS["9:16"] == (9 / 16, 1080, 1920)


def _session(tmp_path: Path) -> Session:
    seed = _mk_clip(tmp_path)
    data = {
        "version": 1, "name": "t",
        "source": {"path": str(tmp_path / "src.mp4"), "width": 1920,
                   "height": 1080, "duration": 10.0},
        "platform": "youtube_shorts",
        "clips": [{
            "id": 1, "title": "c1", "start": 0.0, "end": 5.0,
            "status": "ready",
            "stages": [
                {"name": "cut", "params": {"start": 0.0, "end": 5.0},
                 "output": str(seed)},
                {"name": "reframe", "params": {"tracked": True},
                 "output": str(seed)},
            ],
            "current": str(seed),
        }],
        "history": [],
    }
    return Session(data, tmp_path / "project.json")


def test_set_aspect_snapshots_and_keys_distinct_artifact(tmp_path, monkeypatch):
    sess = _session(tmp_path)

    # Stub the reframe render so it just writes the requested out_path.
    def _fake_tracked(inp, out_path=None, aspect="9:16"):
        Path(out_path).write_bytes(f"reframed-{aspect}".encode())
        return out_path
    monkeypatch.setattr("pipeline.tracking.reframe_vertical_tracked",
                        _fake_tracked)

    n_before = len(sess.data["history"])
    res = set_aspect(sess, 1, "1:1")
    assert res["ok"] is True
    assert res["aspect"] == "1:1"
    # snapshot pushed.
    assert len(sess.data["history"]) == n_before + 1
    # reframe param now carries the aspect.
    reframe = next(st for st in sess.clip(1)["stages"]
                   if st["name"] == "reframe")
    assert reframe["params"]["aspect"] == "1:1"
    # The 1:1 artifact name differs from the 9:16 one (param-keyed _out).
    out_11 = res["file"]
    res_916 = set_aspect(sess, 1, "9:16")
    assert res_916["ok"] is True
    assert res_916["file"] != out_11


def test_set_aspect_rejects_unknown(tmp_path):
    sess = _session(tmp_path)
    res = set_aspect(sess, 1, "4:3")
    assert res["ok"] is False
    assert "aspect" in res["error"]


def test_export_clip_replays_per_aspect_without_mutating_recipe(
        tmp_path, monkeypatch):
    sess = _session(tmp_path)

    def _fake_cut(footage, start, end, **kw):
        out = kw["out_path"]
        Path(out).write_bytes(b"cut")
        return out
    monkeypatch.setattr("pipeline.cut.cut_clip", _fake_cut)

    def _fake_tracked(inp, out_path=None, aspect="9:16"):
        Path(out_path).write_bytes(f"reframed-{aspect}".encode())
        return out_path
    monkeypatch.setattr("pipeline.tracking.reframe_vertical_tracked",
                        _fake_tracked)
    monkeypatch.setattr("pipeline.media.ffprobe_info",
                        lambda p: {"width": 1080, "height": 1080})

    res = export_clip(sess, 1, aspect="1:1")
    assert res["ok"] is True
    assert res["aspect"] == "1:1"
    # The stored editable recipe is untouched (no aspect pinned permanently).
    reframe = next(st for st in sess.clip(1)["stages"]
                   if st["name"] == "reframe")
    assert "aspect" not in reframe["params"]


def test_export_clip_rejects_unknown_aspect(tmp_path):
    sess = _session(tmp_path)
    res = export_clip(sess, 1, aspect="4:3")
    assert res["ok"] is False
