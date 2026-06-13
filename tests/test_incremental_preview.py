"""incremental_preview: cheaper proposal previews via cache reuse + advisory hints.

_render_plan_preview replays a plan on a throwaway copy of session.data, captures
the resulting artifact, then restores the backup. The render saving is latent:
set_stages replays from the earliest changed stage and every artifact is an
on-disk param-keyed cache hit, so a RE-preview of the same plan re-encodes
nothing. This item surfaces that floor (preview['preview_from']) and, for
time-localized edits, the affected span (preview['span']) — both ADVISORY,
neither changes rendered bytes or the A/B gate contract.
"""

import copy
from pathlib import Path

from chat import tools
from chat.session import Session


def test_changed_stage_floor_maps_actions():
    # Single-action plans map to their canonical stage.
    assert tools._changed_stage_floor(
        {"steps": [{"action": "set_subtitles", "args": {}}]}) == "subtitles"
    assert tools._changed_stage_floor(
        {"steps": [{"action": "cut_silences", "args": {}}]}) == "jumpcut"
    assert tools._changed_stage_floor(
        {"steps": [{"action": "remove_section", "args": {}}]}) == "trim"
    # Multi-step: earliest (most upstream) stage wins.
    floor = tools._changed_stage_floor({"steps": [
        {"action": "set_subtitles", "args": {}},
        {"action": "cut_silences", "args": {}},
        {"action": "set_music", "args": {}},
    ]})
    assert floor == "jumpcut"
    # Unknown / metadata-only plan -> None (advisory absent, never crashes).
    assert tools._changed_stage_floor(
        {"steps": [{"action": "generate_metadata", "args": {}}]}) is None
    assert tools._changed_stage_floor({"steps": []}) is None


def test_changed_span_extracts_localized_region():
    # start/end pair.
    assert tools._changed_span(
        {"steps": [{"action": "remove_section",
                    "args": {"start": 2.0, "end": 4.5}}]}) == [2.0, 4.5]
    # time/duration pair -> [time, time+duration].
    assert tools._changed_span(
        {"steps": [{"action": "add_zoom",
                    "args": {"time": 3.0, "duration": 1.5}}]}) == [3.0, 4.5]
    # Whole-clip edit (no span args) -> None.
    assert tools._changed_span(
        {"steps": [{"action": "set_subtitles",
                    "args": {"scale": 1.2}}]}) is None
    # Union across several localized steps.
    span = tools._changed_span({"steps": [
        {"action": "add_zoom", "args": {"time": 5.0, "duration": 1.0}},
        {"action": "remove_section", "args": {"start": 1.0, "end": 2.0}},
    ]})
    assert span == [1.0, 6.0]


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


def test_repreview_is_pure_cache_hit(tmp_path, monkeypatch):
    """Re-previewing the same plan re-encodes nothing: the param-keyed artifacts
    persist on disk across the backup/restore, so the second pass is all cache."""
    sess = _session(tmp_path)

    # Count real encodes; write the requested out_path so it survives as cache.
    calls: list[str] = []
    real_out = sess._out

    def _counting_run_stage(clip, name, params, inp):
        out = real_out(clip, name, params, inp)
        if not Path(out).exists():
            calls.append(name)
            Path(out).write_bytes(f"{name}".encode())
        return out
    monkeypatch.setattr(sess, "_run_stage", _counting_run_stage)
    # Keep the ghost-diff timeline serialize out of the way (engine boundary).
    monkeypatch.setattr("pipeline.media.ffprobe_info",
                        lambda p: {"width": 1080, "height": 1920,
                                   "duration": 5.0})

    # Planner injects clip_id into each step's args (planner.py:433).
    plan = {"clip_id": 1, "steps": [
        {"action": "set_subtitles", "args": {"clip_id": 1, "scale": 1.3}}]}

    p1 = tools._render_plan_preview(sess, plan)
    assert p1 is not None
    n_first = len(calls)
    assert n_first >= 1  # first preview re-encodes from the subtitles floor

    p2 = tools._render_plan_preview(sess, plan)
    assert p2 is not None
    # Second preview: identical params -> every stage is a cache hit, 0 encodes.
    assert len(calls) == n_first


def test_preview_ab_contract_and_advisory_keys(tmp_path, monkeypatch):
    """Preview returns {file, clip_id} (+ additive advisory keys) and leaves
    session.data byte-identical to the pre-preview backup."""
    sess = _session(tmp_path)

    def _run_stage(clip, name, params, inp):
        out = sess._out(clip, name, params, inp)
        Path(out).write_bytes(b"x")
        return out
    monkeypatch.setattr(sess, "_run_stage", _run_stage)
    # Avoid the transcribe engine boundary remove_section reaches through.
    monkeypatch.setattr(sess, "words_for", lambda clip: [])
    monkeypatch.setattr(sess, "speed_factor", lambda clip: 1.0)
    monkeypatch.setattr("pipeline.media.ffprobe_info",
                        lambda p: {"width": 1080, "height": 1920,
                                   "duration": 5.0})

    before = copy.deepcopy(sess.data)
    plan = {"clip_id": 1, "steps": [
        {"action": "remove_section",
         "args": {"clip_id": 1, "start": 1.0, "end": 2.0}}]}
    preview = tools._render_plan_preview(sess, plan)

    assert preview is not None
    # Back-compat A/B contract keys.
    assert "file" in preview and Path(preview["file"]).exists()
    assert preview["clip_id"] == 1
    # Additive advisory keys for this localized edit.
    assert preview["preview_from"] == "trim"
    assert preview["span"] == [1.0, 2.0]
    # Session untouched after the throwaway replay.
    assert sess.data == before
