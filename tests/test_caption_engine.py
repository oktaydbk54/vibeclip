"""Caption engine: font portability fallback, pill rendering, LLM emphasis
fallback, and a regression guard that the DEFAULT karaoke render still emits one
overlay event per word with the historical enable ranges (animation='none' must
reproduce today's output)."""

from pathlib import Path

import pytest

from pipeline import subtitle as sub


def _has_red_pixel(png_path) -> bool:
    """True if any visible pixel is dominantly red (the pill color)."""
    from PIL import Image
    with Image.open(png_path) as im:
        rgba = im.convert("RGBA")
        px = rgba.load()
        w, h = rgba.size
        for yy in range(0, h, 2):
            for xx in range(0, w, 2):
                r, g, b, a = px[xx, yy]
                if a > 0 and r > 150 and g < 90 and b < 90:
                    return True
    return False


def test_resolve_font_falls_back_on_missing_path(tmp_path):
    # A bogus path must NOT raise — it degrades through the bundled/default chain.
    font = sub._resolve_font(str(tmp_path / "nope.ttf"), 84)
    assert font is not None
    # And a font is still usable for measuring text.
    from PIL import Image, ImageDraw
    d = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    assert d.textlength("hi", font=font) >= 0


def test_plan_emphasis_no_key_returns_empty(monkeypatch):
    # No LLM key configured -> graceful empty fallback, never raises.
    def _boom(*a, **k):
        raise RuntimeError("No LLM key configured")
    monkeypatch.setattr(sub.config, "llm_settings", _boom)
    words = [{"start": 0.0, "end": 0.5, "word": "money"}]
    emph, emoji = sub._plan_emphasis(words, want_emphasis=True, want_emoji=True)
    assert emph == []
    assert emoji == {}


def test_pill_renders_non_transparent_pixels_behind_text(tmp_path):
    out = tmp_path / "pill.png"
    st = sub.SubStyle(pill="#ff2d2d")
    sub._render_png(["hello", "world"], 400, 200, str(out), highlight=0, style=st)
    found = _has_red_pixel(out)
    assert found, "expected red pill pixels behind the active word"


def test_default_render_has_no_pill_pixels(tmp_path):
    # Backward-compat: default SubStyle never draws a pill background.
    out = tmp_path / "plain.png"
    sub._render_png(["hello", "world"], 400, 200, str(out), highlight=0)
    assert not _has_red_pixel(out)


def _fake_ffprobe(_path):
    return {"width": 400, "height": 200}


def test_default_karaoke_emits_one_event_per_word(monkeypatch, tmp_path):
    """Regression guard: animation='none' must produce exactly one overlay
    event per word with overlay=0:0 + the word's own enable range."""
    captured = {}

    def _fake_run(args):
        # Pull the filter_complex out of the argv.
        fg = args[args.index("-filter_complex") + 1]
        captured["fg"] = fg

    monkeypatch.setattr(sub, "ffprobe_info", _fake_ffprobe)
    monkeypatch.setattr(sub, "run_ffmpeg", _fake_run)

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    words = [
        {"start": 0.0, "end": 0.4, "word": "alpha"},
        {"start": 0.4, "end": 0.8, "word": "beta"},
        {"start": 0.8, "end": 1.2, "word": "gamma"},
    ]
    sub.burn_subtitles(str(clip), words, karaoke=True,
                       out_path=str(tmp_path / "out.mp4"))
    fg = captured["fg"]
    # One overlay per word, each with its enable range. The band-crop optimization
    # may overlay at y=band_top (not 0:0), so match any integer y offset.
    import re
    assert len(re.findall(r"overlay=0:\d+:enable=", fg)) == 3
    assert "between(t,0.000,0.400)" in fg
    assert "between(t,0.400,0.800)" in fg
    assert "between(t,0.800,1.200)" in fg


def _count_overlays(monkeypatch, tmp_path, word, animation, name):
    captured = {}
    monkeypatch.setattr(sub, "ffprobe_info", _fake_ffprobe)
    monkeypatch.setattr(sub, "run_ffmpeg",
                        lambda args: captured.__setitem__(
                            "fg", args[args.index("-filter_complex") + 1]))
    clip = tmp_path / f"{name}.mp4"
    clip.write_bytes(b"x")
    st = sub.SubStyle(animation=animation)
    sub.burn_subtitles(str(clip), [word], karaoke=True, style=st,
                       out_path=str(tmp_path / f"{name}_o.mp4"))
    return captured["fg"].count("overlay=0:0:enable=")


def test_animation_emits_bounded_extra_events(monkeypatch, tmp_path):
    """A 'pop' entrance adds a BOUNDED number of sub-PNG overlays per word —
    never a per-frame explosion. The exact count is adaptive, but capped."""
    word = {"start": 0.0, "end": 1.0, "word": "boom"}
    n = _count_overlays(monkeypatch, tmp_path, word, "pop", "clip2")
    # entrance frames + the settled hold, never exceeding the cap.
    assert sub.ANIM_STEPS_MIN <= n <= sub.ANIM_STEPS
    assert n <= 1 + sub.ANIM_STEPS  # strictly bounded


def test_animation_step_count_is_adaptive(monkeypatch, tmp_path):
    """A long entrance window renders more (smoother) sub-PNGs than a short one,
    and a very short window floors at ANIM_STEPS_MIN — both still bounded."""
    long_w = {"start": 0.0, "end": 2.0, "word": "boom"}    # dur caps at 0.18
    short_w = {"start": 0.0, "end": 0.08, "word": "hi"}    # dur ~0.048
    n_long = _count_overlays(monkeypatch, tmp_path, long_w, "pop", "long")
    n_short = _count_overlays(monkeypatch, tmp_path, short_w, "pop", "short")
    assert n_long > n_short
    assert n_short >= sub.ANIM_STEPS_MIN
    assert n_long <= sub.ANIM_STEPS


def test_ease_curves_end_settled():
    """Every animation's final step is the settled state (1.0, 0.0, 1.0) so the
    word lands exactly where the static PNG would — no residual offset/scale."""
    for anim in ("pop", "spring", "slide", "none", "bogus"):
        steps = sub._ease_steps(anim, 5)
        sc, dy, al = steps[-1]
        assert abs(sc - 1.0) < 1e-6 and abs(dy) < 1e-6 and abs(al - 1.0) < 1e-6
    # spring genuinely overshoots above full size somewhere mid-entrance.
    spring = sub._ease_steps("spring", 8)
    assert any(sc > 1.0 for sc, _, _ in spring[:-1])


if __name__ == "__main__":
    pytest.main([str(Path(__file__))])
