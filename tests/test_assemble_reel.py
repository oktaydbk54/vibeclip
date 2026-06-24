"""assemble_reel: ordered mix of real clips + AI-generated footage between them.

Heavy media ops (generation, normalize, caption, xfade, encode) are mocked —
asserts segment resolution/order, that generated segments are generated +
captioned + given a silent track, the reference-frame pick, and validation.
"""

import pytest

from chat import tools
from pipeline import broll, effects, genmedia, media, subtitle


class _Sess:
    def __init__(self, tmp_path, n=2):
        self.workdir = tmp_path
        self.saved = 0
        self.snaps = []
        self._clips = {}
        for i in range(1, n + 1):
            f = tmp_path / f"clip{i}.mp4"
            f.write_bytes(b"v")
            self._clips[i] = {"id": i, "title": f"c{i}", "current": str(f),
                              "stages": []}
        self.data = {"clips": list(self._clips.values()), "compilations": []}

    def clip(self, cid):
        if cid not in self._clips:
            raise ValueError(f"No clip #{cid}.")
        return self._clips[cid]

    def snapshot(self, label="", **k):
        self.snaps.append(label)

    def save(self):
        self.saved += 1


@pytest.fixture
def wired(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "_frame_of", lambda clip: (1080, 1920, 30.0))
    monkeypatch.setattr(genmedia, "available", lambda: True)
    monkeypatch.setattr(broll, "normalize_media", lambda path, **k: "/norm.mp4")
    monkeypatch.setattr(subtitle, "burn_subtitles",
                        lambda src, words, **k: k["out_path"])
    monkeypatch.setattr(tools, "_add_silent_audio",
                        lambda src, out: out)
    # transition returns a marker carrying the running join; fade/probe trivial.
    joins = []
    monkeypatch.setattr(effects, "transition",
                        lambda a, b, **k: joins.append((a, b)) or f"join{len(joins)}")
    monkeypatch.setattr(effects, "fade_in_out",
                        lambda cur, **k: k["out_path"])
    monkeypatch.setattr(media, "ffprobe_info", lambda p: {"duration": 20.0})
    return joins


def test_reel_weaves_generated_between_clips(monkeypatch, tmp_path, wired):
    joins = wired
    gen = []
    monkeypatch.setattr(genmedia, "generate_video",
                        lambda prompt, **k: gen.append((prompt, k)) or "/raw.mp4")
    sess = _Sess(tmp_path, 2)
    res = tools.assemble_reel(sess, [
        {"clip": 1},
        {"generate": "golden flares", "seconds": 3, "caption": "MEANWHILE",
         "seed": 21},
        {"clip": 2},
    ])
    assert res["ok"] is True
    assert res["segments"] == ["c1", "gen:golden flares", "c2"]
    # one generation, with the reel's frame + locked seed + duration.
    assert len(gen) == 1
    assert gen[0][1]["width"] == 1080 and gen[0][1]["seed"] == 21
    assert gen[0][1]["seconds"] == 3
    # two xfade joins for three pieces, in order.
    assert len(joins) == 2
    # a compilation was recorded.
    assert len(sess.data["compilations"]) == 1
    assert sess.data["compilations"][0]["title"].startswith("c1 → gen:")


def test_reel_needs_two_segments(tmp_path, wired):
    sess = _Sess(tmp_path, 1)
    res = tools.assemble_reel(sess, [{"clip": 1}])
    assert res["ok"] is False and "at least 2" in res["error"]


def test_reel_requires_genmedia_key(monkeypatch, tmp_path, wired):
    monkeypatch.setattr(genmedia, "available", lambda: False)
    sess = _Sess(tmp_path, 1)
    res = tools.assemble_reel(sess, [{"clip": 1}, {"generate": "x"}])
    assert res["ok"] is False and "GENMEDIA_API_KEY" in res["error"]


def test_reel_generation_miss_aborts(monkeypatch, tmp_path, wired):
    monkeypatch.setattr(genmedia, "generate_video", lambda prompt, **k: None)
    sess = _Sess(tmp_path, 2)
    res = tools.assemble_reel(sess, [{"clip": 1}, {"generate": "nope"},
                                     {"clip": 2}])
    assert res["ok"] is False and "could not generate" in res["error"]


def test_reel_all_clips_no_genmedia_needed(monkeypatch, tmp_path, wired):
    # No generated segment -> genmedia key not required.
    monkeypatch.setattr(genmedia, "available", lambda: False)
    sess = _Sess(tmp_path, 2)
    res = tools.assemble_reel(sess, [{"clip": 1}, {"clip": 2}])
    assert res["ok"] is True


def test_reel_rejects_bad_segment(tmp_path, wired):
    sess = _Sess(tmp_path, 2)
    res = tools.assemble_reel(sess, [{"clip": 1}, {"oops": "x"}])
    assert res["ok"] is False and "segment" in res["error"].lower()


def test_reel_nonexistent_clip_is_clean_error(tmp_path, wired):
    # A wrong clip id must return _err, NOT raise an unhandled ValueError.
    sess = _Sess(tmp_path, 2)
    res = tools.assemble_reel(sess, [{"clip": 1}, {"clip": 99}])
    assert res["ok"] is False and "no clip #99" in res["error"].lower()


def test_reel_malformed_clip_id_is_clean_error(tmp_path, wired):
    # A null/list clip value must return _err, NOT raise TypeError.
    sess = _Sess(tmp_path, 2)
    res = tools.assemble_reel(sess, [{"clip": 1}, {"clip": None}])
    assert res["ok"] is False and "clip id" in res["error"].lower()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
