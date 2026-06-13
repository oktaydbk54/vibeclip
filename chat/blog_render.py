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


def org_schema() -> dict:
    """The Organization entity (@id `#org`), single-sourced from blog_content.

    Mirrors the landing page's `#org` node so the entire site — landing, blog
    index, every article — resolves to ONE organization in Google's and the AI
    answer engines' knowledge graphs. Blog `author`/`publisher` fields reference
    this same `@id` rather than repeating an inline name, which is what lets the
    graph link up across pages.
    """
    return {
        "@type": "Organization",
        "@id": f"{bc.SITE_URL}/#org",
        "name": bc.SITE_NAME,
        "url": f"{bc.SITE_URL}/",
        "logo": {"@type": "ImageObject", "url": bc.OG_IMAGE, "width": 512, "height": 512},
        "description": "Open-source (AGPL-3.0) AI video editor you control by talking.",
        "sameAs": bc.SAME_AS,
    }


def website_schema() -> dict:
    """The WebSite entity (@id `#website`) with a blog SearchAction. Mirrors the
    landing page so the sitelinks search box can attach to the brand."""
    return {
        "@type": "WebSite",
        "@id": f"{bc.SITE_URL}/#website",
        "url": f"{bc.SITE_URL}/",
        "name": bc.SITE_NAME,
        "publisher": {"@id": f"{bc.SITE_URL}/#org"},
        "potentialAction": {
            "@type": "SearchAction",
            "target": {
                "@type": "EntryPoint",
                "urlTemplate": f"{bc.SITE_URL}/blog?q={{search_term_string}}",
            },
            "query-input": "required name=search_term_string",
        },
    }


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
<link rel="stylesheet" media="print" onload="this.media='all'" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700;800&family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700;12..96,800&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<noscript><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700;800&family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700;12..96,800&family=IBM+Plex+Mono:wght@400;500;600&display=swap"></noscript>
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
        "publisher": {"@id": f"{bc.SITE_URL}/#org"},
        "blogPost": [
            {
                "@type": "BlogPosting",
                "headline": p["title"],
                "description": p["description"],
                "datePublished": p["date"],
                "url": f"{bc.SITE_URL}/blog/{p['slug']}",
                "author": {"@id": f"{bc.SITE_URL}/#org"},
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
        jsonld=[org_schema(), website_schema(), item_list],
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


def _faq_blocks(p: dict) -> tuple[str, dict | None]:
    """Optional FAQ for GEO: returns (prose-HTML, FAQPage JSON-LD).

    Posts may carry a `faq` list of {"q", "a"} (plain text). AI answer engines
    and Google rich results lift question/answer pairs verbatim, so a well-formed
    FAQPage block is the single biggest citation lever. Empty when no `faq`.
    """
    faq = p.get("faq")
    if not faq:
        return "", None
    items = ""
    entities = []
    for qa in faq:
        items += f"\n<h3>{html.escape(qa['q'])}</h3>\n<p>{html.escape(qa['a'])}</p>"
        entities.append({
            "@type": "Question",
            "name": qa["q"],
            "acceptedAnswer": {"@type": "Answer", "text": qa["a"]},
        })
    block = f'\n<h2 id="faq">Frequently asked questions</h2>{items}'
    jsonld = {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": entities}
    return block, jsonld


def render_article(slug: str) -> str | None:
    p = bc.get_post(slug)
    if not p:
        return None
    canonical = f"{bc.SITE_URL}/blog/{slug}"
    idx = bc.POSTS.index(p)
    related = [q for q in bc.POSTS if q["slug"] != slug][:3]
    faq_html, faq_ld = _faq_blocks(p)

    article_ld = {
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "headline": p["title"],
        "description": p["description"],
        "datePublished": p["date"],
        "dateModified": p.get("updated", p["date"]),
        "author": {"@id": f"{bc.SITE_URL}/#org"},
        "publisher": {"@id": f"{bc.SITE_URL}/#org"},
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
        jsonld=[org_schema(), article_ld, breadcrumb],
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
    # A post's lastmod is its `updated` date when present, else its publish date,
    # so editing a post (and bumping `updated`) re-signals freshness to crawlers.
    def lastmod_of(p: dict) -> str:
        return p.get("updated", p["date"])

    # The home + blog index change whenever any post does → newest post date.
    newest = max((lastmod_of(p) for p in bc.POSTS), default=None)
    urls = [
        (f"{bc.SITE_URL}/", "1.0", "weekly", newest),
        (f"{bc.SITE_URL}/blog", "0.9", "weekly", newest),
    ]
    for p in bc.POSTS:
        urls.append((f"{bc.SITE_URL}/blog/{p['slug']}", "0.7", "monthly", lastmod_of(p)))
    rows = ""
    for loc, prio, freq, lastmod in urls:
        lm = f"\n    <lastmod>{lastmod}</lastmod>" if lastmod else ""
        rows += (f"\n  <url>\n    <loc>{loc}</loc>{lm}"
                 f"\n    <changefreq>{freq}</changefreq>"
                 f"\n    <priority>{prio}</priority>\n  </url>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{rows}\n</urlset>\n")


def render_robots() -> str:
    return (f"User-agent: *\nAllow: /\n"
            f"Disallow: /studio\nDisallow: /projects\nDisallow: /admin\n"
            f"Disallow: /api/\n\n"
            f"Sitemap: {bc.SITE_URL}/sitemap.xml\n")


def render_llms() -> str:
    """A plain, JS-free product card for AI answer engines (GEO). Served at
    /llms.txt. One citable fact per line; SITE_URL is single-sourced from
    blog_content so it never drifts from canonical/sitemap. Lists the blog posts
    so engines have crawlable, quotable sources to cite."""
    posts = "\n".join(
        f"- {bc.SITE_URL}/blog/{p['slug']} — {p['title']}" for p in bc.POSTS)
    return (
        f"# {bc.SITE_NAME} — open-source AI video editor you control by talking\n\n"
        f"URL: {bc.SITE_URL}\n"
        "License: AGPL-3.0 (open source, self-hostable)\n"
        "What: Turn long landscape videos into publish-ready 9:16 vertical shorts "
        "by describing edits in plain language — cut, caption, reframe — then "
        "review the before/after and approve in one click.\n"
        "Who it's for: creators, podcasters, streamers, educators repurposing long "
        "recordings into shorts for TikTok, Reels and YouTube Shorts.\n"
        "Features: talk-to-edit; smart reframe (speaker-tracked 16:9 to 9:16); "
        "word-synced auto-captions; silence and filler removal; A/B before-after "
        "approval (fully reversible); style presets; gameplay split-screen; "
        "browser-native, no install, no GPU.\n"
        "Pricing: self-host free forever (bring your own LLM key, ~cents per short "
        "in tokens); managed hosted version free during early access.\n"
        "Privacy: when self-hosted, footage and speech-to-text stay on your "
        "machine; only your chosen LLM provider is called. Keys encrypted at rest.\n"
        f"Repository: https://github.com/oktaydbk54/vibeclip\n"
        f"Docs and articles to cite:\n{posts}\n")
