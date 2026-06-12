"""Faz 2 — Highlight selection with DeepSeek.

DeepSeek reads the timestamped transcript (NOT the video) and returns the most
clip-worthy moments as structured JSON. Each highlight is then snapped to real
word boundaries so cuts land on word edges, not mid-syllable.
"""

from __future__ import annotations


from pipeline import config

# Per-platform guidance the model uses to size and frame clips.
PLATFORM_HINTS = {
    "youtube_shorts": "YouTube Shorts: up to 60s, strong hook in first 2s.",
    "instagram_reels": "Instagram Reels: 15-45s sweet spot, punchy and visual.",
    "tiktok": "TikTok: 15-40s, fast hook, conversational.",
}

_SYSTEM_PROMPT = """You are an expert short-form video editor. You are given a \
timestamped transcript of a long video. Select the most engaging, self-contained \
moments that would perform well as standalone short clips.

All timestamps are in SECONDS (e.g. [83.5s] means 83.5 seconds in). Return start
and end as float seconds in that same scale.

Rules:
- Each clip must START and END on a complete thought (don't cut mid-sentence).
- Each clip must make sense WITHOUT the rest of the video.
- Prefer moments with a hook, a surprising claim, a strong tip, or emotional payoff.
- Respect the requested platform duration limits.
- Return ONLY valid JSON, no prose.

Output JSON schema:
{
  "clips": [
    {
      "start": <float seconds>,
      "end": <float seconds>,
      "title": "<catchy short title>",
      "hook": "<the opening line that grabs attention>",
      "reason": "<why this works as a short>",
      "score": <int 1-100 viral potential>
    }
  ]
}
"""


def _client_and_model():
    from openai import OpenAI

    api_key, base_url, model = config.llm_settings()
    client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
    return client, model, base_url


def _snap_to_words(start: float, end: float, words: list[dict]) -> tuple[float, float]:
    """Snap a [start, end] window to the nearest enclosing word boundaries."""
    if not words:
        return start, end
    inside = [w for w in words if w["end"] > start and w["start"] < end]
    if not inside:
        return start, end
    return inside[0]["start"], inside[-1]["end"]


def find_highlights(
    transcript: dict,
    platform: str = "youtube_shorts",
    count: int = 5,
    max_duration: float | None = None,
    structure: list[dict] | None = None,
) -> list[dict]:
    """Pick the best `count` clips from a transcript for `platform`.

    max_duration=None (default) = NO fixed cap: the LLM inspects the content's
    structure and chooses each clip's natural length — a punchy moment may be
    15s, a complete argument may need 2-3 minutes. Pass a number only when the
    user explicitly asks for a ceiling.

    If `structure` (scored moments from pipeline.structure) is given, clips are
    drawn from those real topic/scene segments instead of a flat text pass — each
    moment already carries word/silence-snapped start/end, title, hook, and score.
    Falls back to the text-only LLM pass when structure is None.

    Returns clip dicts with start/end snapped to word boundaries, sorted by score.
    """
    if structure:
        clips = []
        for m in structure:
            start, end = float(m["start"]), float(m["end"])
            if max_duration and end - start > max_duration:
                end = start + max_duration
            clips.append({
                "start": round(start, 2),
                "end": round(end, 2),
                "duration": round(end - start, 2),
                "title": m.get("title", ""),
                "hook": "",  # structure moments carry numeric sub-scores, not hook text
                "reason": m.get("reason", m.get("topic", "")),
                "score": int(m.get("score", 0)),
                # named sub-scores for transparency
                "scores": {
                    "hook": int(m.get("hook", 0)),
                    "flow": int(m.get("flow", 0)),
                    "value": int(m.get("value", 0)),
                    "hook_first3s": int(m.get("hook_first3s", 0)),
                },
                "topic": m.get("topic", ""),
            })
        clips.sort(key=lambda x: x["score"], reverse=True)
        return clips[:count]

    from pipeline.transcribe import transcript_as_text

    hint = PLATFORM_HINTS.get(platform, PLATFORM_HINTS["youtube_shorts"])
    length_rule = (
        f"each at most {max_duration:.0f} seconds" if max_duration else
        "choose each clip's length from the content itself: a tight punchy "
        "moment can be 15-30s, a complete story or argument may need 1-3 "
        "minutes. Never pad a thin moment and never cut off mid-thought — "
        "the clip ends where the idea lands"
    )
    user_prompt = (
        f"Platform: {hint}\n"
        f"Pick the {count} best clips; {length_rule}.\n\n"
        f"Transcript:\n{transcript_as_text(transcript)}"
    )

    client, model, base_url = _client_and_model()
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.4,
        **config.json_response_format(base_url),
    )
    data = config.extract_json(resp.choices[0].message.content)
    clips = data.get("clips", [])

    words = transcript.get("words", [])
    cleaned: list[dict] = []
    for c in clips:
        try:
            start = float(c["start"])
            end = float(c["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        if max_duration and end - start > max_duration:
            end = start + max_duration
        start, end = _snap_to_words(start, end, words)
        cleaned.append(
            {
                "start": round(start, 2),
                "end": round(end, 2),
                "duration": round(end - start, 2),
                "title": c.get("title", ""),
                "hook": c.get("hook", ""),
                "reason": c.get("reason", ""),
                "score": int(c.get("score", 0)),
            }
        )

    cleaned.sort(key=lambda x: x["score"], reverse=True)
    return cleaned
