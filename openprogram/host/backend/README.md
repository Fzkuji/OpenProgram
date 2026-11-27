# `openprogram/host/backend/`

> Pluggable execution backend for shell-style tools.

## Overview

The ``bash`` tool (and any future sibling that wants to) routes
command execution through ``machines.get_active_backend().run(...)`` instead
of calling ``subprocess`` directly. That keeps the tool code
backend-agnostic.

Three backends ship out of the box:
    local    — subprocess.run on this computer
    docker   — a long-lived container per conversation (``docker exec``)
    ssh      — ``ssh <target> "..."`` per call, one shared connection

Which one runs is decided by the session's environment
(``openprogram.host.machines``, which sits above the backends): a
conversation runs on its project's machine, and the backend follows that
machine's connection method. This module keeps, in ``SHELLS``, which
methods commands run through: ``local``, ``ssh`` and ``docker``; the
machines ask ``shell_for`` and ``backend_for``, build the backend for the
running session (``machines.get_active_backend``) and register ``ssh``
and ``docker`` as connection methods (``machines.shells``). The backends
know nothing of configured machines.

## Files in this directory

- **`base.py`** — Backend ABC + shared RunResult type
- **`docker.py`** — Docker backend
- **`local.py`** — Local backend
- **`process.py`** — Collect a local command with execution cancellation and child cleanup
- **`ssh.py`** — SSH backend
- **`ssh_config.py`** — The hosts named in the user's SSH configuration, for Add device → SSH server
- **`workdir.py`** — Resolve a one-shot shell location without changing worktree or process state

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/generators/gen_dir_readmes.py` from the repo root to refresh._
