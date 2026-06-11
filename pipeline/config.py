"""Central configuration for shorts-mcp.

Paths and tunables live here so every pipeline module agrees on locations.
Secrets come from the environment (.env), never hardcoded.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Project root = the directory that contains this package.
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# --- Directories -----------------------------------------------------------
CACHE_DIR = ROOT / "cache"
OUTPUTS_DIR = ROOT / "outputs"
for _d in (CACHE_DIR, OUTPUTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --- Speech-to-text (Faz 1) ------------------------------------------------
# faster-whisper model size: tiny|base|small|medium|large-v3
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
# CTranslate2 has no Metal backend, so on Apple Silicon we run on CPU with int8.
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE", "int8")

# --- LLM "brain" (Faz 2) ---------------------------------------------------
# Provider-agnostic via the OpenAI-compatible client. If OPENAI_API_KEY is set,
# we use OpenAI (default base_url); otherwise fall back to DeepSeek.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
# Chat "Pro" tier — a stronger brain for understanding intent + planning edits.
# Falls back to the fast model if unset.
OPENAI_MODEL_PRO = os.getenv("OPENAI_MODEL_PRO", "gpt-4o")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_MODEL_PRO = os.getenv("DEEPSEEK_MODEL_PRO", "deepseek-chat")


def llm_settings(tier: str = "fast") -> tuple[str, str | None, str]:
    """Resolve (api_key, base_url, model) for the chat/highlight LLM.

    tier: "fast" (default) uses the cheap model; "pro" uses the stronger model
    for sharper intent understanding + planning. Prefers OpenAI when
    OPENAI_API_KEY is present, else DeepSeek. Raises if neither is configured.
    """
    pro = tier == "pro"
    if OPENAI_API_KEY:
        return OPENAI_API_KEY, None, (OPENAI_MODEL_PRO if pro else OPENAI_MODEL)
    if DEEPSEEK_API_KEY:
        return (DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL,
                DEEPSEEK_MODEL_PRO if pro else DEEPSEEK_MODEL)
    raise RuntimeError(
        "No LLM key configured. Set OPENAI_API_KEY (or DEEPSEEK_API_KEY) in .env"
    )

# --- Stock b-roll (V2.3) ---------------------------------------------------
# Free key from https://www.pexels.com/api/ (200 req/h). Empty = b-roll search
# is disabled; local files still work via add_broll(file=...).
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")

# --- Social connect + share (zernio) ---------------------------------------
# Bearer key from https://zernio.com (free tier = first 2 connected accounts).
# Empty = the Share feature is visible but disabled (UI explains how to enable).
# Like every secret it lives ONLY in the gitignored .env, never committed.
ZERNIO_API_KEY = os.getenv("ZERNIO_API_KEY", "")
ZERNIO_BASE_URL = os.getenv("ZERNIO_BASE_URL", "https://zernio.com/api/v1")

# --- Encode (Faz 3/4) ------------------------------------------------------
# Apple Silicon hardware encoder; falls back to libx264 if unavailable.
VIDEO_ENCODER = os.getenv("VIDEO_ENCODER", "h264_videotoolbox")

# --- Proxy spine (Phase 0) -------------------------------------------------
# A cheap 540p H.264 mirror of the full source. ANALYSIS/PREVIEW run against
# the proxy; final EXPORT stays against the full-res source. The proxy reuses
# VIDEO_ENCODER by default; PROXY_ENCODER only exists as an override knob (e.g.
# force libx264 for a smaller, perfectly portable file).
PROXY_HEIGHT = int(os.getenv("PROXY_HEIGHT", "540"))
PROXY_ENCODER = os.getenv("PROXY_ENCODER", VIDEO_ENCODER)
