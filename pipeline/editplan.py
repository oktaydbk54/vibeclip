"""Faz 5e (part 1) — LLM-driven edit decisions.

Given a clip's word-timestamped transcript, the LLM marks which phrases deserve
a punch-in zoom (emphasis) and where a sound effect fits (e.g. a whoosh on a
scene/idea shift). Returned times are snapped to word boundaries.

If no LLM key is set, falls back to a simple heuristic so auto_edit still works.
"""

from __future__ import annotations

import json

from pipeline import config

_SYSTEM = """You are a short-form video editor planning emphasis for ONE clip.
You get a word-timestamped transcript (clip-local seconds). Decide:
- emphasis: phrases that deserve a punch-in zoom (the punchy/important lines).
- sfx: moments that benefit from a sound effect. kind is "whoosh" (transitions/
  idea shifts) or "ding" (a key point / reveal).

Keep it tasteful: at most 1 emphasis per ~{sec_per_zoom}s and at most {sfx_cap} sfx total.
Return ONLY JSON:
{{
  "emphasis": [{{"start": <s>, "end": <s>}}],
  "sfx": [{{"time": <s>, "kind": "whoosh|ding"}}]
}}
"""


def _words_local(words: list[dict], clip_start: float, clip_end: float) -> list[dict]:
    return [
        {"start": round(w["start"] - clip_start, 2),
         "end": round(w["end"] - clip_start, 2),
         "word": w["word"]}
        for w in words
        if w["end"] > clip_start and w["start"] < clip_end
    ]


def _snap(s: float, e: float, words: list[dict]) -> tuple[float, float]:
    inside = [w for w in words if w["end"] > s and w["start"] < e]
    if not inside:
        return s, e
    return inside[0]["start"], inside[-1]["end"]


def _heuristic(local: list[dict]) -> dict:
    """No-LLM fallback: emphasize the first phrase (the hook)."""
    if not local:
        return {"emphasis": [], "sfx": []}
    end = min(local[-1]["end"], local[0]["start"] + 3.0)
    return {"emphasis": [{"start": local[0]["start"], "end": end}], "sfx": []}


def plan_clip_edits(words: list[dict], clip_start: float, clip_end: float,
                    density: float = 0.25, sfx_cap: int = 3) -> dict:
    """Return {emphasis:[{start,end}], sfx:[{time,kind}]} in clip-local seconds.

    density: target zooms per second (0.25 = one per ~4s). sfx_cap: max sfx.
    Both are style-driven taste knobs; results are hard-capped post-hoc too.
    """
    local = _words_local(words, clip_start, clip_end)
    if not local:
        return {"emphasis": [], "sfx": []}
    if density <= 0 and sfx_cap <= 0:
        return {"emphasis": [], "sfx": []}

    try:
        api_key, base_url, model = config.llm_settings()
    except RuntimeError:
        return _heuristic(local)

    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
    transcript = "\n".join(f"[{w['start']:.2f}-{w['end']:.2f}] {w['word']}" for w in local)
    sec_per_zoom = max(1, round(1.0 / density)) if density > 0 else 9999
    system = _SYSTEM.format(sec_per_zoom=sec_per_zoom, sfx_cap=max(0, sfx_cap))

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"Clip transcript:\n{transcript}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        data = json.loads(resp.choices[0].message.content)
    except Exception:
        return _heuristic(local)

    emphasis = []
    for em in data.get("emphasis", []):
        try:
            s, e = float(em["start"]), float(em["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if e > s:
            s, e = _snap(s, e, local)
            emphasis.append({"start": round(s, 2), "end": round(e, 2)})

    sfx = []
    for sx in data.get("sfx", []):
        try:
            t = float(sx["time"])
        except (KeyError, TypeError, ValueError):
            continue
        kind = sx.get("kind", "ding")
        if kind not in ("whoosh", "ding"):
            kind = "ding"
        sfx.append({"time": round(t, 2), "kind": kind})

    # Hard caps regardless of what the model returned.
    dur = max(1.0, (local[-1]["end"] - local[0]["start"]))
    max_emph = max(0, int(dur * density + 0.5)) if density > 0 else 0
    return {"emphasis": emphasis[:max_emph], "sfx": sfx[:max(0, sfx_cap)]}
