"""Build the OpenProgram documentation site.

Scans docs/ for markdown + hand-written html, renders each into one unified
shell (left nav tree, right on-page toc, dark/light theme, search), and writes
the static site to docs/_site/.

Run:  python -m tools.docs_site.build
"""

from __future__ import annotations

import html as _html
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
    md = MarkdownIt("gfm-like", {"html": True, "linkify": True, "highlight": _highlight_code})
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


# ── toc extraction (from rendered html headings) ────────────────────────────

_HEADING_RE = re.compile(r'<h([23])[^>]*\bid="([^"]+)"[^>]*>(.*?)</h[23]>', re.DOTALL)


def extract_toc(body_html: str) -> str:
    items = []
    for m in _HEADING_RE.finditer(body_html):
        level, hid, inner = m.group(1), m.group(2), m.group(3)
        inner = re.sub(r'<a class="header-anchor".*?</a>', "", inner, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", "", inner).strip()
        if not text:
            continue
        items.append((level, hid, text))
    if not items:
        return ""
    rows = ['<div class="toc-title">本页内容</div>']
    for level, hid, text in items:
        cls = "lvl-3" if level == "3" else ""
        rows.append(f'<a class="{cls}" href="#{_html.escape(hid)}">{_html.escape(text)}</a>')
    return "\n".join(rows)


def relink_internal(body_html: str) -> str:
    """Rewrite relative .md / README links to their built .html targets."""
    def repl(m):
        attr, url = m.group(1), m.group(2)
        if re.match(r"^[a-z]+://", url) or url.startswith("#") or url.startswith("mailto:"):
            return m.group(0)
        anchor = ""
        if "#" in url:
            url, anchor = url.split("#", 1)
            anchor = "#" + anchor
        if url.endswith(".md"):
            url = url[:-3] + ".html"
        return f'{attr}"{url}{anchor}"'
    return re.sub(r'(href=)"([^"]+)"', repl, body_html)


# ── hand-written html embedding ─────────────────────────────────────────────

def embed_html(raw_url: str) -> str:
    """Embed a self-contained hand-written html via an isolated iframe.

    iframe sandboxing keeps the original's own fixed-position layouts, styles,
    scripts, and visualizations 100% intact without leaking into the site shell.
    The original file is shipped verbatim alongside as a .raw.html sibling.
    """
    return (
        f'<iframe class="viz-frame" src="{raw_url}" loading="lazy" '
        f'title="可视化文档"></iframe>'
    )


# ── nav tree -> html ────────────────────────────────────────────────────────

def render_nav(groups, current_out: Path, base: str) -> str:
    def contains_current(g) -> bool:
        if any(p.out == current_out for p in g.pages):
            return True
        return any(contains_current(sg) for sg in g.subgroups)

    def render_pages_and_subs(g) -> str:
        out = []
        for p in g.pages:
            href = base + str(p.out).replace("\\", "/")
            active = " active" if p.out == current_out else ""
            label = _html.escape(p.title)
            out.append(f'<a class="navlink{active}" href="{href}">{label}</a>')
        for sg in g.subgroups:
            out.append(render_group(sg))
        return "\n".join(out)

    def render_group(g, top=False) -> str:
        # The synthetic root "Guides" group: render its loose pages as a
        # collapsible "Guides" section (default open), then its subgroups as
        # their own top-level collapsible sections.
        if top:
            parts = []
            if g.pages:
                key = "__guides__"
                is_open = any(p.out == current_out for p in g.pages) or current_out == Path("index.html")
                open_attr = " open" if is_open else ""
                pages_html = "\n".join(
                    f'<a class="navlink{" active" if p.out == current_out else ""}" '
                    f'href="{base + str(p.out).replace(chr(92), "/")}">{_html.escape(p.title)}</a>'
                    for p in g.pages
                )
                parts.append(
                    f'<details class="group" data-key="{key}"{open_attr}>'
                    f'<summary class="group-title">指南 Guides</summary>'
                    f'<div class="group-body">{pages_html}</div></details>'
                )
            for sg in g.subgroups:
                parts.append(render_group(sg))
            return "\n".join(parts)
        key = str(g.rel_dir).replace("\\", "/")
        is_open = contains_current(g)
        open_attr = " open" if is_open else ""
        return (
            f'<details class="group" data-key="{_html.escape(key)}"{open_attr}>'
            f'<summary class="group-title">{_html.escape(g.title)}</summary>'
            f'<div class="group-body">{render_pages_and_subs(g)}</div>'
            f"</details>"
        )

    return "\n".join(render_group(g, top=True) for g in groups)


# ── main build ──────────────────────────────────────────────────────────────

def build() -> int:
    if not DOCS_ROOT.exists():
        print(f"docs root not found: {DOCS_ROOT}", file=sys.stderr)
        return 1

    pages = navmod.discover(DOCS_ROOT)
    groups = navmod.build_tree(DOCS_ROOT, pages)

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

    md = make_md()
    search_records: list[dict] = []
    rendered = 0

    for p in pages:
        global _SLUG_DEDUP
        _SLUG_DEDUP = {}
        depth = len(p.out.parts) - 1
        base = "../" * depth

        if p.kind == "md":
            text = p.src.read_text(encoding="utf-8", errors="replace")
            body = md.render(text)
            body = relink_internal(body)
            toc = extract_toc(body)
        else:
            # Ship the original verbatim and embed it via an isolated iframe.
            raw_rel = p.out.with_suffix("").with_suffix(".raw.html")
            raw_path = OUT_ROOT / raw_rel
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(p.src.read_text(encoding="utf-8", errors="replace"),
                                encoding="utf-8")
            body = embed_html(raw_rel.name)
            toc = ""

        nav_html = render_nav(groups, p.out, base)
        full = render_page(
            title=p.title, body_html=body, nav_html=nav_html,
            toc_html=toc, base=base,
        )
        out_path = OUT_ROOT / p.out
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(full, encoding="utf-8")
        rendered += 1

        search_records.append({
            "title": p.title,
            "url": str(p.out).replace("\\", "/"),
            "text": searchmod.plain_text(body),
        })

    searchmod.write_index(search_records, OUT_ROOT)
    _write_home(groups)

    print(f"built {rendered} pages → {OUT_ROOT}")
    return 0


def _write_home(groups) -> None:
    """Landing page: a card grid of the top-level sections only."""
    root = groups[0]

    def count_pages(g) -> int:
        return len(g.pages) + sum(count_pages(sg) for sg in g.subgroups)

    def landing(g):
        # link to the group's README if it has one, else its first page
        readme = next((p for p in g.pages if p.is_readme), None)
        target = readme or (g.pages[0] if g.pages else None)
        if target is None:
            for sg in g.subgroups:
                t = landing(sg)
                if t:
                    return t
            return None
        return str(target.out).replace("\\", "/")

    cards = []
    for sg in root.subgroups:
        url = landing(sg)
        if url:
            cards.append((sg.title, url, count_pages(sg)))

    body = ['<h1>OpenProgram 设计文档</h1>',
            '<p class="page-meta">框架的设计笔记、API 与指南，按子系统组织。'
            '左侧目录浏览，或按 <kbd>⌘K</kbd> 搜索。</p>',
            '<div class="home-grid">']
    for title, url, n in cards:
        body.append(
            f'<a class="home-card" href="{url}">'
            f'<span class="hc-title">{_html.escape(title)}</span>'
            f'<span class="hc-count">{n} 篇</span></a>'
        )
    body.append("</div>")
    nav_html = render_nav(groups, Path("index.html"), "")
    full = render_page(title="OpenProgram 设计文档", body_html="\n".join(body),
                       nav_html=nav_html, toc_html="", base="")
    (OUT_ROOT / "index.html").write_text(full, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(build())
