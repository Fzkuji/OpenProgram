# `openprogram/_cli_chat/`

> Internal CLI-chat implementation, split out of openprogram/cli_chat.py.

## Overview

cli_chat.py keeps the ``run_cli_chat`` entry point + the small helpers
external callers import directly (``_get_chat_runtime`` etc.). All the
slash-command handlers, banner rendering, and per-turn execution live
in topic modules here:

    setup.py    — runtime detection + first-run wizard prompt
    banner.py   — tools/skills/functions/apps inventory + welcome panel
    handlers.py — every ``_handle_*`` slash command + dispatcher table
    turn.py     — ``_run_turn_with_history`` (one exec turn + persist)

cli_chat.py re-exports these at module level so external callers
(``_timing.py``, ``openprogram.setup``, ``openprogram._cli_cmds.chat``,
tests) that import ``_get_chat_runtime`` / ``run_cli_chat`` etc. keep
working unchanged.

## Files in this directory

- **`banner.py`** — Inventory helpers + Hermes-style welcome banner for the CLI chat REPL
- **`handlers.py`** — Slash-command dispatch + per-verb handlers for the CLI chat REPL
- **`setup.py`** — Runtime detection + first-run wizard prompt for the CLI chat
- **`turn.py`** — One chat turn: load history, exec (with optional streaming), persist

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `_gen_dir_readmes.py` to refresh._
