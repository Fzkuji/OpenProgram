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

# Top-level loose pages get collected under this synthetic group.
GUIDES_GROUP = "Guides"


@dataclass
class Page:
    src: Path          # absolute source path
    rel: Path          # path relative to docs/ (e.g. design/runtime/rewind.md)
    out: Path          # output path relative to _site/ (always .html)
    title: str
    is_readme: bool
    kind: str          # "md" or "html"


@dataclass
class Group:
    title: str
    rel_dir: Path                       # relative to docs/ ("" for root)
    pages: list[Page] = field(default_factory=list)
    subgroups: list["Group"] = field(default_factory=list)


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
        pages.append(
            Page(
                src=path,
                rel=rel,
                out=out,
                title=extract_title(path),
                is_readme=path.stem.upper() == "README",
                kind=path.suffix.lstrip("."),
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
            g = Group(title=GUIDES_GROUP, rel_dir=Path("."))
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

    for p in pages:
        group_for(p.rel.parent if str(p.rel.parent) != "." else Path(".")).pages.append(p)

    # sort pages within each group: README first, then by title
    for g in groups.values():
        g.pages.sort(key=lambda p: (not p.is_readme, p.title.lower()))
        g.subgroups.sort(key=lambda sg: sg.title.lower())

    root = groups.get(Path("."))
    return [root] if root else []
