"""Generative media — text-to-video / text-to-image as GENERATED b-roll (Faz 1).

Closes the one feature where the timeline editors that ship AI generation are
genuinely ahead: footage that doesn't exist yet. The design deliberately mirrors
`pipeline.tts`: one entry point per kind dispatches to a provider chosen by
`config.GENMEDIA_PROVIDER`, everything is BYOK, and ANY failure returns None so
the caller keeps its existing behavior (a missing/over-quota key never breaks a
render — generated b-roll is simply skipped).

Providers front hosted aggregators that proxy SOTA models, so no weights ship:
- "fal"        — fal.ai-style sync endpoint at BASE_URL/{model} (default). One
                 POST blocks until the model finishes and returns a result URL.
- "replicate"  — create-prediction + poll the standard Replicate REST API.

Results are content-addressed in cache/genmedia so the same prompt+model+seed is
generated ONCE and reused on replays — the same caching discipline as
`broll.search_broll`. The returned file is at the model's native size; callers
normalize it to the clip frame via `broll.normalize_media`, exactly like a local
b-roll file.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from pipeline import config, nethttp

GENMEDIA_DIR = config.CACHE_DIR / "genmedia"

# Selectable models per kind, surfaced to the studio "Generate" panel. fal ids
# (the default provider); on a different provider the configured default is the
# only entry. The configured default is always marked so the UI can preselect it.
_FAL_CATALOG = {
    "video": [   # text-to-video
        ("fal-ai/bytedance/seedance/v1/lite/text-to-video", "Seedance 1.0 Lite"),
        ("fal-ai/bytedance/seedance/v1/pro/text-to-video", "Seedance 1.0 Pro"),
        ("fal-ai/kling-video/v2/master/text-to-video", "Kling 2 Master"),
        ("fal-ai/minimax/hailuo-02/standard/text-to-video", "Hailuo 02"),
    ],
    "image": [   # text-to-image
        ("fal-ai/flux/schnell", "FLUX schnell (fast)"),
        ("fal-ai/flux/dev", "FLUX dev"),
        ("fal-ai/flux-pro/v1.1", "FLUX 1.1 Pro"),
        ("fal-ai/nano-banana", "Nano Banana"),
    ],
    "i2v": [     # image-to-video
        ("fal-ai/bytedance/seedance/v1/lite/image-to-video", "Seedance Lite i2v"),
        ("fal-ai/kling-video/v2/master/image-to-video", "Kling 2 i2v"),
        ("fal-ai/minimax/hailuo-02/standard/image-to-video", "Hailuo 02 i2v"),
    ],
}

_DEFAULTS = {
    "video": lambda: config.GENMEDIA_VIDEO_MODEL,
    "image": lambda: config.GENMEDIA_IMAGE_MODEL,
    "i2v": lambda: config.GENMEDIA_I2V_MODEL,
}


def models(kind: str) -> list[dict]:
    """Catalog of selectable models for a kind ('video'|'image'|'i2v').

    Each entry is {id, label, default}. On the fal provider this is the curated
    list (plus the configured default if it isn't already in it); on any other
    provider only the configured default is offered."""
    default = _DEFAULTS.get(kind, _DEFAULTS["video"])()
    if (config.GENMEDIA_PROVIDER or "fal") != "fal":
        return [{"id": default, "label": default, "default": True}]
    rows = [{"id": mid, "label": label, "default": mid == default}
            for mid, label in _FAL_CATALOG.get(kind, [])]
    if not any(r["default"] for r in rows):   # configured default not in catalog
        rows.insert(0, {"id": default, "label": default, "default": True})
    return rows


def available() -> bool:
    """True when generation is configured (an API key is present)."""
    return bool(config.GENMEDIA_API_KEY)


def _aspect_ratio(width: int, height: int) -> str:
    """Closest common aspect-ratio string the gen models accept."""
    if width <= 0 or height <= 0:
        return "9:16"
    r = width / height
    table = {"9:16": 9 / 16, "1:1": 1.0, "16:9": 16 / 9,
             "4:5": 4 / 5, "3:4": 3 / 4}
    return min(table, key=lambda k: abs(table[k] - r))


def _cache_path(kind: str, prompt: str, model: str, seed, width: int,
                height: int, seconds) -> Path:
    raw = f"{kind}:{prompt}:{model}:{seed}:{width}x{height}:{seconds}"
    key = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    ext = "mp4" if kind == "video" else "png"
    return GENMEDIA_DIR / f"{kind}_{key}.{ext}"


def _find_url(data) -> str | None:
    """Pull the first media URL out of a provider response of unknown shape.

    Models differ: video -> {"video": {"url": ...}}; image -> {"images":
    [{"url": ...}]}; some return a bare URL string. Walk the structure and
    return the first http(s) URL found."""
    if isinstance(data, str):
        return data if data.startswith("http") else None
    if isinstance(data, dict):
        # Prefer obvious media keys before a blind walk.
        for k in ("video", "image", "url", "images", "output"):
            if k in data:
                found = _find_url(data[k])
                if found:
                    return found
        for v in data.values():
            found = _find_url(v)
            if found:
                return found
    elif isinstance(data, (list, tuple)):
        for v in data:
            found = _find_url(v)
            if found:
                return found
    return None


# ------------------------------------------------------------------- HTTP
# Generation requests go through the shared `nethttp` helper, which retries
# transient blips/5xx ONCE (retries=1) — a single fal hiccup under load no
# longer fails a render — while a permanent 4xx (bad model id) still fails fast.
def _download(url: str, out_path: Path) -> str | None:
    """Download a result URL to `out_path` (kept as its own symbol so tests and
    callers can stub the network layer)."""
    return nethttp.download(url, out_path, headers={"User-Agent": "shorts-mcp"},
                            timeout=config.GENMEDIA_TIMEOUT)


# ------------------------------------------------------------------- providers
def _fal(model: str, payload: dict) -> dict:
    """fal.ai sync endpoint: POST BASE_URL/{model}, blocks, returns result JSON."""
    url = f"{config.GENMEDIA_BASE_URL.rstrip('/')}/{model}"
    headers = {"Authorization": f"Key {config.GENMEDIA_API_KEY}"}
    return nethttp.request_json(url, headers=headers, body=payload,
                                timeout=config.GENMEDIA_TIMEOUT, retries=1)


def _replicate(model: str, payload: dict) -> dict:
    """Replicate create-prediction + poll. `model` is a version hash."""
    import time  # local: only the poll path needs it

    base = "https://api.replicate.com/v1/predictions"
    headers = {"Authorization": f"Token {config.GENMEDIA_API_KEY}"}
    pred = nethttp.request_json(base, headers=headers,
                                body={"version": model, "input": payload},
                                timeout=config.GENMEDIA_TIMEOUT, retries=1)
    get_url = (pred.get("urls") or {}).get("get") or f"{base}/{pred.get('id')}"
    waited = 0
    while pred.get("status") not in ("succeeded", "failed", "canceled"):
        if waited >= config.GENMEDIA_TIMEOUT:
            break
        time.sleep(2)
        waited += 2
        pred = nethttp.request_json(get_url, headers=headers, timeout=30,
                                    retries=1)
    return pred.get("output") if pred.get("status") == "succeeded" else pred


def _generate(kind: str, prompt: str, payload: dict, model: str,
              out_path: Path) -> str | None:
    """Provider-dispatch + download. Returns the saved path or None on failure."""
    provider = config.GENMEDIA_PROVIDER or "fal"
    data = _replicate(model, payload) if provider == "replicate" \
        else _fal(model, payload)
    media_url = _find_url(data)
    if not media_url:
        return None
    GENMEDIA_DIR.mkdir(parents=True, exist_ok=True)
    return _download(media_url, out_path)


# ------------------------------------------------------------------- entry
def generate_video(prompt: str, *, model: str | None = None,
                   seconds: float | None = None, width: int = 1080,
                   height: int = 1920, seed: int | None = None) -> str | None:
    """Generate a short video from `prompt`. Returns a cached mp4 path, or None
    on any failure (no key, provider error, empty response). Cached by
    prompt+model+seed+dims so a replay reuses the same generation."""
    prompt = (prompt or "").strip()
    if not prompt or not available():
        return None
    model = model or config.GENMEDIA_VIDEO_MODEL
    seconds = seconds or config.GENMEDIA_VIDEO_SECONDS
    out = _cache_path("video", prompt, model, seed, width, height, seconds)
    if out.exists():
        return str(out)
    payload = {"prompt": prompt, "aspect_ratio": _aspect_ratio(width, height),
               "duration": int(round(seconds))}
    if seed is not None:
        payload["seed"] = int(seed)
    try:
        return _generate("video", prompt, payload, model, out)
    except Exception:  # noqa: BLE001 — generation never crashes a render
        return None


def generate_image(prompt: str, *, model: str | None = None, width: int = 1080,
                   height: int = 1920, seed: int | None = None,
                   reference_path: str | None = None,
                   negative_prompt: str = "",
                   strength: float | None = None) -> str | None:
    """Generate a still image from `prompt`. Returns a cached png path, or None
    on any failure. Cached like generate_video.

    reference_path: an optional init/reference image (Palmier's "@-mention a
    photo → generate"). When set, the still is generated FROM that image —
    generation routes to an image-edit model (GENMEDIA_IMAGE_EDIT_MODEL) unless
    the caller passes an explicit edit-capable model, and the image is sent
    inline as a data-URI (both `image_url` and `image_urls` so FLUX-i2i and
    nano-banana-edit schemas are both satisfied; the provider ignores the key it
    doesn't use). negative_prompt / strength are forwarded where supported. The
    reference content hash + negative + strength fold into the cache key so a
    reference-guided gen never collides with the plain text gen."""
    prompt = (prompt or "").strip()
    if not prompt or not available():
        return None

    ref = Path(reference_path) if reference_path else None
    ref_ok = bool(ref and ref.exists())
    if ref_ok:
        # Reference-guided: a text-only model (FLUX schnell) can't honor an input
        # image, so prefer an edit model unless the caller chose one explicitly.
        if not model or model == config.GENMEDIA_IMAGE_MODEL:
            model = config.GENMEDIA_IMAGE_EDIT_MODEL
    model = model or config.GENMEDIA_IMAGE_MODEL

    # Build a cache key that captures the reference + tuning so replays reuse the
    # exact image and reference-guided ≠ text-only for the same prompt.
    cache_prompt = prompt
    if ref_ok:
        import base64
        import mimetypes
        img_bytes = ref.read_bytes()
        img_key = hashlib.sha1(img_bytes).hexdigest()[:12]
        cache_prompt = (f"ref:{img_key}|neg:{negative_prompt}|"
                        f"s:{strength}|{prompt}")
    out = _cache_path("image", cache_prompt, model, seed, width, height, None)
    if out.exists():
        return str(out)

    payload = {"prompt": prompt,
               "image_size": {"width": width, "height": height},
               "aspect_ratio": _aspect_ratio(width, height)}
    if seed is not None:
        payload["seed"] = int(seed)
    if negative_prompt:
        payload["negative_prompt"] = negative_prompt
    if ref_ok:
        mime = mimetypes.guess_type(str(ref))[0] or "image/png"
        data_uri = f"data:{mime};base64," + base64.b64encode(img_bytes).decode()
        payload["image_url"] = data_uri          # FLUX-style image-to-image
        payload["image_urls"] = [data_uri]       # nano-banana-edit schema
        if strength is not None:
            payload["strength"] = max(0.0, min(1.0, float(strength)))
    try:
        return _generate("image", prompt, payload, model, out)
    except Exception:  # noqa: BLE001
        return None


def generate_video_from_image(image_path: str, prompt: str = "", *,
                              model: str | None = None,
                              seconds: float | None = None, width: int = 1080,
                              height: int = 1920,
                              seed: int | None = None) -> str | None:
    """Animate a still image into a short video (image-to-video). The image is
    sent inline as a data-URI `image_url`, so a freshly uploaded/generated still
    works with no separate upload step. Returns a cached mp4 path, or None on any
    failure. Cached by image content + prompt + model + seed so replays reuse it.

    `prompt` is optional motion guidance ("slow push in, gentle parallax")."""
    import base64
    import mimetypes

    src = Path(image_path)
    if not src.exists() or not available():
        return None
    model = model or config.GENMEDIA_I2V_MODEL
    seconds = seconds or config.GENMEDIA_VIDEO_SECONDS
    img_bytes = src.read_bytes()
    # Key on the image CONTENT (not path) so the same still animates once.
    img_key = hashlib.sha1(img_bytes).hexdigest()[:12]
    out = _cache_path("video", f"i2v:{img_key}:{prompt}", model, seed,
                      width, height, seconds)
    if out.exists():
        return str(out)
    mime = mimetypes.guess_type(str(src))[0] or "image/png"
    data_uri = f"data:{mime};base64," + base64.b64encode(img_bytes).decode()
    payload = {"prompt": (prompt or "").strip(), "image_url": data_uri,
               "aspect_ratio": _aspect_ratio(width, height),
               "duration": int(round(seconds))}
    if seed is not None:
        payload["seed"] = int(seed)
    try:
        return _generate("video", prompt, payload, model, out)
    except Exception:  # noqa: BLE001 — generation never crashes a render
        return None
