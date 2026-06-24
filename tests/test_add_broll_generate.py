"""add_broll generate=true (Faz 1): AI-generated footage as b-roll.

Verifies the wiring — generation gated on a configured key, the generated file
normalized to the clip frame like a local asset, and the prompt/model/seed
stored on the event (Palmier-style per-clip provenance for re-tuning).
"""

import pytest

from chat import tools
from pipeline import broll, config, genmedia


class _FakeSession:
    def __init__(self):
        self._clip = {"id": 1, "current": "/x.mp4",
                      "stages": [{"name": "cut", "params": {},
                                  "output": "/x.mp4"}]}
        self.snaps = []
        self.set_calls = []

    def clip(self, cid):
        if cid != 1:
            raise ValueError(f"No clip #{cid}.")
        return self._clip

    def words_for(self, clip):
        return [{"start": 0.0, "end": 5.0, "word": "hi"}]

    def speed_factor(self, clip):
        return 1.0

    def snapshot(self, label="", **kw):
        self.snaps.append(label)

    @property
    def last_notes(self):
        return []

    def set_stage(self, cid, name, params):
        self.set_calls.append((name, params))
        return "/out.mp4"


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(tools, "_frame_of", lambda clip: (1080, 1920, 30.0))
    monkeypatch.setattr(config, "GENMEDIA_API_KEY", "k")
    monkeypatch.setattr(config, "GENMEDIA_VIDEO_MODEL", "vendor/model-x")
    monkeypatch.setattr(config, "GENMEDIA_PROVIDER", "fal")
    monkeypatch.setattr(broll, "normalize_media",
                        lambda path, **kw: "/norm.mp4")
    return _FakeSession()


def test_generate_requires_key(monkeypatch, patched):
    monkeypatch.setattr(config, "GENMEDIA_API_KEY", "")
    res = tools.add_broll(patched, 1, auto=False, query="ocean waves",
                          start=4.0, end=7.0, generate=True)
    assert res["ok"] is False
    assert "GENMEDIA_API_KEY" in res["error"]


def test_generate_stores_provenance_on_event(monkeypatch, patched):
    seen = {}

    def fake_gen(prompt, **kw):
        seen.update(prompt=prompt, kw=kw)
        return "/raw_gen.mp4"

    monkeypatch.setattr(genmedia, "generate_video", fake_gen)

    res = tools.add_broll(patched, 1, auto=False, query="ocean waves at sunset",
                          start=4.0, end=7.0, generate=True, seed=42)
    assert res["ok"] is True
    # generation got the query as prompt + clip dims + the locked seed.
    assert seen["prompt"] == "ocean waves at sunset"
    assert seen["kw"]["seed"] == 42 and seen["kw"]["width"] == 1080
    # the broll stage was set with one event carrying gen provenance.
    name, params = patched.set_calls[-1]
    assert name == "broll"
    ev = params["events"][-1]
    assert ev["path"] == "/norm.mp4"          # normalized to the clip frame
    assert ev["gen"]["prompt"] == "ocean waves at sunset"
    assert ev["gen"]["model"] == "vendor/model-x"
    assert ev["gen"]["seed"] == 42


def test_generate_failure_reports_could_not_generate(monkeypatch, patched):
    monkeypatch.setattr(genmedia, "generate_video", lambda prompt, **kw: None)
    res = tools.add_broll(patched, 1, auto=False, query="nope",
                          start=4.0, end=7.0, generate=True)
    assert res["ok"] is False
    assert "generate footage" in res["error"]


def test_seed_negative_means_unlocked(monkeypatch, patched):
    seen = {}
    monkeypatch.setattr(genmedia, "generate_video",
                        lambda prompt, **kw: seen.update(kw=kw) or "/raw.mp4")
    tools.add_broll(patched, 1, auto=False, query="city", start=4.0, end=7.0,
                    generate=True, seed=-1)
    assert seen["kw"]["seed"] is None


# ------------------------------------------------------------- multicam (3.1)

class _MultiSession(_FakeSession):
    """Two-clip session so one clip's footage can overlay another."""

    def __init__(self):
        super().__init__()
        self._clips = {
            1: self._clip,
            2: {"id": 2, "current": "/clip2.mp4",
                "stages": [{"name": "cut", "params": {}, "output": "/clip2.mp4"}]},
        }

    def clip(self, cid):
        if cid not in self._clips:
            raise ValueError(f"No clip #{cid}.")
        return self._clips[cid]


def test_source_ref_overlays_other_clip(monkeypatch, patched):
    sess = _MultiSession()
    monkeypatch.setattr(tools, "_frame_of", lambda clip: (1080, 1920, 30.0))
    monkeypatch.setattr(broll, "normalize_media", lambda path, **kw: "/norm.mp4")
    # clip #2's footage must exist on disk for the multicam resolve.
    monkeypatch.setattr(tools.Path, "exists", lambda self: True)

    res = tools.add_broll(sess, 1, auto=False, start=4.0, end=7.0, source_ref=2)
    assert res["ok"] is True
    name, params = sess.set_calls[-1]
    ev = params["events"][-1]
    assert ev["source_ref"] == 2
    assert ev["query"] == "clip2" and ev["path"] == "/norm.mp4"


def test_source_ref_self_rejected(monkeypatch, patched):
    sess = _MultiSession()
    monkeypatch.setattr(tools, "_frame_of", lambda clip: (1080, 1920, 30.0))
    res = tools.add_broll(sess, 1, auto=False, start=4.0, end=7.0, source_ref=1)
    assert res["ok"] is False and "DIFFERENT" in res["error"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
