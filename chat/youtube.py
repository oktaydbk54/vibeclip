"""Keyless YouTube channel watcher — stdlib only (no new deps, no API key).

How a creator's new uploads are detected WITHOUT the Google Data API: every
channel exposes a public Atom feed at
    https://www.youtube.com/feeds/videos.xml?channel_id=UC...
which lists the latest ~15 uploads. We resolve a human @handle (or a full URL,
or a raw UC… id) to that channel_id by scraping the channel's public HTML once,
then poll the lightweight feed.

Network I/O is split from parsing on purpose: resolve_channel/fetch_uploads do
the (failure-tolerant) HTTP, while _channel_id_from_html/parse_feed are pure
string→data functions the tests exercise with fixtures (no network).
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

# A browser-ish UA — YouTube serves a trimmed/blocking page to the default
# urllib agent. Kept here so resolve + feed fetch agree.
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_TIMEOUT = 8  # seconds — short so a hung fetch never stalls the poller

_ATOM = "{http://www.w3.org/2005/Atom}"
_YT = "{http://www.youtube.com/xml/schemas/2015}"
_MEDIA = "{http://search.yahoo.com/mrss/}"

_CHANNEL_ID_RE = re.compile(r"UC[0-9A-Za-z_-]{22}")
_FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id="


def _get(url: str) -> str | None:
    """GET a URL as text. Returns None on ANY error (the caller degrades)."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310 — fixed https hosts
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")
    except (urllib.error.URLError, OSError, ValueError):
        return None


# --------------------------------------------------------------- channel id
def _normalize_channel_input(handle_or_url: str) -> tuple[str | None, str | None]:
    """Map raw user input to (channel_id, page_url_to_scrape).

    - A raw "UC…" id (or a /channel/UC… URL) → (id, None): no scrape needed.
    - An @handle, a youtube.com/@handle URL, a /c//user/ URL, or a bare handle
      → (None, the channel page URL to GET and scrape for its channelId)."""
    s = (handle_or_url or "").strip()
    if not s:
        return None, None
    # Already a channel id, possibly inside a /channel/ URL.
    m = re.search(r"/channel/(UC[0-9A-Za-z_-]{22})", s)
    if m:
        return m.group(1), None
    if re.fullmatch(_CHANNEL_ID_RE, s):
        return s, None
    # A full URL → scrape it as-is (handle/custom/user pages all carry channelId).
    if s.startswith("http://") or s.startswith("https://"):
        return None, s
    # @handle or a bare handle/name.
    handle = s.lstrip("@")
    return None, f"https://www.youtube.com/@{handle}"


def _channel_id_from_html(html: str) -> str | None:
    """Pull the canonical UC… channel id out of a channel page's HTML."""
    if not html:
        return None
    for pat in (r'"channelId":"(UC[0-9A-Za-z_-]{22})"',
                r'/channel/(UC[0-9A-Za-z_-]{22})',
                r'"externalId":"(UC[0-9A-Za-z_-]{22})"'):
        m = re.search(pat, html)
        if m:
            return m.group(1)
    return None


def _title_from_html(html: str) -> str:
    """Best-effort channel title from og:title / <title>."""
    if not html:
        return ""
    m = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    if m:
        return _unescape(m.group(1))
    m = re.search(r"<title>([^<]+)</title>", html)
    if m:
        return _unescape(m.group(1)).replace(" - YouTube", "").strip()
    return ""


def _unescape(s: str) -> str:
    import html as _html
    return _html.unescape(s or "").strip()


def resolve_channel(handle_or_url: str) -> dict | None:
    """Resolve an @handle / channel URL / raw UC… id to
    {channel_id, title}. Returns None when it can't be resolved (bad handle,
    network error). Never raises."""
    cid, page = _normalize_channel_input(handle_or_url)
    title = ""
    if cid is None:
        if not page:
            return None
        html = _get(page)
        cid = _channel_id_from_html(html or "")
        title = _title_from_html(html or "")
        if cid is None:
            return None
    if not title:
        # Pull a title from the feed (cheap, and confirms the id is real).
        ups = fetch_uploads(cid)
        title = ups[0].get("channel_title", "") if ups else ""
    return {"channel_id": cid, "title": title}


# ------------------------------------------------------------------ uploads
def parse_feed(xml_text: str) -> list[dict]:
    """Parse a YouTube channel Atom feed into a newest-first list of
    {video_id, title, published, url, channel_title}. Pure (no network);
    returns [] on malformed input."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    feed_title = ""
    ft = root.find(f"{_ATOM}title")
    if ft is not None and ft.text:
        feed_title = ft.text.strip()
    # The author name is the channel title; prefer it over the feed <title>.
    author = root.find(f"{_ATOM}author/{_ATOM}name")
    if author is not None and author.text:
        feed_title = author.text.strip()

    out: list[dict] = []
    for entry in root.findall(f"{_ATOM}entry"):
        vid_el = entry.find(f"{_YT}videoId")
        if vid_el is None or not vid_el.text:
            continue
        vid = vid_el.text.strip()
        title_el = entry.find(f"{_ATOM}title")
        pub_el = entry.find(f"{_ATOM}published")
        out.append({
            "video_id": vid,
            "title": (title_el.text or "").strip() if title_el is not None else "",
            "published": (pub_el.text or "").strip() if pub_el is not None else "",
            "url": f"https://www.youtube.com/watch?v={vid}",
            "channel_title": feed_title,
        })
    return out


def fetch_uploads(channel_id: str) -> list[dict]:
    """Fetch + parse the channel's upload feed (newest first). [] on any
    failure. Never raises — the poller treats [] as 'nothing new'."""
    cid = (channel_id or "").strip()
    if not re.fullmatch(_CHANNEL_ID_RE, cid):
        return []
    xml_text = _get(_FEED_URL + cid)
    if not xml_text:
        return []
    return parse_feed(xml_text)
