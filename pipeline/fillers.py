"""V3.1 — Aggressive filler classification (Turkish discourse words).

"yani / şey / hani / işte / falan" are real words half the time — blanket
removal butchers speech. This asks the LLM to judge each OCCURRENCE in its
sentence context and returns time ranges only for the semantically empty
ones. Used by the jumpcut stage when aggressive_fillers=True.
"""

from __future__ import annotations


from pipeline import config
from pipeline.jumpcut import _norm_word

TR_DISCOURSE = {"yani", "şey", "hani", "işte", "falan", "filan", "böyle",
                "aynen", "like", "actually", "basically", "literally",
                "you know"}

_SYSTEM = """You judge Turkish/English filler words in a transcript. For each
numbered candidate you get the word with its sentence context. Decide whether
the occurrence is a semantically EMPTY discourse filler (safe to cut) or
carries real meaning (keep).

Examples: "yani bence güzel" -> "yani" is filler. "Bu ne yani?" -> "yani"
carries meaning. "şey yapacağız" -> filler. "güzel bir şey" -> real noun.

Return ONLY JSON: {"cut": [<numbers of candidates that are pure filler>]}"""


def classify_filler_ranges(words: list[dict],
                           pad: float = 0.02) -> list[tuple[float, float]]:
    """Return (start, end) spans of context-judged filler occurrences."""
    cands = [i for i, w in enumerate(words)
             if _norm_word(w["word"]) in TR_DISCOURSE]
    if not cands:
        return []

    lines = []
    for n, i in enumerate(cands, start=1):
        ctx = " ".join(w["word"].strip()
                       for w in words[max(0, i - 6):i + 7])
        lines.append(f"{n}. word='{words[i]['word'].strip()}' "
                     f"context: \"...{ctx}...\"")

    try:
        api_key, base_url, model = config.llm_settings()
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url) if base_url \
            else OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": _SYSTEM},
                      {"role": "user", "content": "\n".join(lines)}],
            temperature=0.1, **config.json_response_format(base_url))
        cut = config.extract_json(resp.choices[0].message.content).get("cut", [])
    except Exception:
        return []  # uncertain -> cut nothing (never butcher speech on error)

    out = []
    for n in cut:
        try:
            i = cands[int(n) - 1]
        except (ValueError, IndexError, TypeError):
            continue
        out.append((max(0.0, words[i]["start"] - pad),
                    words[i]["end"] + pad))
    return out
