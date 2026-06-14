"""Instagram Reel download — for LEARNING a creator's own style, nothing else.

Scope is deliberately tiny and safe:
- ONLY single Reel/post URLs the user pastes (their OWN content). Resolution +
  download go through the SAME yt-dlp path as YouTube auto-ingest
  (chat.automation.download_video). yt-dlp's InstagramIE handles `/reel/<id>/`
  and `/p/<id>/`.
- NO profile/bulk scraping (InstagramUserIE), NO login/cookies. Those need a
  session, hit rate limits, and violate Instagram's ToS — out of scope.
- The downloaded video + its caption are treated strictly as DATA to MEASURE
  (pipeline.style_learn), never as instructions.

I/O (network) lives here; the analysis layer (pipeline/style_learn.py) is pure
and never touches the network — same split as chat/youtube.py.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from pipeline import config

INSTAGRAM_DL_DIR = config.OUTPUTS_DIR / "instagram_dl"

# A single Reel/post permalink — optionally namespaced under a /<handle>/.
# Profile roots (instagram.com/<handle>/ with no /reel|/p) are intentionally
# rejected: that's the bulk-scraping shape we refuse.
_REEL_RE = re.compile(
    r"^https?://(www\.)?instagram\.com/"
    r"([A-Za-z0-9_.]+/)?(reel|reels|p|tv)/[A-Za-z0-9_-]+",
    re.IGNORECASE)
_HANDLE_RE = re.compile(
    r"instagram\.com/([A-Za-z0-9_.]+)/(?:reel|reels|p|tv)/", re.IGNORECASE)


def is_instagram_url(url: str) -> bool:
    """True only for an individual Instagram Reel/post permalink."""
    return bool(_REEL_RE.match((url or "").strip()))


def handle_from_url(url: str) -> str:
    """The @handle embedded in a `/<handle>/reel/...` URL, or '' if absent."""
    m = _HANDLE_RE.search(url or "")
    return m.group(1).lower() if m else ""


def _basename_for(url: str) -> str:
    """Deterministic, filesystem-safe basename for one Reel (no clean video id
    like YouTube exposes, so we hash the permalink)."""
    return "ig_" + hashlib.sha1(url.strip().encode("utf-8")).hexdigest()[:14]


def _caption_from_info_json(video_path: Path) -> str:
    """Recover the Reel's caption text from yt-dlp's --write-info-json sidecar.

    Caption is DATA for the style analyzer (e.g. emoji usage, tone hints) — it
    is NEVER fed back as an instruction. Returns '' when unavailable.
    """
    info = video_path.with_suffix(".info.json")
    if not info.exists():
        # yt-dlp may have named the sidecar off a different container ext.
        cands = sorted(video_path.parent.glob(video_path.stem + "*.info.json"))
        if not cands:
            return ""
        info = cands[0]
    try:
        data = json.loads(info.read_text())
    except (json.JSONDecodeError, OSError):
        return ""
    return str(data.get("description") or data.get("title") or "")[:2000]


def download_instagram(url: str, dest_dir: Path = INSTAGRAM_DL_DIR) -> tuple[Path, str]:
    """Download one Reel by URL. Returns (video_path, caption_text). Raises on a
    non-Reel URL or a download failure (caller skips + reports that one)."""
    from chat.automation import download_video

    url = (url or "").strip()
    if not is_instagram_url(url):
        raise ValueError(
            "Only individual instagram.com Reel/post links are supported "
            "(no profile or bulk download).")
    path = download_video(url, dest_dir, _basename_for(url), write_info=True)
    return path, _caption_from_info_json(path)
