"""Voice dubbing: graceful fallbacks, timing/speed mapping, the mux filtergraph,
and the set_dub tool wiring. ffmpeg / ffprobe / TTS are all mocked so the tests
run offline and deterministically."""

import pipeline.dub as dub


def _segs():
    return [
        {"start": 0.0, "end": 2.0, "text": "hello there"},
        {"start": 2.5, "end": 4.0, "text": "how are you"},
    ]


def _patch_common(monkeypatch, ffmpeg_calls, durations=None):
    """Wire translate + tts + ffmpeg/ffprobe stubs. `durations` maps a path
    substring to a duration; default 1.0 for anything unmatched."""
    monkeypatch.setattr(dub, "translate_lines",
                        lambda texts, lang: [f"T:{t}" for t in texts])
    monkeypatch.setattr(dub.tts, "synthesize",
                        lambda text, path, voice=None: path)
    monkeypatch.setattr(dub, "run_ffmpeg",
                        lambda args, **k: ffmpeg_calls.append(args))

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
    monkeypatch.setattr(dub, "translate_lines", lambda texts, lang: None)
    assert dub.apply_dub("/x/clip.mp4", _segs(), "Spanish") == "/x/clip.mp4"


def test_all_tts_failures_keeps_original(monkeypatch):
    calls = []
    _patch_common(monkeypatch, calls)
    monkeypatch.setattr(dub.tts, "synthesize",
                        lambda text, path, voice=None: None)  # every synth fails
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
