"""Faz 1 — Speech-to-text with word-level timestamps.

Uses faster-whisper (CTranslate2). Word timestamps are essential downstream:
the highlight selector needs them to snap clip boundaries to sentence edges,
and the subtitle burner needs them for word-synced captions.

Transcripts are cached by a hash of (file content size+mtime, model) so the
same video is never transcribed twice.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from pipeline import config

# The model is heavy to load; keep one instance per process, keyed by size.
_MODEL_CACHE: dict[str, object] = {}
# BatchedInferencePipeline wrappers, keyed by model size (one per WhisperModel).
_BATCHED_CACHE: dict[str, object] = {}


@dataclass
class Word:
    start: float
    end: float
    word: str


@dataclass
class Segment:
    start: float
    end: float
    text: str


def _cache_key(video_path: Path, model: str) -> str:
    stat = video_path.stat()
    raw = f"{video_path.resolve()}:{stat.st_size}:{int(stat.st_mtime)}:{model}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _get_model(model_size: str):
    if model_size not in _MODEL_CACHE:
        # Imported lazily so the MCP server starts instantly without loading ML libs.
        from faster_whisper import WhisperModel

        _MODEL_CACHE[model_size] = WhisperModel(
            model_size,
            device=config.WHISPER_DEVICE,
            compute_type=config.WHISPER_COMPUTE,
        )
    return _MODEL_CACHE[model_size]


def _get_batched(model_size: str):
    """A BatchedInferencePipeline wrapping the cached WhisperModel, or None if
    this faster-whisper build doesn't ship one (older versions). The batched
    pipeline internally VAD-segments the audio into windows and transcribes
    them in batches, returning segments/words with ABSOLUTE timestamps already
    stitched — same Segment/Word object shape as the sequential path."""
    if model_size in _BATCHED_CACHE:
        return _BATCHED_CACHE[model_size]
    obj = None
    try:
        from faster_whisper import BatchedInferencePipeline  # may be absent
        obj = BatchedInferencePipeline(_get_model(model_size))
    except Exception:  # noqa: BLE001 — fall back to windowed/sequential
        obj = None
    _BATCHED_CACHE[model_size] = obj
    return obj


def _collect(seg_iter, time_offset: float = 0.0) -> tuple[list[dict], list[dict]]:
    """Drain a faster-whisper segment iterator into (segments, words) dicts.

    time_offset is ADDED to every segment/word start/end — this is the exact
    offset stitching used by the windowed fallback. The single-pass and the
    BatchedInferencePipeline paths pass 0.0 because they already emit absolute
    times. Output dict shape is identical for every path so downstream
    consumers (highlights, structure, subtitle, session.words_for) are
    unaffected.
    """
    segments: list[dict] = []
    words: list[dict] = []
    from pipeline import progress as _pg
    for seg in seg_iter:
        # faster-whisper transcribes LAZILY as this generator is drained, so the
        # cancel poll lives here: pressing ✕ during TRANSCRIBING stops at the
        # next segment (no ffmpeg to kill — this is the in-process Whisper pass).
        if _pg.should_cancel():
            raise _pg.CancelledError()
        segments.append(asdict(Segment(seg.start + time_offset,
                                       seg.end + time_offset,
                                       seg.text.strip())))
        for w in seg.words or []:
            words.append(asdict(Word(w.start + time_offset,
                                     w.end + time_offset,
                                     w.word.strip())))
        # Stream "transcribing MM:SS" as windows land (no-op without a job ctx).
        _pg.note(f"transcribing {_fmt(seg.end + time_offset)}")
    return segments, words


def _fmt(t: float) -> str:
    t = max(0.0, t)
    return f"{int(t) // 60:02d}:{int(t) % 60:02d}"


def _vad_windows(path: Path, target: float = 45.0) -> list[tuple[float, float]]:
    """Split audio at VAD silence boundaries into ~`target`-second windows.

    Used only by the windowed fallback (when BatchedInferencePipeline is
    absent). Returns [(start, end)] spans in seconds covering all speech; each
    window is later transcribed independently and merged with its start as the
    exact time offset. Windows are cut at silence so no word straddles a
    boundary. Falls back to one window over the whole file if VAD is
    unavailable.
    """
    from pipeline.media import ffprobe_info
    dur = float(ffprobe_info(str(path)).get("duration", 0.0)) or 0.0
    if dur <= 0:
        return [(0.0, 0.0)]
    try:
        from faster_whisper.audio import decode_audio
        from faster_whisper.vad import VadOptions, get_speech_timestamps
        sr = 16000
        audio = decode_audio(str(path), sampling_rate=sr)
        speech = get_speech_timestamps(
            audio, VadOptions(min_silence_duration_ms=500))
        gaps = [(s["start"] / sr, s["end"] / sr) for s in speech]
    except Exception:  # noqa: BLE001 — no VAD -> single window, still correct
        return [(0.0, dur)]
    if not gaps:
        return [(0.0, dur)]
    # Greedily pack speech spans into ~target-second windows, breaking only at
    # the silence gaps between spans so a window edge never bisects a word.
    windows: list[tuple[float, float]] = []
    win_start = gaps[0][0]
    win_end = gaps[0][1]
    for s, e in gaps[1:]:
        if e - win_start > target:
            windows.append((win_start, win_end))
            win_start = s
        win_end = e
    windows.append((win_start, win_end))
    return windows


def _transcribe_windowed(path: Path, model, windows: list[tuple[float, float]]
                         ) -> tuple[list[dict], list[dict], str | None]:
    """Sequential windowed fallback: transcribe each VAD window via clip_timestamps
    and stitch with EXACT per-window offsets. Returns (segments, words, language).

    Each window is transcribed by clipping to [start, end] (clip_timestamps in
    seconds), then every emitted time has the window's `start` added back so the
    merged timeline is absolute and monotonic across windows.
    """
    all_segs: list[dict] = []
    all_words: list[dict] = []
    language: str | None = None
    for (ws, we) in windows:
        if we <= ws:
            continue
        seg_iter, info = model.transcribe(
            str(path),
            word_timestamps=True,
            vad_filter=False,  # already cut to a speech window
            clip_timestamps=[ws],
        )
        if language is None:
            language = info.language
        # clip_timestamps yields times relative to the clip start (ws); add it
        # back as the exact offset so words stitch onto the global timeline.
        segs, words = _collect(seg_iter, time_offset=0.0)
        # Drop anything past this window's end (the clip runs to EOF) and shift
        # is already absolute because faster-whisper reports clip times as
        # absolute from `ws`. Keep words within [ws, we] (+ small slack).
        segs = [s for s in segs if s["start"] < we + 0.5]
        words = [w for w in words if w["start"] < we + 0.5]
        all_segs.extend(segs)
        all_words.extend(words)
    return all_segs, all_words, language


def transcribe(video_path: str, model_size: str | None = None,
               batched: bool = False) -> dict:
    """Transcribe a video/audio file to word-timestamped text.

    Returns: {language, duration, segments[], words[]} and caches the result by
    (resolved input file, model) — so transcribing the cheap Phase-0 proxy
    caches under the proxy's own key, which is exactly what we want.

    batched=False (default): the original single-pass model.transcribe — kept
    callable so any path that wants the old behavior is unchanged.

    batched=True: the fast analysis path. Uses faster-whisper's
    BatchedInferencePipeline when present (it VAD-windows + batches internally
    and returns absolute, already-stitched word times). When that class is not
    available in the installed build, falls back to a sequential VAD-windowed
    pass that merges each window with its exact start offset. Either way
    word_timestamps stays True and the output dict shape is identical to the
    single-pass path, so downstream _snap_to_words etc. are unaffected.
    """
    path = Path(video_path)
    if not path.exists():
        raise FileNotFoundError(f"Video not found: {path}")

    model_size = model_size or config.WHISPER_MODEL
    cache_file = config.CACHE_DIR / f"transcript_{_cache_key(path, model_size)}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text())

    model = _get_model(model_size)
    language: str | None = None
    duration = 0.0

    if batched:
        pipe = _get_batched(model_size)
        if pipe is not None:
            # BatchedInferencePipeline: internal VAD windowing + batching, with
            # absolute (already-stitched) word times. No manual offset needed.
            seg_iter, info = pipe.transcribe(
                str(path),
                word_timestamps=True,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                batch_size=8,
            )
            segments, words = _collect(seg_iter, time_offset=0.0)
            language, duration = info.language, info.duration
        else:
            # Windowed fallback with exact per-window offset stitching.
            windows = _vad_windows(path)
            segments, words, language = _transcribe_windowed(path, model, windows)
            from pipeline.media import ffprobe_info
            duration = float(ffprobe_info(str(path)).get("duration", 0.0))
    else:
        # VAD filter skips silence -> faster and cleaner timestamps.
        seg_iter, info = model.transcribe(
            str(path),
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        segments, words = _collect(seg_iter, time_offset=0.0)
        language, duration = info.language, info.duration

    result = {
        "language": language,
        "duration": round(duration, 2),
        "segments": segments,
        "words": words,
    }
    cache_file.write_text(json.dumps(result, ensure_ascii=False))
    return result


def transcript_as_text(transcript: dict) -> str:
    """Flatten segments into timestamped lines for an LLM prompt.

    Format: '[12.3s] text' — RAW SECONDS, not mm:ss. mm:ss is ambiguous: models
    read '[01:30]' as the decimal 1.30 instead of 90 seconds, collapsing all
    timestamps toward zero. Seconds are unambiguous and match the start/end the
    model must return.
    """
    lines = []
    for seg in transcript["segments"]:
        lines.append(f"[{seg['start']:.1f}s] {seg['text']}")
    return "\n".join(lines)
