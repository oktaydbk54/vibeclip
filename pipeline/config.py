"""Central configuration for shorts-mcp.

Paths and tunables live here so every pipeline module agrees on locations.
Secrets come from the environment (.env), never hardcoded.
"""

from __future__ import annotations

import contextvars
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
# Optional: any OpenAI-compatible endpoint (local models, proxies, gateways).
# Applies to the env OpenAI key path; empty = the provider default.
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "").strip() or None

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_MODEL_PRO = os.getenv("DEEPSEEK_MODEL_PRO", "deepseek-chat")


# --- BYOK: per-request user-supplied key override --------------------------
# The pipeline is full of deep call sites (highlights, broll, soundbed…) that
# never see a request or a user — they only call llm_settings(). To let a logged
# -in user run on *their own* key without threading it through ~12 signatures, we
# stash the override in a ContextVar for the duration of a chat turn / job. The
# env key remains the default (and the only thing a single-key self-host needs).
_LLM_OVERRIDE: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "vibeclip_llm_override", default=None)


def set_llm_override(override: dict | None):
    """Activate a per-request BYOK override; returns a token for reset()."""
    return _LLM_OVERRIDE.set(override or None)


def reset_llm_override(token) -> None:
    try:
        _LLM_OVERRIDE.reset(token)
    except (ValueError, LookupError):
        pass  # token from a different context — safe to ignore


def current_override() -> dict | None:
    """The override active in this context (for jobs to capture at submit time)."""
    return _LLM_OVERRIDE.get()


def json_response_format(base_url: str | None) -> dict:
    """Kwargs for forcing JSON output, but ONLY where the provider honors it.

    OpenAI and DeepSeek support response_format={"type":"json_object"}. Google's
    Gemini OpenAI-compat layer can ERROR on it (and forbids json+tools together),
    and Anthropic's silently IGNORES it. So for those (and unknown custom
    endpoints) we send nothing and lean on the prompt ("Return ONLY JSON") plus
    extract_json() to parse a possibly fenced/prose-wrapped reply.

    Usage:  client.chat.completions.create(..., **json_response_format(base_url))
    """
    b = (base_url or "").lower()
    # Known compat layers that DON'T honor response_format. (Gemini's URL even
    # contains "/openai/", so block it before the native check below.)
    if "anthropic" in b or "googleapis" in b or "generativelanguage" in b:
        return {}
    # Native json_object support: OpenAI (no/explicit base_url) + DeepSeek.
    native = (not b) or ("openai.com" in b) or ("deepseek" in b)
    return {"response_format": {"type": "json_object"}} if native else {}


def extract_json(content: str | None):
    """Parse JSON from an LLM reply. Tolerant of ```json fences and surrounding
    prose (common when a provider doesn't enforce JSON mode). Raises the usual
    json.JSONDecodeError if nothing parses, so existing error handling still
    works (JSONDecodeError is a ValueError)."""
    import json as _json
    import re as _re
    s = (content or "").strip()
    candidates = [s]
    fence = _re.search(r"```(?:json)?\s*(.*?)```", s, _re.DOTALL)
    if fence:
        candidates.append(fence.group(1).strip())
    for open_c, close_c in (("{", "}"), ("[", "]")):
        i, j = s.find(open_c), s.rfind(close_c)
        if 0 <= i < j:
            candidates.append(s[i:j + 1])
    for cand in candidates:
        if not cand:
            continue
        try:
            return _json.loads(cand)
        except ValueError:
            continue
    return _json.loads(s or "")  # re-raise the original JSONDecodeError


def llm_settings(tier: str = "fast",
                 override: dict | None = None) -> tuple[str, str | None, str]:
    """Resolve (api_key, base_url, model) for the chat/highlight LLM.

    Precedence: explicit `override` > the context BYOK override > env key.
    tier: "fast" (default) uses the cheap model; "pro" uses the stronger model
    for sharper intent understanding + planning. Raises if nothing is configured.

    An override dict has: {api_key, base_url?, model?, model_pro?}. Missing model
    falls back to the env default; pro falls back to the fast model.
    """
    pro = tier == "pro"
    ov = override if override is not None else _LLM_OVERRIDE.get()
    if ov and ov.get("api_key"):
        model = (ov.get("model_pro") or ov.get("model")) if pro else ov.get("model")
        model = model or (OPENAI_MODEL_PRO if pro else OPENAI_MODEL)
        return ov["api_key"], (ov.get("base_url") or None), model
    if ov and ov.get("require_byok"):
        # A locked-down public instance: this account has no key of its own and
        # is NOT allowed to spend the operator's server key. Force BYOK instead
        # of silently falling through to OPENAI_API_KEY below.
        raise RuntimeError(
            "Add your own LLM API key in Settings to use VibeClip — this instance "
            "doesn't lend its server key to other accounts."
        )
    if OPENAI_API_KEY:
        return OPENAI_API_KEY, LLM_BASE_URL, (OPENAI_MODEL_PRO if pro else OPENAI_MODEL)
    if DEEPSEEK_API_KEY:
        return (DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL,
                DEEPSEEK_MODEL_PRO if pro else DEEPSEEK_MODEL)
    raise RuntimeError(
        "No LLM key configured. Set OPENAI_API_KEY (or DEEPSEEK_API_KEY) in .env, "
        "or add your own key in Settings."
    )

# --- Planner / agent model tier -------------------------------------------
# The proposer (chat.planner.propose) drives edit-intent routing. By default it
# runs on the SAME tier the chat turn chose (per-request, stashed on the
# session) — usually "fast" (gpt-4o-mini) to keep cost/latency low. Setting
# PLANNER_TIER=pro makes the planner OPT IN to the stronger model
# (OPENAI_MODEL_PRO) for sharper intent understanding without changing anything
# else: the deterministic approve/reject backstops and the detailed phrase maps
# in agent.SYSTEM_RULES / planner._SYSTEM stay as safety/latency optimizations,
# and llm_settings already falls back pro->fast for BYOK users with no pro
# model configured, so this is always safe. Empty/unset = no override (the
# session's tier wins, preserving today's behavior).
PLANNER_TIER = os.getenv("PLANNER_TIER", "").strip().lower() or None

# --- Visual perception (give the agent eyes) -------------------------------
# OFF by default: when set (VISION_VERIFY=1), the proposal loop extracts a few
# keyframes from the PREVIEW artifact and asks the "pro" vision model to verify
# the result (crop centered? sticker over captions?). A found problem feeds ONE
# extra round of validator feedback back into the planner. Everything degrades
# gracefully to current (no-vision) behavior when off or when the model/key is
# unavailable or rejects images — so existing behavior/tests are unchanged.
VISION_VERIFY = os.getenv("VISION_VERIFY", "").strip().lower() in ("1", "true", "yes", "on")

# --- DNN face detection for reframe (YuNet) --------------------------------
# opencv-python-headless>=4.13 ships cv2.FaceDetectorYN (YuNet). We download the
# small .onnx model into CACHE_DIR once at runtime (best-effort, short timeout)
# and use it for active-speaker reframe — it handles profile/tilted/multiple
# faces far better than the Haar cascade. On ANY failure (no network, download
# error, create error) the reframe detection degrades GRACEFULLY to the existing
# Haar cascade so nothing ever hard-breaks offline. Set YUNET_DISABLE=1 to force
# the Haar path. URL points at the pinned OpenCV Zoo model by default.
YUNET_URL = os.getenv(
    "YUNET_URL",
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx",
)
YUNET_DISABLE = os.getenv("YUNET_DISABLE", "").strip().lower() in (
    "1", "true", "yes", "on")
YUNET_MODEL_PATH = CACHE_DIR / "face_detection_yunet_2023mar.onnx"

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

# --- Per-user storage quota ------------------------------------------------
# Default UNLIMITED — self-host runs on your own disk, no reason to cap. A shared
# PUBLIC instance sets these (e.g. 600 / 1) to protect finite server storage.
MAX_UPLOAD_SECONDS = int(os.getenv("MAX_UPLOAD_SECONDS", "0"))      # 0 = no limit
MAX_PROJECTS_PER_USER = int(os.getenv("MAX_PROJECTS_PER_USER", "0"))  # 0 = unlimited

# Legacy projects created before per-user ownership carry owner_uid=None. On a
# single-tenant self-host box any logged-in user should see and claim them (they
# get backfilled to the opener). Set False on a PUBLIC multi-tenant instance so
# orphaned projects never leak between accounts.
CLAIM_ORPHAN_PROJECTS = os.getenv(
    "CLAIM_ORPHAN_PROJECTS", "true").lower() in ("1", "true", "yes", "on")
