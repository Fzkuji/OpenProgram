# `openprogram/backend/`

> Pluggable execution backend for shell-style tools.

## Overview

The ``bash`` tool (and any future sibling that wants to) routes
command execution through ``get_active_backend().run(...)`` instead
of calling ``subprocess`` directly. That keeps the tool code
backend-agnostic and lets ``openprogram config backend`` actually
reroute where commands execute.

Three backends ship out of the box:
    local    — subprocess.run on the host (default, unchanged behavior)
    docker   — ``docker run --rm -i <image> sh -c "..."`` per call
    ssh      — ``ssh <target> "..."`` per call

Selection is read lazily from ``~/.agentic/config.json`` (via
``setup._read_config``) so ``--profile`` and live config
edits take effect without restarting anything.

## Files in this directory

- **`base.py`** — Backend ABC + shared RunResult type
- **`docker.py`** — Docker backend
- **`local.py`** — Local backend
- **`ssh.py`** — SSH backend

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `_gen_dir_readmes.py` to refresh._
