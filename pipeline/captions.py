"""Caption segmentation + sidecar serializers (SRT / WebVTT).

The SAME segmenter feeds both the burned-in PNG captions (subtitle.py) and the
sidecar files exported here — so what a viewer sees on screen matches the .srt
to the word. `subtitle.py` imports `build_caption_segments` from this module;
nothing here renders or re-transcribes (it consumes clip-local word timings the
caller already has from `session.words_for`).
"""

from __future__ import annotations


def build_caption_segments(words: list[dict], clip_start: float = 0.0,
                           max_words: int = 4,
                           max_gap: float = 0.6) -> list[dict]:
    """Group words into caption chunks (clip-local seconds).

    A chunk breaks after `max_words` words or a silence gap > `max_gap`.
    Each chunk: {start, end, text, words:[{start,end,word}]}. Word casing is
    preserved (burn-in applies UPPERCASE at render; sidecar keeps it natural).
    """
    chunks: list[dict] = []
    cur: list[dict] = []

    def flush() -> None:
        if not cur:
            return
        local = [{
            "start": max(0.0, w["start"] - clip_start),
            "end": max(0.0, w["end"] - clip_start),
            "word": w["word"].strip(),
        } for w in cur]
        text = " ".join(w["word"] for w in local).strip()
        if not text:
            return
        chunks.append({
            "start": local[0]["start"],
            "end": local[-1]["end"],
            "text": text,
            "words": local,
        })

    prev_end = None
    for w in words:
        gap = (w["start"] - prev_end) if prev_end is not None else 0.0
        if cur and (len(cur) >= max_words or gap > max_gap):
            flush()
            cur = []
        cur.append(w)
        prev_end = w["end"]
    flush()
    return chunks


def _ts(seconds: float, sep: str) -> str:
    """Format seconds as HH:MM:SS<sep>mmm. sep is ',' for SRT, '.' for VTT."""
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    s = (total_ms // 1000) % 60
    m = (total_ms // 60000) % 60
    h = total_ms // 3600000
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_srt(segments: list[dict]) -> str:
    """Serialize caption segments to SubRip (.srt)."""
    blocks = []
    for i, seg in enumerate(segments, 1):
        blocks.append(
            f"{i}\n"
            f"{_ts(seg['start'], ',')} --> {_ts(seg['end'], ',')}\n"
            f"{seg['text']}\n"
        )
    return "\n".join(blocks)


def to_vtt(segments: list[dict]) -> str:
    """Serialize caption segments to WebVTT (.vtt)."""
    blocks = ["WEBVTT\n"]
    for seg in segments:
        blocks.append(
            f"{_ts(seg['start'], '.')} --> {_ts(seg['end'], '.')}\n"
            f"{seg['text']}\n"
        )
    return "\n".join(blocks)
