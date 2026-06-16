"""Voice dubbing: graceful fallbacks, timing/speed mapping, the mux filtergraph,
and the set_dub tool wiring. ffmpeg / ffprobe / TTS are all mocked so the tests
run offline and deterministically."""

import pipeline.dub as dub


def _segs():
    return [
        {"start": 0.0, "end": 2.0, "text": "hello there"},
        {"start": 2.5, "end": 4.0, "text": "how are you"},
    ]


def _patch_common(monkeypatch, ffmpeg_calls, durations=None, alt_short=False):
    """Wire translate + tts + ffmpeg/ffprobe stubs. `durations` maps a path
    substring to a duration; default 1.0 for anything unmatched. alt_short=True
    makes the budget-aware translation return a DIFFERENT tighter rewrite."""
    monkeypatch.setattr(dub, "translate_lines_fitted",
                        lambda texts, lang, budgets: [
                            {"text": f"T:{t}",
                             "alt_short": (f"S:{t}" if alt_short else f"T:{t}")}
                            for t in texts])
    monkeypatch.setattr(dub, "translate_lines",
                        lambda texts, lang: [f"T:{t}" for t in texts])
    monkeypatch.setattr(dub.tts, "synthesize",
                        lambda text, path, voice=None, instructions=None,
                        speed=None: path)
    monkeypatch.setattr(dub, "run_ffmpeg",
                        lambda args, **k: ffmpeg_calls.append(args))
    # Force atempo (not rubberband) so timing tests are deterministic across
    # ffmpeg builds; a dedicated test covers the rubberband selection.
    monkeypatch.setattr(dub, "_has_filter", lambda name: False)

    def fake_dur(path):
        for frag, d in (durations or {}).items():
            if frag in str(path):
                return d
        return 1.0
    monkeypatch.setattr(dub, "_audio_dur", fake_dur)


def test_no_target_returns_clip_unchanged():
    assert dub.apply_dub("/x/clip.mp4", _segs(), "") == "/x/clip.mp4"


def test_no_segments_returns_clip_unchanged():
    assert dub.apply_dub("/x/clip.mp4", [], "Spanish") == "/x/clip.mp4"


def test_translation_failure_keeps_original_audio(monkeypatch):
    # Both the budget-aware and the plain translator fail -> keep original.
    monkeypatch.setattr(dub, "translate_lines_fitted",
                        lambda texts, lang, budgets: None)
    monkeypatch.setattr(dub, "translate_lines", lambda texts, lang: None)
    assert dub.apply_dub("/x/clip.mp4", _segs(), "Spanish") == "/x/clip.mp4"


def test_fitted_translation_failure_falls_back_to_plain(monkeypatch):
    # translate_lines_fitted None but plain translate_lines works -> still dubs.
    calls = []
    _patch_common(monkeypatch, calls, durations={"clip.mp4": 10.0})
    monkeypatch.setattr(dub, "translate_lines_fitted",
                        lambda texts, lang, budgets: None)
    out = dub.apply_dub("/x/clip.mp4", _segs(), "Spanish", out_path="/x/o.mp4")
    assert out == "/x/o.mp4" and calls  # reached the mux via the plain path


def test_all_tts_failures_keeps_original(monkeypatch):
    calls = []
    _patch_common(monkeypatch, calls)
    monkeypatch.setattr(dub.tts, "synthesize",
                        lambda text, path, voice=None, instructions=None,
                        speed=None: None)  # every synth fails
    assert dub.apply_dub("/x/clip.mp4", _segs(), "Spanish") == "/x/clip.mp4"
    assert calls == []  # never reached the mux


def test_happy_path_builds_mux_filtergraph(monkeypatch):
    calls = []
    # clip is 10s; each synthesized utterance is 1.0s (fits its window -> no fit).
    _patch_common(monkeypatch, calls, durations={"clip.mp4": 10.0})
    out = dub.apply_dub("/x/clip.mp4", _segs(), "Spanish", out_path="/x/out.mp4")
    assert out == "/x/out.mp4"
    # The LAST ffmpeg call is the mux (no atempo fit needed here).
    args = calls[-1]
    assert args.count("-i") == 3                 # clip + 2 utterances
    assert "0:v" in args and "[mix]" in args     # video kept, dubbed audio mapped
    fg = args[args.index("-filter_complex") + 1]
    assert "anullsrc" in fg                       # silent bed
    assert fg.count("adelay=") == 2               # one delay per utterance
    assert "amix=inputs=3" in fg                  # bed + 2 utterances


def test_overlong_utterance_is_sped_up_to_fit(monkeypatch):
    calls = []
    # Each utterance is 5s but its window is ~1.5-2s -> must atempo-speed up.
    _patch_common(monkeypatch, calls,
                  durations={"clip.mp4": 10.0, "_dub": 5.0})
    dub.apply_dub("/x/clip.mp4", _segs(), "Spanish")
    atempo = [a for a in calls if any("atempo=" in str(x) for x in a)]
    assert atempo, "expected an atempo fit pass for the overlong utterances"
    # Speedup is capped at TUS_MAX_SPEEDUP.
    import pipeline.config as cfg
    for a in atempo:
        f = float(next(x for x in a if "atempo=" in str(x)).split("atempo=")[1])
        assert 1.0 < f <= cfg.TTS_MAX_SPEEDUP + 1e-6


def test_speed_factor_maps_start_times(monkeypatch):
    calls = []
    _patch_common(monkeypatch, calls, durations={"clip.mp4": 10.0})
    # speed=2.0 -> a segment starting at 2.5s lands at 1.25s in the sped timeline.
    dub.apply_dub("/x/clip.mp4", _segs(), "Spanish", speed=2.0)
    fg = calls[-1][calls[-1].index("-filter_complex") + 1]
    # second utterance: 2.5 / 2.0 = 1.25s -> 1250ms delay.
    assert "adelay=1250|1250" in fg
    # first utterance: 0.0s -> 0ms.
    assert "adelay=0|0" in fg


def _w(start, end, word):
    return {"start": start, "end": end, "word": word}


def test_refine_units_splits_one_long_run_by_max_sec():
    # A 12s flat run of words with no punctuation/gaps must still break by max_sec.
    words = [_w(i * 0.5, i * 0.5 + 0.5, f"w{i}") for i in range(24)]  # 0..12s
    units = dub.refine_units(words, gap=5.0, max_sec=3.5, min_sec=0.6)
    assert len(units) > 1
    for u in units:
        assert u["end"] - u["start"] <= 3.5 + 1e-9
    # Real bounds, not synthetic.
    assert units[0]["start"] == 0.0
    assert units[-1]["end"] == words[-1]["end"]


def test_refine_units_gap_split():
    a = [_w(0.0, 0.4, "the"), _w(0.4, 0.8, "cat")]
    b = [_w(1.4, 1.8, "sat")]  # 0.6s gap >= 0.35
    assert len(dub.refine_units(a + b, gap=0.35, max_sec=10, min_sec=0.1)) == 2
    near = [_w(0.0, 0.4, "the"), _w(0.5, 0.9, "cat")]  # 0.1s gap < 0.35
    assert len(dub.refine_units(near, gap=0.35, max_sec=10, min_sec=0.1)) == 1


def test_refine_units_punctuation_split():
    words = [_w(0.0, 0.4, "hi."), _w(0.5, 0.9, "yo")]  # '.' forces a boundary
    units = dub.refine_units(words, gap=5.0, max_sec=10, min_sec=0.1)
    assert len(units) == 2
    assert units[0]["text"] == "hi." and units[1]["text"] == "yo"


def test_refine_units_merges_sub_min_units_forward():
    # A trailing micro-unit (0.2s) must merge into the previous unit.
    words = [_w(0.0, 1.0, "hello."), _w(1.1, 1.3, "ok.")]
    units = dub.refine_units(words, gap=0.05, max_sec=10, min_sec=0.6)
    assert len(units) == 1
    assert units[0]["text"] == "hello. ok." and units[0]["end"] == 1.3


def test_refine_units_merges_LEADING_sub_min_into_next():
    # A leading micro-unit (0.2s 'Oh!') must merge FORWARD into the next unit,
    # not survive as a rhythm-breaking fragment at clip start.
    words = [_w(0.0, 0.2, "Oh!"), _w(0.3, 1.8, "big long unit.")]
    units = dub.refine_units(words, gap=5.0, max_sec=10, min_sec=0.6)
    assert len(units) == 1
    assert units[0]["start"] == 0.0 and units[0]["end"] == 1.8
    assert units[0]["text"] == "Oh! big long unit."


def test_refine_units_lone_sub_min_unit_survives():
    # If the WHOLE clip is one sub-min fragment, keep it (can't merge anywhere).
    units = dub.refine_units([_w(0.0, 0.3, "hi.")], min_sec=0.6)
    assert [u["text"] for u in units] == ["hi."]


def test_overflow_retries_alt_short_before_stretch(monkeypatch):
    calls = []
    synth_texts = []
    _patch_common(monkeypatch, calls, durations={"clip.mp4": 10.0, "_dub": 5.0},
                  alt_short=True)

    def rec_synth(text, path, voice=None, instructions=None, speed=None):
        synth_texts.append(text)
        return path
    monkeypatch.setattr(dub.tts, "synthesize", rec_synth)
    dub.apply_dub("/x/clip.mp4", _segs(), "Spanish")
    # Each overlong unit synthesizes text THEN its tighter alt_short ("S:...").
    assert any(t.startswith("S:") for t in synth_texts)


def test_no_overflow_skips_resynth_and_stretch(monkeypatch):
    calls = []
    n_synth = {"n": 0}
    _patch_common(monkeypatch, calls, durations={"clip.mp4": 10.0},
                  alt_short=True)  # short alt available but must NOT be used

    def rec_synth(text, path, voice=None, instructions=None, speed=None):
        n_synth["n"] += 1
        return path
    monkeypatch.setattr(dub.tts, "synthesize", rec_synth)
    dub.apply_dub("/x/clip.mp4", _segs(), "Spanish")
    assert n_synth["n"] == 2          # one synth per unit, no alt_short re-synth
    # Only the final mux ran — no per-unit atempo/_fit ffmpeg pass.
    assert len(calls) == 1


def test_anchoring_no_drift_when_a_unit_overflows(monkeypatch):
    calls = []
    # Unit 0 overflows (5s into a ~2s window); unit 1 must STILL be anchored at
    # its own real start (2.5s), unaffected by unit 0's overrun.
    _patch_common(monkeypatch, calls, durations={"clip.mp4": 10.0, "_dub000": 5.0})
    dub.apply_dub("/x/clip.mp4", _segs(), "Spanish")
    fg = calls[-1][calls[-1].index("-filter_complex") + 1]
    assert "adelay=0|0" in fg        # unit 0 at 0.0s
    assert "adelay=2500|2500" in fg  # unit 1 at 2.5s (its real start)


def test_three_unit_no_drift_each_anchored_absolutely(monkeypatch):
    calls = []
    segs = [{"start": 0.0, "end": 2.0, "text": "one"},
            {"start": 3.0, "end": 5.0, "text": "two"},
            {"start": 6.0, "end": 8.0, "text": "three"}]
    # Unit 0 overflows hugely; units 1 & 2 must STILL anchor at their real starts.
    _patch_common(monkeypatch, calls, durations={"clip.mp4": 12.0, "_dub000": 9.0})
    dub.apply_dub("/x/clip.mp4", segs, "Spanish")
    fg = calls[-1][calls[-1].index("-filter_complex") + 1]
    for ms in ("adelay=0|0", "adelay=3000|3000", "adelay=6000|6000"):
        assert ms in fg


def test_fit_uses_rubberband_when_available(monkeypatch):
    calls = []
    monkeypatch.setattr(dub, "run_ffmpeg", lambda args, **k: calls.append(args))
    monkeypatch.setattr(dub, "_audio_dur", lambda p: 4.0)  # 4s into a 2s window
    monkeypatch.setattr(dub, "_has_filter", lambda name: True)
    monkeypatch.setattr(dub.config, "TTS_PITCH_PRESERVE", True)
    dub._fit("/x/u.wav", window=2.0, stem="clip", idx=0)
    flt = calls[-1][calls[-1].index("-filter:a") + 1]
    assert "rubberband" in flt
    import pipeline.config as cfg
    factor = float(flt.split("tempo=")[1])
    assert 1.0 < factor <= cfg.TTS_MAX_SPEEDUP + 1e-6


def test_fit_falls_back_to_atempo_without_rubberband(monkeypatch):
    calls = []
    monkeypatch.setattr(dub, "run_ffmpeg", lambda args, **k: calls.append(args))
    monkeypatch.setattr(dub, "_audio_dur", lambda p: 4.0)
    monkeypatch.setattr(dub, "_has_filter", lambda name: False)
    dub._fit("/x/u.wav", window=2.0, stem="clip", idx=0)
    flt = calls[-1][calls[-1].index("-filter:a") + 1]
    assert flt.startswith("atempo=")


def test_atempo_chain_splits_above_2x():
    # A single atempo can't exceed 2.0 -> a 3.0x stretch must chain.
    chain = dub._atempo_chain(3.0)
    assert chain == "atempo=2.0,atempo=1.5000"
    assert dub._atempo_chain(1.6) == "atempo=1.6000"  # under 2.0 -> single


def test_fit_chains_atempo_for_high_speedup(monkeypatch):
    calls = []
    monkeypatch.setattr(dub, "run_ffmpeg", lambda args, **k: calls.append(args))
    monkeypatch.setattr(dub, "_audio_dur", lambda p: 10.0)  # 10s into 2s window
    monkeypatch.setattr(dub, "_has_filter", lambda name: False)
    monkeypatch.setattr(dub.config, "TTS_MAX_SPEEDUP", 4.0)  # allow > 2x
    dub._fit("/x/u.wav", window=2.0, stem="clip", idx=0)
    flt = calls[-1][calls[-1].index("-filter:a") + 1]
    assert flt.count("atempo=") >= 2 and "atempo=2.0" in flt


def test_fit_returns_unstretched_on_ffmpeg_failure(monkeypatch):
    def boom(args, **k):
        raise RuntimeError("bad filter")
    monkeypatch.setattr(dub, "run_ffmpeg", boom)
    monkeypatch.setattr(dub, "_audio_dur", lambda p: 4.0)
    monkeypatch.setattr(dub, "_has_filter", lambda name: False)
    # Must NOT raise — returns the original (unstretched) path.
    assert dub._fit("/x/u.wav", window=2.0, stem="clip", idx=0) == "/x/u.wav"


def test_apply_dub_zero_total_dur_keeps_original(monkeypatch):
    calls = []
    _patch_common(monkeypatch, calls, durations={"clip.mp4": 0.0})
    # ffprobe couldn't measure the clip -> return original, never build a -t 0 mux.
    assert dub.apply_dub("/x/clip.mp4", _segs(), "Spanish") == "/x/clip.mp4"
    assert calls == []


def test_apply_dub_mux_failure_keeps_original(monkeypatch):
    calls = []
    _patch_common(monkeypatch, calls, durations={"clip.mp4": 10.0})

    def boom(args, **k):
        raise RuntimeError("mux exploded")
    monkeypatch.setattr(dub, "run_ffmpeg", boom)
    # HARD CONTRACT: an ffmpeg mux failure returns the original clip, no crash.
    assert dub.apply_dub("/x/clip.mp4", _segs(), "Spanish") == "/x/clip.mp4"


def test_instructions_for_varies_by_punctuation():
    assert "energetically" in dub._instructions_for("Wow!", 2.0).lower()
    assert "inquisitive" in dub._instructions_for("Really?", 2.0).lower()
    # A cramped line (high chars/sec) gets the brisk hint appended.
    long = "x" * 200
    assert "briskly" in dub._instructions_for(long, 1.0).lower()


def _dub_units_stub(words_result):
    """A minimal object carrying Session.dub_units_for + stubbed words/segments.
    words_result: a list, or an Exception instance to raise from words_for."""
    import chat.session as sess

    class Stub:
        dub_units_for = sess.Session.dub_units_for

        def words_for(self, clip):
            if isinstance(words_result, Exception):
                raise words_result
            return words_result

        def segments_for(self, clip):
            return ["SENTINEL_SEGMENTS"]
    return Stub()


def test_dub_units_for_disabled_uses_raw_segments(monkeypatch):
    import pipeline.config as cfg
    monkeypatch.setattr(cfg, "DUB_FINE_SEGMENTS", False)
    s = _dub_units_stub([_w(0.0, 1.0, "hi.")])
    assert s.dub_units_for({}) == ["SENTINEL_SEGMENTS"]


def test_dub_units_for_falls_back_when_no_words(monkeypatch):
    import pipeline.config as cfg
    monkeypatch.setattr(cfg, "DUB_FINE_SEGMENTS", True)
    assert _dub_units_stub([]).dub_units_for({}) == ["SENTINEL_SEGMENTS"]
    assert _dub_units_stub(ValueError("no cut")).dub_units_for({}) \
        == ["SENTINEL_SEGMENTS"]


def test_dub_units_for_refines_when_words_present(monkeypatch):
    import pipeline.config as cfg
    monkeypatch.setattr(cfg, "DUB_FINE_SEGMENTS", True)
    # Units must clear DUB_MIN_UNIT_SEC (0.6) or they'd merge; a 0.4s gap splits.
    words = [_w(0.0, 0.8, "hi."), _w(1.2, 2.0, "there.")]
    units = _dub_units_stub(words).dub_units_for({})
    assert units != ["SENTINEL_SEGMENTS"]
    assert [u["text"] for u in units] == ["hi.", "there."]


def test_set_dub_tool_sets_and_clears():
    import chat.tools as tools

    rendered = {}

    class FakeSession:
        last_notes = "ok"

        def __init__(self):
            self._clip = {"id": 1, "stages": []}

        def clip(self, cid):
            return self._clip

        def snapshot(self, *a, **k):
            pass

        def set_stage(self, cid, name, params):
            rendered["name"] = name
            rendered["params"] = params
            self._clip["stages"] = [{"name": name, "params": params}]
            return "/tmp/out.mp4"

    s = FakeSession()
    r = tools.set_dub(s, 1, "Spanish", voice="alloy")
    assert r["ok"] and r["language"] == "Spanish"
    assert rendered["name"] == "dub"
    assert rendered["params"]["lang"] == "Spanish"
    assert rendered["params"]["voice"] == "alloy"

    r2 = tools.set_dub(s, 1, "original")
    assert r2["ok"] and r2["language"] is None
    assert "lang" not in rendered["params"]
