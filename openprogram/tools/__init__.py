"""Tool registry.

Each tool lives under openprogram/tools/<name>/ with at minimum:
    <name>/__init__.py exporting TOOL = {"spec": {...}, "execute": callable}

Registration is lazy — import only the tools you pass to runtime.runtime.exec(..., tools=...).
"""

from __future__ import annotations

from typing import Any

from .bash import TOOL as BASH
from .read import TOOL as READ
from .write import TOOL as WRITE
from .edit import TOOL as EDIT
from .glob import TOOL as GLOB
from .grep import TOOL as GREP


ALL_TOOLS: dict[str, dict[str, Any]] = {
    "bash": BASH,
    "read": READ,
    "write": WRITE,
    "edit": EDIT,
    "glob": GLOB,
    "grep": GREP,
}


def get(name: str) -> dict[str, Any]:
    """Look up a tool record by name. Raises KeyError if not registered."""
    return ALL_TOOLS[name]


def get_many(names: list[str]) -> list[dict[str, Any]]:
    """Look up several tools. Use this when passing tools= to runtime.exec()."""
    return [get(n) for n in names]


__all__ = ["ALL_TOOLS", "BASH", "READ", "WRITE", "EDIT", "GLOB", "GREP", "get", "get_many"]
