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
    chat.py      — interactive cli chat
    cron.py      — scheduler-worker

cli.py re-exports these at module level so external callers
(``openprogram.cli_chat``, tests, ``openprogram.cli_ink``) that import
``_cmd_<name>`` from ``openprogram.cli`` keep working.

## Files in this directory

- **`acp.py`** — `openprogram acp`
- **`agents.py`** — ``openprogram agents <verb>`` dispatcher
- **`backup.py`** — Create, list, and restore snapshots of the profile state dir
- **`browser.py`** — ``openprogram browser`` handlers
- **`channels.py`** — ``openprogram channels`` accounts + bindings dispatchers + login flow
- **`chat.py`** — Interactive CLI chat entry point
- **`completion.py`** — ``openprogram completion <shell>``
- **`cron.py`** — ``openprogram scheduler-worker`` handler (cron-worker is an alias)
- **`diagnostics.py`** — ``openprogram diagnostics``
- **`doctor.py`** — ``openprogram doctor``
- **`jobs.py`** — Read-only CLI job resource views
- **`logs.py`** — ``openprogram logs``
- **`mcp.py`** — ``openprogram mcp`` CLI subcommands
- **`plugins.py`** — ``openprogram plugins`` handlers
- **`programs.py`** — ``openprogram programs`` + provider-config wizard handlers
- **`rescue.py`** — ``openprogram rescue``
- **`sessions.py`** — ``openprogram sessions`` handlers (list / resume / export / archive)
- **`skills.py`** — ``openprogram skills`` handlers
- **`subagent.py`** — ``openprogram subagent``
- **`trash.py`** — List and restore recoverable local deletion records
- **`upgrade.py`** — Explicit stable-Release or source-checkout upgrades
- **`web.py`** — ``openprogram web`` handler

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
