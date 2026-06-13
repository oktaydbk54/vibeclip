"""visual_perception: give the agent eyes — a flag-gated verify-and-refine pass.

Default behavior (VISION_VERIFY unset) is byte-identical to before: _render_plan_
preview is unchanged and the vision hook is a no-op (no keyframe extraction, no
critique call). critique_clip degrades gracefully to {ok:True, problems:[]} when
the flag is off or no LLM key is configured. With the flag on and a fake vision
client reporting a defect, the propose loop receives ONE extra refine round and
then stops (bounded to a single vision-refine iteration).
"""

import copy
from pathlib import Path

from chat import tools
from chat.session import Session
from pipeline import perception


# --------------------------------------------------------------------------- #
# (b) critique_clip degrades to no-vision when the flag is off / no key.
# --------------------------------------------------------------------------- #
def test_critique_no_vision_when_flag_off(monkeypatch):
    monkeypatch.setattr("pipeline.config.VISION_VERIFY", False)
    # Even WITH frames, flag-off short-circuits before any LLM call.
    assert perception.critique_clip(["/nope.jpg"], "summary") == {
        "ok": True, "problems": []}


def test_critique_no_vision_when_no_key(monkeypatch):
    monkeypatch.setattr("pipeline.config.VISION_VERIFY", True)

    def _raise(tier="fast", override=None):
        raise RuntimeError("No LLM key configured.")
    monkeypatch.setattr("pipeline.config.llm_settings", _raise)
    # No data URI possible / no key -> graceful no-vision.
    assert perception.critique_clip([], "summary") == {"ok": True, "problems": []}


# --------------------------------------------------------------------------- #
# Session fixture (mirrors tests/test_incremental_preview.py).
# --------------------------------------------------------------------------- #
def _session(tmp_path: Path) -> Session:
    seed = tmp_path / "clip01_subtitles_seed.mp4"
    seed.write_bytes(b"seed")
    data = {
        "version": 1, "name": "t",
        "source": {"path": str(tmp_path / "src.mp4"), "width": 1920,
                   "height": 1080, "duration": 10.0, "fps": 30},
        "platform": "youtube_shorts",
        "clips": [{
            "id": 1, "title": "c1", "start": 0.0, "end": 5.0,
            "status": "ready",
            "stages": [
                {"name": "cut", "params": {"start": 0.0, "end": 5.0},
                 "output": str(seed)},
                {"name": "subtitles", "params": {"karaoke": True},
                 "output": str(seed)},
            ],
            "current": str(seed),
        }],
        "history": [],
    }
    return Session(data, tmp_path / "project.json")


def _stub_preview(sess, monkeypatch):
    """Make _render_plan_preview deterministic without touching ffmpeg."""
    def _run_stage(clip, name, params, inp):
        out = sess._out(clip, name, params, inp)
        Path(out).write_bytes(b"x")
        return out
    monkeypatch.setattr(sess, "_run_stage", _run_stage)
    monkeypatch.setattr(sess, "words_for", lambda clip: [])
    monkeypatch.setattr(sess, "speed_factor", lambda clip: 1.0)
    monkeypatch.setattr("pipeline.media.ffprobe_info",
                        lambda p: {"width": 1080, "height": 1920,
                                   "duration": 5.0})


# --------------------------------------------------------------------------- #
# (a) flag UNSET -> the vision hook is a pure no-op (no extract/critique).
# --------------------------------------------------------------------------- #
def test_vision_refine_noop_when_flag_off(tmp_path, monkeypatch):
    sess = _session(tmp_path)
    _stub_preview(sess, monkeypatch)
    monkeypatch.setattr("pipeline.config.VISION_VERIFY", False)

    extract_calls = {"n": 0}
    critique_calls = {"n": 0}
    monkeypatch.setattr(perception, "extract_keyframes",
                        lambda *a, **k: extract_calls.__setitem__(
                            "n", extract_calls["n"] + 1) or [])
    monkeypatch.setattr(perception, "critique_clip",
                        lambda *a, **k: critique_calls.__setitem__(
                            "n", critique_calls["n"] + 1) or {"ok": True,
                                                              "problems": []})

    plan = {"clip_id": 1, "instruction": "x", "summary": "s", "steps": [
        {"action": "set_subtitles", "args": {"clip_id": 1, "scale": 1.3}}]}
    preview = tools._render_plan_preview(sess, plan)
    out_plan, out_prev = tools._vision_refine(sess, plan, preview)

    # No-op: nothing extracted/critiqued, plan + preview returned unchanged.
    assert extract_calls["n"] == 0
    assert critique_calls["n"] == 0
    assert out_plan is plan
    assert out_prev is preview


# --------------------------------------------------------------------------- #
# (c) flag ON + a fake critique with a problem -> exactly one re-plan, bounded.
# --------------------------------------------------------------------------- #
def test_vision_refine_one_bounded_replan(tmp_path, monkeypatch):
    sess = _session(tmp_path)
    _stub_preview(sess, monkeypatch)
    monkeypatch.setattr("pipeline.config.VISION_VERIFY", True)

    # First (and only) critique reports a defect; if called again it would pass.
    monkeypatch.setattr(perception, "extract_keyframes",
                        lambda *a, **k: ["/frame0.jpg"])
    monkeypatch.setattr(perception, "critique_clip",
                        lambda frames, summary: {
                            "ok": False,
                            "problems": ["recenter the crop on the speaker"]})

    # The planner re-plan is the single bounded extra round — stub it so no LLM
    # is hit, and count the invocations.
    replans = {"n": 0}

    def _fake_propose(session, clip_id, instruction, extra_note=""):
        replans["n"] += 1
        assert "recenter the crop" in extra_note  # critique fed back as feedback
        return {"clip_id": clip_id, "instruction": instruction,
                "summary": "refined", "steps": [
                    {"action": "set_subtitles",
                     "args": {"clip_id": clip_id, "scale": 1.1}}]}
    monkeypatch.setattr("chat.planner.propose", _fake_propose)

    plan = {"clip_id": 1, "instruction": "make it pop", "summary": "s",
            "steps": [{"action": "set_subtitles",
                       "args": {"clip_id": 1, "scale": 1.3}}]}
    preview = tools._render_plan_preview(sess, plan)
    before = copy.deepcopy(sess.data)
    out_plan, out_prev = tools._vision_refine(sess, plan, preview)

    # Exactly one re-plan happened (bounded), and the refined plan came back.
    assert replans["n"] == 1
    assert out_plan.get("vision_refined") is True
    assert out_plan["summary"] == "refined"
    assert out_plan["vision_problems"] == ["recenter the crop on the speaker"]
    assert out_prev is not None and "file" in out_prev
    # Session state untouched by the throwaway refine preview.
    assert sess.data == before
