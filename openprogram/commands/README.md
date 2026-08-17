# `openprogram/commands/`

> Unified slash-command system.

## Overview

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
  (theme, pickers, ...) stay in ``apps/cli/src/commands/``, everything
  else expands into the chat turn.
* Rich REPL — registers its local actions into the builtin layer
  (``openprogram/cli/repl/handlers.py:register_repl_builtins``) and
  dispatches every ``/slash`` through ``dispatch.invoke``; ``/help``
  is rendered from ``list_all()``.

See ``docs/reference/design/cli/slash-commands.md`` for the full design.

## Files in this directory

- **`_plugin_adapter.py`** — Bridge the existing plugin loader's ``contrib._commands`` list into
- **`_skill_adapter.py`** — Project every loaded skill into the slash-command registry
- **`commit_message.py`** — Read-only commit-message generation from the current Git diff
- **`dispatch.py`** — Resolve + render a slash-command invocation into the next action
- **`frontmatter.py`** — Frontmatter parsing for command files
- **`loader.py`** — Scan command source directories and yield parsed entries
- **`registry.py`** — Process-wide merge of every command source
- **`template.py`** — Render a command body with user-supplied arguments and env

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
