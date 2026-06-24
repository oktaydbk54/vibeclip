"""Optional libass caption path (Faz 2.3). The .ass document generation is pure
string logic and fully tested; the burn is gated by libass_available() so on the
default (no-libass) ffmpeg it returns None and the PNG path is used.
"""

import pytest

from pipeline import captions_ass as ca
from pipeline.subtitle import SubStyle


def _words():
    return [{"start": 0.30, "end": 0.70, "word": "hello"},
            {"start": 0.70, "end": 1.10, "word": "world"},
            {"start": 1.10, "end": 1.80, "word": "again"}]


# ------------------------------------------------------------------- helpers

def test_ass_timestamp_format():
    assert ca._ass_ts(0) == "0:00:00.00"
    assert ca._ass_ts(75.5) == "0:01:15.50"
    assert ca._ass_ts(3661.25) == "1:01:01.25"
    assert ca._ass_ts(-5) == "0:00:00.00"


def test_ass_color_is_inverted_alpha_bgr():
    # opaque white -> alpha 00, BGR FFFFFF.
    assert ca._ass_color((255, 255, 255, 255)) == "&H00FFFFFF"
    # opaque pure red -> BGR puts blue/green 00, red FF last.
    assert ca._ass_color((255, 0, 0, 255)) == "&H000000FF"
    # half alpha inverts (128 -> 7F).
    assert ca._ass_color((0, 0, 0, 128)) == "&H7F000000"


def test_font_name_from_path():
    assert ca._font_name("/x/Archivo-Black.ttf") == "Archivo Black"
    assert ca._font_name("") == "Arial"


# ------------------------------------------------------------------- build_ass

def test_build_ass_is_well_formed():
    ass = ca.build_ass(_words(), 1080, 1920, SubStyle(animation="spring"))
    assert "[Script Info]" in ass
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "[V4+ Styles]" in ass and "Style: Hero," in ass
    assert "[Events]" in ass
    # one Dialogue line per caption segment (3 short words -> 1 line here).
    dialogues = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
    assert dialogues, "expected at least one Dialogue line"
    # spring animation injects a scale transform tag.
    assert r"\t(" in dialogues[0]
    # uppercase default applied to the caption text.
    assert "HELLO" in ass


def test_build_ass_none_animation_has_no_transform():
    ass = ca.build_ass(_words(), 1080, 1920, SubStyle(animation="none"))
    dialogue = next(ln for ln in ass.splitlines() if ln.startswith("Dialogue:"))
    assert r"\t(" not in dialogue and r"\fad" not in dialogue


def test_build_ass_respects_lowercase_style():
    ass = ca.build_ass(_words(), 1080, 1920,
                       SubStyle(animation="none", uppercase=False))
    assert "hello world" in ass.lower()
    assert "HELLO WORLD" not in ass


# ------------------------------------------------------------------- burn gate

def test_burn_ass_none_without_libass(monkeypatch):
    monkeypatch.setattr(ca, "libass_available", lambda: False)
    assert ca.burn_ass("/x.mp4", _words(), style=SubStyle()) is None


def test_burn_ass_none_on_empty_words():
    assert ca.burn_ass("/x.mp4", [], style=SubStyle()) is None


def test_libass_available_reads_filters(monkeypatch):
    import subprocess

    def fake_run(args, **kw):
        return type("R", (), {"stdout": " ... ass  subtitles  ..."})()

    monkeypatch.setattr(subprocess, "run", fake_run)
    ca.libass_available.cache_clear()
    assert ca.libass_available() is True
    ca.libass_available.cache_clear()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
