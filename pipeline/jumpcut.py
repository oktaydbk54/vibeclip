"""Faz 5a — Silence / dead-air removal (jump cuts).

Uses the word timestamps we already have. Any pause longer than `max_pause`
between consecutive words is collapsed to `keep_pause` (a small natural gap),
producing tighter, higher-retention clips — the Opus/Descript "auto-cut" effect.

Implemented as a single select/aselect filtergraph so audio and video stay in
sync (kept frames are renumbered with setpts/asetpts and concatenated).
"""

from __future__ import annotations

from pathlib import Path

from pipeline import config
from pipeline.media import ffprobe_info, run_ffmpeg

# Pure hesitation sounds — safe to auto-remove. Turkish discourse fillers
# ("yani", "şey", "hani") are real words half the time, so they are NOT here;
# removing those needs per-occurrence LLM judgment (future "aggressive" mode).
FILLER_WORDS = frozenset({
    "um", "uh", "uhm", "er", "erm", "hmm", "hm", "mm", "mhm",
    "ee", "eee", "ii", "ıı", "ııı", "aa", "aaa", "eh", "ah",
})


def _norm_word(w: str) -> str:
    return w.strip().lower().strip(".,!?;:…\"'")


def _subtract_ranges(
    intervals: list[tuple[float, float]],
    drops: list[tuple[float, float]],
    min_len: float = 0.04,
) -> list[tuple[float, float]]:
    """Remove `drops` spans from keep-`intervals` (both clip-local)."""
    out: list[tuple[float, float]] = []
    for s, e in intervals:
        segs = [(s, e)]
        for ds, de in drops:
            nxt: list[tuple[float, float]] = []
            for a, b in segs:
                if de <= a or ds >= b:
                    nxt.append((a, b))
                    continue
                if ds > a:
                    nxt.append((a, ds))
                if de < b:
                    nxt.append((de, b))
            segs = nxt
        out.extend((a, b) for a, b in segs if b - a > min_len)
    return out


def _union_protect(
    intervals: list[tuple[float, float]],
    protects: list[tuple[float, float]],
    dur: float,
) -> list[tuple[float, float]]:
    """Merge protect spans back into keep-intervals (protect wins over cuts)."""
    spans = [tuple(i) for i in intervals]
    spans += [(max(0.0, float(s)), min(dur, float(e)))
              for s, e in protects if float(e) > float(s)]
    spans.sort()
    merged: list[tuple[float, float]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1] + 1e-6:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def keep_intervals_from_words(
    words: list[dict],
    clip_start: float,
    clip_end: float,
    max_pause: float = 0.5,
    keep_pause: float = 0.15,
    pad: float = 0.05,
    drop_ranges: list[tuple[float, float]] = (),
    drop_words: frozenset[str] = frozenset(),
    protect_ranges: list[tuple[float, float]] = (),
) -> list[tuple[float, float]]:
    """Compute [start, end] keep-windows (in clip-local time) from word timings.

    Words are in SOURCE time; we shift to clip-local by subtracting clip_start.
    Long gaps between words are trimmed to `keep_pause`.

    drop_ranges: clip-local spans to force-cut regardless of speech.
    drop_words: words (normalized) to excise — their spans become forced cuts
    too, since a 0.3s "um" gap is below max_pause and mere exclusion would
    leave its audio in the merged window.
    protect_ranges: clip-local spans the user RESTORED — always kept, applied
    last so they win over both silence cuts and drops.
    """
    drops: list[tuple[float, float]] = [tuple(r) for r in drop_ranges]
    kept_words = []
    for w in words:
        if w["end"] <= clip_start or w["start"] >= clip_end:
            continue
        if drop_words and _norm_word(w["word"]) in drop_words:
            drops.append((max(0.0, w["start"] - clip_start - 0.02),
                          w["end"] - clip_start + 0.02))
            continue
        kept_words.append(w)

    local = [(w["start"] - clip_start, w["end"] - clip_start)
             for w in kept_words]
    if not local:
        dur = clip_end - clip_start
        base = (_subtract_ranges([(0.0, dur)], drops)
                if drops else [(0.0, dur)])
        return (_union_protect(base, list(protect_ranges), dur)
                if protect_ranges else base)

    intervals: list[list[float]] = []
    for ws, we in local:
        ws = max(0.0, ws - pad)
        we = we + pad
        if not intervals:
            intervals.append([ws, we])
            continue
        gap = ws - intervals[-1][1]
        if gap <= max_pause:
            # Small gap: merge (keep speech continuous).
            intervals[-1][1] = we
        else:
            # Big gap: keep only `keep_pause` of it, then start a new window.
            intervals[-1][1] += keep_pause
            intervals.append([ws, we])

    dur = clip_end - clip_start
    result = [(max(0.0, s), min(dur, e)) for s, e in intervals if e > s]
    if drops:
        result = _subtract_ranges(result, drops)
    if protect_ranges:
        result = _union_protect(result, list(protect_ranges), dur)
    return result


def _select_expr(intervals: list[tuple[float, float]]) -> str:
    return "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in intervals)


def _render_keep(src: Path, intervals: list[tuple[float, float]],
                 out: str) -> str:
    expr = _select_expr(intervals)
    filtergraph = (
        f"[0:v]select='{expr}',setpts=N/FRAME_RATE/TB[v];"
        f"[0:a]aselect='{expr}',asetpts=N/SR/TB[a]"
    )
    run_ffmpeg([
        "-i", str(src.resolve()),
        "-filter_complex", filtergraph,
        "-map", "[v]", "-map", "[a]",
        "-c:v", config.VIDEO_ENCODER,
        "-c:a", "aac", "-b:a", "160k",
        str(Path(out).resolve()),
    ])
    return out


def remove_silences(
    clip_path: str,
    words: list[dict],
    clip_start: float = 0.0,
    max_pause: float = 0.5,
    keep_pause: float = 0.15,
    out_path: str | None = None,
    drop_ranges: list[tuple[float, float]] = (),
    remove_fillers: bool = False,
    filler_words: list[str] | None = None,
    protect_ranges: list[tuple[float, float]] = (),
) -> str:
    """Cut dead air out of a clip using word timings. Returns output path."""
    src = Path(clip_path)
    info = ffprobe_info(clip_path)
    clip_end = clip_start + info["duration"]

    drop_words = frozenset()
    if remove_fillers:
        drop_words = frozenset(_norm_word(w) for w in filler_words) \
            if filler_words else FILLER_WORDS

    intervals = keep_intervals_from_words(
        words, clip_start, clip_end, max_pause, keep_pause,
        drop_ranges=drop_ranges, drop_words=drop_words,
        protect_ranges=protect_ranges,
    )
    # Nothing to trim -> return as-is.
    kept = sum(e - s for s, e in intervals)
    if not intervals or kept >= info["duration"] - 0.1:
        return clip_path

    out = out_path or str(src.with_name(src.stem + "_tight.mp4"))
    result = _render_keep(src, intervals, out)
    # Sidecar AFTER the (atomic) render: the resolved intervals only exist
    # here, and the source↔output timemap needs them (Pro Faz 6).
    from pipeline.timemap import write_sidecar
    write_sidecar(result, intervals)
    return result


def remove_ranges(
    clip_path: str,
    drop_ranges: list[tuple[float, float]],
    out_path: str | None = None,
) -> str:
    """Force-cut clip-local [start, end] spans out of a clip (no silence logic).

    Powers the 'trim' stage: "şu kısmı at" / remove_section.
    """
    src = Path(clip_path)
    info = ffprobe_info(clip_path)
    intervals = _subtract_ranges([(0.0, info["duration"])],
                                 [tuple(r) for r in drop_ranges])
    if not intervals:
        raise ValueError("remove_ranges would delete the whole clip.")
    cut = info["duration"] - sum(e - s for s, e in intervals)
    if cut < 0.05:
        return clip_path

    out = out_path or str(src.with_name(src.stem + "_trim.mp4"))
    result = _render_keep(src, intervals, out)
    from pipeline.timemap import write_sidecar
    write_sidecar(result, intervals)
    return result
