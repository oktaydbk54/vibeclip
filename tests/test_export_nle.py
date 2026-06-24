"""NLE export (Faz 0.3): timing-only stays byte-compatible; a reframed clip
opens at its canvas with a scale-to-fill Basic Motion; a caption .srt sidecar is
written alongside. Well-formedness guarded by an actual XML parse.
"""

import xml.dom.minidom as minidom

import pytest

from chat import export_nle as nle
from pipeline.timebase import Timebase


class _Tmap:
    def __init__(self, kept):
        self._kept = kept

    def kept_spans(self):
        return self._kept


class _FakeSession:
    """Minimal Session stand-in for export_timeline."""

    def __init__(self, tmp_path, stages, words, source=None):
        self.workdir = tmp_path
        self._clip = {"id": 1, "title": "demo", "stages": stages,
                      "markers": [{"t": 0.5, "label": "hook", "color": "red"}]}
        self._words = words
        self.data = {"source": source or {
            "path": str(tmp_path / "src.mp4"), "width": 1920, "height": 1080,
            "fps": 30, "duration": 120.0}}

    def clip(self, clip_id):
        if clip_id != 1:
            raise ValueError(f"No clip #{clip_id}.")
        return self._clip

    def timemap_for(self, clip):
        return _Tmap([(2.0, 4.0), (10.0, 12.5)])

    def words_for(self, clip):
        return self._words


# ------------------------------------------------------------------- helpers

def test_fill_scale_fills_both_axes():
    # 16:9 source into a 9:16 frame -> scale up by height ratio, center-crop.
    assert nle._fill_scale(1920, 1080, 1080, 1920) == pytest.approx(177.78, abs=0.1)
    # same aspect, bigger frame -> pure upscale.
    assert nle._fill_scale(720, 1280, 1080, 1920) == pytest.approx(150.0)
    # degenerate source never divides by zero.
    assert nle._fill_scale(0, 0, 1080, 1920) == 100.0


def test_reframe_canvas_reads_aspect():
    assert nle._reframe_canvas({"stages": [
        {"name": "reframe", "params": {"aspect": "9:16"}}]}) == (1080, 1920)
    assert nle._reframe_canvas({"stages": [
        {"name": "reframe", "params": {"aspect": "1:1"}}]}) == (1080, 1080)
    # no reframe stage -> no canvas (timing-only export).
    assert nle._reframe_canvas({"stages": [{"name": "cut", "params": {}}]}) is None


# ------------------------------------------------------------------- xmeml

def _words():
    return [{"start": 2.0, "end": 2.4, "word": "hello"},
            {"start": 2.4, "end": 2.9, "word": "world"},
            {"start": 10.0, "end": 10.6, "word": "again"}]


def test_timing_only_export_has_no_motion_and_source_canvas(tmp_path):
    sess = _FakeSession(tmp_path, stages=[{"name": "cut", "params": {}}],
                        words=_words())
    out = nle.export_timeline(sess, 1, "xml")
    xml = out.read_text()
    minidom.parseString(xml)                      # well-formed
    assert "Basic Motion" not in xml              # no reframe -> no transform
    assert "<width>1920</width><height>1080</height>" in xml  # source canvas


def test_reframed_export_opens_at_canvas_with_fill(tmp_path):
    sess = _FakeSession(
        tmp_path,
        stages=[{"name": "cut", "params": {}},
                {"name": "reframe", "params": {"aspect": "9:16"}},
                {"name": "subtitles", "params": {}}],
        words=_words())
    out = nle.export_timeline(sess, 1, "xml")
    xml = out.read_text()
    minidom.parseString(xml)
    assert "Basic Motion" in xml
    # sequence format opens at the reframed 9:16 canvas...
    assert "<format><samplecharacteristics><width>1080</width>" \
           "<height>1920</height>" in xml
    # ...while the source FILE keeps its native 1920x1080 characteristics.
    assert "<file id=\"src1\">" in xml and "<width>1920</width>" in xml
    # fill scale present (177.78 for 16:9 -> 9:16).
    assert "<value>177.78</value>" in xml


def test_caption_sidecar_written_alongside(tmp_path):
    sess = _FakeSession(tmp_path,
                        stages=[{"name": "reframe", "params": {"aspect": "9:16"}}],
                        words=_words())
    nle.export_timeline(sess, 1, "xml")
    srt = tmp_path / "clip01.srt"
    assert srt.exists()
    body = srt.read_text()
    assert "-->" in body and "hello" in body.lower()


def test_caption_sidecar_skips_when_no_words(tmp_path):
    sess = _FakeSession(tmp_path, stages=[{"name": "cut", "params": {}}],
                        words=[])
    assert nle.write_caption_sidecar(sess, 1) is None
    assert not (tmp_path / "clip01.srt").exists()


def test_edl_still_timing_only_but_gets_sidecar(tmp_path):
    sess = _FakeSession(tmp_path,
                        stages=[{"name": "reframe", "params": {"aspect": "9:16"}}],
                        words=_words())
    out = nle.export_timeline(sess, 1, "edl")
    assert out.suffix == ".edl"
    assert "Basic Motion" not in out.read_text()   # EDL carries no transform
    assert (tmp_path / "clip01.srt").exists()       # but sidecar still written


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
