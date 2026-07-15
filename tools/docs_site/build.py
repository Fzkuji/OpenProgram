"""Build the OpenProgram documentation site.

Scans docs/ for markdown + hand-written html, renders each into one unified
shell (left nav tree, right on-page toc, dark/light theme, search), and writes
the static site to docs/_site/.

Run:  python -m tools.docs_site.build
"""

from __future__ import annotations

import html as _html
import os
import re
import shutil
import sys
from pathlib import Path

from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin
from mdit_py_plugins.deflist import deflist_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight as pyg_highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

from . import nav as navmod
from . import search as searchmod
from .template import render_page

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_ROOT = REPO_ROOT / "docs"
OUT_ROOT = DOCS_ROOT / "_site"
ASSETS_SRC = Path(__file__).parent / "assets"

# Absolute URL prefix the site is mounted under. The worker serves it at /docs,
# so all asset/nav/internal links are absolute (/docs/...) — this makes them
# resolve correctly whether the page URL has a trailing slash or not.
# Override with OPENPROGRAM_DOCS_BASE (must start and end with "/").
DEPLOY_BASE = os.environ.get("OPENPROGRAM_DOCS_BASE", "/docs/")

_SLUG_DEDUP: dict[str, int] = {}


# ── markdown rendering ──────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    s = re.sub(r"<[^>]+>", "", text)
    s = s.strip().lower()
    s = re.sub(r"[^\w一-鿿\- ]+", "", s)
    s = re.sub(r"[\s]+", "-", s).strip("-")
    return s or "section"


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

_CALLOUT_KINDS = {
    "NOTE": ("note", "ℹ", "提示"),
    "TIP": ("tip", "✓", "建议"),
    "IMPORTANT": ("important", "★", "重要"),
    "WARNING": ("warning", "⚠", "注意"),
    "CAUTION": ("caution", "⛔", "警告"),
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
        cls, icon, label = _CALLOUT_KINDS[kind]
        rest_first = head.group(2).strip()  # text on the same line after [!KIND]
        rest = inner[head.end():]
        first = f"<p>{rest_first}</p>" if rest_first else ""
        return (f'<div class="callout callout-{cls}">'
                f'<div class="callout-head"><span class="callout-icon">{icon}</span>{label}</div>'
                f'<div class="callout-body">{first}{rest}</div></div>')
    return _BLOCKQUOTE_RE.sub(repl, html)


# ── toc extraction (from rendered html headings) ────────────────────────────

_HEADING_RE = re.compile(r'<h([23])[^>]*\bid="([^"]+)"[^>]*>(.*?)</h[23]>', re.DOTALL)


def extract_toc(body_html: str) -> str:
    items = []
    for m in _HEADING_RE.finditer(body_html):
        level, hid, inner = m.group(1), m.group(2), m.group(3)
        inner = re.sub(r'<a class="header-anchor".*?</a>', "", inner, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", "", inner).strip()
        text = _html.unescape(text)  # decode &quot; etc.; re-escaped on output
        if not text:
            continue
        items.append((level, hid, text))
    if not items:
        return ""
    rows = ['<div class="toc-title" data-i18n="on_this_page">本页内容</div>',
            '<div class="toc-list">']
    for level, hid, text in items:
        cls = "lvl-3" if level == "3" else ""
        # quote=False: this is element text, not an attribute — keep real quotes
        rows.append(f'<a class="{cls}" href="#{_html.escape(hid)}">{_html.escape(text, quote=False)}</a>')
    rows.append("</div>")
    return "\n".join(rows)


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
        if url.endswith(".md"):
            url = url[:-3] + ".html"
        if not url:  # pure anchor like "#foo" already handled above
            return f'{attr}"{anchor}"'
        # resolve relative to the current page's directory, then make absolute
        resolved = os.path.normpath(str(cur_dir / url)).replace("\\", "/")
        return f'{attr}"{DEPLOY_BASE}{resolved}{anchor}"'
    return re.sub(r'(href=)"([^"]+)"', repl, body_html)


# ── hand-written html ───────────────────────────────────────────────────────

_FULL_PAGE_RE = re.compile(r"<\s*(html|body)\b", re.IGNORECASE)
_H1_TAG_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)


def is_full_page(html_text: str) -> bool:
    """True if the file is a standalone page (has <html> or <body>).

    Standalone pages are embedded via an isolated iframe (their own layout/
    styles stay intact). Body-only fragments are wrapped in the site shell.
    """
    return bool(_FULL_PAGE_RE.search(html_text))


def embed_html(raw_url: str) -> str:
    """Embed a standalone hand-written page via an isolated iframe.

    iframe sandboxing keeps the original's fixed-position layouts, styles,
    scripts, and visualizations 100% intact without leaking into the shell.
    The original file is shipped verbatim alongside as a .raw.html sibling.
    """
    return (
        f'<iframe class="viz-frame" src="{raw_url}" loading="lazy" '
        f'title="可视化文档"></iframe>'
    )


def render_html_fragment(html_text: str, scope_id: str) -> str:
    """Wrap a body-only hand-written fragment for inclusion in the site shell.

    The author writes just the content (headings, prose, <canvas>, <script>,
    <style> …) and gets the full chrome (nav, theme, search) for free. Any
    <style> blocks are scoped to this page's container so they can't restyle
    the shell; <script> blocks run as-is so animations/interactions work.
    """
    container = f"viz-frag-{scope_id}"

    def scope_style(m):
        css = m.group(1)
        return f"<style>{_scope_css(css, '#' + container)}</style>"

    scoped = re.sub(r"<style[^>]*>(.*?)</style>", scope_style, html_text,
                    flags=re.IGNORECASE | re.DOTALL)
    return f'<div class="viz-fragment" id="{container}">{scoped}</div>'


def _scope_css(css: str, prefix: str) -> str:
    """Prefix every selector in a CSS block with `prefix` so the styles only
    apply inside the page container. Skips @-rules' first line and keyframes."""
    out, i, n = [], 0, len(css)
    while i < n:
        at = css.find("@", i)
        brace = css.find("{", i)
        if brace == -1:
            out.append(css[i:])
            break
        # pass @media/@keyframes wrapper through untouched (recurse on inner)
        if at != -1 and at < brace:
            # find matching close of the at-block by brace counting
            depth, j = 0, brace
            while j < n:
                if css[j] == "{":
                    depth += 1
                elif css[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            header = css[i:brace]
            inner = css[brace + 1:j]
            out.append(header + "{" + _scope_css(inner, prefix) + "}")
            i = j + 1
            continue
        selector = css[i:brace].strip()
        block_end = css.find("}", brace)
        if block_end == -1:
            block_end = n
        body = css[brace:block_end + 1]
        scoped_sel = ", ".join(
            prefix if s.strip() in (":root", "html", "body", "*") else f"{prefix} {s.strip()}"
            for s in selector.split(",") if s.strip()
        )
        out.append(f"{scoped_sel} {body}")
        i = block_end + 1
    return "".join(out)


# ── ordered flatten + breadcrumbs + git mtime ───────────────────────────────

def flatten_pages(groups):
    """Pages in sidebar display order, each with its group chain (for prev/next
    and breadcrumbs). Returns list of (page, [group_titles])."""
    out = []

    def walk(g, chain):
        # each chain entry is (zh_title, en_title) so breadcrumbs can be bilingual
        new_chain = chain if not g.title else chain + [(g.title, g.title_en or g.title)]
        for c in navmod.ordered_children(g):
            if isinstance(c, navmod.Page):
                out.append((c, new_chain))
            else:
                walk(c, new_chain)

    for g in groups:
        walk(g, [])
    return out


def _bc_span(zh, en, cls=""):
    c = f' {cls}' if cls else ""
    if en and en != zh:
        return (f'<span class="bc-seg{c}" data-title-en="{_html.escape(en, quote=True)}"'
                f' data-title-zh="{_html.escape(zh, quote=True)}">{_html.escape(zh)}</span>')
    return f'<span class="bc-seg{c}">{_html.escape(zh)}</span>'


def render_breadcrumb(chain, title, title_en=""):
    if not chain:
        return ""
    sep = " <span class='bc-sep'>›</span> "
    segs = [_bc_span(zh, en) for (zh, en) in chain]
    segs.append(_bc_span(title, title_en or title, cls="bc-current"))
    return f'<nav class="breadcrumb">{sep.join(segs)}</nav>'


def render_prevnext(prev_p, next_p):
    if not prev_p and not next_p:
        return ""
    left = right = ""
    if prev_p:
        href = DEPLOY_BASE + str(prev_p.out).replace("\\", "/")
        left = (f'<a class="pn-link pn-prev" href="{href}">'
                f'<span class="pn-dir" data-i18n="prev">上一篇</span>'
                f'<span class="pn-title">{_html.escape(prev_p.title)}</span></a>')
    if next_p:
        href = DEPLOY_BASE + str(next_p.out).replace("\\", "/")
        right = (f'<a class="pn-link pn-next" href="{href}">'
                 f'<span class="pn-dir" data-i18n="next">下一篇</span>'
                 f'<span class="pn-title">{_html.escape(next_p.title)}</span></a>')
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
        en = (f' data-title-en="{_html.escape(t.title_en, quote=True)}"'
              if t.title_en and t.title_en != t.title else "")
        links.append(f'<a class="tablink{cls}" href="{href}"{en}>{_html.escape(t.title)}</a>')
    return "".join(links)



def render_nav(groups, current_out: Path, base: str) -> str:
    def contains_current(g) -> bool:
        if any(p.out == current_out for p in g.pages):
            return True
        return any(contains_current(sg) for sg in g.subgroups)

    def navlink(p) -> str:
        href = base + str(p.out).replace("\\", "/")
        active = " active" if p.out == current_out else ""
        i18n = f' data-i18n="{p.i18n_key}"' if p.i18n_key else ""
        # If an English version exists, carry its label + URL so the language
        # toggle can switch this sidebar entry to English.
        extra = ""
        if p.en_out is not None:
            en_href = base + str(p.en_out).replace("\\", "/")
            extra = (f' data-title-en="{_html.escape(p.title_en or p.title, quote=True)}"'
                     f' data-href-zh="{href}" data-href-en="{en_href}"')
        return f'<a class="navlink{active}" href="{href}"{i18n}{extra}>{_html.escape(p.title)}</a>'

    def render_pages_and_subs(g) -> str:
        return "\n".join(
            navlink(c) if isinstance(c, navmod.Page) else render_group(c)
            for c in navmod.ordered_children(g)
        )

    def render_group(g, top=False) -> str:
        # Root group: render its children directly (loose pages and
        # collapsible sections interleaved in PAGE_ORDER).
        if top:
            return render_pages_and_subs(g)
        key = str(g.rel_dir).replace("\\", "/")
        # synthetic root subgroups (快速上手/集成/参考) default-open on the home page
        is_synthetic = key.startswith("__")
        is_open = contains_current(g) or (is_synthetic and current_out == Path("index.html"))
        open_attr = " open" if is_open else ""
        i18n = f' data-i18n="{g.i18n_key}"' if g.i18n_key else ""
        # real directory groups carry their English label so the toggle can
        # switch the section header (synthetic groups use i18n_key instead).
        ten = f' data-title-en="{_html.escape(g.title_en, quote=True)}"' if (g.title_en and not g.i18n_key) else ""
        return (
            f'<details class="group" data-key="{_html.escape(key)}"{open_attr}>'
            f'<summary class="group-title"{i18n}{ten}>{_html.escape(g.title)}</summary>'
            f'<div class="group-body">{render_pages_and_subs(g)}</div>'
            f"</details>"
        )

    return "\n".join(render_group(g, top=True) for g in groups)


# ── main build ──────────────────────────────────────────────────────────────

def build() -> int:
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
            if final.exists():
                final.rename(old)
            OUT_ROOT.rename(final)
            shutil.rmtree(old, ignore_errors=True)
        return rc
    finally:
        OUT_ROOT = final


def _build_into_out_root() -> int:
    if not DOCS_ROOT.exists():
        print(f"docs root not found: {DOCS_ROOT}", file=sys.stderr)
        return 1

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

    # copy image/static assets referenced by docs (svg/png/jpg/gif/webp),
    # preserving their relative path so in-doc <img>/![] links resolve.
    IMG_EXT = {".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico"}
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

    # ordered sequence (for prev/next) + per-page group chain (for breadcrumbs),
    # tab by tab so reading order follows the navbar.
    ordered = []
    tab_key_of: dict[Path, str] = {}
    for tab in tabs:
        for pg, chain in flatten_pages([tab.root]):
            ordered.append((pg, [(tab.title, tab.title_en)] + chain))
            tab_key_of[pg.out] = tab.key
    tab_by_key = {t.key: t for t in tabs}
    seq = [pg for pg, _chain in ordered]
    chain_of = {pg.out: chain for pg, chain in ordered}
    idx_of = {pg.out: i for i, pg in enumerate(seq)}

    for p in pages:
        global _SLUG_DEDUP
        _SLUG_DEDUP = {}
        base = DEPLOY_BASE

        if p.kind == "md":
            text = p.src.read_text(encoding="utf-8", errors="replace")
            body = md.render(text)
            body = apply_callouts(body)
            body = relink_internal(body, p.out.parent)
            toc = extract_toc(body)
        else:
            html_text = p.src.read_text(encoding="utf-8", errors="replace")
            if is_full_page(html_text):
                # Standalone page → ship verbatim + isolate in an iframe.
                raw_rel = p.out.with_suffix("").with_suffix(".raw.html")
                raw_path = OUT_ROOT / raw_rel
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                raw_path.write_text(html_text, encoding="utf-8")
                body = embed_html(DEPLOY_BASE + str(raw_rel).replace("\\", "/"))
                toc = ""
            else:
                # Body-only fragment → wrap in the site shell (chrome for free).
                scope = re.sub(r"[^\w]+", "-", str(p.out.with_suffix("")))
                body = render_html_fragment(html_text, scope)
                body = relink_internal(body, p.out.parent)
                toc = extract_toc(body)

        tab = tab_by_key[tab_key_of[p.out]]
        nav_html = render_nav([tab.root], p.out, base)
        tabbar_html = render_tabbar(tabs, tab.key, base)

        # breadcrumb + prev/next + last-updated
        chain = chain_of.get(p.out, [])
        breadcrumb = render_breadcrumb(chain, p.title, p.title_en)
        i = idx_of.get(p.out)
        prev_p = seq[i - 1] if i and i > 0 else None
        next_p = seq[i + 1] if i is not None and i + 1 < len(seq) else None
        prevnext = render_prevnext(prev_p, next_p)
        updated = git_mtime(p.src)
        meta_html = (f'<div class="page-updated"><span data-i18n="updated">最后更新</span>'
                     f' · {updated}</div>') if updated else ""

        # bilingual: if an English version exists, its URL lets the language
        # toggle jump straight to it (and vice-versa from the EN page).
        alt_url = (DEPLOY_BASE + str(p.en_out).replace("\\", "/")) if p.en_out else ""

        full = render_page(
            title=p.title, body_html=body, nav_html=nav_html,
            toc_html=toc, base=base, page_lang="zh", alt_lang_url=alt_url,
            breadcrumb_html=breadcrumb, prevnext_html=prevnext, meta_html=meta_html,
            tabbar_html=tabbar_html,
        )
        out_path = OUT_ROOT / p.out
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(full, encoding="utf-8")
        rendered += 1

        # English version (same shell, English body), if present.
        if p.en_src is not None and p.en_out is not None:
            _SLUG_DEDUP = {}
            en_text = p.en_src.read_text(encoding="utf-8", errors="replace")
            en_body = relink_internal(apply_callouts(md.render(en_text)), p.en_out.parent)
            en_toc = extract_toc(en_body)
            en_back = DEPLOY_BASE + str(p.out).replace("\\", "/")
            en_full = render_page(
                title=p.title, body_html=en_body, nav_html=nav_html,
                toc_html=en_toc, base=base, page_lang="en", alt_lang_url=en_back,
                breadcrumb_html=breadcrumb, prevnext_html=prevnext, meta_html=meta_html,
                tabbar_html=tabbar_html,
            )
            en_path = OUT_ROOT / p.en_out
            en_path.parent.mkdir(parents=True, exist_ok=True)
            en_path.write_text(en_full, encoding="utf-8")
            rendered += 1

        search_records.append({
            "title": p.title,
            "url": str(p.out).replace("\\", "/"),
            "group": " › ".join(zh for (zh, _en) in chain) if chain else "",
            "text": searchmod.plain_text(body),
        })

    searchmod.write_index(search_records, OUT_ROOT)
    _write_home(tabs)

    print(f"built {rendered} pages → {OUT_ROOT}")
    return 0


def _write_home(tabs) -> None:
    """/docs/ IS the 项目总览 page: reuse the rendered README.html verbatim
    (its links are all DEPLOY_BASE-absolute, so it works at either URL)."""
    readme = OUT_ROOT / "README.html"
    if readme.exists():
        shutil.copy2(readme, OUT_ROOT / "index.html")


if __name__ == "__main__":
    raise SystemExit(build())
