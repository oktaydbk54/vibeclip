"""Tiny page-head helpers shared by the HTML-serving routes.

Keeps hosted-instance coupling (analytics) out of the static files: the GA4
snippet is injected at serve time only when GA_MEASUREMENT_ID is set, so a
self-hosted instance ships with NO analytics by default. Imports nothing from
the app/auth modules — safe to use from either without a circular import.
"""

from __future__ import annotations

import os
import re

# Matches the Google Analytics block baked into the static pages (optional
# leading comment + the async loader + the inline gtag config), tolerant of
# indentation differences between files.
_GA_BLOCK = re.compile(
    r'(?:[ \t]*<!--\s*Google Analytics[^>]*-->\s*)?'
    r'<script async src="https://www\.googletagmanager\.com[^"]*"></script>\s*'
    r'<script>.*?gtag\(\s*[\'"]config[\'"].*?</script>',
    re.DOTALL,
)


def hosted_studio() -> bool:
    """Does THIS deployment offer a usable hosted studio (login + signup)?

    Default True: a self-hoster running it locally needs to sign in to their own
    instance. Boran's v1 PUBLIC marketing site sets HOSTED_STUDIO=false so the
    landing becomes a pure "go to GitHub & self-host" funnel — no login pushed.
    v2 (the paid hosted platform) flips it back to true.
    """
    return os.getenv("HOSTED_STUDIO", "1").strip().lower() not in (
        "0", "false", "no", "off", "")


def inject_head(html: str) -> str:
    """Swap the hardcoded GA id for the env one (or strip GA when unset), and
    stamp the hosted-studio flag onto <html> so static pages/JS can switch their
    login vs. self-host CTAs without a rebuild."""
    gid = os.getenv("GA_MEASUREMENT_ID", "").strip()
    if gid:
        # Replace whatever id the static file hardcoded with the configured one.
        html = re.sub(r'G-[A-Z0-9]{6,}', gid, html)
    else:
        html = _GA_BLOCK.sub("", html)
    flag = "1" if hosted_studio() else "0"
    # `count=1` => only the document's opening <html ...> tag, never content.
    html = re.sub(r'<html\b', f'<html data-hosted-studio="{flag}"', html, count=1)
    return html
