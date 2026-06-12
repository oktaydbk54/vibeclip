"""Server-side rendering for the VibeClip blog.

Pages are built here as full HTML strings (head + SEO metadata + body) from the
single source of truth in blog_content.py. Server-rendering — not a JS shell — is
deliberate: crawlers get real content and complete <meta>/JSON-LD on first byte.

Shared head bits (favicon, GA4, fonts) mirror the other static pages so the blog
is visually and analytically part of the same site.
"""
from __future__ import annotations

import html
import json
import os
from pathlib import Path

from . import blog_content as bc

STATIC = Path(__file__).parent / "static"


def _ga_block() -> str:
    """GA4 snippet, only when GA_MEASUREMENT_ID is configured (empty on self-host)."""
    gid = os.getenv("GA_MEASUREMENT_ID", "").strip()
    if not gid:
        return ""
    return (
        '\n<!-- Google Analytics (GA4) -->'
        f'\n<script async src="https://www.googletagmanager.com/gtag/js?id={gid}"></script>'
        '\n<script>'
        '\n  window.dataLayer = window.dataLayer || [];'
        '\n  function gtag(){dataLayer.push(arguments);}'
        "\n  gtag('js', new Date());"
        f"\n  gtag('config', '{gid}');"
        '\n</script>'
    )


def _ver(rel: str) -> str:
    """Cache-buster matching app._serve_html: ?v=<mtime> for local static assets."""
    try:
        return f"?v={int((STATIC / rel).stat().st_mtime)}"
    except OSError:
        return ""


def _head(*, title: str, description: str, canonical: str, keywords: str = "",
          og_type: str = "website", published: str = "", jsonld: list | None = None) -> str:
    """Common <head>: SEO meta + Open Graph + Twitter + favicon + GA4 + fonts."""
    desc = html.escape(description, quote=True)
    ttl = html.escape(title, quote=True)
    kw = f'\n<meta name="keywords" content="{html.escape(keywords, quote=True)}">' if keywords else ""
    art = ""
    if og_type == "article" and published:
        art = (f'\n<meta property="article:published_time" content="{published}">'
               f'\n<meta property="article:author" content="{bc.SITE_NAME}">')
    ld = ""
    for block in (jsonld or []):
        ld += ('\n<script type="application/ld+json">'
               + json.dumps(block, ensure_ascii=False, separators=(",", ":"))
               + "</script>")
    return f"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{ttl}</title>
<meta name="description" content="{desc}">{kw}
<meta name="robots" content="index, follow, max-image-preview:large">
<link rel="canonical" href="{canonical}">
<link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
<link rel="icon" type="image/x-icon" href="/static/favicon.ico" sizes="any">
<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
<meta name="theme-color" content="#9b5cff">
<meta property="og:site_name" content="{bc.SITE_NAME}">
<meta property="og:type" content="{og_type}">
<meta property="og:title" content="{ttl}">
<meta property="og:description" content="{desc}">
<meta property="og:url" content="{canonical}">
<meta property="og:image" content="{bc.OG_IMAGE}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{ttl}">
<meta name="twitter:description" content="{desc}">
<meta name="twitter:image" content="{bc.OG_IMAGE}">{art}{_ga_block()}{ld}
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700;800&family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700;12..96,800&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/blog.css{_ver('blog.css')}">"""


def _nav() -> str:
    return """<nav class="bnav" id="bnav">
  <a href="/" class="brand" aria-label="VibeClip studio">
    <span class="brand-block"></span>
    <span class="word">VibeClip</span><span class="sub mono">studio</span>
  </a>
  <div class="bnav-links mono">
    <a href="/#how">How it works</a>
    <a href="/blog" class="is-here">Blog</a>
    <a href="/#pricing">Pricing</a>
  </div>
  <div class="bnav-cta">
    <a href="/login" class="btn btn-ghost">Sign in</a>
    <a href="/signup" class="btn btn-solid">START FREE</a>
  </div>
</nav>"""


def _footer() -> str:
    return """<footer class="bfoot">
  <a href="/" class="brand">
    <span class="brand-block"></span>
    <span class="word">VibeClip</span><span class="sub mono">studio</span>
  </a>
  <span class="copy mono">© 2026 VibeClip</span>
  <div class="foot-links mono">
    <a href="/blog">Blog</a><a href="/#how">How it works</a><a href="/signup">Start free</a>
  </div>
</footer>"""


def _date_label(iso: str) -> str:
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    y, m, d = iso.split("-")
    return f"{months[int(m) - 1]} {int(d)}, {y}"


def _card(p: dict) -> str:
    url = f"/blog/{p['slug']}"
    return f"""<a class="bcard" href="{url}">
  <div class="bcard-top mono">
    <span class="bcard-tag">{html.escape(p['tag'])}</span>
    <span class="bcard-read">{p['read_min']} MIN READ</span>
  </div>
  <h2 class="bcard-h">{html.escape(p['title'])}</h2>
  <p class="bcard-x">{html.escape(p['excerpt'])}</p>
  <div class="bcard-foot mono">
    <time datetime="{p['date']}">{_date_label(p['date'])}</time>
    <span class="bcard-go">Read →</span>
  </div>
</a>"""


def render_index() -> str:
    canonical = f"{bc.SITE_URL}/blog"
    item_list = {
        "@context": "https://schema.org",
        "@type": "Blog",
        "name": f"{bc.SITE_NAME} Blog",
        "description": "Guides on AI video editing — turning long videos into vertical shorts.",
        "url": canonical,
        "blogPost": [
            {
                "@type": "BlogPosting",
                "headline": p["title"],
                "description": p["description"],
                "datePublished": p["date"],
                "url": f"{bc.SITE_URL}/blog/{p['slug']}",
                "author": {"@type": "Organization", "name": bc.SITE_NAME},
            }
            for p in bc.POSTS
        ],
    }
    head = _head(
        title="The VibeClip Blog — AI Video Editing Guides & Playbooks",
        description="Guides, playbooks, and explainers on AI video editing — how to turn "
                    "long videos into viral-ready vertical shorts with captions, smart "
                    "reframe, and talk-to-edit.",
        keywords="ai video editor blog, ai video editing, long video to shorts, video "
                 "repurposing, talk to edit",
        canonical=canonical,
        jsonld=[item_list],
    )
    cards = "\n".join(_card(p) for p in bc.POSTS)
    return f"""<!doctype html>
<html lang="en">
<head>
{head}
</head>
<body class="blog">
{_nav()}
<header class="bhero">
  <div class="bhero-glow" aria-hidden="true"></div>
  <div class="eyebrow mono">THE VIBECLIP BLOG</div>
  <h1>Field notes on<br><span class="grad">AI video editing.</span></h1>
  <p class="bhero-sub">Playbooks on shorts that travel, the craft behind talk-to-edit, and
    how creators turn one long video into a week of posts.</p>
</header>
<main class="bwrap">
  <div class="bgrid">
{cards}
  </div>
</main>
<section class="bcta">
  <h2>Stop scrubbing. Start shipping.</h2>
  <p>Turn your next long video into shorts by talking to it.</p>
  <a href="/signup" class="btn btn-solid btn-lg">START FREE →</a>
</section>
{_footer()}
</body>
</html>"""


def render_article(slug: str) -> str | None:
    p = bc.get_post(slug)
    if not p:
        return None
    canonical = f"{bc.SITE_URL}/blog/{slug}"
    idx = bc.POSTS.index(p)
    related = [q for q in bc.POSTS if q["slug"] != slug][:3]

    article_ld = {
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "headline": p["title"],
        "description": p["description"],
        "datePublished": p["date"],
        "dateModified": p["date"],
        "author": {"@type": "Organization", "name": bc.SITE_NAME, "url": bc.SITE_URL},
        "publisher": {
            "@type": "Organization",
            "name": bc.SITE_NAME,
            "logo": {"@type": "ImageObject", "url": bc.OG_IMAGE},
        },
        "image": bc.OG_IMAGE,
        "url": canonical,
        "mainEntityOfPage": {"@type": "WebPage", "@id": canonical},
        "keywords": p["keywords"],
    }
    breadcrumb = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": bc.SITE_URL},
            {"@type": "ListItem", "position": 2, "name": "Blog", "item": f"{bc.SITE_URL}/blog"},
            {"@type": "ListItem", "position": 3, "name": p["title"], "item": canonical},
        ],
    }
    head = _head(
        title=f"{p['title']} | {bc.SITE_NAME}",
        description=p["description"],
        keywords=p["keywords"],
        canonical=canonical,
        og_type="article",
        published=p["date"],
        jsonld=[article_ld, breadcrumb],
    )
    related_html = "\n".join(_card(q) for q in related)
    return f"""<!doctype html>
<html lang="en">
<head>
{head}
</head>
<body class="blog">
{_nav()}
<article class="post-page">
  <nav class="crumb mono" aria-label="Breadcrumb">
    <a href="/">Home</a><span>/</span><a href="/blog">Blog</a><span>/</span>
    <span class="crumb-now">{html.escape(p['tag'].title())}</span>
  </nav>
  <header class="post-head">
    <div class="post-kicker mono">
      <span class="bcard-tag">{html.escape(p['tag'])}</span>
      <span>{p['read_min']} MIN READ</span>
      <time datetime="{p['date']}">{_date_label(p['date'])}</time>
    </div>
    <h1>{html.escape(p['title'])}</h1>
  </header>
  <div class="post-prose">
{p['body']}
  </div>
  <div class="post-share mono">
    <a href="/signup" class="btn btn-solid btn-lg">Try VibeClip free →</a>
  </div>
</article>
<section class="brelated">
  <div class="brelated-head mono">KEEP READING</div>
  <div class="bgrid">
{related_html}
  </div>
</section>
{_footer()}
</body>
</html>"""


def render_sitemap() -> str:
    urls = [
        (f"{bc.SITE_URL}/", "1.0", None),
        (f"{bc.SITE_URL}/blog", "0.9", bc.POSTS[0]["date"] if bc.POSTS else None),
    ]
    for p in bc.POSTS:
        urls.append((f"{bc.SITE_URL}/blog/{p['slug']}", "0.7", p["date"]))
    rows = ""
    for loc, prio, lastmod in urls:
        lm = f"\n    <lastmod>{lastmod}</lastmod>" if lastmod else ""
        rows += (f"\n  <url>\n    <loc>{loc}</loc>{lm}"
                 f"\n    <priority>{prio}</priority>\n  </url>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{rows}\n</urlset>\n")


def render_robots() -> str:
    return (f"User-agent: *\nAllow: /\n"
            f"Disallow: /studio\nDisallow: /projects\nDisallow: /admin\n"
            f"Disallow: /api/\n\n"
            f"Sitemap: {bc.SITE_URL}/sitemap.xml\n")
