"""Faz 5f — Intra-clip structured timeline.

A clip is normally one monolithic cut. Submagic/Vizard-style editing instead
splits a clip at its internal topic/scene beats and rejoins the pieces with
varied micro-transitions (match-cut, quick fade, dissolve, slide), giving each
clip an edited, segmented rhythm.

This module:
  - plan_subsegments(...)  -> the list of sub-segments + the boundary kinds.
  - choose_transition(...) -> maps a boundary to an xfade type + duration.
  - render_structured_clip(...) -> splits with trim/atrim and folds the pieces
    left-to-right, reusing effects.transition() for each xfade join and plain
    concat for 'cut' joins.
  - apply_internal_transitions(clip_path, structure_for_clip) -> the orchestrate
    entry point. Inserted as a new step BETWEEN reframe and captions. If a clip
    has fewer than 2 sub-segments it returns the input unchanged (no-op).

Boundary sources (all clip-LOCAL seconds, i.e. relative to the start of this
clip): topic boundaries from a transcript's word gaps, and scene cuts from
ffmpeg's scene-detection. A future structure.py can supply these directly via
``structure_for_clip``; if absent we derive them here so the module is
self-contained.

This build's ffmpeg has xfade/concat/trim/atrim but NO libass/freetype, so we
never touch text filters here (captions happen in a later step).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from pipeline import config
from pipeline.media import ffprobe_info, run_ffmpeg

# --- Transition tuning -----------------------------------------------------
MIN_SEG = 2.5            # sub-segments shorter than this are not worth splitting
MIN_XFADE = 0.25         # shortest crossfade
MAX_XFADE = 0.50         # longest crossfade
HARD_ENERGY_DELTA = 0.18 # RMS-fraction jump above which a beat reads as a "hard" cut


# ---------------------------------------------------------------------------
# Analysis helpers (self-contained; a future structure.py can replace these).
# ---------------------------------------------------------------------------
def detect_scene_cuts(clip_path: str, threshold: float = 0.30) -> list[float]:
    """Return clip-local seconds where ffmpeg detects a scene change.

    Uses the ``select='gt(scene,threshold)'`` + showinfo trick and parses the
    ``pts_time`` values from stderr. Returns an empty list on any failure.
    """
    src = Path(clip_path)
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats",
        "-i", str(src.resolve()),
        "-filter_complex", f"select='gt(scene,{threshold})',showinfo",
        "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except Exception:
        return []
    cuts: list[float] = []
    for m in re.finditer(r"pts_time:([0-9.]+)", proc.stderr):
        try:
            cuts.append(round(float(m.group(1)), 3))
        except ValueError:
            continue
    return sorted(set(cuts))


def clip_energy_envelope(clip_path: str, window: float = 0.5) -> list[tuple[float, float]]:
    """Return a coarse [(t, rms_fraction)] audio-energy envelope, clip-local.

    rms_fraction is normalized to [0, 1] across the clip so it is comparable
    regardless of absolute loudness. Falls back to a flat envelope if the audio
    cannot be read (e.g. a silent clip).
    """
    import numpy as np  # lazy: heavy-ish import

    info = ffprobe_info(clip_path)
    dur = info["duration"]
    sr = 8000  # decimated mono PCM is plenty for an energy envelope
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", str(Path(clip_path).resolve()),
        "-vn", "-ac", "1", "-ar", str(sr),
        "-f", "f32le", "-",
    ]
    try:
        raw = subprocess.run(cmd, capture_output=True, check=True).stdout
        samples = np.frombuffer(raw, dtype="<f4")
    except Exception:
        samples = np.zeros(0, dtype="float32")

    if samples.size == 0:
        return [(0.0, 0.0), (max(dur, MIN_XFADE), 0.0)]

    step = max(1, int(sr * window))
    env: list[tuple[float, float]] = []
    for i in range(0, samples.size, step):
        chunk = samples[i:i + step]
        rms = float(np.sqrt(np.mean(np.square(chunk)))) if chunk.size else 0.0
        env.append((round(i / sr, 3), rms))

    peak = max((v for _, v in env), default=0.0)
    if peak <= 0:
        return [(t, 0.0) for t, _ in env]
    return [(t, round(v / peak, 4)) for t, v in env]


def _energy_at(envelope: list[tuple[float, float]], t: float) -> float:
    """Nearest-sample energy lookup."""
    if not envelope:
        return 0.0
    best = envelope[0][1]
    best_dt = abs(envelope[0][0] - t)
    for ts, val in envelope:
        dt = abs(ts - t)
        if dt < best_dt:
            best, best_dt = val, dt
    return best


def _energy_delta(envelope: list[tuple[float, float]], t: float, span: float = 0.4) -> float:
    """Signed energy change across boundary t: mean(after) - mean(before)."""
    if not envelope:
        return 0.0
    before = [v for ts, v in envelope if t - span <= ts < t]
    after = [v for ts, v in envelope if t <= ts < t + span]
    if not before or not after:
        return 0.0
    return (sum(after) / len(after)) - (sum(before) / len(before))


def _snap_to_energy_peak(t: float, envelope: list[tuple[float, float]],
                         window: float = 0.4) -> float:
    """Snap `t` to the nearest local RMS peak within ±window (beat-sync).

    A peak is a sample louder than both neighbors. Returns `t` unchanged when
    the envelope is empty or no peak falls inside the window.
    """
    if len(envelope) < 3:
        return t
    best, best_dist = None, window
    for i in range(1, len(envelope) - 1):
        ts, v = envelope[i]
        if abs(ts - t) > window:
            continue
        if v > envelope[i - 1][1] and v >= envelope[i + 1][1]:
            d = abs(ts - t)
            if d < best_dist:
                best, best_dist = ts, d
    return round(best, 3) if best is not None else t


def topic_boundaries_from_words(
    clip_local_words: list[dict],
    min_gap: float = 0.45,
) -> list[float]:
    """Derive topic-beat seconds from clip-local word timings.

    A boundary sits at the START of a word that follows a pause longer than
    ``min_gap`` — the same signal jump-cut/caption chunking already uses to mark
    a new thought. Words must already be clip-local (start at ~0).
    """
    bounds: list[float] = []
    prev_end: float | None = None
    for w in clip_local_words:
        try:
            ws, we = float(w["start"]), float(w["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if prev_end is not None and (ws - prev_end) > min_gap:
            bounds.append(round(ws, 3))
        prev_end = we
    return bounds


# ---------------------------------------------------------------------------
# Planning.
# ---------------------------------------------------------------------------
def plan_subsegments(
    clip_local_words: list[dict],
    scene_cuts_local: list[float],
    energy: list[tuple[float, float]],
    duration: float,
    min_seg: float = MIN_SEG,
) -> list[dict]:
    """Split a clip into sub-segments at topic/scene beats.

    Returns ``[{start, end, kind}]`` where each sub-segment's ``kind`` describes
    the boundary AT ITS START ('hard' for an RMS jump, 'soft' for a gentle one;
    the first sub-segment is always 'start'). Boundaries closer than ``min_seg``
    to a kept boundary (or the clip edges) are dropped so we never make a stub.
    """
    topic = topic_boundaries_from_words(clip_local_words)
    raw = sorted(b for b in (topic + list(scene_cuts_local))
                 if min_seg < b < duration - min_seg)

    # Beat-sync: snap each boundary onto the nearest audio-energy peak so the
    # transition lands ON the beat instead of just near it.
    raw = sorted(
        b for b in (_snap_to_energy_peak(b, energy) for b in raw)
        if min_seg < b < duration - min_seg
    )

    # Drop boundaries that crowd one another.
    kept: list[float] = []
    for b in raw:
        if not kept or (b - kept[-1]) >= min_seg:
            kept.append(round(b, 3))

    # Build sub-segments. The boundary BEFORE each (non-first) segment sets kind.
    cuts = [0.0, *kept, duration]
    segs: list[dict] = []
    for i in range(len(cuts) - 1):
        s, e = cuts[i], cuts[i + 1]
        if i == 0:
            kind = "start"
        else:
            delta = _energy_delta(energy, s)
            kind = "hard" if abs(delta) >= HARD_ENERGY_DELTA else "soft"
        segs.append({"start": round(s, 3), "end": round(e, 3), "kind": kind})
    return segs


def choose_transition(kind: str, energy_delta: float) -> dict:
    """Map a boundary to an xfade ``{type, duration}``.

    'hard' beats become a plain 'cut' (match-cut energy) or a snappy slide; soft
    beats dissolve/fade. Duration scales with how gentle the beat is: gentle =>
    longer dissolve, punchy => short.
    """
    mag = min(1.0, abs(energy_delta))
    # Gentle beats get a longer crossfade; punchy beats get a short one.
    duration = round(MAX_XFADE - (MAX_XFADE - MIN_XFADE) * mag, 3)

    if kind == "hard":
        # A strong rise reads best as a hard match-cut; a strong drop as a slide.
        ttype = "cut" if energy_delta >= 0 else "slideleft"
        if ttype == "slideleft":
            duration = MIN_XFADE
    else:  # soft
        ttype = "dissolve" if energy_delta < 0 else "fade"
        # Occasional flourish on a near-flat soft beat.
        if mag < 0.04:
            ttype = "circleopen"
    return {"type": ttype, "duration": duration}


# ---------------------------------------------------------------------------
# Rendering.
# ---------------------------------------------------------------------------
def _conform(width: int, height: int, fps: float) -> str:
    """Filter that conforms a piece to a common SAR/fps so xfade/concat accept it."""
    rate = fps if fps and fps > 0 else 30
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
        f"setsar=1,fps={rate}"
    )


def _split_subsegment(
    clip_path: str, start: float, end: float, idx: int,
    width: int, height: int, fps: float,
) -> str:
    """Trim one sub-segment to its own file (video+audio), conformed for joining."""
    src = Path(clip_path)
    out = str(config.CACHE_DIR / f"{src.stem}_seg{idx:03d}.mp4")
    vf = _conform(width, height, fps)
    run_ffmpeg([
        "-i", str(src.resolve()),
        "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
        "-vf", vf,
        "-af", "aresample=async=1:first_pts=0",
        "-c:v", config.VIDEO_ENCODER,
        "-c:a", "aac", "-b:a", "160k",
        "-avoid_negative_ts", "make_zero",
        out,
    ])
    return out


def _concat_cut(piece_a: str, piece_b: str, out_path: str) -> str:
    """Join two pieces with a hard cut (re-encode concat — pieces are conformed)."""
    run_ffmpeg([
        "-i", str(Path(piece_a).resolve()),
        "-i", str(Path(piece_b).resolve()),
        "-filter_complex",
        "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]",
        "-map", "[v]", "-map", "[a]",
        "-c:v", config.VIDEO_ENCODER,
        "-c:a", "aac", "-b:a", "160k",
        out_path,
    ])
    return out_path


def render_structured_clip(
    clip_path: str,
    subsegments: list[dict],
    transitions: list[dict],
    out_path: str | None = None,
) -> str:
    """Split into sub-segments and fold them left-to-right with their transitions.

    ``transitions[i]`` is the join BETWEEN subsegment i and i+1 (so there are
    len(subsegments) - 1 of them). 'cut' joins use plain concat; everything else
    reuses effects.transition()'s xfade+acrossfade. Returns the final path.
    """
    from pipeline.effects import transition as xfade_join

    src = Path(clip_path)
    if len(subsegments) < 2:
        return clip_path

    info = ffprobe_info(clip_path)
    width, height, fps = info["width"], info["height"], info["fps"]

    # 1. Split each sub-segment to its own conformed file.
    pieces: list[str] = []
    for i, seg in enumerate(subsegments):
        if seg["end"] - seg["start"] <= 0.05:
            continue
        pieces.append(
            _split_subsegment(clip_path, seg["start"], seg["end"], i, width, height, fps)
        )
    if len(pieces) < 2:
        return clip_path

    # 2. Fold left-to-right. ``acc`` is the running joined file.
    acc = pieces[0]
    fold_idx = 0
    for i in range(1, len(pieces)):
        join = transitions[i - 1] if i - 1 < len(transitions) else {"type": "cut", "duration": MIN_XFADE}
        joined = str(config.CACHE_DIR / f"{src.stem}_fold{fold_idx:03d}.mp4")
        ttype = join.get("type", "cut")
        if ttype == "cut":
            acc = _concat_cut(acc, pieces[i], joined)
        else:
            # xfade needs the join duration to be shorter than each input.
            dur = float(join.get("duration", MIN_XFADE))
            dur = max(0.05, min(dur, ffprobe_info(acc)["duration"] - 0.05,
                                ffprobe_info(pieces[i])["duration"] - 0.05))
            acc = xfade_join(acc, pieces[i], kind=ttype, duration=dur, out_path=joined)
        fold_idx += 1

    out = out_path or str(src.with_name(src.stem + "_structured.mp4"))
    # Copy the folded result to the conventional output name.
    run_ffmpeg([
        "-i", str(Path(acc).resolve()),
        "-c", "copy",
        str(Path(out).resolve()),
    ])
    return out


# ---------------------------------------------------------------------------
# Orchestrate entry point.
# ---------------------------------------------------------------------------
def apply_internal_transitions(
    clip_path: str,
    structure_for_clip: dict | None = None,
    min_seg: float = MIN_SEG,
    out_path: str | None = None,
) -> str:
    """Give a single clip an internal segmented rhythm. Returns output path.

    Drop-in step BETWEEN reframe and captions in orchestrate. ``structure_for_clip``
    may carry pre-computed clip-LOCAL signals (any subset of)::

        {
          "words":      [{start,end,word}, ...],   # clip-local seconds
          "scene_cuts": [s, ...],                  # clip-local seconds
          "energy":     [(t, rms_fraction), ...],  # clip-local
        }

    Anything missing is derived here. If the clip yields fewer than 2
    sub-segments (too short / no internal beats) this is a NO-OP and the input
    path is returned unchanged.
    """
    structure_for_clip = structure_for_clip or {}
    info = ffprobe_info(clip_path)
    duration = info["duration"]
    if duration < 2 * min_seg:
        return clip_path  # too short to split meaningfully

    words = structure_for_clip.get("words") or []
    scene_cuts = structure_for_clip.get("scene_cuts")
    if scene_cuts is None:
        scene_cuts = detect_scene_cuts(clip_path)
    energy = structure_for_clip.get("energy")
    if energy is None:
        energy = clip_energy_envelope(clip_path)

    subsegments = plan_subsegments(words, scene_cuts, energy, duration, min_seg=min_seg)
    if len(subsegments) < 2:
        return clip_path  # no internal beats -> no-op

    transitions = [
        choose_transition(seg["kind"], _energy_delta(energy, seg["start"]))
        for seg in subsegments[1:]
    ]
    return render_structured_clip(clip_path, subsegments, transitions, out_path=out_path)
