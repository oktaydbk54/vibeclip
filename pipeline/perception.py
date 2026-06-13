"""Visual perception for the proposal loop — give the agent eyes.

The proposal loop renders a throwaway PREVIEW artifact (a 540p proxy clip) for
the A/B gate. Before surfacing that gate we can OPTIONALLY pull a few keyframe
thumbnails from the preview and ask a vision-capable model to verify the result
(crop centered? sticker overlapping captions? wrong aspect?). A found problem is
fed back into the bounded planner loop as one extra round of validator feedback.

Everything here is flag-gated (config.VISION_VERIFY) and degrades GRACEFULLY to
current no-vision behavior — `critique_clip` returns {ok:True, problems:[]} when
the flag is off, no LLM key is configured, the provider rejects image input, or
anything goes wrong. So existing behavior/tests are byte-identical when the flag
is unset. Vision/LLM calls reuse config.llm_settings('pro') + json_response_format
exactly like pipeline/editplan.py and chat/planner.py.
"""

from __future__ import annotations

import base64
import json

from pipeline import config
from pipeline.config import CACHE_DIR
from pipeline.media import ffprobe_info, run_ffmpeg

# Cost/latency guards: a small number of small frames, one critique call.
MAX_FRAMES = 4
_DEFAULT_FRAMES = 3

_SYSTEM = (
    "You are a meticulous short-form video QA reviewer. You are shown a few "
    "evenly-spaced keyframes from a vertical (or square/wide) clip the editor "
    "just produced, plus a short summary of what the edit was meant to do. "
    "Judge ONLY clear, objective visual defects you can SEE in the frames:\n"
    "- the subject/face is badly off-center or cropped out of frame\n"
    "- a sticker/overlay/emoji overlaps or hides the captions or the speaker's face\n"
    "- captions run off the edge, overlap each other, or are unreadable\n"
    "- the framing is the wrong aspect (e.g. pillarboxed/letterboxed when it "
    "should be full-frame vertical)\n"
    "Do NOT critique content, taste, color grading, or anything you cannot "
    "directly see. If the frames look fine, say so. Return ONLY a JSON object: "
    '{"ok": true|false, "problems": ["short actionable defect", ...]}. '
    "When ok is true, problems MUST be []. Keep each problem to one short "
    "imperative phrase the editor can act on (e.g. 'recenter the crop on the "
    "speaker', 'move the sticker off the captions')."
)


def extract_keyframes(video_path: str, n: int = _DEFAULT_FRAMES) -> list[str]:
    """Write up to `n` evenly-spaced JPEG keyframes from `video_path` into
    CACHE_DIR and return their paths. The preview proxy is 540p so this is cheap.

    Returns [] on any failure (missing file, ffprobe/ffmpeg error) so callers can
    fall back to no-vision silently. Frames are hash-named off the source path +
    timestamp so re-previewing the same artifact reuses them as cache.
    """
    n = max(1, min(MAX_FRAMES, int(n)))
    try:
        info = ffprobe_info(video_path)
    except Exception:  # noqa: BLE001 — any probe failure -> no frames (no-vision)
        return []
    dur = float(info.get("duration") or 0.0)
    if dur <= 0:
        return []
    # Sample at the interior of each of n equal segments (avoid the very first/
    # last frame which can be a fade-in/out or black).
    stamps = [dur * (i + 0.5) / n for i in range(n)]
    import hashlib
    key = hashlib.sha1(video_path.encode("utf-8")).hexdigest()[:12]
    out: list[str] = []
    for i, t in enumerate(stamps):
        dst = CACHE_DIR / f"kf_{key}_{n}_{i}.jpg"
        if not dst.exists():
            try:
                # -ss before -i = fast keyframe seek; scale long edge down so the
                # data URI stays small even if the source isn't a 540p proxy.
                run_ffmpeg(["-ss", f"{t:.3f}", "-i", str(video_path),
                            "-frames:v", "1",
                            "-vf", "scale='min(540,iw)':-2",
                            "-q:v", "4", str(dst)])
            except Exception:  # noqa: BLE001 — skip a bad frame, keep the rest
                continue
        if dst.exists():
            out.append(str(dst))
    return out


def _data_uri(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"
    except Exception:  # noqa: BLE001
        return None


def critique_clip(frames: list[str], plan_summary: str) -> dict:
    """Ask the 'pro' vision model whether the rendered frames look right.

    Returns {"ok": bool, "problems": [str, ...]}. GRACEFUL FALLBACK to
    {"ok": True, "problems": []} (i.e. current no-vision behavior) whenever:
    the VISION_VERIFY flag is off, no frames were extracted, no LLM key is
    configured, or the provider/model rejects image input (caught broadly —
    same defensive pattern as json_response_format gating in editplan.py).
    """
    no_problem = {"ok": True, "problems": []}
    if not config.VISION_VERIFY or not frames:
        return no_problem
    try:
        api_key, base_url, model = config.llm_settings("pro")
    except RuntimeError:
        return no_problem  # no key -> no-vision

    uris = [u for u in (_data_uri(f) for f in frames[:MAX_FRAMES]) if u]
    if not uris:
        return no_problem

    content: list[dict] = [
        {"type": "text",
         "text": ("EDIT SUMMARY (what the editor intended):\n"
                  + (plan_summary or "(no summary)")
                  + "\n\nReview the following keyframes and report only visible "
                    "defects as instructed.")},
    ]
    content.extend({"type": "image_url", "image_url": {"url": u}} for u in uris)

    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=base_url) if base_url \
        else OpenAI(api_key=api_key)
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": _SYSTEM},
                      {"role": "user", "content": content}],
            temperature=0.1,
            **config.json_response_format(base_url))
        data = config.extract_json(resp.choices[0].message.content)
    except Exception:  # noqa: BLE001 — non-vision model / image rejection / etc.
        return no_problem  # fall back to no-vision silently

    if not isinstance(data, dict):
        return no_problem
    problems = data.get("problems") or []
    if not isinstance(problems, list):
        problems = []
    problems = [str(p).strip() for p in problems if str(p).strip()][:5]
    ok = bool(data.get("ok", not problems)) and not problems
    return {"ok": ok, "problems": problems}


def critique_summary(problems: list[str]) -> str:
    """A compact validator-feedback line from a critique's problems list."""
    return json.dumps(problems, ensure_ascii=False)
