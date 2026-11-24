# `openprogram/host/backend/`

> Pluggable execution backend for shell-style tools.

## Overview

The ``bash`` tool (and any future sibling that wants to) routes
command execution through ``get_active_backend().run(...)`` instead
of calling ``subprocess`` directly. That keeps the tool code
backend-agnostic.

Three backends ship out of the box:
    local    — subprocess.run on this computer
    docker   — a long-lived container per conversation (``docker exec``)
    ssh      — ``ssh <target> "..."`` per call, one shared connection

Which one runs is decided by the session's environment
(``openprogram.host.machines``): every conversation picks a machine when it
is created, and the backend follows that machine's shell kind. Each backend
registers as a shell kind at the end of this module.

## Files in this directory

- **`base.py`** — Backend ABC + shared RunResult type
- **`docker.py`** — Docker backend
- **`local.py`** — Local backend
- **`process.py`** — Collect a local command with execution cancellation and child cleanup
- **`ssh.py`** — SSH backend
- **`ssh_config.py`** — The hosts named in the user's SSH configuration, for Add device → SSH server
- **`workdir.py`** — Resolve a one-shot shell location without changing worktree or process state

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/generators/gen_dir_readmes.py` from the repo root to refresh._
