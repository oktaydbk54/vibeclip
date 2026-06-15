"""Text-to-speech — provider-agnostic voice synthesis for dubbing (B1).

Mirrors the model-agnostic LLM layer: one `synthesize()` entry point dispatches
to a provider chosen by `config.TTS_PROVIDER`. Three providers ship:

- "openai"     — reuses the OpenAI(-compatible) key from llm_settings(); zero
                 extra setup for a user who already brought an OpenAI key.
- "elevenlabs" — higher-quality multilingual voices (ELEVENLABS_API_KEY).
- "piper"      — a LOCAL piper binary: fully offline dubbing, the privacy story
                 cloud-only competitors structurally cannot offer.

Every path is best-effort: on ANY failure synthesize() returns None and the
caller (pipeline.dub) keeps the original audio rather than breaking the render.
Output is written to `out_path` as-is; downstream ffmpeg probes by content, so
the on-disk container (wav/mp3) need not match the extension.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pipeline import config


def synthesize(text: str, out_path: str, voice: str | None = None) -> str | None:
    """Render `text` to speech at `out_path`. Returns the path, or None on any
    failure (missing key, network error, provider unavailable, empty text)."""
    text = (text or "").strip()
    if not text:
        return None
    provider = config.TTS_PROVIDER or "openai"
    try:
        if provider == "elevenlabs":
            return _elevenlabs(text, out_path, voice)
        if provider == "piper":
            return _piper(text, out_path)
        return _openai(text, out_path, voice)
    except Exception:  # noqa: BLE001 — TTS never crashes a render
        return None


def _openai(text: str, out_path: str, voice: str | None) -> str | None:
    # Reuse whatever OpenAI-compatible key/base the rest of the pipeline uses.
    api_key, base_url, _ = config.llm_settings()
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=base_url) if base_url \
        else OpenAI(api_key=api_key)
    voice = voice or config.TTS_VOICE
    # Prefer the streaming-response API (current SDK); fall back to the legacy
    # create().write_to_file for older clients.
    try:
        with client.audio.speech.with_streaming_response.create(
                model=config.TTS_MODEL, voice=voice, input=text,
                response_format="wav") as resp:
            resp.stream_to_file(out_path)
    except AttributeError:
        resp = client.audio.speech.create(
            model=config.TTS_MODEL, voice=voice, input=text,
            response_format="wav")
        resp.write_to_file(out_path)
    return out_path if Path(out_path).exists() else None


def _elevenlabs(text: str, out_path: str, voice: str | None) -> str | None:
    import json
    import urllib.request

    key = config.ELEVENLABS_API_KEY
    voice_id = voice or config.ELEVENLABS_VOICE
    if not key or not voice_id:
        return None
    url = f"{config.ELEVENLABS_BASE_URL}/v1/text-to-speech/{voice_id}"
    body = json.dumps({"text": text,
                       "model_id": config.ELEVENLABS_MODEL}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "xi-api-key": key, "Content-Type": "application/json",
        "Accept": "audio/mpeg"})
    with urllib.request.urlopen(req, timeout=60) as r:
        audio = r.read()
    if not audio:
        return None
    Path(out_path).write_bytes(audio)
    return out_path


def _piper(text: str, out_path: str) -> str | None:
    model = config.PIPER_MODEL
    if not model or not Path(model).exists():
        return None
    # piper reads text on stdin and writes a wav to -f.
    proc = subprocess.run(
        [config.PIPER_BIN, "-m", model, "-f", out_path],
        input=text.encode("utf-8"),
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120)
    if proc.returncode != 0:
        return None
    return out_path if Path(out_path).exists() else None
