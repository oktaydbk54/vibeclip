"""Faz 6 — Video structure understanding.

Goal (1): turn a long video into real *story units* by fusing three cheap,
GPU-free signals into a single structural index that downstream modules consume
(clip sub-segments, b-roll placement, music energy):

  (a) analyze_scenes      — ffmpeg scene-change cut times (visual boundaries)
  (b) analyze_audio_energy — windowed RMS + silence spans (audio boundaries)
  (c) segment_topics      — TextTiling/LLM topic boundaries (semantic boundaries)
  (d) build_moment_index  — intersect topics x scenes x silence x word edges
  (e) score_moments       — LLM rubric: Hook / Flow / Value, first-3s judged
                            separately as the hook sub-score

`find_highlights` (in highlights.py) gains an optional `structure=` param so
selection can draw from these real segments; the text-only path stays the
fallback. Nothing here needs a GPU; heavy libs are imported lazily.

Design notes for this machine:
- ffmpeg here has NO libass/freetype. We never use drawtext/subtitles. Any text
  preview uses Pillow PNG + the core `overlay` filter (see render_index_overlay,
  mirroring pipeline/subtitle.py).
- Reuses pipeline.media.run_ffmpeg + ffprobe_info, config.llm_settings, and
  highlights._snap_to_words so boundary-snapping stays consistent.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from pipeline import config
from pipeline.media import ffprobe_info, run_ffmpeg


# ===========================================================================
# (a) Visual boundaries — scene cuts
# ===========================================================================

def analyze_scenes(video_path: str, threshold: float = 0.3) -> list[float]:
    """Return scene-change cut times (seconds) via ffmpeg scene detection.

    Uses the `select='gt(scene,threshold)',showinfo` filter and parses
    `pts_time:` from stderr. `threshold` is a 0..1 scene score (0.3 is a
    moderate cut; lower = more cuts).
    """
    path = Path(video_path)
    if not path.exists():
        raise FileNotFoundError(f"Video not found: {path}")

    # showinfo prints to stderr regardless of -loglevel, so we run ffmpeg
    # directly (run_ffmpeg forces loglevel=error and swallows the output).
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats",
        "-i", str(path.resolve()),
        "-vf", f"select='gt(scene,{threshold})',showinfo",
        "-an", "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    # showinfo lines look like: ... pts_time:3.5 ... scene:...
    times: list[float] = []
    for m in re.finditer(r"pts_time:([0-9]+\.?[0-9]*)", proc.stderr):
        try:
            times.append(round(float(m.group(1)), 3))
        except ValueError:
            continue
    return sorted(set(times))


# ===========================================================================
# (b) Audio boundaries — RMS energy + silence spans
# ===========================================================================

def _decode_mono_wav(video_path: str, sr: int = 16000) -> str:
    """Decode the audio track to a mono 16k PCM wav in the cache. Returns path."""
    src = Path(video_path)
    out = config.CACHE_DIR / f"{src.stem}_energy16k.wav"
    run_ffmpeg([
        "-i", str(src.resolve()),
        "-vn", "-ac", "1", "-ar", str(sr),
        "-c:a", "pcm_s16le",
        str(out.resolve()),
    ])
    return str(out)


def _silence_spans(video_path: str, noise_db: float = -30.0,
                   min_dur: float = 0.4) -> list[tuple[float, float]]:
    """Return [(start, end)] silence spans via ffmpeg silencedetect (stderr)."""
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats",
        "-i", str(Path(video_path).resolve()),
        "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}",
        "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    spans: list[tuple[float, float]] = []
    cur_start: float | None = None
    for m in re.finditer(
        r"silence_(start|end):\s*(-?[0-9]+\.?[0-9]*)", proc.stderr
    ):
        kind, val = m.group(1), float(m.group(2))
        if kind == "start":
            cur_start = val
        elif kind == "end" and cur_start is not None:
            spans.append((round(cur_start, 3), round(val, 3)))
            cur_start = None
    return spans


def analyze_audio_energy(video_path: str, hop: float = 0.5,
                         noise_db: float = -30.0) -> list[dict]:
    """Return windowed audio energy: list of {t, rms, is_silence}.

    Decodes to mono 16k wav, computes windowed RMS with numpy (librosa is used
    if present, else a pure-numpy framing), and flags windows that fall inside
    a silencedetect span. `t` is the window START time in seconds.
    """
    import numpy as np

    wav_path = _decode_mono_wav(video_path)
    sr = 16000

    # Load samples as float32 in [-1, 1]. Prefer librosa if installed.
    samples: "np.ndarray"
    try:
        import librosa  # type: ignore

        samples, sr = librosa.load(wav_path, sr=sr, mono=True)
        samples = samples.astype("float32")
    except Exception:
        import wave

        with wave.open(wav_path, "rb") as wf:
            sr = wf.getframerate()
            n = wf.getnframes()
            raw = wf.readframes(n)
        samples = (np.frombuffer(raw, dtype=np.int16).astype("float32") / 32768.0)

    hop_n = max(1, int(round(hop * sr)))
    win_n = hop_n  # non-overlapping windows of length `hop`
    n_win = max(1, len(samples) // hop_n) if len(samples) else 0

    spans = _silence_spans(video_path, noise_db=noise_db)

    def in_silence(t0: float, t1: float) -> bool:
        return any(s <= t0 and t1 <= e for s, e in spans) or any(
            s < t1 and t0 < e and (min(e, t1) - max(s, t0)) > (t1 - t0) * 0.5
            for s, e in spans
        )

    out: list[dict] = []
    for i in range(n_win):
        a = i * hop_n
        b = min(len(samples), a + win_n)
        chunk = samples[a:b]
        rms = float(np.sqrt(np.mean(chunk * chunk))) if chunk.size else 0.0
        t0 = i * hop
        t1 = t0 + hop
        out.append({
            "t": round(t0, 3),
            "rms": round(rms, 5),
            "is_silence": in_silence(t0, t1),
        })
    return out


def _energy_at(energy: list[dict], start: float, end: float) -> float:
    """Mean RMS over [start, end], normalized 0..1 against the track max."""
    if not energy:
        return 0.0
    vals = [w["rms"] for w in energy if w["t"] >= start - 1e-6 and w["t"] < end]
    if not vals:
        # nearest window
        nearest = min(energy, key=lambda w: abs(w["t"] - start))
        vals = [nearest["rms"]]
    peak = max((w["rms"] for w in energy), default=0.0) or 1.0
    return round(min(1.0, (sum(vals) / len(vals)) / peak), 4)


# ===========================================================================
# (c) Semantic boundaries — topic segmentation
# ===========================================================================

def _sentences_from_transcript(transcript: dict) -> list[dict]:
    """Build sentence-ish units from segments with word-index ranges.

    Each unit: {text, start, end, word_idx_range:[i0,i1)}. Word indices map into
    transcript['words'] so callers can snap on word boundaries later.
    """
    words = transcript.get("words", [])
    segs = transcript.get("segments", [])
    units: list[dict] = []

    wptr = 0
    n_words = len(words)
    for seg in segs:
        s_start, s_end = float(seg["start"]), float(seg["end"])
        # Greedily consume words whose midpoint falls within the segment.
        i0 = wptr
        while wptr < n_words:
            w = words[wptr]
            mid = (float(w["start"]) + float(w["end"])) / 2.0
            if mid < s_start - 0.05:
                wptr += 1
                i0 = wptr
                continue
            if mid > s_end + 0.05:
                break
            wptr += 1
        i1 = wptr
        units.append({
            "text": seg.get("text", "").strip(),
            "start": s_start,
            "end": s_end,
            "word_idx_range": [i0, i1],
        })
    return units


def _texttiling_boundaries(units: list[dict]) -> list[int]:
    """Return indices into `units` that START a new topic (always includes 0).

    Embedding-cosine TextTiling: embed each sentence with sentence-transformers,
    compute adjacent-window cosine similarity, and cut at local depth valleys.
    Returns [0, ...]. Raises ImportError if sentence-transformers is absent so
    the caller can fall back to the LLM path.
    """
    import numpy as np
    from sentence_transformers import SentenceTransformer  # may raise ImportError

    model = SentenceTransformer("all-MiniLM-L6-v2")
    texts = [u["text"] or " " for u in units]
    emb = model.encode(texts, normalize_embeddings=True)
    emb = np.asarray(emb, dtype="float32")

    # Adjacent cosine similarity gap scores.
    sims = np.array([
        float(np.dot(emb[i], emb[i + 1])) for i in range(len(emb) - 1)
    ]) if len(emb) > 1 else np.array([])

    boundaries = [0]
    if sims.size:
        # depth score: how much a valley dips below its neighbors.
        depths = np.zeros_like(sims)
        for i in range(len(sims)):
            left = sims[max(0, i - 1)]
            right = sims[min(len(sims) - 1, i + 1)]
            depths[i] = (left - sims[i]) + (right - sims[i])
        cutoff = float(depths.mean() + depths.std())
        for i, d in enumerate(depths):
            if d > cutoff and d > 0:
                boundaries.append(i + 1)
    return sorted(set(boundaries))


def _lexical_texttiling_boundaries(units: list[dict]) -> list[int]:
    """Dependency-free TextTiling — adjacent-unit lexical (bag-of-words) cosine,
    cut at depth valleys. Returns unit indices that START a new topic (incl. 0).

    Mirrors _texttiling_boundaries' depth-valley logic exactly, but builds the
    similarity from normalized word-count vectors instead of sentence-embeddings
    — so it needs NO model and ALWAYS runs on this machine (sentence-transformers
    is not installed here). Crucially, because it only chooses boundary indices
    AMONG ALL units, every unit is assigned to some topic: the resulting topic
    segmentation covers the ENTIRE transcript, not just the opening (which is the
    failure mode of the single-pass LLM fallback on a long word list).
    """
    import numpy as np
    from collections import Counter

    toks = [Counter(re.findall(r"[a-z0-9']+", (u.get("text") or "").lower()))
            for u in units]
    vocab: dict[str, int] = {}
    for c in toks:
        for w in c:
            vocab.setdefault(w, len(vocab))

    def vec(c: "Counter") -> "np.ndarray":
        v = np.zeros(len(vocab), dtype="float32")
        for w, n in c.items():
            v[vocab[w]] = n
        nrm = float(np.linalg.norm(v))
        return v / nrm if nrm else v

    vecs = [vec(c) for c in toks]
    sims = (np.array([float(np.dot(vecs[i], vecs[i + 1]))
                      for i in range(len(vecs) - 1)])
            if len(vecs) > 1 else np.array([]))

    boundaries = [0]
    if sims.size:
        depths = np.zeros_like(sims)
        for i in range(len(sims)):
            left = sims[max(0, i - 1)]
            right = sims[min(len(sims) - 1, i + 1)]
            depths[i] = (left - sims[i]) + (right - sims[i])
        cutoff = float(depths.mean() + depths.std())
        for i, d in enumerate(depths):
            if d > cutoff and d > 0:
                boundaries.append(i + 1)
    return sorted(set(boundaries))


def _llm_topic_boundaries(transcript: dict) -> list[dict]:
    """LLM fallback: return topic segments as {start,end,label,word_idx_range}.

    Asks the model for topic boundaries by WORD INDEX (so we can snap on words),
    given a numbered word list. Robust to missing keys.
    """
    from openai import OpenAI

    words = transcript.get("words", [])
    if not words:
        return []

    api_key, base_url, model = config.llm_settings()
    client = (OpenAI(api_key=api_key, base_url=base_url)
              if base_url else OpenAI(api_key=api_key))

    numbered = " ".join(f"[{i}]{w['word']}" for i, w in enumerate(words))
    system = (
        "You segment a transcript into TOPIC units. You get words tagged with "
        "their index like [0]Hello [1]world. Return ONLY JSON:\n"
        '{"topics":[{"start_idx":<int>,"end_idx":<int>,"label":"<2-5 words>"}]}\n'
        "Rules: cover all words, contiguous & non-overlapping, end_idx exclusive, "
        "each topic a coherent self-contained idea. Aim for 2-8 topics."
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"Words:\n{numbered}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        data = json.loads(resp.choices[0].message.content)
    except Exception:
        data = {}

    n = len(words)
    topics: list[dict] = []
    for t in data.get("topics", []):
        try:
            i0 = max(0, min(n, int(t["start_idx"])))
            i1 = max(i0 + 1, min(n, int(t["end_idx"])))
        except (KeyError, TypeError, ValueError):
            continue
        topics.append({
            "start": float(words[i0]["start"]),
            "end": float(words[i1 - 1]["end"]),
            "label": str(t.get("label", "")).strip() or "topic",
            "word_idx_range": [i0, i1],
        })
    if not topics:
        # last-resort single topic over the whole transcript
        topics = [{
            "start": float(words[0]["start"]),
            "end": float(words[-1]["end"]),
            "label": "full",
            "word_idx_range": [0, n],
        }]
    return topics


def segment_topics(transcript: dict) -> list[dict]:
    """Return topic segments: list of {start, end, label, word_idx_range}.

    Prefers embedding-cosine TextTiling (sentence-transformers). If that library
    is not installed, falls back to a single LLM pass returning boundaries by
    word index.
    """
    units = _sentences_from_transcript(transcript)
    if not units:
        return _llm_topic_boundaries(transcript)

    # PREFER embedding TextTiling; if sentence-transformers is absent (this
    # machine) or it errors, use the dependency-free LEXICAL TextTiling — both
    # return unit-index boundaries, so topics cover the WHOLE transcript. The
    # single-pass LLM is only a last resort (when neither yields boundaries),
    # because on a long word list it tends to label only the opening and leave
    # the tail uncovered — which collapses a 20-min video to a few early clips.
    try:
        starts = _texttiling_boundaries(units)
    except Exception:
        try:
            starts = _lexical_texttiling_boundaries(units)
        except Exception:
            return _llm_topic_boundaries(transcript)

    topics: list[dict] = []
    for k, s_idx in enumerate(starts):
        e_idx = starts[k + 1] if k + 1 < len(starts) else len(units)
        block = units[s_idx:e_idx]
        if not block:
            continue
        i0 = block[0]["word_idx_range"][0]
        i1 = block[-1]["word_idx_range"][1]
        label_text = block[0]["text"].strip()
        label = " ".join(label_text.split()[:6]) or "topic"
        topics.append({
            "start": round(block[0]["start"], 3),
            "end": round(block[-1]["end"], 3),
            "label": label,
            "word_idx_range": [i0, i1],
        })
    return topics


# ===========================================================================
# (d) Fuse — the moment index
# ===========================================================================

# Platform-sized candidate window. Topic segments are the PRIMARY splitter;
# these guards reshape topic spans into shippable clip lengths:
#   - sub-MIN fragments get MERGED with topically-adjacent neighbors,
#   - over-MAX blobs get SPLIT/trimmed at the best internal silence/sentence cut.
MIN_CLIP_S = 15.0
MAX_CLIP_S = 90.0
# When two adjacent moments are this close (gap), treat them as contiguous and
# eligible to merge a short fragment into its neighbor.
MERGE_GAP_S = 2.0


def _nearest(values: list[float], target: float, max_dist: float) -> float | None:
    """Nearest value within max_dist, else None."""
    best, best_d = None, max_dist
    for v in values:
        d = abs(v - target)
        if d <= best_d:
            best, best_d = v, d
    return best


def _snap_edge_to_silence(t: float, spans: list[tuple[float, float]],
                          window: float, prefer_end: bool) -> float:
    """Snap an edge to the nearest silence gap edge within `window` seconds.

    For a clip START we prefer snapping to a silence END (speech onset); for a
    clip END we prefer a silence START (speech offset).
    """
    best, best_d = t, window
    for s, e in spans:
        cand = s if prefer_end else e  # prefer_end=True => clip END => silence start
        d = abs(cand - t)
        if d <= best_d:
            best, best_d = cand, d
    return round(best, 3)


# Words a clean clip should not OPEN on — leading conjunctions / dangling
# discourse markers make a clip feel like it starts mid-thought.
_LEADING_STOPWORDS = {
    "and", "but", "or", "so", "because", "however", "therefore", "thus",
    "then", "also", "yet", "though", "although", "while", "which", "that",
}


def _clean_word_start(start: float, end: float, words: list[dict]) -> float:
    """Advance `start` to a clean sentence/word opening.

    Snaps to the enclosing word boundary (via _snap_to_words) and then, if the
    opening word is a leading conjunction/discourse marker, advances to the next
    word — so clips open on a substantive word, never mid-word or on "and…".
    Never advances past `end` (or leaves < MIN_CLIP_S of room when possible).
    """
    if not words:
        return start
    from pipeline.highlights import _snap_to_words
    snapped_start, _ = _snap_to_words(start, end, words)
    inside = [w for w in words if w["end"] > snapped_start and w["start"] < end]
    # Drop at most one leading conjunction so we don't eat the whole clip.
    if len(inside) >= 2:
        first = str(inside[0].get("word", "")).strip().lower().strip(".,!?;:")
        if first in _LEADING_STOPWORDS:
            nxt = float(inside[1]["start"])
            if end - nxt >= MIN_CLIP_S or end - nxt >= (end - snapped_start) * 0.5:
                return nxt
    return snapped_start


def _best_internal_cut(start: float, end: float, target: float,
                       spans: list[tuple[float, float]],
                       words: list[dict]) -> float:
    """Pick the best place to break a long span near `target` seconds in.

    Prefers a silence-gap edge (clause/sentence pause) close to `target`; falls
    back to the nearest word boundary. Always returns a time strictly inside
    (start, end).
    """
    lo, hi = start + MIN_CLIP_S, end - MIN_CLIP_S
    if hi <= lo:
        return min(end - 0.5, max(start + 0.5, target))
    target = min(hi, max(lo, target))
    # Candidate silence edges inside the legal window.
    cands = [s for (s, e) in spans if lo <= s <= hi]
    cands += [e for (s, e) in spans if lo <= e <= hi]
    if not cands and words:
        cands = [float(w["start"]) for w in words if lo <= w["start"] <= hi]
    if not cands:
        return target
    return round(min(cands, key=lambda c: abs(c - target)), 3)


def _split_long_moment(m: dict, spans: list[tuple[float, float]],
                       words: list[dict]) -> list[dict]:
    """Split a >MAX_CLIP_S moment into <=MAX_CLIP_S pieces at silence/word cuts.

    Each piece reuses the parent topic/word range metadata; energy is left to be
    recomputed by the caller. Returns 1+ moments, each within [MIN, MAX] when the
    leftover allows it (a final tail shorter than MIN is merged back).
    """
    start, end = float(m["start"]), float(m["end"])
    if end - start <= MAX_CLIP_S:
        return [m]
    pieces: list[tuple[float, float]] = []
    cur = start
    while end - cur > MAX_CLIP_S:
        target = cur + MAX_CLIP_S * 0.9  # aim near the top of the window
        cut = _best_internal_cut(cur, end, target, spans, words)
        if cut <= cur + 1e-3 or cut >= end:
            break
        pieces.append((cur, cut))
        cur = cut
    pieces.append((cur, end))
    # Fold a too-short tail back into the previous piece.
    if len(pieces) >= 2 and pieces[-1][1] - pieces[-1][0] < MIN_CLIP_S:
        a, _ = pieces[-2]
        _, b = pieces[-1]
        pieces[-2] = (a, b)
        pieces.pop()
    out: list[dict] = []
    for ps, pe in pieces:
        piece = dict(m)
        piece["start"] = round(ps, 3)
        piece["end"] = round(pe, 3)
        piece["duration"] = round(pe - ps, 3)
        out.append(piece)
    return out


def _merge_short_moments(moments: list[dict]) -> list[dict]:
    """Merge sub-MIN_CLIP_S moments into a topically-contiguous neighbor.

    A short fragment is fused with the adjacent moment when the time gap between
    them is small (<= MERGE_GAP_S) and the merged span stays <= MAX_CLIP_S. This
    runs left-to-right so a fragment prefers merging forward (its idea usually
    continues), else backward.
    """
    if not moments:
        return []
    moments = sorted(moments, key=lambda m: m["start"])
    out: list[dict] = []
    for m in moments:
        dur = float(m["end"]) - float(m["start"])
        if out:
            prev = out[-1]
            gap = float(m["start"]) - float(prev["end"])
            prev_dur = float(prev["end"]) - float(prev["start"])
            merged_dur = float(m["end"]) - float(prev["start"])
            short = dur < MIN_CLIP_S or prev_dur < MIN_CLIP_S
            if short and gap <= MERGE_GAP_S and merged_dur <= MAX_CLIP_S:
                prev["end"] = m["end"]
                prev["duration"] = round(float(prev["end"]) - float(prev["start"]), 3)
                # widen the word range / keep the higher-energy topic label
                if m.get("word_idx_range") and prev.get("word_idx_range"):
                    prev["word_idx_range"] = [prev["word_idx_range"][0],
                                              m["word_idx_range"][1]]
                continue
        out.append(dict(m))
    return out


def build_moment_index(video_path: str, transcript: dict,
                       scene_threshold: float = 0.3,
                       energy_hop: float = 0.5) -> list[dict]:
    """Fuse topic, scene, silence and word boundaries into a moment index.

    Returns list of dicts:
      {start, end, topic, scene_aligned:bool, energy:float,
       has_silence_edges:bool, word_idx_range}

    PRIMARY SPLITTER = transcript topic segments (segment_topics). Audio
    silence + word edges REFINE the boundaries; scene cuts are SNAP-ONLY — a
    chosen start may snap onto a nearby visual cut, but scene cuts never spawn
    candidates. After the per-topic pass, sub-MIN fragments are merged with
    contiguous neighbors and >MAX blobs are split at internal silence/word cuts,
    so every candidate lands in [MIN_CLIP_S, MAX_CLIP_S].
    """
    from pipeline.highlights import _snap_to_words

    words = transcript.get("words", [])
    topics = segment_topics(transcript)            # PRIMARY clip splitter
    scenes = analyze_scenes(video_path, threshold=scene_threshold)  # snap-only
    energy = analyze_audio_energy(video_path, hop=energy_hop)
    spans = _silence_spans(video_path)

    moments: list[dict] = []
    for tp in topics:
        start, end = float(tp["start"]), float(tp["end"])

        # Snap to word boundaries, then advance off any leading conjunction so
        # the clip OPENS on a substantive word, never mid-word / on "and…".
        start = _clean_word_start(start, end, words)
        _, end = _snap_to_words(start, end, words)

        # Snap edges to silence gaps (speech onset/offset) within a window.
        snapped_start = _snap_edge_to_silence(start, spans, 0.6, prefer_end=True)
        snapped_end = _snap_edge_to_silence(end, spans, 0.6, prefer_end=False)
        has_silence_edges = (snapped_start != start) or (snapped_end != end)
        # only adopt silence-snapped edges if they keep a valid window
        if snapped_end > snapped_start + 0.5:
            start, end = snapped_start, snapped_end

        # SCENE CUTS ARE SNAP-ONLY: nudge the start onto a visual cut ONLY if one
        # sits within a tight window AND it doesn't re-introduce a mid-word edge.
        scene_aligned = False
        near_cut = _nearest(scenes, start, max_dist=0.5)
        if near_cut is not None:
            cut_start, _ = _snap_to_words(near_cut, end, words)
            if abs(cut_start - start) <= 0.5 and end - cut_start > 0.5:
                start = cut_start
                scene_aligned = True

        if end <= start:
            continue

        moments.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
            "topic": tp.get("label", "topic"),
            "scene_aligned": bool(scene_aligned),
            "energy": _energy_at(energy, start, end),
            "has_silence_edges": bool(has_silence_edges),
            "word_idx_range": tp.get("word_idx_range", []),
        })

    # --- Duration guards: reshape topic spans into platform-sized candidates ---
    # 1) MERGE sub-MIN fragments into topically-contiguous neighbors.
    moments = _merge_short_moments(moments)
    # 2) SPLIT >MAX blobs at the best internal silence/sentence boundary.
    split: list[dict] = []
    for m in moments:
        split.extend(_split_long_moment(m, spans, words))
    moments = split
    # 3) Re-clean each start (split pieces need a fresh clean opening) and
    #    recompute duration/energy on the final spans.
    for m in moments:
        cs = _clean_word_start(float(m["start"]), float(m["end"]), words)
        if float(m["end"]) - cs > 0.5:
            m["start"] = round(cs, 3)
        m["duration"] = round(float(m["end"]) - float(m["start"]), 3)
        m["energy"] = _energy_at(energy, float(m["start"]), float(m["end"]))

    moments.sort(key=lambda m: m["start"])
    return moments


# ===========================================================================
# (e) Score — Hook / Flow / Value rubric, first 3s judged separately
# ===========================================================================

_SCORE_SYSTEM = """You are a short-form video strategist scoring candidate \
clips ("moments") cut from a longer video. For EACH moment you receive its \
transcript text and, separately, the transcript of its FIRST 3 SECONDS.

Score each moment on these axes (0-100):
- hook: how strongly the moment as a whole pulls a viewer in.
- flow: pacing/coherence as a self-contained story unit (clear beginning->payoff).
- value: insight/entertainment/emotional payoff a viewer takes away.
- hook_first3s: judged ONLY from the FIRST 3 SECONDS text — would a scroller stop?
Then set score = a weighted blend you choose that best predicts virality
(weight the first-3s hook heavily). Also write a punchy `title` and one-line
`reason`.

Return ONLY JSON:
{"moments":[{"index":<int matching input>,"hook":<int>,"flow":<int>,
"value":<int>,"hook_first3s":<int>,"score":<int>,"title":"...","reason":"..."}]}
"""


def _words_text(words: list[dict], start: float, end: float) -> str:
    return " ".join(
        w["word"] for w in words
        if w["end"] > start and w["start"] < end
    ).strip()


def _heuristic_scores(moments: list[dict], transcript: dict) -> list[dict]:
    """No-LLM fallback: derive scores from energy, duration, position."""
    words = transcript.get("words", [])
    out = []
    for i, m in enumerate(moments):
        energy = m.get("energy", 0.0)
        dur = m.get("duration", 0.0)
        dur_fit = 100 - min(100, abs(dur - 30) * 2)  # ~30s sweet spot
        base = int(0.5 * dur_fit + 50 * energy)
        scored = dict(m)
        scored.update({
            "hook": min(100, base + (10 if m.get("scene_aligned") else 0)),
            "flow": min(100, base),
            "value": min(100, base),
            "hook_first3s": min(100, int(60 * energy) + 30),
            "score": min(100, base),
            "title": (m.get("topic") or "Clip")[:60],
            "reason": "heuristic (no LLM key): scored on energy + duration fit",
        })
        out.append(scored)
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


def score_moments(moments: list[dict], transcript: dict,
                  platform: str = "youtube_shorts",
                  llm_settings=None) -> list[dict]:
    """Add hook/flow/value/score/hook_first3s/title/reason to each moment.

    The LLM rubric scores the FIRST 3 SECONDS separately as the hook sub-score.
    `llm_settings` may be passed for testing; defaults to config.llm_settings.
    Falls back to a heuristic when no LLM key is configured.
    """
    if not moments:
        return []

    words = transcript.get("words", [])
    try:
        api_key, base_url, model = (llm_settings or config.llm_settings)()
    except RuntimeError:
        return _heuristic_scores(moments, transcript)

    from openai import OpenAI

    client = (OpenAI(api_key=api_key, base_url=base_url)
              if base_url else OpenAI(api_key=api_key))

    blocks = []
    for i, m in enumerate(moments):
        full = _words_text(words, m["start"], m["end"]) or m.get("topic", "")
        first3 = _words_text(words, m["start"], m["start"] + 3.0) or full[:120]
        blocks.append(
            f"--- moment {i} (dur={m.get('duration', 0)}s, "
            f"energy={m.get('energy', 0)}, scene_aligned={m.get('scene_aligned')})\n"
            f"FULL: {full}\n"
            f"FIRST3S: {first3}"
        )

    from pipeline.highlights import PLATFORM_HINTS
    hint = PLATFORM_HINTS.get(platform, PLATFORM_HINTS["youtube_shorts"])
    user = f"Platform: {hint}\n\nMoments:\n" + "\n\n".join(blocks)

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SCORE_SYSTEM},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        data = json.loads(resp.choices[0].message.content)
    except Exception:
        return _heuristic_scores(moments, transcript)

    by_index = {}
    for s in data.get("moments", []):
        try:
            by_index[int(s["index"])] = s
        except (KeyError, TypeError, ValueError):
            continue

    def _i(d, k, default=0):
        try:
            return max(0, min(100, int(d.get(k, default))))
        except (TypeError, ValueError):
            return default

    out: list[dict] = []
    for i, m in enumerate(moments):
        s = by_index.get(i, {})
        scored = dict(m)
        scored.update({
            "hook": _i(s, "hook"),
            "flow": _i(s, "flow"),
            "value": _i(s, "value"),
            "hook_first3s": _i(s, "hook_first3s"),
            "score": _i(s, "score"),
            "title": str(s.get("title", m.get("topic", "")))[:80],
            "reason": str(s.get("reason", "")),
        })
        out.append(scored)

    out.sort(key=lambda x: x["score"], reverse=True)
    return out


# ===========================================================================
# Top-level convenience + (optional) PNG-overlay preview
# ===========================================================================

def analyze_structure(video_path: str, transcript: dict,
                      platform: str = "youtube_shorts",
                      scene_threshold: float = 0.3) -> list[dict]:
    """End-to-end: build the moment index and score it. Returns scored moments.

    This is the function downstream modules (and find_highlights' optional
    `structure=` param) should call.
    """
    moments = build_moment_index(video_path, transcript,
                                 scene_threshold=scene_threshold)
    return score_moments(moments, transcript, platform=platform)


def render_index_overlay(video_path: str, moments: list[dict],
                         out_path: str | None = None) -> str:
    """Debug preview: burn the top moment's title/score as a Pillow PNG overlay.

    Mirrors pipeline/subtitle.py — NO drawtext/libass — so it runs on this
    ffmpeg build. Returns the output video path. Useful to eyeball the index.
    """
    src = Path(video_path)
    info = ffprobe_info(video_path)
    w, h = info["width"], info["height"]

    from PIL import Image, ImageDraw, ImageFont

    font_path = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(font_path, max(24, w // 24))

    lines = [f"{m['score']:>3} {m.get('title','')[:28]}" for m in moments[:5]]
    y = int(h * 0.08)
    for ln in lines:
        draw.text((int(w * 0.05), y), ln, font=font, fill=(255, 214, 10, 255),
                  stroke_width=3, stroke_fill=(0, 0, 0, 255))
        y += int(font.size * 1.3)

    png = str(config.CACHE_DIR / f"{src.stem}_index.png")
    img.save(png)

    out = out_path or str(src.with_name(src.stem + "_index.mp4"))
    run_ffmpeg([
        "-i", str(src.resolve()),
        "-i", png,
        "-filter_complex", "[0:v][1:v]overlay=0:0[v]",
        "-map", "[v]", "-map", "0:a?",
        "-c:v", config.VIDEO_ENCODER,
        "-c:a", "copy",
        str(Path(out).resolve()),
    ])
    return out
