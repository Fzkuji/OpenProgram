"""HTML shell that wraps every rendered page in the unified site chrome."""

from __future__ import annotations

import hashlib
import html as _html
import json
import posixpath
from pathlib import Path


def _asset_version() -> str:
    """Short content hash of shared docs assets, for cache-busting their URLs.
    Same content → same version (stable diffs); any edit → new version (browsers
    re-fetch instead of serving a stale cached copy)."""
    h = hashlib.md5()
    for name in ("site.css", "site.js", "theme.js"):
        p = Path(__file__).parent / "assets" / name
        try:
            h.update(p.read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:8]


ASSET_VER = _asset_version()

# Per-build stamp: lets an open SPA tab detect that the site was rebuilt
# underneath it (fetched page carries a different stamp → full reload).
import time as _time
BUILD_STAMP = str(int(_time.time()))

# Inline SVG icons (stroke follows currentColor, so they theme for free).
_IC_MENU = (
    '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor"'
    ' stroke-width="1.6" stroke-linecap="round"><path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11"/></svg>'
)
_IC_SEARCH = (
    '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor"'
    ' stroke-width="1.6" stroke-linecap="round"><circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5 14 14"/></svg>'
)
_IC_MOON = (
    '<svg class="ic-moon" width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor"'
    ' stroke-width="1.5" stroke-linejoin="round"><path d="M13.5 9.5A6 6 0 1 1 6.5 2.5a5 5 0 0 0 7 7z"/></svg>'
)
_IC_SUN = (
    '<svg class="ic-sun" width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor"'
    ' stroke-width="1.5" stroke-linecap="round"><circle cx="8" cy="8" r="3.2"/>'
    '<path d="M8 1.2v1.8M8 13v1.8M1.2 8H3M13 8h1.8M3.2 3.2l1.3 1.3M11.5 11.5l1.3 1.3M12.8 3.2l-1.3 1.3M4.5 11.5l-1.3 1.3"/></svg>'
)
_IC_SYSTEM = (
    '<svg class="ic-system" width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor"'
    ' stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
    '<rect x="1.5" y="2.5" width="13" height="9" rx="1.5"/><path d="M8 11.5V14M5.5 14h5"/></svg>'
)
_IC_GLOBE = (
    '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor"'
    ' stroke-width="1.4"><circle cx="8" cy="8" r="6.2"/><path d="M1.8 8h12.4" stroke-linecap="round"/>'
    '<ellipse cx="8" cy="8" rx="2.8" ry="6.2"/></svg>'
)
_IC_CHEV = (
    '<svg class="chev" width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor"'
    ' stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6l4 4 4-4"/></svg>'
)
_IC_CHECK = (
    '<svg class="check" width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"'
    ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8.5l3.2 3L13 5"/></svg>'
)
# corner brackets pointing out (expand) / in (collapse)
_IC_EXPAND = (
    '<svg class="ic-expand" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor"'
    ' stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M6.2 2H2v4.2M9.8 2H14v4.2M6.2 14H2V9.8M9.8 14H14V9.8"/></svg>'
)
_IC_COLLAPSE = (
    '<svg class="ic-collapse" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor"'
    ' stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M2 6.2h4.2V2M14 6.2H9.8V2M2 9.8h4.2V14M14 9.8H9.8V14"/></svg>'
)



def render_page(
    *,
    title: str,
    body_html: str,
    nav_html: str,
    toc_html: str,
    base: str,
    page_lang: str = "en",
    alt_lang_url: str = "",
    alt_lang_canonical_url: str = "",
    breadcrumb_html: str = "",
    prevnext_html: str = "",
    meta_html: str = "",
    extra_head: str = "",
    tabbar_html: str = "",
    canonical_url: str = "",
    description: str = "",
    docs_root_url: str = "",
    markdown_url: str = "",
    llms_url: str = "",
    article_class: str = "",
) -> str:
    """Assemble one full HTML document.

    base — relative prefix back to _site root (e.g. "../../") so asset and nav
    links resolve from any nesting depth.
    page_lang — this page's own content language ("zh"/"en").
    alt_lang_url — URL of the other-language version of THIS page, if any; the
    language toggle navigates there.
    """
    safe_title = _html.escape(title)
    canonical = (
        f'<link rel="canonical" href="{_html.escape(canonical_url, quote=True)}">'
        if canonical_url else ""
    )
    markdown_alternate = (
        f'<link rel="alternate" type="text/markdown" '
        f'href="{_html.escape(markdown_url, quote=True)}">'
        if markdown_url else ""
    )
    describedby = (
        f'<link rel="describedby" href="{_html.escape(llms_url, quote=True)}">'
        if llms_url else ""
    )
    language_alternates = ""
    if canonical_url and alt_lang_canonical_url:
        if page_lang == "zh":
            en_url, zh_url = alt_lang_canonical_url, canonical_url
        else:
            en_url, zh_url = canonical_url, alt_lang_canonical_url
        language_alternates = "\n".join(
            (
                f'<link rel="alternate" hreflang="en" href="{_html.escape(en_url, quote=True)}">',
                f'<link rel="alternate" hreflang="zh-Hans" href="{_html.escape(zh_url, quote=True)}">',
                f'<link rel="alternate" hreflang="x-default" href="{_html.escape(en_url, quote=True)}">',
            )
        )
    description_meta = (
        f'<meta name="description" content="{_html.escape(description, quote=True)}">'
        if description else ""
    )
    social_description = _html.escape(description, quote=True)
    social_url = _html.escape(canonical_url, quote=True)
    # The site root holds favicon.ico; the docs are mounted one level below it.
    site_root = posixpath.dirname(base.rstrip("/")).rstrip("/") + "/"
    social_image = _html.escape(
        (docs_root_url or base) + "images/openprogram-social-card.png", quote=True)
    breadcrumb_data = ""
    if canonical_url and docs_root_url and canonical_url.rstrip("/") != docs_root_url.rstrip("/"):
        payload = {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": 1,
                    "name": "OpenProgram Docs",
                    "item": docs_root_url,
                },
                {"@type": "ListItem", "position": 2, "name": title},
            ],
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
        breadcrumb_data = f'<script type="application/ld+json">{encoded}</script>'
    article_attr = f' class="{_html.escape(article_class, quote=True)}"' if article_class else ""
    alt_attr = f' data-alt-lang-url="{alt_lang_url}"' if alt_lang_url else ""
    if nav_html:
        layout_cls = ""
        sidebar_html = (
            '<nav class="sidebar" aria-label="Documentation">\n'
            '    <input class="nav-filter" type="text" data-i18n-ph="nav_filter"\n'
            '           aria-label="Filter docs" placeholder="Filter docs…" autocomplete="off" spellcheck="false">\n'
            f'    <div class="nav-tree">{nav_html}</div>\n'
            '  </nav>\n  '
        )
    else:
        layout_cls = " no-side"
        sidebar_html = ""
    if not toc_html:
        layout_cls += " no-toc"
    return f"""<!DOCTYPE html>
<html lang="{page_lang}" data-base="{base}" data-page-lang="{page_lang}" data-build="{BUILD_STAMP}"{alt_attr}>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title} · OpenProgram Docs</title>
{description_meta}
{canonical}
{markdown_alternate}
{describedby}
{language_alternates}
{breadcrumb_data}
<meta property="og:type" content="article">
<meta property="og:locale" content="{('zh_CN' if page_lang == 'zh' else 'en_US')}">
<meta property="og:site_name" content="OpenProgram">
<meta property="og:url" content="{social_url}">
<meta property="og:title" content="{safe_title} · OpenProgram Docs">
<meta property="og:description" content="{social_description}">
<meta property="og:image" content="{social_image}">
<meta property="og:image:secure_url" content="{social_image}">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="OpenProgram: Self-Programming AI Agent Framework">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{safe_title} · OpenProgram Docs">
<meta name="twitter:description" content="{social_description}">
<meta name="twitter:image" content="{social_image}">
<meta name="twitter:image:alt" content="OpenProgram: Self-Programming AI Agent Framework">
<link rel="icon" href="{site_root}favicon.ico" sizes="any">
<link rel="icon" type="image/svg+xml" href="{base}assets/mark.svg">
<link rel="stylesheet" href="{base}assets/site.css?v={ASSET_VER}">
<link rel="stylesheet" href="{base}assets/pygments-light.css" media="(prefers-color-scheme: light)" id="pyg-light">
<link rel="stylesheet" href="{base}assets/pygments-dark.css" media="(prefers-color-scheme: dark)" id="pyg-dark">
<script src="{base}assets/theme.js?v={ASSET_VER}"></script>
{extra_head}
</head>
<body>
<a class="skip" href="#content" data-i18n="skip">Skip to content</a>
<header class="topbar">
  <button class="icon hamburger" aria-label="Menu">{_IC_MENU}</button>
  <a class="brand" href="{base}index.html"><img class="mark" src="{base}assets/mark.svg" alt="">OpenProgram Docs</a>
  <div class="spacer"></div>
  <button class="search-trigger" aria-label="Search">
    {_IC_SEARCH}<span class="label" data-i18n="search">Search docs</span><kbd>⌘K</kbd>
  </button>
  <div class="lang-wrap">
    <button class="icon lang-trigger" id="lang-toggle" aria-haspopup="true" aria-expanded="false" aria-label="Language">
      {_IC_GLOBE}<span class="lang-label">EN</span>{_IC_CHEV}
    </button>
    <div class="lang-menu" id="lang-menu" role="menu">
      <button class="lang-opt" data-lang="en" role="menuitem"><span>English</span>{_IC_CHECK}</button>
      <button class="lang-opt" data-lang="zh" role="menuitem"><span>简体中文</span>{_IC_CHECK}</button>
    </div>
  </div>
  <div class="theme-wrap">
    <button class="icon" id="theme-toggle" aria-label="Theme" aria-haspopup="menu" aria-controls="theme-menu" aria-expanded="false">{_IC_SYSTEM}{_IC_MOON}{_IC_SUN}</button>
    <div class="theme-menu" id="theme-menu" role="menu" aria-labelledby="theme-toggle">
      <button class="theme-opt" data-theme-choice="system" role="menuitemradio" aria-checked="true"><span>Follow system</span>{_IC_CHECK}</button>
      <button class="theme-opt" data-theme-choice="light" role="menuitemradio" aria-checked="false"><span>Light</span>{_IC_CHECK}</button>
      <button class="theme-opt" data-theme-choice="dark" role="menuitemradio" aria-checked="false"><span>Dark</span>{_IC_CHECK}</button>
    </div>
  </div>
</header>
<nav class="tabbar">{tabbar_html}</nav>

<div class="scrim"></div>
<div class="layout{layout_cls}">
  {sidebar_html}<main class="content" id="content"><button class="fs-toggle" aria-label="Fullscreen" title="Fullscreen (Esc to exit)">{_IC_EXPAND}{_IC_COLLAPSE}</button><article{article_attr}>{breadcrumb_html}{body_html}{meta_html}{prevnext_html}</article></main>
  <aside class="toc">{toc_html}</aside>
</div>

<div class="search-overlay">
  <div class="search-box">
    <input type="text" data-i18n-ph="search_ph" placeholder="Search titles or text…" autocomplete="off" spellcheck="false">
    <div class="search-results"></div>
  </div>
</div>

<script src="{base}assets/site.js?v={ASSET_VER}"></script>
</body>
</html>
"""
