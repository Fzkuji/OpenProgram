"""Fast tools: a stdio MCP server that does in one call what an agent
otherwise takes many turns for, given to Antigravity as a plugin.

A port of yetone/magpie's ``internal/fasttools`` package (MIT License,
Copyright (c) yetone): ``fasttools.go``, ``plugin.go``, ``session.go``,
``search.go``, ``slices.go``, ``edits.go``, ``checks.go``, ``tools.go``,
``group_other.go`` and ``group_windows.go``, with the same tools, limits and
behaviour.

The server runs several searches at once (batch_search), several files'
outlines and line ranges (read_slices), every edit of a change, checked
before any is written and verified after (apply_edits), and independent
checks side by side (run_checks).

It is given to Antigravity (agy, and the Antigravity app, which read the
same ~/.gemini/config/plugins) as a plugin of its own (``plugin.py``), with
a SessionStart hook that tells a new conversation where it is
(``session.py``), so its first turns aren't spent finding out. In agy a turn
costs seconds before the model says anything, and much of a session is
turns of one search or one edit each: one call doing several saves those
turns.

  server.py   ``openprogram fast-tools mcp | session-start`` (:func:`run`), the MCP server
  plugin.py   the plugin under ~/.gemini/config/plugins: write / remove / is_on / keep
  session.py  the SessionStart hook's workspace snapshot
  search.py   batch_search
  slices.py   read_slices and the outliners
  edits.py    apply_edits
  checks.py   run_checks
  tools.py    the tools' list and schemas, the server's instructions
"""
from __future__ import annotations

from .plugin import (
    MARK_FILE,
    PLUGIN_NAME,
    NotOursError,
    is_on,
    keep,
    plugin_dir,
    remove,
    write,
)
from .server import OUT_CAP, USAGE, call, cap_text, run, serve
from .session import session_start, snapshot
from .slices import outline_of
from .tools import INSTRUCTIONS, SERVER_NAME, TOOL_NAMES, tool_list

__all__ = [
    "INSTRUCTIONS",
    "MARK_FILE",
    "NotOursError",
    "OUT_CAP",
    "PLUGIN_NAME",
    "SERVER_NAME",
    "TOOL_NAMES",
    "USAGE",
    "call",
    "cap_text",
    "is_on",
    "keep",
    "outline_of",
    "plugin_dir",
    "remove",
    "run",
    "serve",
    "session_start",
    "snapshot",
    "tool_list",
    "write",
]
