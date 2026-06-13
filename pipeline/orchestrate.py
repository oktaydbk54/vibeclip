"""End-to-end orchestration: video -> ready short clips.

make_short chains the phases. With auto_edit=True each highlight runs through the
full Faz 5 treatment (silence trim, vertical reframe, LLM-planned punch-in zooms,
karaoke captions, ducked music, timed SFX, fades).

Ordering note: video filters copy the audio stream and audio filters copy the
video stream, so the chain composes. Silence removal changes timing, so after it
we RE-transcribe the tightened clip to get fresh word timings for captions/zoom.
"""

from __future__ import annotations

from pathlib import Path

from pipeline import config
from pipeline.cut import cut_clip
from pipeline.highlights import find_highlights
from pipeline.transcribe import transcribe

def _scan_sfx_library() -> dict[str, str]:
    """assets/sfx/<name>.<ext> -> {"<name>": path}. Drop a file in, get a new
    sfx kind with no code change (riser, impact, pop, boom, ...)."""
    lib: dict[str, str] = {}
    d = config.ROOT / "assets" / "sfx"
    if d.exists():
        for f in sorted(d.iterdir()):
            if f.suffix.lower() in (".m4a", ".wav", ".mp3", ".aac", ".ogg"):
                lib[f.stem.lower()] = str(f)
    return lib


SFX_LIBRARY = _scan_sfx_library()


def _auto_edit_clip(
    video_path: str,
    clip: dict,
    index: int,
    transcript: dict,
    jumpcut: bool,
    zoom: bool,
    subtitles: bool,
    sfx: bool,
    music_path: str | None,
    tracked_reframe: bool = True,
    internal_transitions: bool = False,
    auto_soundbed: bool = True,
) -> tuple[str, list[str]]:
    """Run one highlight through the full edit treatment.

    Returns (final_path, warnings). Each step is checkpointed: a failing step
    logs a warning and the chain continues from the last good artifact instead
    of losing the whole clip.
    """
    from pipeline.audio import add_background_music
    from pipeline.effects import build_zoom_vf, fade_in_out
    from pipeline.editplan import plan_clip_edits
    from pipeline.jumpcut import remove_silences
    from pipeline.sfx import add_sfx
    from pipeline.subtitle import burn_subtitles

    warnings: list[str] = []

    def _safe(step: str, fn, current):
        """Run one chain step; on failure keep the last good artifact."""
        try:
            return fn()
        except Exception as e:
            warnings.append(f"{step}: {type(e).__name__}: {e}")
            return current

    # 1. Raw cut (must succeed — nothing to fall back to).
    path = cut_clip(video_path, clip["start"], clip["end"],
                    title=clip["title"], precise=True, index=index)

    # 2. Silence trim. Re-transcribe so downstream timings are clip-local & fresh.
    words = transcript["words"]
    clip_start, clip_end = clip["start"], clip["end"]
    if jumpcut:
        clip_words = [w for w in words
                      if w["end"] > clip["start"] and w["start"] < clip["end"]]
        tightened = _safe("jumpcut", lambda: remove_silences(
            path, clip_words, clip_start=clip["start"]), path)
        if tightened != path:
            path = tightened
            local = transcribe(path)
            words = local["words"]            # already clip-local (start at 0)
            clip_start, clip_end = 0.0, local["duration"]

    # 3b. Intra-clip sub-segment transitions (gated, default off). Needs its own
    #     render + re-transcribe (it shortens the clip), so it stays sequential.
    if internal_transitions:
        from pipeline.tracking import reframe_vertical_tracked
        from pipeline.segmenter import apply_internal_transitions
        path = _safe("reframe", lambda: reframe_vertical_tracked(path), path)
        local_words = [{"start": w["start"] - clip_start, "end": w["end"] - clip_start,
                        "word": w["word"]} for w in words
                       if w["end"] > clip_start and w["start"] < clip_end]
        seg = _safe("internal_transitions",
                    lambda: apply_internal_transitions(path, {"words": local_words}),
                    path)
        if seg != path:
            path = seg
            local = transcribe(path)
            words = local["words"]
            clip_start, clip_end = 0.0, local["duration"]
        reframe_done = True
    else:
        reframe_done = False

    # Plan emphasis/sfx from the (clip-local) transcript.
    plan = {}
    if zoom or sfx:
        plan = _safe("editplan",
                     lambda: plan_clip_edits(words, clip_start, clip_end), {})

    # 3+4+5. FUSED RENDER PASS — reframe crop + eased zoom + caption overlays in
    # ONE encode (was 3 separate h264 generations). The hook window (first 3s)
    # gets a stronger punch — those seconds decide the swipe.
    from pipeline.media import ffprobe_info, run_ffmpeg as _run

    vf_parts: list[str] = []
    reframed = False  # did the tracked-reframe vf to a 9:16 canvas actually run?
    if tracked_reframe and not reframe_done:
        from pipeline.tracking import build_reframe_vf
        vf = _safe("reframe_vf", lambda: build_reframe_vf(path), "")
        if vf:
            vf_parts.append(vf)
            reframed = True
    elif not tracked_reframe and not reframe_done:
        from pipeline.reframe import reframe_vertical
        path = _safe("reframe", lambda: reframe_vertical(path), path)

    # plan_clip_edits returns CLIP-LOCAL seconds already — use them directly.
    if zoom and plan.get("emphasis"):
        windows = [
            (e["start"], e["end"], 1.26 if e["start"] < 3.0 else 1.16)
            for e in plan["emphasis"]
        ]
        fps = ffprobe_info(path)["fps"] or 30
        zvf = build_zoom_vf(windows, 1080, 1920, fps) if reframed else \
            build_zoom_vf(windows, *_dims(path), fps)
        if zvf:
            vf_parts.append(zvf)

    pre_vf = ",".join(vf_parts)
    if subtitles:
        # Only claim the 9:16 canvas if the reframe vf was actually built and
        # appended — if build_reframe_vf raised (caught by _safe -> ""), the
        # frame stays at its original size, so captions must use those dims.
        # (vf_parts can be non-empty from zoom alone, so it's not the right
        # signal here — track the reframe explicitly.)
        canvas = (1080, 1920) if reframed else None
        path = _safe("render", lambda: burn_subtitles(
            path, words, clip_start=clip_start, karaoke=True,
            pre_vf=pre_vf, canvas=canvas), path)
    elif pre_vf:
        def _render_vf() -> str:
            out = str(Path(path).with_name(Path(path).stem + "_r.mp4"))
            _run(["-i", str(Path(path).resolve()), "-vf", pre_vf,
                  "-c:v", config.VIDEO_ENCODER, "-c:a", "copy",
                  str(Path(out).resolve())])
            return out
        path = _safe("render", _render_vf, path)

    # 6. Soundbed — auto energy-matched music (ducked) + topic-keyed ambience.
    if auto_soundbed:
        from pipeline.soundbed import add_ambience, build_soundbed
        bed = _safe("soundbed",
                    lambda: build_soundbed(path, topic_label=clip.get("title")), {})
        if music_path:                       # manual override wins
            bed["music_path"] = music_path
        if bed.get("music_path"):
            path = _safe("music", lambda: add_background_music(
                path, bed["music_path"],
                music_volume=bed.get("music_volume", 0.18), duck=True), path)
        if bed.get("ambience_path"):
            path = _safe("ambience", lambda: add_ambience(
                path, bed["ambience_path"],
                volume=bed.get("ambience_volume", 0.06)), path)
    elif music_path:
        path = _safe("music", lambda: add_background_music(
            path, music_path, duck=True), path)

    # 7. Timed sound effects.
    if sfx and plan.get("sfx"):
        events = [{"time": s["time"], "path": SFX_LIBRARY[s["kind"]], "volume": 0.6}
                  for s in plan["sfx"] if s["kind"] in SFX_LIBRARY]
        if events:
            path = _safe("sfx", lambda: add_sfx(path, events), path)

    # 8. Fade in/out + the chain's single loudness normalization.
    path = _safe("fade", lambda: fade_in_out(path, fade=0.3, normalize=True), path)
    return path, warnings


def _dims(path: str) -> tuple[int, int]:
    from pipeline.media import ffprobe_info
    info = ffprobe_info(path)
    return info["width"], info["height"]


def make_short(
    video_path: str,
    platform: str = "youtube_shorts",
    count: int = 5,
    max_duration: float = 60.0,
    precise: bool = True,
    vertical: bool = False,
    subtitles: bool = False,
    auto_edit: bool = False,
    jumpcut: bool = True,
    zoom: bool = True,
    sfx: bool = False,
    music_path: str | None = None,
    structure_aware: bool = True,
    tracked_reframe: bool = True,
    internal_transitions: bool = False,
    auto_soundbed: bool = True,
) -> dict:
    """Transcribe -> understand structure -> pick clips -> cut/edit each. Manifest.

    structure_aware=True builds a scene+speech+energy moment index and draws clips
    from real topic segments (coherent story units). auto_edit=True then applies
    the full treatment per clip: silence trim, tracked vertical reframe, optional
    intra-clip transitions, punch-in zoom, karaoke captions, auto energy-matched
    music + ambience, timed SFX, and fades.
    """
    transcript = transcribe(video_path)

    structure = None
    if structure_aware:
        try:
            from pipeline.structure import analyze_structure
            structure = analyze_structure(video_path, transcript, platform=platform)
        except Exception as e:
            structure = None  # fall back to flat text selection
            print(f"[structure] skipped ({type(e).__name__}: {e}); using text selection")

    clips = find_highlights(transcript, platform, count, max_duration, structure=structure)

    results = []
    for i, clip in enumerate(clips):
        if auto_edit:
            path, clip_warnings = _auto_edit_clip(
                video_path, clip, i, transcript,
                jumpcut=jumpcut, zoom=zoom, subtitles=True, sfx=sfx,
                music_path=music_path,
                tracked_reframe=tracked_reframe,
                internal_transitions=internal_transitions,
                auto_soundbed=auto_soundbed,
            )
            if clip_warnings:
                clip = {**clip, "warnings": clip_warnings}
        else:
            path = cut_clip(video_path, clip["start"], clip["end"],
                            title=clip["title"], precise=precise, index=i)
            if vertical or subtitles:
                from pipeline.reframe import reframe_vertical
                from pipeline.subtitle import burn_subtitles
                if vertical:
                    path = reframe_vertical(path)
                if subtitles:
                    cw = [w for w in transcript["words"]
                          if w["end"] > clip["start"] and w["start"] < clip["end"]]
                    path = burn_subtitles(path, cw, clip_start=clip["start"])

        results.append({**clip, "file": path})

    return {
        "video": video_path,
        "platform": platform,
        "language": transcript["language"],
        "clip_count": len(results),
        "clips": results,
    }
