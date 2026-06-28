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
EXCLUDE_DIRS = {"_site", "images", "slides"}

# The top-level loose pages in docs/ have no folder of their own, so we group
# them logically by filename. (rel-path string -> (display title, subgroup).)
# This keeps the source files in place — they're linked from many other docs —
# while giving the sidebar clean names and a sensible structure.
ROOT_PAGE_GROUPS: dict[str, tuple[str, str]] = {
    "README.md":                  ("项目总览", "快速上手"),
    "README_CN.md":               ("项目总览（中文）", "快速上手"),
    "GETTING_STARTED.md":         ("快速上手", "快速上手"),
    "install.md":                 ("安装", "快速上手"),
    "features.md":                ("功能详解", "快速上手"),
    "INTEGRATION_CLAUDE_CODE.md": ("集成 Claude Code", "集成"),
    "INTEGRATION_OPENCLAW.md":    ("集成 OpenClaw", "集成"),
    "installing-harnesses.md":    ("安装与编写 Harness", "集成"),
    "API.md":                     ("API 参考", "参考"),
    "provider-token-tracking.md": ("Provider Token 追踪", "参考"),
    "troubleshooting.md":         ("故障排查", "参考"),
}
# Order the synthetic root subgroups appear in.
ROOT_SUBGROUP_ORDER = ["快速上手", "集成", "参考"]

# i18n key for each synthetic subgroup (so the sidebar headers can switch lang).
ROOT_SUBGROUP_I18N = {"快速上手": "grp_start", "集成": "grp_integ", "参考": "grp_ref"}

# Per-root-page i18n key (clean bilingual display names for the loose pages).
ROOT_PAGE_I18N = {
    "README.md": "p_overview", "README_CN.md": "p_overview_cn",
    "GETTING_STARTED.md": "p_start", "install.md": "p_install",
    "features.md": "p_features", "INTEGRATION_CLAUDE_CODE.md": "p_int_cc",
    "INTEGRATION_OPENCLAW.md": "p_int_oc", "installing-harnesses.md": "p_harness",
    "API.md": "p_api", "provider-token-tracking.md": "p_token", "troubleshooting.md": "p_trouble",
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


@dataclass
class Group:
    title: str
    rel_dir: Path                       # relative to docs/ ("" for root)
    pages: list[Page] = field(default_factory=list)
    subgroups: list["Group"] = field(default_factory=list)
    i18n_key: str = ""  # if set, group header switches with the UI language


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
    """All renderable pages under docs/, excluding EXCLUDE_DIRS."""
    pages: list[Page] = []
    for path in sorted(docs_root.rglob("*")):
        if path.suffix not in (".md", ".html"):
            continue
        rel = path.relative_to(docs_root)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        out = rel.with_suffix(".html")
        rel_str = str(rel).replace("\\", "/")
        override = ROOT_PAGE_GROUPS.get(rel_str)
        title = override[0] if override else extract_title(path)
        pages.append(
            Page(
                src=path,
                rel=rel,
                out=out,
                title=title,
                is_readme=path.stem.upper() == "README",
                kind=path.suffix.lstrip("."),
                i18n_key=ROOT_PAGE_I18N.get(rel_str, ""),
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


def build_tree(docs_root: Path, pages: list[Page]) -> list[Group]:
    """Group pages into a nested tree mirroring the directory structure."""
    # Map relative dir -> Group
    groups: dict[Path, Group] = {}

    def group_for(rel_dir: Path) -> Group:
        if rel_dir in groups:
            return groups[rel_dir]
        if rel_dir == Path("."):
            g = Group(title="", rel_dir=Path("."))
        else:
            readme = docs_root / rel_dir / "README.md"
            title = extract_title(readme) if readme.exists() else prettify(rel_dir.name)
            g = Group(title=title, rel_dir=rel_dir)
        groups[rel_dir] = g
        # attach to parent
        if rel_dir != Path("."):
            parent = group_for(rel_dir.parent if str(rel_dir.parent) != "." else Path("."))
            parent.subgroups.append(g)
        return g

    # Synthetic subgroups for the loose root pages (快速上手 / 集成 / 参考).
    root_subgroups: dict[str, Group] = {}

    def root_subgroup(name: str) -> Group:
        if name not in root_subgroups:
            root_subgroups[name] = Group(
                title=name, rel_dir=Path(f"__{name}__"),
                i18n_key=ROOT_SUBGROUP_I18N.get(name, ""),
            )
        return root_subgroups[name]

    for p in pages:
        parent_str = str(p.rel.parent)
        if parent_str == ".":
            rel_str = str(p.rel).replace("\\", "/")
            override = ROOT_PAGE_GROUPS.get(rel_str)
            if override:
                root_subgroup(override[1]).pages.append(p)
            else:
                group_for(Path(".")).pages.append(p)  # uncategorized → root
        else:
            group_for(p.rel.parent).pages.append(p)

    # sort pages within each real group
    for g in groups.values():
        g.pages.sort(key=lambda p: (not p.is_readme, p.title.lower()))
        g.subgroups.sort(key=lambda sg: sg.title.lower())

    root = group_for(Path("."))
    # Prepend the synthetic root subgroups in a fixed order.
    ordered = [root_subgroups[n] for n in ROOT_SUBGROUP_ORDER if n in root_subgroups]
    root.subgroups = ordered + root.subgroups
    return [root]
