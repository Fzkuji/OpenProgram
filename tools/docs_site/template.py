"""HTML shell that wraps every rendered page in the unified site chrome."""

from __future__ import annotations

import hashlib
import html as _html
from pathlib import Path


def _asset_version() -> str:
    """Short content hash of site.css + site.js, for cache-busting their URLs.
    Same content → same version (stable diffs); any edit → new version (browsers
    re-fetch instead of serving a stale cached copy)."""
    h = hashlib.md5()
    for name in ("site.css", "site.js"):
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

# Inline head script: set theme before first paint to avoid a flash.
_THEME_BOOT = """
(function(){try{var t=localStorage.getItem('op-docs-theme');
if(!t)t=matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';
document.documentElement.setAttribute('data-theme',t);}catch(e){}})();
"""


def render_page(
    *,
    title: str,
    body_html: str,
    nav_html: str,
    toc_html: str,
    base: str,
    page_lang: str = "en",
    alt_lang_url: str = "",
    breadcrumb_html: str = "",
    prevnext_html: str = "",
    meta_html: str = "",
    extra_head: str = "",
    tabbar_html: str = "",
    canonical_url: str = "",
    description: str = "",
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
    description_meta = (
        f'<meta name="description" content="{_html.escape(description, quote=True)}">'
        if description else ""
    )
    social_description = _html.escape(description, quote=True)
    social_url = _html.escape(canonical_url, quote=True)
    alt_attr = f' data-alt-lang-url="{alt_lang_url}"' if alt_lang_url else ""
    if nav_html:
        layout_cls = ""
        sidebar_html = (
            '<nav class="sidebar">\n'
            '    <input class="nav-filter" type="text" data-i18n-ph="nav_filter"\n'
            '           placeholder="Filter docs…" autocomplete="off" spellcheck="false">\n'
            f'    <div class="nav-tree">{nav_html}</div>\n'
            '  </nav>\n  '
        )
    else:
        layout_cls = " no-side"
        sidebar_html = ""
    return f"""<!DOCTYPE html>
<html lang="{page_lang}" data-base="{base}" data-page-lang="{page_lang}" data-build="{BUILD_STAMP}"{alt_attr}>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title} · OpenProgram Docs</title>
{description_meta}
{canonical}
<meta property="og:type" content="article">
<meta property="og:locale" content="{('zh_CN' if page_lang == 'zh' else 'en_US')}">
<meta property="og:site_name" content="OpenProgram">
<meta property="og:url" content="{social_url}">
<meta property="og:title" content="{safe_title} · OpenProgram Docs">
<meta property="og:description" content="{social_description}">
<meta property="og:image" content="https://openprogram.io/docs/images/openprogram-social-card.png">
<meta property="og:image:secure_url" content="https://openprogram.io/docs/images/openprogram-social-card.png">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="OpenProgram: Self-Programming AI Agent Framework">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{safe_title} · OpenProgram Docs">
<meta name="twitter:description" content="{social_description}">
<meta name="twitter:image" content="https://openprogram.io/docs/images/openprogram-social-card.png">
<meta name="twitter:image:alt" content="OpenProgram: Self-Programming AI Agent Framework">
<script>{_THEME_BOOT}</script>
<link rel="icon" href="/favicon.ico" sizes="any">
<link rel="icon" type="image/svg+xml" href="{base}assets/mark.svg">
<link rel="stylesheet" href="{base}assets/site.css?v={ASSET_VER}">
<link rel="stylesheet" href="{base}assets/pygments-light.css" media="(prefers-color-scheme: light)" id="pyg-light">
<link rel="stylesheet" href="{base}assets/pygments-dark.css" media="(prefers-color-scheme: dark)" id="pyg-dark">
{extra_head}
</head>
<body>
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
  <button class="icon" id="theme-toggle" aria-label="Toggle theme">{_IC_MOON}{_IC_SUN}</button>
</header>
<nav class="tabbar">{tabbar_html}</nav>

<div class="scrim"></div>
<div class="layout{layout_cls}">
  {sidebar_html}<main class="content"><button class="fs-toggle" aria-label="Fullscreen" title="Fullscreen (Esc to exit)">{_IC_EXPAND}{_IC_COLLAPSE}</button><article>{breadcrumb_html}{body_html}{meta_html}{prevnext_html}</article></main>
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
