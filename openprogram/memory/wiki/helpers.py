"""Wiki helpers — deterministic Python utilities.

Frontmatter parse/dump, folder tree, find-by-filename, wikilink
extract / rewrite, fenced-code masking. Ported from
research_harness/stages/wiki/_helpers.py and trimmed.

The frontmatter dumper preserves nashsu's richer key set (`type:`,
`sources:`, `related:`, `status:`, etc.) so we get both halves of
the hybrid design.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable


# ---------------------------------------------------------------------------
# Frontmatter
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return ``(frontmatter_dict, body)``. Empty dict when missing.

    Yaml-lite: flat scalars and one-level lists (``- "x"`` indented).
    Good enough for our hybrid schema.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    raw_fm, body = m.group(1), m.group(2)
    fm: dict = {}
    current_key: str | None = None
    for line in raw_fm.splitlines():
        if line.startswith("  - ") or line.startswith("- "):
            if current_key is None:
                continue
            item = line.lstrip().removeprefix("- ").strip()
            fm.setdefault(current_key, []).append(_strip_quotes(item))
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if not val:
            current_key = key
            fm[key] = []
        else:
            current_key = None
            # Inline list form: `tags: [a, b, c]`
            if val.startswith("[") and val.endswith("]"):
                inner = val[1:-1].strip()
                if not inner:
                    fm[key] = []
                else:
                    fm[key] = [_strip_quotes(x.strip()) for x in inner.split(",")]
            else:
                fm[key] = _strip_quotes(val)
    return fm, body


def dump_frontmatter(fm: dict, body: str) -> str:
    """Render frontmatter + body."""
    lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, list):
            if not v:
                lines.append(f"{k}: []")
                continue
            lines.append(f"{k}:")
            for item in v:
                lines.append(f'  - "{item}"')
        elif isinstance(v, (int, float)) or v is None:
            lines.append(f"{k}: {v if v is not None else ''}")
        else:
            s = str(v)
            if any(ch in s for ch in (":", "#", "[", "]", "{", "}")) or " " in s:
                lines.append(f'{k}: "{s}"')
            else:
                lines.append(f"{k}: {s}")
    lines.append("---")
    return "\n".join(lines) + "\n" + body


def _strip_quotes(s: str) -> str:
    if len(s) >= 2 and ((s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")):
        return s[1:-1]
    return s


# ---------------------------------------------------------------------------
# Folder tree
# ---------------------------------------------------------------------------


def folder_tree(root: Path, *, max_depth: int = 8) -> str:
    """Render the wiki folder tree as an indented ASCII outline.

    Excludes hidden entries (``.git``, ``Attachments``). Governance
    docs are kept at the top.
    """
    lines: list[str] = []

    def _walk(dir_path: Path, depth: int) -> None:
        if depth > max_depth:
            return
        entries = sorted(
            (e for e in dir_path.iterdir() if not e.name.startswith(".") and e.name != "Attachments"),
            key=lambda p: (not p.is_dir(), p.name.lower()),
        )
        for entry in entries:
            prefix = "  " * depth + ("- " if depth else "")
            if entry.is_dir():
                lines.append(f"{prefix}{entry.name}/")
                _walk(entry, depth + 1)
            elif entry.suffix == ".md":
                lines.append(f"{prefix}{entry.name}")

    if not root.exists():
        return ""
    _walk(root, 0)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Iterate / find
# ---------------------------------------------------------------------------


def iter_md_files(root: Path, *, skip: set[str] | None = None) -> Iterable[Path]:
    """Every .md under root except governance docs."""
    from .. import store
    skip = set(skip or store.GOVERNANCE_PAGES)
    for p in sorted(root.rglob("*.md")):
        if p.name in skip:
            continue
        yield p


def find_node(root: Path, name: str) -> Path | None:
    """Find a page by filename stem (case-insensitive) anywhere
    under root. Returns the absolute path."""
    name_l = name.lower().removesuffix(".md")
    # Prefer folder-form match (e.g. `Foo/Foo.md`) over bare leaf.
    candidates: list[tuple[int, Path]] = []
    for p in root.rglob("*.md"):
        if p.stem.lower() == name_l:
            rank = 0 if p.parent.name.lower() == name_l else 1
            candidates.append((rank, p))
    if not candidates:
        return None
    candidates.sort(key=lambda r: r[0])
    return candidates[0][1]


# ---------------------------------------------------------------------------
# Wikilinks
# ---------------------------------------------------------------------------

_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(\|[^\]]+?)?(#[^\]]+?)?\]\]")
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`]+`")


def mask_code(text: str) -> tuple[str, dict[str, str]]:
    """Replace code blocks and inline code with sentinels so wikilink
    operations don't touch them."""
    replacements: dict[str, str] = {}
    counter = [0]

    def _sub(match: re.Match) -> str:
        token = f"\0CODE{counter[0]}\0"
        replacements[token] = match.group(0)
        counter[0] += 1
        return token

    masked = _FENCE_RE.sub(_sub, text)
    masked = _INLINE_CODE_RE.sub(_sub, masked)
    return masked, replacements


def unmask_code(text: str, replacements: dict[str, str]) -> str:
    for token, original in replacements.items():
        text = text.replace(token, original)
    return text


def extract_wikilinks(body: str) -> list[str]:
    """All wikilink targets in body, code-fence-aware, lower-cased, deduped."""
    masked, _ = mask_code(body)
    seen: dict[str, None] = {}
    for m in _WIKILINK_RE.finditer(masked):
        seen[m.group(1).strip().lower()] = None
    return list(seen)


def rewrite_wikilinks(text: str, old: str, new: str) -> str:
    """Rewrite all ``[[old]]`` → ``[[new]]`` preserving alias and anchor.
    Skips code blocks."""
    masked, repls = mask_code(text)
    old_l = old.lower()

    def _sub(m: re.Match) -> str:
        target = m.group(1).strip()
        if target.lower() != old_l:
            return m.group(0)
        alias = m.group(2) or ""
        anchor = m.group(3) or ""
        return f"[[{new}{alias}{anchor}]]"

    masked = _WIKILINK_RE.sub(_sub, masked)
    return unmask_code(masked, repls)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def topic_path(root: Path, *, parent: str | Path | None, name: str,
                folder_form: bool = False) -> Path:
    """Compute where a new topic page should live.

    Args:
        root: wiki vault root.
        parent: optional parent folder (relative to root, or absolute Path,
            or None for vault root).
        name: the topic's filename stem.
        folder_form: if True, return ``<parent>/<name>/<name>.md`` so the
            page can have children. If False, return ``<parent>/<name>.md``.
    """
    if parent is None:
        base = root
    elif isinstance(parent, Path):
        base = parent
    else:
        base = root / parent
    if folder_form:
        return base / name / f"{name}.md"
    return base / f"{name}.md"


def ensure_page(path: Path, *, frontmatter: dict, body: str = "") -> Path:
    """Create a page if missing, with the given frontmatter + body.
    Idempotent — returns ``path`` either way.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path
    path.write_text(dump_frontmatter(frontmatter, body), encoding="utf-8")
    return path
