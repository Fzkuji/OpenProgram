# `openprogram/commands/`

> Unified slash-command system.

## Overview

Sources merged into one registry (low → high priority; later wins):

  L0 builtin   — hardcoded in code (registered via ``register_builtin``)
  L1 plugin    — plugin.json ``entrypoints.commands``
  L2 mcp       — MCP server ``list_prompts()`` (auto-injected, future)
  L3 skill     — skills/<name>/SKILL.md     (auto-injected, future)
  L4 user      — ~/.openprogram/commands/**/*.md
  L5 project   — <cwd>/.openprogram/commands/**/*.md

See ``docs/design/slash-commands.md`` for the full design.

## Files in this directory

- **`_plugin_adapter.py`** — Bridge the existing plugin loader's ``contrib._commands`` list into
- **`_skill_adapter.py`** — Project every loaded skill into the slash-command registry
- **`dispatch.py`** — Resolve + render a slash-command invocation into the next action
- **`frontmatter.py`** — Frontmatter parsing for command files
- **`loader.py`** — Scan command source directories and yield parsed entries
- **`registry.py`** — Process-wide merge of every command source
- **`template.py`** — Render a command body with user-supplied arguments and env

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `_gen_dir_readmes.py` to refresh._
