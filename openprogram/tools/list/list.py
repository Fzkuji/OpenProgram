"""list tool — list directory contents."""

from __future__ import annotations

import os
from typing import Any


NAME = "list"

DESCRIPTION = (
    "List the contents of a directory. Returns entries one per line, with a "
    "trailing `/` on directories, plus a terse size column for files.\n"
    "\n"
    "- Path MUST be absolute.\n"
    "- Hidden entries (starting with `.`) are omitted unless show_hidden=true.\n"
    "- For content search use `grep`; for pattern-based file discovery use `glob`.\n"
)

SPEC: dict[str, Any] = {
    "name": NAME,
    "description": DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path of the directory to list.",
            },
            "show_hidden": {
                "type": "boolean",
                "description": "Include dotfiles and dot-directories. Default false.",
            },
        },
        "required": ["path"],
    },
}


def _fmt_size(n: int) -> str:
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024 or unit == "T":
            return f"{n}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n}T"


def execute(path: str, show_hidden: bool = False, **_: Any) -> str:
    if not os.path.isabs(path):
        return f"Error: path must be absolute, got {path!r}"
    if not os.path.isdir(path):
        return f"Error: not a directory: {path}"

    try:
        entries = sorted(os.listdir(path))
    except Exception as e:
        return f"Error listing {path}: {type(e).__name__}: {e}"

    rows: list[str] = []
    for name in entries:
        if not show_hidden and name.startswith("."):
            continue
        full = os.path.join(path, name)
        try:
            if os.path.isdir(full):
                rows.append(f"{name}/")
            else:
                size = os.path.getsize(full)
                rows.append(f"{name}  {_fmt_size(size)}")
        except OSError:
            rows.append(f"{name}  [unreadable]")

    if not rows:
        return f"(empty{' — pass show_hidden=true to include dotfiles' if not show_hidden else ''})"
    return "\n".join(rows)
