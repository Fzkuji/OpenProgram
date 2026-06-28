"""HTML shell that wraps every rendered page in the unified site chrome."""

from __future__ import annotations

import html as _html

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
    breadcrumb_html: str = "",
    prevnext_html: str = "",
    meta_html: str = "",
    extra_head: str = "",
) -> str:
    """Assemble one full HTML document.

    base — relative prefix back to _site root (e.g. "../../") so asset and nav
    links resolve from any nesting depth.
    """
    safe_title = _html.escape(title)
    return f"""<!DOCTYPE html>
<html lang="zh" data-base="{base}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title} · OpenProgram Docs</title>
<script>{_THEME_BOOT}</script>
<link rel="stylesheet" href="{base}assets/site.css">
<link rel="stylesheet" href="{base}assets/pygments-light.css" media="(prefers-color-scheme: light)" id="pyg-light">
<link rel="stylesheet" href="{base}assets/pygments-dark.css" media="(prefers-color-scheme: dark)" id="pyg-dark">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
{extra_head}
</head>
<body>
<header class="topbar">
  <button class="icon hamburger" aria-label="菜单">☰</button>
  <a class="brand" href="{base}index.html"><span class="dot"></span>OpenProgram Docs</a>
  <div class="spacer"></div>
  <button class="search-trigger" aria-label="搜索">
    <span>🔍</span><span class="label" data-i18n="search">搜索文档</span><kbd>⌘K</kbd>
  </button>
  <button class="icon" id="lang-toggle" aria-label="切换语言">中</button>
  <button class="icon" id="theme-toggle" aria-label="切换主题">☾</button>
</header>

<div class="scrim"></div>
<div class="layout">
  <nav class="sidebar">
    <input class="nav-filter" type="text" data-i18n-ph="nav_filter"
           placeholder="过滤目录…" autocomplete="off" spellcheck="false">
    <div class="nav-tree">{nav_html}</div>
  </nav>
  <main class="content"><article>{breadcrumb_html}{body_html}{meta_html}{prevnext_html}</article></main>
  <aside class="toc">{toc_html}</aside>
</div>

<div class="search-overlay">
  <div class="search-box">
    <input type="text" data-i18n-ph="search_ph" placeholder="搜索标题或正文…" autocomplete="off" spellcheck="false">
    <div class="search-results"></div>
  </div>
</div>

<script src="{base}assets/site.js"></script>
</body>
</html>
"""
