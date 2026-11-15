"""Yaml-lite frontmatter of the markdown files the state directory keeps.

Skills (``SKILL.md``) and output styles open with a ``---`` block of flat
keys; this module reads and writes that block. It is a text format with no
knowledge of what the keys mean, so it sits in state, below the skills
loader, the CLI and output styles that all use it.
"""

from __future__ import annotations

import re

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)
_BLOCK_SCALARS = frozenset({">", ">-", ">+", "|", "|-", "|+"})


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return ``(frontmatter_dict, body)``. Empty dict when missing.

    Yaml-lite: flat scalars, one-level lists (``- "x"`` indented) and
    block scalars (``key: >-`` or ``key: |`` followed by indented lines;
    ``>`` joins the lines with spaces, ``|`` keeps them).
    Good enough for our hybrid schema.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    raw_fm, body = m.group(1), m.group(2)
    fm: dict = {}
    current_key: str | None = None
    block: tuple[str, str, list[str]] | None = None  # key, ">" or "|", lines

    def close_block() -> None:
        nonlocal block
        if block is not None:
            key, style, lines = block
            fm[key] = (" " if style == ">" else "\n").join(lines).strip()
            block = None

    for line in raw_fm.splitlines():
        if block is not None:
            if not line.strip() or line[0] in " \t":
                block[2].append(line.strip())
                continue
            close_block()
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
        elif val in _BLOCK_SCALARS:
            current_key = None
            block = (key, val[0], [])
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
    close_block()
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

