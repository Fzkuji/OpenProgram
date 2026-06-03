"""Unified slash-command system.

Sources merged into one registry (low → high priority; later wins):

  L0 builtin   — hardcoded in code (registered via ``register_builtin``)
  L1 plugin    — plugin.json ``entrypoints.commands``
  L2 mcp       — MCP server ``list_prompts()`` (auto-injected, future)
  L3 skill     — skills/<name>/SKILL.md     (auto-injected, future)
  L4 user      — ~/.openprogram/commands/**/*.md
  L5 project   — <cwd>/.openprogram/commands/**/*.md

See ``docs/design/cli/slash-commands.md`` for the full design.
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
