"""Yaml-lite frontmatter, as skill files use it.

Lived under the previous memory layer's wiki helpers, and moved here
when that layer was replaced: skills is the only thing that reads it,
and it was never about memory.
"""

from __future__ import annotations

import re

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

