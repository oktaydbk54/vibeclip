"""Voice dubbing — replace a clip's spoken audio with a translated voice (B1).

Pipeline per clip:
  1. Work in short, rhythm-true UNITS (refine_units splits one long Whisper
     segment on pauses + clause punctuation), so each utterance lands on the
     speaker's real cadence instead of one long line per segment.
  2. Translate each unit BUDGET-AWARE (translate_lines_fitted): the line must be
     speakable inside its time window, with a tighter alt_short fallback — so the
     voice rarely needs stretching (stretching is what flattens prosody).
  3. Synthesize via the provider-agnostic `pipeline.tts.synthesize` with per-unit
     delivery `instructions` (intonation/emphasis from punctuation + rate).
  4. TWO-SIDED FIT: an overflowing utterance first retries alt_short, then is
     pitch-preservingly stretched (rubberband, else atempo, capped at
     config.TTS_MAX_SPEEDUP); a short one sits at its real start and the original
     pause is preserved (NOT padded with dead air).
  5. Anchor every utterance by ABSOLUTE start (adelay) on a silent bed and mux it
     in place of the clip's audio — so music/ambience/sfx/fade still layer on top
     and a long unit can never push later units out of sync (zero drift).

Everything degrades GRACEFULLY: a translation or TTS failure returns the
ORIGINAL clip untouched (original-language audio) instead of breaking a render.
"""

from __future__ import annotations

import functools
import subprocess
from pathlib import Path

from pipeline import config, tts
from pipeline.media import ffprobe_info, run_ffmpeg
from pipeline.translate import translate_lines, translate_lines_fitted

# Sentence-final vs soft (clause) punctuation — both end a dub unit, so each
# synthesized utterance is a short, natural breath group that lands on the
# speaker's real rhythm instead of one long line per Whisper segment.
_BREAK = (".", "!", "?", "…")
_SOFT = (",", ";", ":", "—")


def _audio_dur(path: str) -> float:
    try:
        return float(ffprobe_info(path).get("duration", 0.0)) or 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def refine_units(words: list[dict], gap: float = 0.35, max_sec: float = 3.5,
                 min_sec: float = 0.6) -> list[dict]:
    """Split word timings into short, rhythm-true dub units.

    A unit breaks on: an inter-word silence >= `gap`, OR a word ending in
    sentence/clause punctuation, OR exceeding `max_sec`. Units shorter than
    `min_sec` are merged forward into the previous one (keeping the real bounds).
    Pure + deterministic; each unit is {start, end, text} (PRE-speed seconds).
    """
    units: list[dict] = []
    cur: list[dict] = []

    def flush() -> None:
        if cur:
            units.append({"start": cur[0]["start"], "end": cur[-1]["end"],
                          "text": " ".join(w["word"] for w in cur).strip()})
            cur.clear()

    for w in words:
        if cur:
            span = w["end"] - cur[0]["start"]
            if (w["start"] - cur[-1]["end"] >= gap) or span > max_sec:
                flush()
        cur.append(w)
        t = (w.get("word") or "").rstrip()
        if t.endswith(_BREAK) or t.endswith(_SOFT):
            flush()
    flush()

    # Absorb sub-min units (rhythm-breaking fragments): a normal-then-tiny pair
    # merges the tiny into the PREVIOUS unit; a LEADING tiny (no previous yet)
    # is held in `pending` and merged forward into the NEXT unit instead.
    out: list[dict] = []
    pending: dict | None = None
    for u in units:
        if (u["end"] - u["start"]) < min_sec:
            if out:
                out[-1]["end"] = u["end"]
                out[-1]["text"] = (out[-1]["text"] + " " + u["text"]).strip()
            elif pending is None:
                pending = dict(u)
            else:  # consecutive leading fragments accumulate
                pending["end"] = u["end"]
                pending["text"] = (pending["text"] + " " + u["text"]).strip()
            continue
        if pending is not None:  # attach held leading fragment to this unit
            u = {"start": pending["start"], "end": u["end"],
                 "text": (pending["text"] + " " + u["text"]).strip()}
            pending = None
        out.append(dict(u))
    if pending is not None:  # everything was sub-min -> keep the lone fragment
        out.append(pending)
    return [u for u in out if u["text"]]


@functools.lru_cache(maxsize=1)
def _ffmpeg_filters() -> str:
    """Cached `ffmpeg -filters` output (empty string if it can't be queried)."""
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                           capture_output=True, text=True, timeout=10)
        return (r.stdout or "") + (r.stderr or "")
    except Exception:  # noqa: BLE001
        return ""


def _has_filter(name: str) -> bool:
    """True if this ffmpeg build exposes `name` (e.g. 'rubberband')."""
    return f" {name} " in _ffmpeg_filters()


def _instructions_for(text: str, window: float) -> str:
    """Derive gpt-4o-mini-tts delivery cues from punctuation + speaking rate —
    no extra model call. Gives the voice real intonation instead of flat TTS."""
    tone = "Match a natural, engaging conversational delivery."
    stripped = text.strip()
    if stripped.endswith("!") or (stripped and stripped.isupper()):
        tone = "Speak energetically, with emphasis."
    elif stripped.endswith("?"):
        tone = "Use a rising, inquisitive intonation."
    elif stripped.endswith(("…", ",")):
        tone = "Speak in a measured, natural cadence."
    rate = (len(stripped) / window) if window > 0 else 0.0
    if rate and rate > config.TTS_TARGET_CPS * 1.15:
        tone += " Speak briskly to keep pace."
    return tone


def _atempo_chain(factor: float) -> str:
    """ffmpeg atempo accepts 0.5–2.0 per instance, so a factor above 2.0 must be
    CHAINED (e.g. 2.4 -> 'atempo=2.0,atempo=1.2'). Without this a tuned-up
    TTS_MAX_SPEEDUP would emit a single out-of-range atempo ffmpeg rejects."""
    parts: list[str] = []
    f = factor
    while f > 2.0:
        parts.append("atempo=2.0")
        f /= 2.0
    parts.append(f"atempo={f:.4f}")
    return ",".join(parts)


def _fit(seg_path: str, window: float, stem: str, idx: int) -> str:
    """Time-stretch a synthesized utterance to fit `window` seconds (capped).
    Uses pitch-preserving rubberband when available (no chipmunk), else a chained
    atempo. A clip that already fits — or a window we can't measure — is returned
    as-is; a stretch FAILURE returns the unstretched unit (never crashes)."""
    d = _audio_dur(seg_path)
    if window <= 0 or d <= window + 0.05:
        return seg_path
    factor = min(config.TTS_MAX_SPEEDUP, d / window)
    if factor <= 1.01:
        return seg_path
    out = str(config.CACHE_DIR / f"{stem}_dubfit{idx:03d}.wav")
    if config.TTS_PITCH_PRESERVE and _has_filter("rubberband"):
        filt = f"rubberband=tempo={factor:.4f}"
    else:
        filt = _atempo_chain(factor)
    try:
        run_ffmpeg(["-i", seg_path, "-filter:a", filt, out])
    except Exception:  # noqa: BLE001 — stretch glitch -> use the raw utterance
        return seg_path
    return out


def apply_dub(clip_path: str, segments: list[dict], target_lang: str,
              speed: float = 1.0, voice: str | None = None,
              out_path: str | None = None) -> str:
    """Return a clip whose voice track is `target_lang`. `segments` are the
    clip-local transcript segments (PRE-speed seconds); `speed` maps them into
    the clip's current (possibly sped) timeline. Falls back to the original clip
    when the target is empty, the transcript is empty, or synthesis fails."""
    target_lang = (target_lang or "").strip()
    src = Path(clip_path)
    segs = [s for s in (segments or []) if (s.get("text") or "").strip()]
    if not target_lang or not segs:
        return clip_path

    f = speed if speed > 1e-6 else 1.0  # guard 0/negative -> no rescale
    # Each unit's window (in the clip's current timeline) and a char budget that
    # keeps the translation speakable inside it — so the voice rarely has to be
    # sped up (which is what flattens prosody).
    windows = [max(0.0, (float(s["end"]) - float(s["start"])) / f) for s in segs]
    budgets = [max(8, int(w * config.TTS_TARGET_CPS)) for w in windows]

    texts = [s["text"].strip() for s in segs]
    fitted = translate_lines_fitted(texts, target_lang, budgets)
    if fitted is None:
        plain = translate_lines(texts, target_lang)
        if plain is None:
            return clip_path  # graceful: keep the original-language audio
        fitted = [{"text": t, "alt_short": t} for t in plain]

    total_dur = _audio_dur(clip_path)
    if total_dur <= 0:
        return clip_path  # can't measure the clip -> don't risk a broken mux
    stem = src.stem

    placed: list[tuple[str, float]] = []  # (audio_path, start_in_current_timeline)
    for i, (seg, tr) in enumerate(zip(segs, fitted)):
        start = max(0.0, float(seg["start"]) / f)
        window = windows[i]
        instr = (_instructions_for(tr["text"], window)
                 if config.TTS_USE_INSTRUCTIONS else None)
        raw = str(config.CACHE_DIR / f"{stem}_dub{i:03d}.wav")
        if not tts.synthesize(tr["text"], raw, voice=voice, instructions=instr):
            continue
        # Overflow: try the tighter alt_short rewrite BEFORE crushing with a
        # time-stretch — a shorter line spoken naturally beats a sped-up one.
        if (window > 0 and _audio_dur(raw) > window * 1.15
                and tr.get("alt_short") and tr["alt_short"] != tr["text"]):
            raw2 = str(config.CACHE_DIR / f"{stem}_dub{i:03d}s.wav")
            if tts.synthesize(tr["alt_short"], raw2, voice=voice,
                              instructions=instr):
                raw = raw2
        # Two-sided fit: a short utterance stays at its real start (the original
        # pause is preserved, NOT filled with dead air); a long one is stretched.
        placed.append((_fit(raw, window, stem, i), round(start, 3)))

    if not placed:
        return clip_path  # nothing synthesized -> leave the clip as-is

    inputs: list[str] = ["-i", str(src.resolve())]
    for path, _ in placed:
        inputs += ["-i", str(Path(path).resolve())]

    parts = [f"anullsrc=r=44100:cl=stereo,atrim=0:{total_dur:.3f}[bed]"]
    labels = ["bed"]
    for i, (_, start) in enumerate(placed, start=1):
        ms = int(round(start * 1000))
        parts.append(
            f"[{i}:a]aformat=sample_rates=44100:channel_layouts=stereo,"
            f"adelay={ms}|{ms}[d{i}]")
        labels.append(f"d{i}")
    mix_in = "".join(f"[{l}]" for l in labels)
    parts.append(f"{mix_in}amix=inputs={len(labels)}:"
                 f"duration=longest:normalize=0[mix]")
    filtergraph = ";".join(parts)

    out = out_path or str(src.with_name(src.stem + "_dub.mp4"))
    try:
        run_ffmpeg([
            *inputs,
            "-filter_complex", filtergraph,
            "-map", "0:v", "-map", "[mix]",
            "-t", f"{total_dur:.3f}",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            str(Path(out).resolve()),
        ])
    except Exception:  # noqa: BLE001 — mux glitch must NOT crash the render
        return clip_path  # HARD CONTRACT: any ffmpeg failure -> original clip
    return out
