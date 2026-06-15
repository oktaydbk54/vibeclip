"""Voice dubbing — replace a clip's spoken audio with a translated voice (B1).

Pipeline per clip:
  1. Translate each transcript SEGMENT (a spoken sentence) into the target
     language — segment granularity gives the TTS natural prosody, unlike the
     4-word caption chunks used for subtitles.
  2. Synthesize each translated sentence with the provider-agnostic
     `pipeline.tts.synthesize` (OpenAI / ElevenLabs / local piper).
  3. TIME-FIT: a translated sentence rarely matches the original's duration, so
     if the synthesized clip overruns its [start, end] window we speed it up
     (atempo, capped at config.TTS_MAX_SPEEDUP) rather than let dub drift out of
     sync. Shorter utterances simply sit at their start with trailing silence.
  4. Lay every fitted utterance onto a silent bed at its original start time and
     mux that voice track in place of the clip's audio — so the downstream
     music/ambience/sfx/fade stages still layer on top of the dubbed voice.

Everything degrades GRACEFULLY: a translation or TTS failure returns the
ORIGINAL clip untouched (original-language audio) instead of breaking a render.
"""

from __future__ import annotations

from pathlib import Path

from pipeline import config, tts
from pipeline.media import ffprobe_info, run_ffmpeg
from pipeline.translate import translate_lines


def _audio_dur(path: str) -> float:
    try:
        return float(ffprobe_info(path).get("duration", 0.0)) or 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def _fit(seg_path: str, window: float, stem: str, idx: int) -> str:
    """Speed a synthesized utterance up to fit `window` seconds (capped). A clip
    that already fits — or a window we can't measure — is returned unchanged."""
    d = _audio_dur(seg_path)
    if window <= 0 or d <= window + 0.05:
        return seg_path
    factor = min(config.TTS_MAX_SPEEDUP, d / window)
    if factor <= 1.01:
        return seg_path
    out = str(config.CACHE_DIR / f"{stem}_dubfit{idx:03d}.wav")
    run_ffmpeg(["-i", seg_path, "-filter:a", f"atempo={factor:.4f}", out])
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

    translations = translate_lines([s["text"].strip() for s in segs], target_lang)
    if translations is None:
        return clip_path  # graceful: keep the original-language audio

    f = speed if abs(speed) > 1e-6 else 1.0
    total_dur = _audio_dur(clip_path)
    stem = src.stem

    placed: list[tuple[str, float]] = []  # (audio_path, start_in_current_timeline)
    for i, (seg, text) in enumerate(zip(segs, translations)):
        start = max(0.0, float(seg["start"]) / f)
        window = max(0.0, (float(seg["end"]) - float(seg["start"])) / f)
        raw = str(config.CACHE_DIR / f"{stem}_dub{i:03d}.wav")
        if not tts.synthesize(text, raw, voice=voice):
            continue
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
    run_ffmpeg([
        *inputs,
        "-filter_complex", filtergraph,
        "-map", "0:v", "-map", "[mix]",
        "-t", f"{total_dur:.3f}",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        str(Path(out).resolve()),
    ])
    return out
