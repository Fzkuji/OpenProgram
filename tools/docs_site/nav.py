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
TABS: dict[str, tuple[str, str]] = {  # dir -> (English label, 中文 label)
    "start":        ("Get started", "开始使用"),
    "install":      ("Install", "安装"),
    "capabilities": ("Capabilities", "能力"),
    "interfaces":   ("Interfaces", "界面"),
    "models":       ("Models", "模型"),
    "integrations": ("Integrations", "集成"),
    "server":       ("Server & Ops", "服务与运维"),
    "reference":    ("Reference", "参考"),
}
# Loose files directly under docs/ belong to a tab too.
ROOT_PAGE_TAB = {"README.md": "start"}
# A top-level dir not listed in TABS falls back to the reference tab, so a
# stray folder degrades to "filed under Reference" instead of vanishing.
FALLBACK_TAB = "reference"

# Display-name overrides for pages whose H1 doesn't make a good sidebar label.
ROOT_PAGE_GROUPS: dict[str, tuple[str, str]] = {
    "README.md": ("Overview", "start"),
    "capabilities/agentic-programming/philosophy.md": ("Philosophy", ""),
}

# Sidebar titles for directories that have no README.md of their own.
DIR_TITLES: dict[str, tuple[str, str]] = {  # rel dir -> (English, 中文)
    "capabilities/agentic-programming/writing-functions": ("Writing functions", "编写函数"),
    "capabilities/agentic-programming/choosing-the-next-step": ("Choosing the next step", "选择下一步"),
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
    zh_src: Path | None = None   # Chinese-version source (xxx.zh.md), if any
    zh_out: Path | None = None   # Chinese-version output path, if any
    title_zh: str = ""  # Chinese sidebar label (from the .zh.md H1), if any


@dataclass
class Group:
    title: str
    rel_dir: Path                       # relative to docs/ ("" for root)
    pages: list[Page] = field(default_factory=list)
    subgroups: list["Group"] = field(default_factory=list)
    i18n_key: str = ""  # if set, group header switches with the UI language
    title_zh: str = ""  # Chinese group label (from README.zh.md H1), if any


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

    Bilingual convention: ``xxx.md`` is the default (English) version; a sibling
    ``xxx.zh.md`` is its Chinese version. The .zh.md does NOT get its own
    sidebar entry — it's attached to xxx.md as ``zh_src`` and reached via the
    language toggle.
    """
    # First pass: collect all .zh.md chinese sources, keyed by their base stem.
    zh_sources: dict[Path, Path] = {}  # base rel (xxx.md) -> zh src path
    for path in docs_root.rglob("*.zh.md"):
        rel = path.relative_to(docs_root)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        base_rel = rel.with_name(rel.name[:-len(".zh.md")] + ".md")
        zh_sources[base_rel] = path

    pages: list[Page] = []
    for path in sorted(docs_root.rglob("*")):
        if path.suffix not in (".md", ".html"):
            continue
        rel = path.relative_to(docs_root)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if rel.name.endswith(".zh.md"):
            continue  # chinese version is attached to its base, not a page
        out = rel.with_suffix(".html")
        rel_str = str(rel).replace("\\", "/")
        override = ROOT_PAGE_GROUPS.get(rel_str)
        title = override[0] if override else extract_title(path)
        zh_src = zh_sources.get(rel)
        zh_out = (rel.with_name(rel.stem + ".zh.html")) if zh_src else None
        title_zh = extract_title(zh_src) if zh_src else ""
        pages.append(
            Page(
                src=path,
                rel=rel,
                out=out,
                title=title,
                is_readme=path.stem.upper() == "README",
                kind=path.suffix.lstrip("."),
                zh_src=zh_src,
                zh_out=zh_out,
                title_zh=title_zh,
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
    title: str          # English (default) label
    title_zh: str
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
    "start/daily-use.md": 2,
    "start/features.md": 3,
    "start/faq.md": 4,
    "install/install.md": 0,
    "install/upgrade.md": 1,
    "install/profiles.md": 2,
    "capabilities/README.md": 0,
    "capabilities/agentic-programming": 1,
    "capabilities/workflows": 2,
    "capabilities/installing-harnesses.md": 3,
    "capabilities/skills.md": 4,
    "capabilities/plugins.md": 5,
    "capabilities/mcp.md": 6,
    "capabilities/tools.md": 7,
    "capabilities/agentic-programming/philosophy.md": 1,
    "capabilities/agentic-programming/writing-functions": 2,
    "capabilities/agentic-programming/choosing-the-next-step": 3,
    "capabilities/workflows/gui-agent.md": 1,
    "capabilities/workflows/research-agent.md": 2,
    "capabilities/workflows/wiki-agent.md": 3,
    "interfaces/README.md": 0,
    "interfaces/web.md": 1,
    "interfaces/tui.md": 2,
    "interfaces/cli.md": 3,
    "models/README.md": 0,
    "models/providers.md": 1,
    "models/auth.md": 2,
    "models/fast-tier.md": 3,
    "models/thinking-effort.md": 4,
    "models/token-tracking.md": 5,
    "integrations/claude-code.md": 0,
    "integrations/openclaw.md": 1,
    "server/README.md": 0,
    "server/configuration.md": 1,
    "server/troubleshooting.md": 2,
    "reference/README.md": 0,
    "reference/API.md": 1,
    "reference/api": 2,
    "reference/cli.md": 3,
    "reference/config.md": 4,
    "reference/claude-code-compaction.md": 5,
    "reference/design": 900,  # design-notes archive always last
}


def _order_key(rel: Path) -> int:
    return PAGE_ORDER.get(str(rel).replace("\\", "/"), 999)


def ordered_children(g: "Group") -> list:
    """Pages and subgroups of a group, merged into sidebar display order:
    by PAGE_ORDER rank, loose pages before groups at equal rank."""
    items = [(_order_key(p.rel), 0, str(p.rel), p) for p in g.pages]
    items += [(_order_key(sg.rel_dir), 1, str(sg.rel_dir), sg) for sg in g.subgroups]
    items.sort(key=lambda t: t[:3])
    return [x for _, _, _, x in items]


def build_tabs(docs_root: Path, pages: list[Page]) -> list[Tab]:
    """Split pages by top-level tab, each with its own directory-mirroring tree."""
    tabs: list[Tab] = []
    for key, (en, zh) in TABS.items():
        tab_pages = [p for p in pages if tab_of(p) == key]
        if not tab_pages:
            continue
        root = _tree_for_tab(docs_root, key, tab_pages)
        tabs.append(Tab(key=key, title=en, title_zh=zh, root=root,
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
        readme_zh = docs_root / rel_dir / "README.zh.md"
        override = DIR_TITLES.get(str(rel_dir).replace("\\", "/"))
        if override:
            title, title_zh = override
        else:
            title = extract_title(readme) if readme.exists() else prettify(rel_dir.name)
            title_zh = extract_title(readme_zh) if readme_zh.exists() else ""
        g = Group(title=title, rel_dir=rel_dir, title_zh=title_zh)
        groups[rel_dir] = g
        group_for(rel_dir.parent).subgroups.append(g)
        return g

    for p in pages:
        group_for(p.rel.parent).pages.append(p)

    for g in groups.values():
        g.pages.sort(key=lambda p: (_order_key(p.rel), not p.is_readme, p.title.lower()))
        g.subgroups.sort(key=lambda sg: (_order_key(sg.rel_dir), sg.title.lower()))
    return root
