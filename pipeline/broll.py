"""V2.3 — Stock b-roll: cover footage over speech segments.

plan_broll: an LLM pass over the clip transcript picks sentences that benefit
from cover footage and emits search queries. search_broll: Pexels Videos API
(free, PEXELS_API_KEY) -> download -> pre-normalize ONCE to the clip's frame
(cache/broll, content-addressed). overlay_media: one ffmpeg pass compositing
each b-roll full-frame with enable='between(t,...)' — same core-overlay
technique as the captions (this ffmpeg build has no libass), original audio
untouched.

Taste rails: b-roll never covers the first 3s (the hook shows the speaker) and
totals at most ~30% of the clip.
"""

from __future__ import annotations

import hashlib
import json
import urllib.parse
import urllib.request
from pathlib import Path

from pipeline import config
from pipeline.media import run_ffmpeg

BROLL_DIR = config.CACHE_DIR / "broll"
HOOK_GUARD_S = 3.0
MAX_COVER_RATIO = 0.30

_SYSTEM = """You pick b-roll (cover footage) moments for ONE short vertical \
clip. You get a word-timestamped transcript (clip-local seconds). Choose up to \
{max_events} sentences that would be CLEARER or more engaging with stock \
footage on top (concrete nouns/actions: places, objects, activities — not \
abstract talk). For each, give a 1-3 word ENGLISH stock-video search query.

Rules:
- Never start before {hook}s (the hook must show the speaker).
- Each event 2-5 seconds; events must not overlap.
- Together they must cover at most {max_ratio}% of the clip.
Return ONLY JSON: {{"events": [{{"start": <s>, "end": <s>, "query": "..."}}]}}
"""


def plan_broll(words: list[dict], max_events: int = 3) -> list[dict]:
    """LLM pass: transcript -> [{start, end, query}] (clip-local seconds)."""
    if not words:
        return []
    dur = words[-1]["end"]
    api_key, base_url, model = config.llm_settings()
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=base_url) if base_url \
        else OpenAI(api_key=api_key)

    transcript = "\n".join(
        f"[{w['start']:.2f}-{w['end']:.2f}] {w['word']}" for w in words)
    system = _SYSTEM.format(max_events=max_events, hook=HOOK_GUARD_S,
                            max_ratio=int(MAX_COVER_RATIO * 100))
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": f"Clip transcript:\n{transcript}"}],
        temperature=0.3,
        **config.json_response_format(base_url),
    )
    data = config.extract_json(resp.choices[0].message.content)

    events, covered, last_end = [], 0.0, HOOK_GUARD_S
    for e in data.get("events", []):
        try:
            s, t = float(e["start"]), float(e["end"])
            q = str(e["query"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
        s = max(s, HOOK_GUARD_S, last_end)
        t = min(t, dur, s + 5.0)
        if t - s < 1.5 or not q:
            continue
        if covered + (t - s) > dur * MAX_COVER_RATIO:
            break
        events.append({"start": round(s, 2), "end": round(t, 2), "query": q})
        covered += t - s
        last_end = t
    return events[:max_events]


def _http_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={
        "Authorization": config.PEXELS_API_KEY, "User-Agent": "shorts-mcp"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def search_broll(query: str, width: int = 1080, height: int = 1920,
                 fps: float = 30.0) -> str | None:
    """Pexels search -> download best portrait hit -> normalized cached mp4."""
    if not config.PEXELS_API_KEY:
        raise RuntimeError(
            "PEXELS_API_KEY is not set. Get a free key at "
            "https://www.pexels.com/api/ and add it to .env")
    BROLL_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{query}:{width}x{height}@{fps:.0f}".encode()) \
        .hexdigest()[:12]
    norm = BROLL_DIR / f"{key}.mp4"
    if norm.exists():
        return str(norm)

    url = ("https://api.pexels.com/videos/search?"
           + urllib.parse.urlencode({"query": query, "orientation": "portrait",
                                     "per_page": 3, "size": "medium"}))
    data = _http_json(url)
    videos = data.get("videos") or []
    if not videos:
        return None

    # Best file: portrait, closest height >= target (or the tallest available).
    best = None
    for v in videos:
        for f in v.get("video_files", []):
            w, h = f.get("width") or 0, f.get("height") or 0
            if h < w:  # not portrait
                continue
            score = (0 if h >= height else 1, abs(h - height))
            if best is None or score < best[0]:
                best = (score, f["link"])
    if not best:
        return None

    raw = BROLL_DIR / f"{key}_raw.mp4"
    req = urllib.request.Request(best[1], headers={"User-Agent": "shorts-mcp"})
    with urllib.request.urlopen(req, timeout=120) as r, open(raw, "wb") as fh:
        fh.write(r.read())

    run_ffmpeg([
        "-i", str(raw),
        "-vf", (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},fps={fps:g}"),
        "-an", "-t", "12",
        "-c:v", config.VIDEO_ENCODER,
        str(norm),
    ])
    raw.unlink(missing_ok=True)
    return str(norm)


def normalize_media(path: str, width: int = 1080, height: int = 1920,
                    fps: float = 30.0, still_duration: float = 4.0) -> str:
    """Normalize ANY local video or image into a b-roll-ready clip (cached).

    Images become a still clip of `still_duration` seconds. This is what lets
    user-library assets be used wherever Pexels footage can.
    """
    src = Path(path)
    if not src.exists():
        raise ValueError(f"Media not found: {path}")
    BROLL_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(
        f"{src.resolve()}:{src.stat().st_mtime}:{width}x{height}@{fps:.0f}"
        f":{still_duration}".encode()).hexdigest()[:12]
    norm = BROLL_DIR / f"local_{key}.mp4"
    if norm.exists():
        return str(norm)

    vf = (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
          f"crop={width}:{height},fps={fps:g}")
    is_image = src.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif")
    if is_image:
        run_ffmpeg(["-loop", "1", "-t", f"{still_duration:.2f}",
                    "-i", str(src), "-vf", vf, "-an",
                    "-c:v", config.VIDEO_ENCODER, str(norm)])
    else:
        run_ffmpeg(["-i", str(src), "-vf", vf, "-an", "-t", "20",
                    "-c:v", config.VIDEO_ENCODER, str(norm)])
    return str(norm)


def overlay_media(clip_path: str, events: list[dict],
                  out_path: str | None = None) -> str:
    """Composite full-frame video overlays, each enabled for its window.

    events: [{"start", "end", "path"}] — path is a pre-normalized video
    matching the clip's frame size. Audio passes through untouched.
    """
    src = Path(clip_path)
    events = [e for e in events if e.get("path") and Path(e["path"]).exists()]
    if not events:
        return clip_path

    inputs: list[str] = ["-i", str(src.resolve())]
    for e in events:
        inputs += ["-i", str(Path(e["path"]).resolve())]

    steps, label = [], "0:v"
    for i, e in enumerate(events):
        s, t = float(e["start"]), float(e["end"])
        # Shift the b-roll's PTS so its first frame appears at its window.
        steps.append(f"[{i + 1}:v]setpts=PTS+{s:.3f}/TB[b{i}]")
        nxt = f"v{i}"
        steps.append(
            f"[{label}][b{i}]overlay=0:0:eof_action=pass:"
            f"enable='between(t,{s:.3f},{t:.3f})'[{nxt}]")
        label = nxt

    out = out_path or str(src.with_name(src.stem + "_broll.mp4"))
    run_ffmpeg([
        *inputs,
        "-filter_complex", ";".join(steps),
        "-map", f"[{label}]", "-map", "0:a?",
        "-c:v", config.VIDEO_ENCODER,
        "-c:a", "copy",
        str(Path(out).resolve()),
    ])
    return out
