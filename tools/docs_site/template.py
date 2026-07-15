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
) -> str:
    """Assemble one full HTML document.

    base — relative prefix back to _site root (e.g. "../../") so asset and nav
    links resolve from any nesting depth.
    page_lang — this page's own content language ("zh"/"en").
    alt_lang_url — URL of the other-language version of THIS page, if any; the
    language toggle navigates there.
    """
    safe_title = _html.escape(title)
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
<script>{_THEME_BOOT}</script>
<link rel="stylesheet" href="{base}assets/site.css?v={ASSET_VER}">
<link rel="stylesheet" href="{base}assets/pygments-light.css" media="(prefers-color-scheme: light)" id="pyg-light">
<link rel="stylesheet" href="{base}assets/pygments-dark.css" media="(prefers-color-scheme: dark)" id="pyg-dark">
{extra_head}
</head>
<body>
<header class="topbar">
  <button class="icon hamburger" aria-label="Menu">☰</button>
  <a class="brand" href="{base}index.html"><span class="dot"></span>OpenProgram Docs</a>
  <div class="spacer"></div>
  <button class="search-trigger" aria-label="Search">
    <span>🔍</span><span class="label" data-i18n="search">Search docs</span><kbd>⌘K</kbd>
  </button>
  <button class="icon" id="lang-toggle" aria-label="Toggle language">EN</button>
  <button class="icon" id="theme-toggle" aria-label="Toggle theme">☾</button>
</header>
<nav class="tabbar">{tabbar_html}</nav>

<div class="scrim"></div>
<div class="layout{layout_cls}">
  {sidebar_html}<main class="content"><article>{breadcrumb_html}{body_html}{meta_html}{prevnext_html}</article></main>
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
