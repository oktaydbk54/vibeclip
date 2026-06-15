"""Caption translation — render a clip's captions in another language.

The hard part of translated captions is KEEPING SYNC: a sentence does not
translate word-for-word, so we cannot map source word i -> target word i.
Instead we translate at the CAPTION-LINE level (the same ~4-word chunks the
burner shows on screen, via `build_caption_segments`), then redistribute each
line's [start, end] time window across its translated words proportionally to
word length. Each displayed line stays pinned to its original time window, so
the karaoke highlight lands on the right line at the right moment — visually
exact for short-form captions even though individual words don't align 1:1.

The LLM call is batched (all lines in ONE request, numbered) and goes through
the same model-agnostic layer as the rest of the pipeline (config.llm_settings
+ json_response_format + extract_json), so BYOK / DeepSeek / local models all
work. Results are disk-cached by (lines, target language) so replays and style
tweaks never re-translate. On ANY failure we return the ORIGINAL words — the
clip still renders with source-language captions rather than crashing.
"""

from __future__ import annotations

import hashlib
import json

from pipeline import config
from pipeline.captions import build_caption_segments

_SYSTEM = """You translate short-form video captions. You get a numbered list of
on-screen caption lines. Translate EACH line into {target}, naturally and
concisely — these are burned-in captions, so keep them punchy and roughly the
same length, never add explanations or notes. Preserve the order and the exact
count. Keep numbers, names and obvious hashtags as-is.

Return ONLY JSON: {{"lines": ["<translation of line 1>", "<line 2>", ...]}}
with EXACTLY the same number of entries as the input, in the same order."""


def _cache_path(texts: list[str], target_lang: str) -> "object":
    raw = (target_lang.strip().lower() + " " + " ".join(texts))
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return config.CACHE_DIR / f"captrans_{digest}.json"


def _redistribute(chunk: dict, translated: str) -> list[dict]:
    """Spread chunk's [start, end] span across the translated line's words,
    proportionally to each word's character length. Returns word dicts in the
    same {start, end, word} shape the renderer/segmenter expect."""
    span_start = chunk["start"]
    span_end = max(chunk["end"], span_start)
    total = span_end - span_start
    parts = [w for w in translated.split() if w]
    if not parts:
        # Empty translation -> fall back to the original line's words so the
        # span is never left blank.
        return [dict(w) for w in chunk["words"]]
    if total <= 0 or len(parts) == 1:
        return [{"start": span_start, "end": span_end, "word": " ".join(parts)}]
    weights = [max(1, len(p)) for p in parts]
    wsum = sum(weights)
    out: list[dict] = []
    t = span_start
    for p, wt in zip(parts, weights):
        dur = total * (wt / wsum)
        out.append({"start": round(t, 3),
                    "end": round(min(span_end, t + dur), 3),
                    "word": p})
        t += dur
    out[-1]["end"] = span_end  # absorb rounding drift onto the last word
    return out


def _llm_translate(texts: list[str], target_lang: str) -> list[str] | None:
    """One batched translation call. Returns a list aligned to `texts`, or None
    on any failure / shape mismatch (caller falls back to the originals)."""
    numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(texts, 1))
    try:
        api_key, base_url, model = config.llm_settings()
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url) if base_url \
            else OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system",
                       "content": _SYSTEM.format(target=target_lang)},
                      {"role": "user", "content": numbered}],
            temperature=0.2, **config.json_response_format(base_url))
        lines = config.extract_json(resp.choices[0].message.content).get("lines")
    except Exception:  # noqa: BLE001 — translation never crashes a render
        return None
    if not isinstance(lines, list) or len(lines) != len(texts):
        return None
    return [str(x) for x in lines]


def translate_lines(texts: list[str], target_lang: str) -> list[str] | None:
    """Translate a list of short strings into `target_lang`, order/count
    preserved. Disk-cached by (texts, language). Returns None on failure or an
    empty/blank target so callers can fall back to the originals. Shared by the
    caption renderer and the dubbing pass."""
    target_lang = (target_lang or "").strip()
    texts = [t for t in texts]
    if not target_lang or not texts:
        return None
    cache_file = _cache_path(texts, target_lang)
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text())
            if isinstance(cached, list) and len(cached) == len(texts):
                return [str(x) for x in cached]
        except (ValueError, OSError):
            pass
    out = _llm_translate(texts, target_lang)
    if out is None:
        return None
    try:
        cache_file.write_text(json.dumps(out, ensure_ascii=False))
    except OSError:
        pass
    return out


def translate_captions(words: list[dict], target_lang: str,
                       clip_start: float = 0.0) -> list[dict]:
    """Return clip-local word dicts whose TEXT is `target_lang` but whose timing
    still tracks the original speech. `words` are the clip's source-language word
    timings (as from session.words_for). Falls back to the originals untouched
    when the target is empty or translation is unavailable."""
    target_lang = (target_lang or "").strip()
    if not target_lang or not words:
        return words
    chunks = [c for c in build_caption_segments(words, clip_start) if c["text"]]
    if not chunks:
        return words

    translations = translate_lines([c["text"] for c in chunks], target_lang)
    if translations is None:
        return words  # graceful: keep source-language captions

    out: list[dict] = []
    for chunk, line in zip(chunks, translations):
        out.extend(_redistribute(chunk, line))
    return out
