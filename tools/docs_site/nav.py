"""Build the left-sidebar navigation tree from the docs/ directory layout.

The tree mirrors the on-disk folder structure. Each directory becomes a group;
its title comes from that directory's README.md H1 if present, else a prettified
folder name. Within a group, README.md is pinned first, the rest sort by name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Folders that are never part of the docs site.
EXCLUDE_DIRS = {"_site", "_site.tmp", "_site.old", "images", "slides"}

# Top-level tabs. Each top-level directory under docs/ is one tab in the top
# navbar; the sidebar only shows the current tab's tree. Order here is the
# navbar order. (dir name -> (中文 label, English label))
TABS: dict[str, tuple[str, str]] = {
    "start":        ("开始使用", "Get started"),
    "install":      ("安装", "Install"),
    "capabilities": ("Capabilities", "Capabilities"),
    "interfaces":   ("界面", "Interfaces"),
    "models":       ("Models", "Models"),
    "integrations": ("Integrations", "Integrations"),
    "server":       ("Server & Ops", "Server & Ops"),
    "reference":    ("Reference", "Reference"),
}
# Loose files directly under docs/ belong to a tab too.
ROOT_PAGE_TAB = {"README.md": "start"}
# A top-level dir not listed in TABS falls back to the reference tab, so a
# stray folder degrades to "filed under Reference" instead of vanishing.
FALLBACK_TAB = "reference"

# Display-name overrides for pages whose H1 doesn't make a good sidebar label.
ROOT_PAGE_GROUPS: dict[str, tuple[str, str]] = {
    "README.md": ("项目总览", "start"),
}
# Per-page i18n key (bilingual sidebar labels for pages the toggle must switch).
ROOT_PAGE_I18N = {
    "README.md": "p_overview",
}


@dataclass
class Page:
    src: Path          # absolute source path
    rel: Path          # path relative to docs/ (e.g. design/runtime/rewind.md)
    out: Path          # output path relative to _site/ (always .html)
    title: str
    is_readme: bool
    kind: str          # "md" or "html"
    i18n_key: str = ""  # if set, sidebar label switches with the UI language
    en_src: Path | None = None   # English-version source (xxx.en.md), if any
    en_out: Path | None = None   # English-version output path, if any
    title_en: str = ""  # English sidebar label (from the .en.md H1), if any


@dataclass
class Group:
    title: str
    rel_dir: Path                       # relative to docs/ ("" for root)
    pages: list[Page] = field(default_factory=list)
    subgroups: list["Group"] = field(default_factory=list)
    i18n_key: str = ""  # if set, group header switches with the UI language
    title_en: str = ""  # English group label (from README.en.md H1), if any


_H1_RE = re.compile(r"^\s{0,3}#\s+(.+?)\s*#*\s*$", re.MULTILINE)
_HTML_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def extract_title(path: Path) -> str:
    """First H1 (md) or <title> (html); fall back to a prettified file stem."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return prettify(path.stem)
    if path.suffix == ".md":
        m = _H1_RE.search(text)
        if m:
            return m.group(1).strip()
    else:
        m = _HTML_TITLE_RE.search(text)
        if m:
            # strip a common " — OpenProgram" style suffix for nav brevity
            return re.sub(r"\s*[—·|-]\s*OpenProgram.*$", "", m.group(1).strip())
        # body-only fragment: fall back to its first <h1>
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", text, re.IGNORECASE | re.DOTALL)
        if h1:
            return re.sub(r"<[^>]+>", "", h1.group(1)).strip()
    return prettify(path.stem)


def prettify(name: str) -> str:
    name = name.replace("_", " ").replace("-", " ")
    return name.strip().title()


def discover(docs_root: Path) -> list[Page]:
    """All renderable pages under docs/, excluding EXCLUDE_DIRS.

    Bilingual convention: ``xxx.md`` is the default (Chinese) version; a sibling
    ``xxx.en.md`` is its English version. The .en.md does NOT get its own
    sidebar entry — it's attached to xxx.md as ``en_src`` and reached via the
    language toggle.
    """
    # First pass: collect all .en.md english sources, keyed by their base stem.
    en_sources: dict[Path, Path] = {}  # base rel (xxx.md) -> en src path
    for path in docs_root.rglob("*.en.md"):
        rel = path.relative_to(docs_root)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        base_rel = rel.with_name(rel.name[:-len(".en.md")] + ".md")
        en_sources[base_rel] = path

    pages: list[Page] = []
    for path in sorted(docs_root.rglob("*")):
        if path.suffix not in (".md", ".html"):
            continue
        rel = path.relative_to(docs_root)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if rel.name.endswith(".en.md"):
            continue  # english version is attached to its base, not a page
        out = rel.with_suffix(".html")
        rel_str = str(rel).replace("\\", "/")
        override = ROOT_PAGE_GROUPS.get(rel_str)
        title = override[0] if override else extract_title(path)
        en_src = en_sources.get(rel)
        en_out = (rel.with_name(rel.stem + ".en.html")) if en_src else None
        title_en = extract_title(en_src) if en_src else ""
        pages.append(
            Page(
                src=path,
                rel=rel,
                out=out,
                title=title,
                is_readme=path.stem.upper() == "README",
                kind=path.suffix.lstrip("."),
                i18n_key=ROOT_PAGE_I18N.get(rel_str, ""),
                en_src=en_src,
                en_out=en_out,
                title_en=title_en,
            )
        )
    return _dedupe_md_html(pages)


def _dedupe_md_html(pages: list[Page]) -> list[Page]:
    """When foo.md and foo.html coexist in the same dir, the md is the canonical
    page and the html is its visualization. Keep both, but the html output path
    is suffixed so it never overwrites the md's output."""
    by_dir_stem: dict[tuple, list[Page]] = {}
    for p in pages:
        by_dir_stem.setdefault((p.rel.parent, p.rel.stem), []).append(p)
    result: list[Page] = []
    for group in by_dir_stem.values():
        kinds = {p.kind for p in group}
        if kinds == {"md", "html"}:
            for p in group:
                if p.kind == "html":
                    p.out = p.rel.parent / f"{p.rel.stem}.viz.html"
                    p.title = f"{p.title}（可视化）"
                result.append(p)
        else:
            result.extend(group)
    return result


@dataclass
class Tab:
    key: str            # top-level dir name ("start", "models", …)
    title: str
    title_en: str
    root: Group         # tree of this tab's pages; root.pages render loose
    landing: Path       # out path the navbar tab links to


def tab_of(p: Page) -> str:
    parts = p.rel.parts
    if len(parts) == 1:
        return ROOT_PAGE_TAB.get(parts[0], FALLBACK_TAB)
    return parts[0] if parts[0] in TABS else FALLBACK_TAB


# Explicit sidebar order for product pages (rel path or rel dir -> rank).
# Tutorial docs must read top-to-bottom; anything unlisted sorts after these,
# alphabetically (which is fine for the design-notes archive).
PAGE_ORDER: dict[str, int] = {
    "README.md": 0,
    "start/GETTING_STARTED.md": 1,
    "start/features.md": 2,
    "capabilities/agentic-programming": 0,
    "capabilities/installing-harnesses.md": 10,
    "integrations/claude-code.md": 0,
    "integrations/openclaw.md": 1,
    "reference/API.md": 0,
    "reference/api": 1,
    "reference/design": 900,  # design-notes archive always last
}


def _order_key(rel: Path) -> int:
    return PAGE_ORDER.get(str(rel).replace("\\", "/"), 999)


def build_tabs(docs_root: Path, pages: list[Page]) -> list[Tab]:
    """Split pages by top-level tab, each with its own directory-mirroring tree."""
    tabs: list[Tab] = []
    for key, (zh, en) in TABS.items():
        tab_pages = [p for p in pages if tab_of(p) == key]
        if not tab_pages:
            continue
        root = _tree_for_tab(docs_root, key, tab_pages)
        tabs.append(Tab(key=key, title=zh, title_en=en, root=root,
                        landing=_landing(root)))
    return tabs


def _landing(root: Group) -> Path:
    if root.pages:
        return root.pages[0].out
    g = root
    while g.subgroups and not g.pages:
        g = g.subgroups[0]
    return g.pages[0].out if g.pages else Path("index.html")


def _tree_for_tab(docs_root: Path, tab_key: str, pages: list[Page]) -> Group:
    """Directory tree rooted at the tab dir: pages directly in the tab dir (and
    loose docs-root pages mapped to this tab) render loose; subdirs become
    collapsible groups."""
    tab_dir = Path(tab_key)
    root = Group(title="", rel_dir=tab_dir)
    groups: dict[Path, Group] = {tab_dir: root, Path("."): root}

    def group_for(rel_dir: Path) -> Group:
        if rel_dir in groups:
            return groups[rel_dir]
        readme = docs_root / rel_dir / "README.md"
        readme_en = docs_root / rel_dir / "README.en.md"
        title = extract_title(readme) if readme.exists() else prettify(rel_dir.name)
        title_en = extract_title(readme_en) if readme_en.exists() else ""
        g = Group(title=title, rel_dir=rel_dir, title_en=title_en)
        groups[rel_dir] = g
        group_for(rel_dir.parent).subgroups.append(g)
        return g

    for p in pages:
        group_for(p.rel.parent).pages.append(p)

    for g in groups.values():
        g.pages.sort(key=lambda p: (_order_key(p.rel), not p.is_readme, p.title.lower()))
        g.subgroups.sort(key=lambda sg: (_order_key(sg.rel_dir), sg.title.lower()))
    return root
