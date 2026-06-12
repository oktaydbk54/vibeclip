"""Faz 5f — Energy/mood-matched music + topic-keyed ambience bed.

Picks a music track whose energy matches the clip (loud/animated -> energetic,
quiet/even -> calm) and, optionally, lays a subtle ambience bed (room/nature/
city/crowd) chosen from the clip's topic label. Both feed the existing ducking
mixer in pipeline.audio so the caller no longer has to hand-pick a track.

Local library (filenames only; the user drops the actual audio files in):
    assets/music/{calm,neutral,energetic}/*.m4a
    assets/ambience/{room,nature,city,crowd}.m4a

Energy analysis is done with ffmpeg's `astats` (no extra deps). If librosa is
installed we also estimate tempo; otherwise we stay energy-only. Mood/ambience
selection can use the configured LLM (config.llm_settings) and gracefully falls
back to a keyword map when no key / no library file is present.

Everything here is pure: functions return paths (or None) and never mutate
shared state. Heavy libs are lazy-imported inside the functions.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from pipeline import config
from pipeline.media import ffprobe_info, run_ffmpeg  # noqa: F401  (run_ffmpeg used by add_ambience)

# --- Library layout --------------------------------------------------------
MUSIC_DIR = config.ROOT / "assets" / "music"
AMBIENCE_DIR = config.ROOT / "assets" / "ambience"

MUSIC_BUCKETS = ("calm", "neutral", "energetic")
AMBIENCE_KINDS = ("room", "nature", "city", "crowd")
MUSIC_EXTS = (".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg")

# Per-bucket music volume (energetic sits a touch louder, calm quieter).
_MUSIC_VOLUME = {"calm": 0.12, "neutral": 0.16, "energetic": 0.22}
_DEFAULT_MUSIC_VOLUME = 0.18
_DEFAULT_AMBIENCE_VOLUME = 0.06

# Keyword -> ambience fallback when no LLM (or LLM declines).
_AMBIENCE_KEYWORDS = {
    "nature": ("nature", "forest", "outdoor", "hike", "mountain", "ocean", "beach",
               "garden", "wild", "animal", "bird", "river", "camp", "park", "rain"),
    "city": ("city", "urban", "street", "traffic", "downtown", "commute", "subway",
             "car", "drive", "travel", "airport", "highway"),
    "crowd": ("crowd", "audience", "event", "conference", "stadium", "concert",
              "party", "market", "sport", "game", "fans", "festival"),
    "room": ("office", "home", "studio", "desk", "interview", "podcast", "meeting",
             "kitchen", "indoor", "room", "work", "talk", "chat"),
}


# --- Energy analysis -------------------------------------------------------
def _astats_rms(clip_path: str) -> tuple[float, float]:
    """Return (rms_mean, rms_var) of the clip's audio, both in [0,1]-ish RMS.

    Uses ffmpeg `astats` per-window RMS_level (dBFS), converts to linear RMS,
    and returns the mean and variance across windows. This is the local stand-in
    for structure.analyze_audio_energy (which this repo build does not ship).
    """
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(Path(clip_path).resolve()),
        "-af",
        "astats=metadata=1:reset=1:length=0.5,"
        "ametadata=print:key=lavfi.astats.Overall.RMS_level",
        "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    # astats prints to stderr.
    text = proc.stderr or ""
    db_vals: list[float] = []
    for m in re.finditer(r"RMS_level=(-?\d+(?:\.\d+)?|-inf)", text):
        raw = m.group(1)
        if raw == "-inf":
            db_vals.append(-90.0)
            continue
        try:
            db_vals.append(float(raw))
        except ValueError:
            continue
    if not db_vals:
        return 0.0, 0.0

    # dBFS -> linear amplitude (0..1).
    lin = [10.0 ** (d / 20.0) for d in db_vals]
    n = len(lin)
    mean = sum(lin) / n
    var = sum((x - mean) ** 2 for x in lin) / n
    return mean, var


def _tempo(clip_path: str) -> float | None:
    """Estimate tempo (BPM) via librosa if available, else None."""
    try:
        import librosa  # type: ignore
    except Exception:
        return None
    try:
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            y, sr = librosa.load(str(Path(clip_path).resolve()), sr=22050, mono=True)
            if y is None or len(y) == 0:
                return None
            tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        t = float(tempo)
        return round(t, 1) if t > 0 else None
    except Exception:
        return None


def analyze_clip_energy(clip_path: str, structure_energy: dict | None = None) -> dict:
    """Return {rms_mean, rms_var, tempo?} for a clip.

    If `structure_energy` (a precomputed {rms_mean, rms_var,...} from a
    structure-analysis pass) is supplied, we trust it and skip the astats pass;
    otherwise we compute RMS locally with ffmpeg. Tempo is added only when
    librosa is importable.
    """
    if structure_energy and "rms_mean" in structure_energy:
        rms_mean = float(structure_energy.get("rms_mean", 0.0))
        rms_var = float(structure_energy.get("rms_var", 0.0))
    else:
        rms_mean, rms_var = _astats_rms(clip_path)

    out = {"rms_mean": round(rms_mean, 5), "rms_var": round(rms_var, 6)}
    tempo = _tempo(clip_path)
    if tempo is not None:
        out["tempo"] = tempo
    return out


# --- Library helpers -------------------------------------------------------
def _pick_track(bucket: str, seed: str = "") -> str | None:
    """Pick a track file from a music bucket. Deterministic given the seed."""
    folder = MUSIC_DIR / bucket
    if not folder.is_dir():
        return None
    tracks = sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in MUSIC_EXTS and not p.name.startswith(".")
    )
    if not tracks:
        return None
    # Stable rotation so different clips in a batch don't all get track #1.
    idx = (abs(hash(seed)) % len(tracks)) if seed else 0
    return str(tracks[idx].resolve())


def _bucket_from_energy(rms_mean: float, rms_var: float, tempo: float | None) -> str:
    """Map energy (and optional tempo) to a music bucket via a simple score."""
    # rms_mean of clean speech sits roughly 0.03..0.25 in linear RMS.
    score = 0.0
    if rms_mean >= 0.18:
        score += 2.0
    elif rms_mean >= 0.10:
        score += 1.0
    elif rms_mean <= 0.04:
        score -= 1.5

    # High variance = dynamic / animated delivery -> more energetic.
    if rms_var >= 0.010:
        score += 1.0
    elif rms_var <= 0.002:
        score -= 0.5

    if tempo is not None:
        if tempo >= 120:
            score += 1.0
        elif tempo <= 80:
            score -= 1.0

    if score >= 1.5:
        return "energetic"
    if score <= -1.0:
        return "calm"
    return "neutral"


def _mood_to_bucket(mood: str) -> str | None:
    m = (mood or "").strip().lower()
    if m in MUSIC_BUCKETS:
        return m
    energetic = {"energetic", "hype", "upbeat", "excited", "intense", "fast",
                 "happy", "fun", "punchy", "high"}
    calm = {"calm", "chill", "relaxed", "soft", "mellow", "slow", "serious",
            "somber", "sad", "low", "reflective", "thoughtful"}
    if m in energetic:
        return "energetic"
    if m in calm:
        return "calm"
    if m:
        return "neutral"
    return None


def _llm_mood(topic_label: str) -> str | None:
    """Ask the LLM for a one-word mood tag from the topic label. None on any miss."""
    if not topic_label:
        return None
    try:
        api_key, base_url, model = config.llm_settings()
    except RuntimeError:
        return None
    try:
        from openai import OpenAI

        client = (OpenAI(api_key=api_key, base_url=base_url)
                  if base_url else OpenAI(api_key=api_key))
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content":
                 "You label the musical mood for a short-form video. Reply with "
                 "exactly ONE word from: calm, neutral, energetic. No punctuation."},
                {"role": "user", "content": f"Topic of the clip: {topic_label}"},
            ],
            temperature=0.0,
            max_tokens=4,
        )
        word = (resp.choices[0].message.content or "").strip().lower()
        word = re.sub(r"[^a-z]", "", word)
        return word or None
    except Exception:
        return None


def _llm_ambience(topic_label: str) -> str | None:
    """Ask the LLM for an ambience kind from the topic label. None on any miss."""
    if not topic_label:
        return None
    try:
        api_key, base_url, model = config.llm_settings()
    except RuntimeError:
        return None
    try:
        from openai import OpenAI

        client = (OpenAI(api_key=api_key, base_url=base_url)
                  if base_url else OpenAI(api_key=api_key))
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content":
                 "Pick the background ambience for a short video. Reply with ONLY "
                 "JSON: {\"ambience\": <one of room|nature|city|crowd|none>}. "
                 "Use 'none' if nothing clearly fits."},
                {"role": "user", "content": f"Topic of the clip: {topic_label}"},
            ],
            temperature=0.0,
            max_tokens=20,
            **config.json_response_format(base_url),
        )
        data = config.extract_json(resp.choices[0].message.content or "{}")
        kind = str(data.get("ambience", "")).strip().lower()
        return kind if kind in AMBIENCE_KINDS else None
    except Exception:
        return None


def _ambience_from_keywords(topic_label: str) -> str | None:
    text = (topic_label or "").lower()
    if not text:
        return None
    best, best_hits = None, 0
    for kind, words in _AMBIENCE_KEYWORDS.items():
        hits = sum(1 for w in words if w in text)
        if hits > best_hits:
            best, best_hits = kind, hits
    return best if best_hits > 0 else None


# --- Public selection API --------------------------------------------------
def select_music(clip_path: str, mood_hint: str | None = None) -> str | None:
    """Pick a music track matching the clip's energy/mood. Returns a path or None.

    mood_hint (one of calm|neutral|energetic, or a freeform mood word / topic
    label) biases the bucket; otherwise the bucket comes purely from clip energy.
    The returned path is ready to hand to audio.add_background_music.
    """
    energy = analyze_clip_energy(clip_path)

    bucket = None
    if mood_hint:
        bucket = _mood_to_bucket(mood_hint)
        if bucket is None:
            # Treat the hint as a topic label and let the LLM tag the mood.
            bucket = _mood_to_bucket(_llm_mood(mood_hint) or "")

    if bucket is None:
        bucket = _bucket_from_energy(
            energy["rms_mean"], energy["rms_var"], energy.get("tempo")
        )

    seed = Path(clip_path).stem
    # Try the chosen bucket, then fall back to neighbours so a sparse library
    # (only one folder filled) still yields a track.
    order = [bucket] + [b for b in MUSIC_BUCKETS if b != bucket]
    for b in order:
        track = _pick_track(b, seed=seed)
        if track:
            return track
    return None


def _music_volume_for(track_path: str | None) -> float:
    if not track_path:
        return _DEFAULT_MUSIC_VOLUME
    bucket = Path(track_path).parent.name
    return _MUSIC_VOLUME.get(bucket, _DEFAULT_MUSIC_VOLUME)


def select_ambience(topic_label: str | None) -> str | None:
    """Pick an ambience file from the clip's topic label. None when nothing fits.

    Tries the LLM first (config.llm_settings), then a keyword map. Returns a path
    only if the matching ambience file actually exists in the library.
    """
    if not topic_label:
        return None
    kind = _llm_ambience(topic_label) or _ambience_from_keywords(topic_label)
    if not kind:
        return None
    for ext in MUSIC_EXTS:
        cand = AMBIENCE_DIR / f"{kind}{ext}"
        if cand.is_file():
            return str(cand.resolve())
    return None


def build_soundbed(clip_path: str, topic_label: str | None = None) -> dict:
    """Resolve the full soundbed for a clip.

    Returns {music_path, music_volume, ambience_path, ambience_volume}. Any of the
    *_path values may be None when the library has no suitable file; callers should
    skip the corresponding mix step when a path is None.
    """
    music_path = select_music(clip_path, mood_hint=topic_label)
    ambience_path = select_ambience(topic_label)
    return {
        "music_path": music_path,
        "music_volume": _music_volume_for(music_path),
        "ambience_path": ambience_path,
        "ambience_volume": _DEFAULT_AMBIENCE_VOLUME,
    }


# --- Low-level ambience mixer ---------------------------------------------
def add_ambience(
    clip_path: str,
    ambience_path: str,
    volume: float = _DEFAULT_AMBIENCE_VOLUME,
    out_path: str | None = None,
) -> str:
    """Lay a looping ambience bed UNDER the clip (no ducking). Returns output path.

    Mirrors add_background_music's non-duck branch: the ambience is attenuated and
    amixed beneath the existing audio. No loudnorm here — normalization happens
    once at the end of the edit chain. No sidechain — ambience is meant to sit
    quietly below speech and music alike.
    """
    src = Path(clip_path)
    amb = Path(ambience_path)
    if not amb.exists():
        raise FileNotFoundError(f"Ambience not found: {amb}")

    dur = ffprobe_info(clip_path)["duration"]
    filtergraph = (
        f"[1:a]volume={volume}[amb];"
        f"[0:a][amb]amix=inputs=2:duration=first:dropout_transition=0[aout]"
    )

    out = out_path or str(src.with_name(src.stem + "_amb.mp4"))
    run_ffmpeg([
        "-i", str(src.resolve()),
        "-stream_loop", "-1", "-i", str(amb.resolve()),
        "-filter_complex", filtergraph,
        "-map", "0:v", "-map", "[aout]",
        "-t", f"{dur:.3f}",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        str(Path(out).resolve()),
    ])
    return out
