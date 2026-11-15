"""Build the OpenProgram documentation site.

Scans docs/ for html documents and generated markdown, renders each into one unified
shell (left nav tree, right on-page toc, dark/light theme, search), and writes
the static site to docs/_site/.

Run:  python -m scripts.docs_site.build
"""

from __future__ import annotations

import html as _html
import json
import os
import re
import shutil
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin
from mdit_py_plugins.deflist import deflist_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight as pyg_highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

from openprogram.host._platform import locking
from openprogram.operations import project_site

from . import html_source
from . import nav as navmod
from . import search as searchmod
from .governance import format_checks
from .template import render_page

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_ROOT = REPO_ROOT / "docs"
OUT_ROOT = DOCS_ROOT / "_site"
ASSETS_SRC = Path(__file__).parent / "assets"

# Path prefix the built site is mounted under. The worker serves it at /docs,
# so all asset/nav/internal links are absolute (/docs/...) — this makes them
# resolve correctly whether the page URL has a trailing slash or not.
# Override with OPENPROGRAM_DOCS_BASE (must start and end with "/"); the
# publishing workflow sets it to project_site.DOCS_PATH.
DEPLOY_BASE = os.environ.get("OPENPROGRAM_DOCS_BASE", "/docs/")

# The public address of the site (openprogram.operations.project_site). Every
# absolute URL the build writes — canonical and language links, the sitemap,
# Markdown copies, llms.txt and the files in docs/_static_root — derives from
# it, whatever path the build is mounted under locally.
SITE_URL = project_site.SITE_URL


def _public_url(path: str = "") -> str:
    return SITE_URL.rstrip("/") + "/" + path.lstrip("/")


def _public_docs_url(path: str = "") -> str:
    return _public_url("docs/" + path.lstrip("/"))

_SLUG_DEDUP: dict[str, int] = {}


@contextmanager
def _site_build_lock(timeout: float = 180.0):
    """Serialize manual, worker, and release documentation builds.

    A thread lock in the worker cannot coordinate with a manual or release
    build in another process. Both used the same ``_site.tmp`` directory, so
    either process could delete the other's partially rendered tree and then
    publish it. The byte lock is advisory and cross-platform through the
    compatibility seam; it does not alter file permissions or ACLs.
    """

    lock_path = DOCS_ROOT / "_site.build.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                locking.flock(
                    handle.fileno(), locking.LOCK_EX | locking.LOCK_NB,
                )
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"could not acquire documentation build lock within {timeout}s"
                    )
                time.sleep(0.1)
        try:
            yield
        finally:
            locking.flock(handle.fileno(), locking.LOCK_UN)


# ── markdown rendering ──────────────────────────────────────────────────────

_slugify = html_source.slugify


def _meta_description(body_html: str, title: str) -> str:
    text = searchmod.plain_text(body_html)
    text = text.removeprefix(title).removeprefix(" #").strip()
    if len(text) <= 160:
        return text
    return text[:160].rsplit(" ", 1)[0]


def _highlight_code(code: str, lang: str, _attrs) -> str:
    # No language tag → plain text (these are often ASCII diagrams; guessing a
    # lexer mangles arrows/emoji into red .err tokens). Return "" so markdown-it
    # emits its own safely-escaped <pre><code>.
    if not lang:
        return ""
    try:
        lexer = get_lexer_by_name(lang, stripnl=False)
    except (ClassNotFound, ValueError):
        return ""
    inner = pyg_highlight(code, lexer, HtmlFormatter(nowrap=True))
    return f'<pre><code class="language-{lang}">{inner}</code></pre>'


def _pyg_css(style: str) -> str:
    """Pygments style defs scoped to our code blocks, minus the bare `pre` and
    line-number rules it emits unscoped (they would override our pre styling)."""
    defs = HtmlFormatter(style=style).get_style_defs("article pre code")
    keep = [ln for ln in defs.splitlines()
            if not ln.startswith("pre ") and "linenos" not in ln
            and not ln.startswith("article pre code {")]  # drop bg, we style it
    return "\n".join(keep)


def make_md() -> MarkdownIt:
    # linkify off: it turns bare code filenames like `branch.py` / `session.py:178`
    # into bogus http://branch.py links (which the browser then tries to resolve).
    # Real links are written explicitly with [](), so auto-linking is all downside.
    md = MarkdownIt("gfm-like", {"html": True, "linkify": False, "highlight": _highlight_code})
    md.use(anchors_plugin, max_level=3, slug_func=_make_unique_slug,
           permalink=True, permalinkSymbol="#", permalinkSpace=False)
    # Assign deeper anchors after existing levels to preserve their public URLs.
    md.use(anchors_plugin, min_level=4, max_level=6, slug_func=_make_unique_slug,
           permalink=True, permalinkSymbol="#", permalinkSpace=False)
    md.use(deflist_plugin)
    md.use(tasklists_plugin, enabled=True)
    md.enable("table")
    return md


def _make_unique_slug(text: str) -> str:
    base = _slugify(text)
    n = _SLUG_DEDUP.get(base, 0)
    _SLUG_DEDUP[base] = n + 1
    return base if n == 0 else f"{base}-{n}"


# ── callouts (GitHub-style > [!NOTE] / [!WARNING] / [!TIP] / [!IMPORTANT]) ───

def _callout_svg(path: str) -> str:
    return (
        '<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor"'
        f' stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">{path}</svg>'
    )


_CALLOUT_KINDS = {  # kind -> (css class, icon svg, English label, 中文 label)
    "NOTE": ("note", _callout_svg('<circle cx="8" cy="8" r="6.4"/><path d="M8 7.4V11M8 5.1v.1"/>'), "Note", "提示"),
    "TIP": ("tip", _callout_svg('<circle cx="8" cy="8" r="6.4"/><path d="M5.2 8.3l2 2L10.8 6"/>'), "Tip", "建议"),
    "IMPORTANT": ("important", _callout_svg('<path d="M8 1.6l2 4.1 4.5.6-3.2 3.1.7 4.5L8 11.8l-4 2.1.7-4.5L1.5 6.3l4.5-.6z"/>'), "Important", "重要"),
    "WARNING": ("warning", _callout_svg('<path d="M8 2.2 1.8 13h12.4z"/><path d="M8 6.6v3M8 11.7v.1"/>'), "Warning", "注意"),
    "CAUTION": ("caution", _callout_svg('<circle cx="8" cy="8" r="6.4"/><path d="M3.5 3.5l9 9"/>'), "Caution", "警告"),
}
_BLOCKQUOTE_RE = re.compile(r"<blockquote>\s*(.*?)\s*</blockquote>", re.DOTALL)
_CALLOUT_HEAD_RE = re.compile(
    r'^\s*<p>\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*(.*?)</p>',
    re.DOTALL | re.IGNORECASE)


def apply_callouts(html: str) -> str:
    """Turn blockquotes whose first line is [!KIND] into styled callout boxes."""
    def repl(m):
        inner = m.group(1)
        head = _CALLOUT_HEAD_RE.match(inner)
        if not head:
            return m.group(0)
        kind = head.group(1).upper()
        cls, icon, label, label_zh = _CALLOUT_KINDS[kind]
        rest_first = head.group(2).strip()  # text on the same line after [!KIND]
        rest = inner[head.end():]
        first = f"<p>{rest_first}</p>" if rest_first else ""
        return (f'<div class="callout callout-{cls}">'
                f'<div class="callout-head"><span class="callout-icon">{icon}</span>'
                f'<span data-title-zh="{label_zh}">{label}</span></div>'
                f'<div class="callout-body">{first}{rest}</div></div>')
    return _BLOCKQUOTE_RE.sub(repl, html)


# ── toc extraction (from rendered html headings) ────────────────────────────

_HEADING_RE = re.compile(r'<h([2-6])[^>]*\bid="([^"]+)"[^>]*>(.*?)</h\1>', re.DOTALL)


def extract_toc(body_html: str) -> str:
    items = []
    for m in _HEADING_RE.finditer(body_html):
        level, hid, inner = m.group(1), m.group(2), m.group(3)
        inner = re.sub(r'<a class="header-anchor".*?</a>', "", inner, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", "", inner).strip()
        text = _html.unescape(text)  # decode &quot; etc.; re-escaped on output
        if not text:
            continue
        items.append((int(level), _html.unescape(hid), text))
    if not items:
        return ""
    tree = []
    stack = []
    for level, hid, text in items:
        while stack and stack[-1][0] >= level:
            stack.pop()
        node = (level, hid, text, [])
        (stack[-1][3] if stack else tree).append(node)
        stack.append(node)

    def render_group(nodes):
        rows = ['<ul class="toc-list">']
        for level, hid, text, children in nodes:
            rows.append('<li>')
            if children:
                rows.append('<details class="toc-disclosure" open=""><summary>')
            rows.append(
                f'<a class="lvl-{level}" href="#{_html.escape(hid)}">'
                f'{_html.escape(text, quote=False)}</a>'
            )
            if children:
                rows.append('</summary>')
                rows.append(render_group(children))
                rows.append('</details>')
            rows.append("</li>")
        rows.append("</ul>")
        return "\n".join(rows)

    return '<div class="toc-title" data-i18n="on_this_page">On this page</div>\n' + render_group(tree)


def relink_internal(body_html: str, cur_dir: Path) -> str:
    """Rewrite relative .md links to absolute built .html URLs.

    cur_dir is the page's directory relative to _site root. Relative links are
    resolved against it and re-emitted under DEPLOY_BASE, so they work no matter
    whether the page URL has a trailing slash.
    """
    def repl(m):
        attr, url = m.group(1), m.group(2)
        if (re.match(r"^[a-z]+://", url) or url.startswith("#")
                or url.startswith("mailto:") or url.startswith("/")):
            return m.group(0)
        anchor = ""
        if "#" in url:
            url, anchor = url.split("#", 1)
            anchor = "#" + anchor
        if url.endswith(".html.md"):
            pass  # a page's published Markdown copy, not a Markdown source
        elif url.endswith(".md"):
            url = url[:-3] + ".html"
        elif url.endswith(".html"):
            source = DOCS_ROOT / cur_dir / url
            primary = (source.with_name(source.name[:-8] + ".html")
                       if source.name.endswith(".zh.html") else source)
            if source.is_file() and primary.is_file() and primary.with_suffix(".md").is_file():
                # Match nav._dedupe_md_html: an explicit HTML source link
                # selects the visualization, not its Markdown sibling.
                url = url[:-5] + ".viz.html"
        if not url:  # pure anchor like "#foo" already handled above
            return f'{attr}"{anchor}"'
        # resolve relative to the current page's directory, then make absolute
        resolved = os.path.normpath(str(cur_dir / url)).replace("\\", "/")
        return f'{attr}"{DEPLOY_BASE}{resolved}{anchor}"'
    # Only attributes of real tags: a page may show `href="x"` inside <code>.
    return re.sub(r'(<[a-zA-Z][^<>]*?[\s:]href=)"([^"]+)"', repl, body_html)


# ── html document sources ───────────────────────────────────────────────────

@dataclass
class _HtmlDocument:
    body: str
    toc: str
    description: str
    search_text: str
    markdown: str


def render_html_document(source: str, cur_dir: Path) -> _HtmlDocument:
    """Render a foundation-styled HTML source for the site chrome."""
    body = html_source.add_heading_ids(html_source.article_markup(source), _make_unique_slug)
    body = relink_internal(body, cur_dir)
    return _HtmlDocument(
        body=body,
        toc=extract_toc(body),
        description=html_source.page_description(source, body),
        search_text=html_source.page_text(body),
        markdown=html_source.to_markdown(
            body, link_base=DEPLOY_BASE, public_base=_public_docs_url()),
    )


# ── hand-written html ───────────────────────────────────────────────────────

# Appended to the shipped .raw.html copy: once the reader clicks inside the
# iframe, focus is in that document and its ESC never reaches the shell's key
# handler, so forward it up (site.js listens for this message).
_ESC_FORWARD = (
    "\n<script>document.addEventListener('keydown',function(e){"
    "if(e.key==='Escape'&&window.parent!==window)"
    "parent.postMessage('op-docs-esc','*');});</script>\n"
)


def add_raw_readability(html_text: str) -> str:
    """Load shared reading support without replacing standalone page styles."""
    llms_url = _html.escape(_public_url("llms.txt"), quote=True)
    resources = (
        f'<link rel="describedby" href="{llms_url}">\n'
        f'<link rel="stylesheet" href="{DEPLOY_BASE}assets/raw-readability.css">\n'
        f'<script defer src="{DEPLOY_BASE}assets/raw-readability.js"></script>\n'
    )
    if re.search(r"</head\s*>", html_text, flags=re.IGNORECASE):
        return re.sub(r"</head\s*>", lambda match: resources + match.group(),
                      html_text, count=1, flags=re.IGNORECASE)
    return resources + html_text


def embed_html(raw_url: str) -> str:
    """Embed a standalone hand-written page via an isolated iframe.

    iframe sandboxing keeps the original's fixed-position layouts, styles,
    scripts, and visualizations 100% intact without leaking into the shell.
    The page is shipped alongside as a .raw.html sibling with site links resolved.
    """
    return (
        f'<iframe class="viz-frame" src="{raw_url}" loading="lazy" '
        f'title="可视化文档"></iframe>'
    )


# ── ordered flatten + breadcrumbs + git mtime ───────────────────────────────

def flatten_pages(sections):
    """Pages in sidebar display order, each with its section chain (for
    prev/next and breadcrumbs). Returns list of (page, [(en, zh)])."""
    out = []
    for sec in sections:
        chain = [(sec.title, sec.title_zh or sec.title)]
        if sec.area:
            chain = [sec.area]
            if sum(other.area == sec.area for other in sections) > 1:
                chain.append((sec.title.split(" · ")[-1], (sec.title_zh or sec.title).split(" · ")[-1]))
        for p in sec.pages:
            out.append((p, chain))
    return out


def _bc_span(en, zh, cls=""):
    c = f' {cls}' if cls else ""
    if zh and zh != en:
        return (f'<span class="bc-seg{c}" data-title-zh="{_html.escape(zh, quote=True)}"'
                f'>{_html.escape(en)}</span>')
    return f'<span class="bc-seg{c}">{_html.escape(en)}</span>'


def render_breadcrumb(chain, title, title_zh=""):
    if not chain:
        return ""
    sep = " <span class='bc-sep'>›</span> "
    # A level that repeats the next one (or the page itself) tells the reader nothing.
    names = [en for en, _zh in chain] + [title]
    kept = [item for i, item in enumerate(chain) if item[0].casefold() != names[i + 1].casefold()]
    segs = [_bc_span(en, zh) for (en, zh) in kept]
    segs.append(_bc_span(title, title_zh or title, cls="bc-current"))
    return f'<nav class="breadcrumb">{sep.join(segs)}</nav>'


def render_prevnext(prev_p, next_p):
    if not prev_p and not next_p:
        return ""
    def link(page, direction, label):
        if page is None:
            return ""
        href = DEPLOY_BASE + page.out.as_posix()
        paths = (f' data-href-en="{href}" data-href-zh="{DEPLOY_BASE}{page.zh_out.as_posix()}"'
                 if page.zh_out else "")
        title = (f' data-title-zh="{_html.escape(page.title_zh, quote=True)}"'
                 if page.title_zh else "")
        return (f'<a class="pn-link pn-{direction}" href="{href}"{paths}>'
                f'<span class="pn-dir" data-i18n="{direction}">{label}</span>'
                f'<span class="pn-title"{title}>{_html.escape(page.title)}</span></a>')
    left, right = link(prev_p, "prev", "Previous"), link(next_p, "next", "Next")
    return f'<div class="prevnext">{left}{right}</div>'


_GIT_MTIME_CACHE: dict[str, str] = {}


def git_mtime(src: Path) -> str:
    """Last commit date (YYYY-MM-DD) for a file, or '' if not tracked."""
    key = str(src)
    if key in _GIT_MTIME_CACHE:
        return _GIT_MTIME_CACHE[key]
    import subprocess
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%ad", "--date=short", "--", str(src)],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:
        out = ""
    _GIT_MTIME_CACHE[key] = out
    return out


# ── nav tree -> html ────────────────────────────────────────────────────────

def render_tabbar(tabs, active_key: str, base: str) -> str:
    links = []
    for t in tabs:
        href = base + str(t.landing).replace("\\", "/")
        cls = " active" if t.key == active_key else ""
        zh = (f' data-title-zh="{_html.escape(t.title_zh, quote=True)}"'
              if t.title_zh and t.title_zh != t.title else "")
        landing = next((p for sec in t.sections for p in sec.pages if p.out == t.landing), None)
        if landing and landing.zh_out:
            zh += f' data-href-en="{href}" data-href-zh="{base}{landing.zh_out.as_posix()}"'
        links.append(f'<a class="tablink{cls}" href="{href}"{zh}>{_html.escape(t.title)}</a>')
    return "".join(links)



def render_nav(sections, current_out: Path, base: str, *, collapsible: bool = False) -> str:
    """Render product groups or native disclosures for the design catalog."""
    def navlink(p) -> str:
        href = base + str(p.out).replace("\\", "/")
        active = " active" if p.out == current_out else ""
        # If a Chinese version exists, carry its label + URL so the language
        # toggle can switch this sidebar entry to Chinese.
        extra = ""
        if p.zh_out is not None:
            zh_href = base + str(p.zh_out).replace("\\", "/")
            extra = (f' data-title-zh="{_html.escape(p.title_zh or p.title, quote=True)}"'
                     f' data-href-en="{href}" data-href-zh="{zh_href}"')
        return f'<a class="navlink{active}" href="{href}"{extra}>{_html.escape(p.title)}</a>'

    def title_attrs(title, title_zh):
        return (f' data-title-zh="{_html.escape(title_zh, quote=True)}"'
                if title_zh and title_zh != title else "")

    def disclosure(title, title_zh, count, active, css):
        opened = " open" if active else ""
        return (f'<details class="{css} nav-disclosure"{opened}>'
                f'<summary><span class="nav-sec-title"{title_attrs(title, title_zh)}>'
                f'{_html.escape(title)}</span><span class="nav-count">{count}</span></summary>')

    def section_html(sec, title=None, title_zh=None):
        en, zh = title or sec.title, title_zh or sec.title_zh
        if collapsible:
            start = disclosure(en, zh, len(sec.pages), any(p.out == current_out for p in sec.pages), "nav-sec")
        else:
            start = f'<div class="nav-sec"><div class="nav-sec-title"{title_attrs(en, zh)}>{_html.escape(en)}</div>'
        return start + "\n".join(navlink(p) for p in sec.pages) + ("</details>" if collapsible else "</div>")

    if not collapsible:
        return "\n".join(section_html(sec) for sec in sections)

    areas = {}
    for sec in sections:
        areas.setdefault(sec.area or (sec.title, sec.title_zh), []).append(sec)
    out = []
    for (en, zh), children in areas.items():
        if len(children) == 1:
            out.append(section_html(children[0], en, zh))
            continue
        pages = [p for sec in children for p in sec.pages]
        out.append(disclosure(en, zh, len(pages), any(p.out == current_out for p in pages), "nav-branch"))
        for sec in children:
            # The parent already provides the domain, so child labels can be short.
            out.append(section_html(sec, sec.title.split(" · ")[-1], sec.title_zh.split(" · ")[-1]))
        out.append("</details>")
    return "\n".join(out)


# ── format gate ─────────────────────────────────────────────────────────────

def format_gate() -> dict[str, list]:
    """Stop on any page that breaks the format; there is no fallback rendering.

    Deploys set OPENPROGRAM_DOCS_FORMAT=quarantine: the build then succeeds and
    a page whose markup is unsafe to show publishes, in that language only, as a
    visible error page. Other faults are logged and the page keeps rendering.
    Returns the quarantined pages.
    """
    errors = format_checks.check_docs_root(DOCS_ROOT)
    if not errors:
        return {}
    if os.environ.get("OPENPROGRAM_DOCS_FORMAT") != "quarantine":
        raise format_checks.DocsFormatError(errors)
    print(format_checks.DocsFormatError(errors), file=sys.stderr)
    quarantined: dict[str, list] = {}
    for finding in errors:
        if finding.rule in format_checks.UNSAFE_TO_RENDER:
            quarantined.setdefault(finding.path, []).append(finding)
    return quarantined


def render_quarantine_page(findings: list) -> str:
    items = "".join(f"<li><code>{_html.escape(f.render())}</code></li>" for f in findings)
    return f"<h1>This page failed the docs format check</h1><ul>{items}</ul>"


# ── main build ──────────────────────────────────────────────────────────────

def build() -> int:
    with _site_build_lock():
        return _build_locked()


def _build_locked() -> int:
    # Regenerate the code-derived reference pages first (CLI / config keys /
    # provider registry) so discover() picks them up. Idempotent writes —
    # unchanged content never touches mtimes, so the worker's mtime-based
    # auto-rebuild can't loop on the generator's own output.
    from .generate_reference import generate_all
    generate_all()

    # Build into _site.tmp, then swap it in with two renames. The worker
    # rebuilds in a background thread while still serving _site; without the
    # swap, readers would see a half-deleted tree for the ~10s a build takes.
    global OUT_ROOT
    final = DOCS_ROOT / "_site"
    OUT_ROOT = DOCS_ROOT / "_site.tmp"
    try:
        rc = _build_into_out_root()
        if rc == 0:
            old = DOCS_ROOT / "_site.old"
            if old.exists():
                shutil.rmtree(old)
            had_final = final.exists()
            if had_final:
                final.rename(old)
            try:
                OUT_ROOT.rename(final)
            except Exception:
                # Never leave /docs missing if the second half of the swap
                # fails (for example because an antivirus briefly holds a
                # Windows directory handle).
                if had_final and old.exists() and not final.exists():
                    old.rename(final)
                raise
            shutil.rmtree(old, ignore_errors=True)
        return rc
    finally:
        OUT_ROOT = final


def _build_into_out_root() -> int:
    if not DOCS_ROOT.exists():
        print(f"docs root not found: {DOCS_ROOT}", file=sys.stderr)
        return 1

    quarantined = format_gate()
    pages = navmod.discover(DOCS_ROOT)
    tabs = navmod.build_tabs(DOCS_ROOT, pages)

    if OUT_ROOT.exists():
        shutil.rmtree(OUT_ROOT)
    OUT_ROOT.mkdir(parents=True)

    # assets
    out_assets = OUT_ROOT / "assets"
    shutil.copytree(ASSETS_SRC, out_assets)
    (out_assets / "pygments-light.css").write_text(
        _pyg_css("default"), encoding="utf-8")
    (out_assets / "pygments-dark.css").write_text(
        _pyg_css("github-dark"), encoding="utf-8")

    # copy image/static assets referenced by docs (svg/png/jpg/gif/webp/avif),
    # preserving their relative path so in-doc <img>/![] links resolve.
    IMG_EXT = {".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".ico"}
    for path in DOCS_ROOT.rglob("*"):
        if path.suffix.lower() not in IMG_EXT or not path.is_file():
            continue
        rel = path.relative_to(DOCS_ROOT)
        if rel.parts and rel.parts[0] in ("_site", "_site.tmp", "_site.old"):
            continue
        dst = OUT_ROOT / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dst)

    md = make_md()
    search_records: list[dict] = []
    rendered = 0

    # ordered sequence (for prev/next) + per-page section chain (breadcrumbs),
    # tab by tab so reading order follows the navbar.
    ordered = []
    tab_key_of: dict[Path, str] = {}
    for tab in tabs:
        for pg, chain in flatten_pages(tab.sections):
            ordered.append((pg, [(tab.title, tab.title_zh)] + chain))
            tab_key_of[pg.out] = tab.key
    tab_by_key = {t.key: t for t in tabs}
    seq = [pg for pg, _chain in ordered]
    chain_of = {pg.out: chain for pg, chain in ordered}
    idx_of = {pg.out: i for i, pg in enumerate(seq)}

    for p in pages:
        global _SLUG_DEDUP
        _SLUG_DEDUP = {}
        base = DEPLOY_BASE

        document = None
        failed = quarantined.get(f"docs/{p.rel.as_posix()}")
        if failed:
            html_text = render_quarantine_page(failed)
            body, toc = html_text, ""
        elif p.kind == "md":
            text = p.src.read_text(encoding="utf-8", errors="replace")
            body = md.render(text)
            body = apply_callouts(body)
            body = relink_internal(body, p.out.parent)
            toc = extract_toc(body)
        else:
            html_text = p.src.read_text(encoding="utf-8", errors="replace")
            if html_source.is_html_document(html_text):
                document = render_html_document(html_text, p.out.parent)
                body, toc = document.body, document.toc
            else:
                # The gate has already let only a declared prototype reach here.
                raw_rel = p.out.with_suffix("").with_suffix(".raw.html")
                raw_path = OUT_ROOT / raw_rel
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                raw_path.write_text(add_raw_readability(relink_internal(html_text, p.out.parent)) + _ESC_FORWARD, encoding="utf-8")
                body = embed_html(DEPLOY_BASE + str(raw_rel).replace("\\", "/"))
                toc = ""

        tab = tab_by_key[tab_key_of[p.out]]
        nav_html = render_nav(tab.sections, p.out, base, collapsible=tab.key == "design")
        tabbar_html = render_tabbar(tabs, tab.key, base)

        # breadcrumb + prev/next + last-updated
        chain = chain_of.get(p.out, [])
        breadcrumb = render_breadcrumb(chain, p.title, p.title_zh)
        i = idx_of.get(p.out)
        prev_p = seq[i - 1] if i and i > 0 else None
        next_p = seq[i + 1] if i is not None and i + 1 < len(seq) else None
        prevnext = render_prevnext(prev_p, next_p)
        updated = git_mtime(p.src)
        meta_html = (f'<div class="page-updated"><span data-i18n="updated">Last updated</span>'
                     f' · {updated}</div>') if updated else ""

        # bilingual: if a Chinese version exists, its URL lets the language
        # toggle jump straight to it (and vice-versa from the zh page).
        alt_url = (DEPLOY_BASE + str(p.zh_out).replace("\\", "/")) if p.zh_out else ""
        page_description = document.description if document else _meta_description(body, p.title)
        page_url = _public_docs_url(p.out.as_posix())
        if p.out.as_posix() == "README.html":
            page_url = _public_docs_url()
        markdown_url = ""
        if p.kind == "md" or document:
            markdown_url = (
                page_url + "index.html.md" if page_url.endswith("/")
                else page_url + ".md"
            )
        llms_url = _public_url("llms.txt")
        zh_page_url = _public_docs_url(p.zh_out.as_posix()) if p.zh_out else ""

        full = render_page(
            title=p.title, body_html=body, nav_html=nav_html,
            toc_html=toc, base=base, page_lang="en", alt_lang_url=alt_url,
            breadcrumb_html=breadcrumb, prevnext_html=prevnext, meta_html=meta_html,
            tabbar_html=tabbar_html,
            canonical_url=page_url, description=page_description,
            alt_lang_canonical_url=zh_page_url,
            docs_root_url=_public_docs_url(),
            markdown_url=markdown_url, llms_url=llms_url,
            article_class="doc" if document else "",
        )
        out_path = OUT_ROOT / p.out
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(full, encoding="utf-8")
        if p.kind == "md":
            shutil.copy2(p.src, out_path.with_suffix(out_path.suffix + ".md"))
        elif document:
            out_path.with_suffix(out_path.suffix + ".md").write_text(document.markdown, encoding="utf-8")
        rendered += 1

        # Chinese version (same shell, Chinese body), if present.
        if p.zh_src is not None and p.zh_out is not None:
            _SLUG_DEDUP = {}
            zh_text = p.zh_src.read_text(encoding="utf-8", errors="replace")
            zh_document = None
            zh_failed = quarantined.get(f"docs/{p.zh_src.relative_to(DOCS_ROOT).as_posix()}")
            if zh_failed:
                zh_text = render_quarantine_page(zh_failed)
                zh_body, zh_toc = zh_text, ""
            elif p.zh_src.suffix == ".html" and html_source.is_html_document(zh_text):
                zh_document = render_html_document(zh_text, p.zh_out.parent)
                zh_body, zh_toc = zh_document.body, zh_document.toc
            elif p.zh_src.suffix == ".md":
                zh_body = relink_internal(
                    apply_callouts(md.render(zh_text)), p.zh_out.parent
                )
                zh_toc = extract_toc(zh_body)
            else:
                zh_raw_rel = p.zh_out.with_suffix(".raw.html")
                zh_raw_path = OUT_ROOT / zh_raw_rel
                zh_raw_path.parent.mkdir(parents=True, exist_ok=True)
                zh_raw_path.write_text(add_raw_readability(relink_internal(zh_text, p.zh_out.parent)) + _ESC_FORWARD, encoding="utf-8")
                zh_body = embed_html(DEPLOY_BASE + str(zh_raw_rel).replace("\\", "/"))
                zh_toc = ""
            zh_back = DEPLOY_BASE + str(p.out).replace("\\", "/")
            zh_full = render_page(
                title=p.title_zh or p.title, body_html=zh_body, nav_html=nav_html,
                toc_html=zh_toc, base=base, page_lang="zh", alt_lang_url=zh_back,
                breadcrumb_html=breadcrumb, prevnext_html=prevnext, meta_html=meta_html,
                tabbar_html=tabbar_html,
                canonical_url=zh_page_url,
                alt_lang_canonical_url=page_url,
                description=(zh_document.description if zh_document
                             else _meta_description(zh_body, p.title_zh or p.title)),
                docs_root_url=_public_docs_url(),
                markdown_url=zh_page_url + ".md" if p.zh_src.suffix == ".md" or zh_document else "",
                llms_url=llms_url,
                article_class="doc" if zh_document else "",
            )
            zh_path = OUT_ROOT / p.zh_out
            zh_path.parent.mkdir(parents=True, exist_ok=True)
            zh_path.write_text(zh_full, encoding="utf-8")
            if p.zh_src.suffix == ".md":
                shutil.copy2(p.zh_src, zh_path.with_suffix(zh_path.suffix + ".md"))
            elif zh_document:
                zh_path.with_suffix(zh_path.suffix + ".md").write_text(zh_document.markdown, encoding="utf-8")
            rendered += 1

        search_records.append({
            "title": p.title,
            "url": str(p.out).replace("\\", "/"),
            "group": " › ".join(zh for (zh, _en) in chain) if chain else "",
            "text": document.search_text if document else searchmod.plain_text(html_text if p.kind == "html" else body),
            "lang": "en",
            "has_translation": p.zh_src is not None,
        })
        if p.zh_src is not None and p.zh_out is not None:
            search_records.append({
                "title": p.title_zh or p.title,
                "url": p.zh_out.as_posix(),
                "group": " › ".join(zh or en for en, zh in chain),
                "text": zh_document.search_text if zh_document else searchmod.plain_text(zh_text if p.zh_src.suffix == ".html" else zh_body),
                "lang": "zh",
                "has_translation": True,
            })

    searchmod.write_index(search_records, OUT_ROOT)
    _write_home(tabs)
    _copy_static_root()
    _write_sitemap()
    _write_redirects()

    print(f"built {rendered} pages -> {OUT_ROOT}")
    return 0


def _write_redirects() -> None:
    """Publish retired URLs without adding duplicate documents to the catalog."""
    source = DOCS_ROOT / "redirects.json"
    if not source.exists():
        return
    aliases = json.loads(source.read_text(encoding="utf-8"))
    for old, target in aliases.items():
        destination = target.split("#", 1)[0]
        # A standalone page moved onto the foundation no longer has a raw copy.
        if destination.endswith(".raw.html") and not (OUT_ROOT / destination).is_file():
            target = target.replace(".raw.html", ".html", 1)
            destination = destination[:-len(".raw.html")] + ".html"
        for path in (old, destination):
            if (Path(path).is_absolute() or ".." in Path(path).parts
                    or ":" in path or "?" in path or "\\" in path
                    or not path.endswith(".html")):
                raise ValueError(f"Invalid documentation redirect path: {path}")
        output = OUT_ROOT / old
        if output.exists() or destination in aliases:
            raise ValueError(f"Documentation redirect collision or chain: {old}")
        if not (OUT_ROOT / destination).is_file():
            raise ValueError(f"Missing documentation redirect target: {target}")
        url = DEPLOY_BASE + target
        escaped = _html.escape(url, quote=True)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            '<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="robots" content="noindex">'
            f'<link rel="canonical" href="{escaped}">'
            '<title>Document moved</title>'
            f'<script src="{_html.escape(DEPLOY_BASE, quote=True)}assets/redirect.js"></script>'
            f'<p>This document has moved to <a href="{escaped}">its canonical page</a>.</p>'
            '</html>', encoding="utf-8",
        )


def _copy_static_root() -> None:
    """Ship root website files and the existing application favicon.

    Search-engine ownership proofs and robots.txt only count when they are
    reachable at the exact root URL the provider asks for, so they cannot go
    through markdown rendering.  The website reuses the application favicon
    so browser and installed-app branding cannot diverge.
    """
    src = DOCS_ROOT / "_static_root"
    if not src.is_dir():
        return
    for path in src.rglob("*"):
        if not path.is_file():
            continue
        dst = OUT_ROOT / path.relative_to(src)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix in SITE_URL_FILES:
            # Absolute links in these files carry the site address as a
            # placeholder, so it is written in one place only.
            text = path.read_text(encoding="utf-8")
            dst.write_text(text.replace(SITE_URL_PLACEHOLDER, _public_url()), encoding="utf-8")
        else:
            shutil.copy2(path, dst)
    shutil.copy2(REPO_ROOT / "apps" / "web" / "app" / "favicon.ico", OUT_ROOT / "favicon.ico")
    # GitHub Pages attaches a custom domain from a CNAME file at the site
    # root; the account's github.io address must not carry one.
    host = urlparse(SITE_URL).hostname or ""
    if not host.endswith(".github.io"):
        (OUT_ROOT / "CNAME").write_text(host + "\n", encoding="utf-8")


SITE_URL_PLACEHOLDER = "{{SITE_URL}}"
SITE_URL_FILES = {".txt", ".md"}


def _write_sitemap() -> None:
    """List every built page so crawlers index the whole site from one fetch.

    Entries are absolute public URLs (SITE_URL + docs/...); the .raw.html
    iframe payloads are left out because their shell page already covers them.
    """
    base = _public_docs_url()
    static_root = {
        p.name for p in (DOCS_ROOT / "_static_root").glob("*") if p.is_file()
    }
    urls = []
    for path in sorted(OUT_ROOT.rglob("*.html")):
        rel = path.relative_to(OUT_ROOT).as_posix()
        if rel.endswith(".raw.html") or rel in static_root:
            continue
        if rel == "README.html":
            continue
        # index.html is the same document as the bare directory URL; list the
        # canonical bare form only.
        if rel == "index.html":
            urls.append(base)
        elif rel.endswith("/index.html"):
            urls.append(base + rel[: -len("index.html")])
        else:
            urls.append(base + rel)

    # The landing page at the site root is a separate document that no built
    # page covers.
    urls.insert(0, _public_url())

    body = "\n".join(
        f"  <url><loc>{_html.escape(u)}</loc></url>" for u in urls)
    (OUT_ROOT / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{body}\n</urlset>\n",
        encoding="utf-8",
    )


def _write_home(tabs) -> None:
    """/docs/ IS the 项目总览 page: reuse the rendered README.html verbatim
    (its links are all DEPLOY_BASE-absolute, so it works at either URL)."""
    readme = OUT_ROOT / "README.html"
    if readme.exists():
        shutil.copy2(readme, OUT_ROOT / "index.html")
    readme_markdown = OUT_ROOT / "README.html.md"
    if readme_markdown.exists():
        shutil.copy2(readme_markdown, OUT_ROOT / "index.html.md")


if __name__ == "__main__":
    raise SystemExit(build())
