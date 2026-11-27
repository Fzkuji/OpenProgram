"""Reading an agent's JSON file in its wiring, and keeping what the user set
inside OpenProgram's own entry.

After yetone/magpie ``internal/agent/sync.go`` ``theirsKept`` / ``mergeTheirs``
(MIT License, Copyright (c) yetone): a key OpenProgram doesn't write in its
own provider block, at any depth, is the user's or another tool's and stays
when the block is written again; the keys named ``whole`` (the model list)
are OpenProgram's alone, so a model gone from the catalog leaves the list.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import edit
from ..edit import ConfigText, get_path


def read_json(path: Path) -> dict[str, Any]:
    """The JSON(C) object at ``path``; ``{}`` where there is none. A file that
    does not parse is :class:`edit.ConfigUnreadable`, never read as empty."""
    text = ConfigText.read(path)
    return edit.load("jsonc", text.text, str(path)) if text.exists else {}


def merge_theirs(have: Any, want: Any, whole: tuple[str, ...] = ()) -> Any:
    """``want`` with each key of ``have`` that ``want`` lacks, objects in both
    merged alike; a key named in ``whole`` is ``want``'s as it is."""
    if not isinstance(have, dict) or not isinstance(want, dict):
        return want
    out = dict(want)
    for key, value in have.items():
        if key not in want:
            out[key] = value
        elif key not in whole and isinstance(value, dict) and isinstance(want[key], dict):
            out[key] = merge_theirs(value, want[key])
    return out


def theirs_kept(doc: dict[str, Any], key: str, want: dict[str, Any], *whole: str) -> dict[str, Any]:
    """``want``, OpenProgram's block at dotted ``key`` of ``doc``, with what
    the block there already holds kept (magpie ``theirsKept``)."""
    present, have = get_path(doc, key)
    return merge_theirs(have, want, whole) if present else want
