# `openprogram/_cli_cmds/`

> Internal CLI subcommand handlers, split out of openprogram/cli.py.

## Overview

cli.py keeps the argparse setup, the dispatch chain, the TUI-tty
globals, and the public ``main`` entry point. All ``_cmd_<verb>`` and
``_dispatch_<group>_verb`` handler bodies live in topic modules here:

    programs.py  — programs list/new/edit/run/app, configure, runtime
    skills.py    — skills list/doctor/install, install_skills
    browser.py   — browser install/status/refresh/reset/list/rm
    sessions.py  — sessions list/resume
    agents.py    — agents list/add/rm/show/set-default
    channels.py  — accounts + bindings + login
    web.py       — web UI launcher
    chat.py      — interactive cli chat + deep_work
    cron.py      — cron-worker

cli.py re-exports these at module level so external callers
(``openprogram.cli_chat``, tests, ``openprogram.cli_ink``) that import
``_cmd_<name>`` from ``openprogram.cli`` keep working.

## Files in this directory

- **`agents.py`** — ``openprogram agents <verb>`` dispatcher
- **`browser.py`** — ``openprogram browser`` handlers
- **`channels.py`** — ``openprogram channels`` accounts + bindings dispatchers + login flow
- **`chat.py`** — Interactive CLI chat entry point + deep_work runner
- **`completion.py`** — ``openprogram completion <shell>``
- **`cron.py`** — ``openprogram cron-worker`` handler
- **`doctor.py`** — ``openprogram doctor``
- **`logs.py`** — ``openprogram logs``
- **`mcp.py`** — ``openprogram mcp`` CLI subcommands
- **`plugins.py`** — ``openprogram plugins`` handlers
- **`programs.py`** — ``openprogram programs`` + ``openprogram configure`` handlers
- **`rescue.py`** — ``openprogram rescue``
- **`sessions.py`** — ``openprogram sessions`` handlers (list / resume)
- **`skills.py`** — ``openprogram skills`` handlers
- **`subagent.py`** — ``openprogram agent``
- **`web.py`** — ``openprogram web`` handler

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `_gen_dir_readmes.py` to refresh._
