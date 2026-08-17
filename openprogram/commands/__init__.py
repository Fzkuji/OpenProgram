"""Unified slash-command system.

Sources merged into one registry (low → high priority; later wins):

  L0 builtin   — hardcoded in code (registered via ``register_builtin``)
  L1 plugin    — plugin.json ``entrypoints.commands``
  L2 mcp       — MCP server ``list_prompts()`` (auto-injected)
  L3 skill     — skills/<name>/SKILL.md     (auto-injected)
  L4 user      — ~/.openprogram/commands/**/*.md
  L5 project   — <cwd>/.openprogram/commands/**/*.md

Every front-end reads this one table — there is no second command
list anywhere:

* Web composer — ``GET /api/commands`` for the menu, ``POST
  /api/commands/invoke`` to render; the rendered body lands in the
  textarea.
* Ink TUI — same two endpoints against the worker; TUI-local actions
  (theme, pickers, ...) stay in ``cli/src/commands/``, everything
  else expands into the chat turn.
* Rich REPL — registers its local actions into the builtin layer
  (``openprogram/cli/repl/handlers.py:register_repl_builtins``) and
  dispatches every ``/slash`` through ``dispatch.invoke``; ``/help``
  is rendered from ``list_all()``.

See ``docs/reference/design/cli/slash-commands.md`` for the full design.
"""
from __future__ import annotations

from .registry import (
    CommandSpec,
    get,
    list_all,
    reload,
    resolve,
    SOURCE_ORDER,
)

__all__ = [
    "CommandSpec",
    "get",
    "list_all",
    "reload",
    "resolve",
    "SOURCE_ORDER",
]
