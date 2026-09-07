# `openprogram/backend/`

> Pluggable execution backend for shell-style tools.

## Overview

The ``bash`` tool (and any future sibling that wants to) routes
command execution through ``get_active_backend().run(...)`` instead
of calling ``subprocess`` directly. That keeps the tool code
backend-agnostic and lets ``openprogram setup backend`` actually
reroute where commands execute.

Three backends ship out of the box:
    local    — subprocess.run on the host (default, unchanged behavior)
    docker   — ``docker run --rm -i <image> sh -c "..."`` per call
    ssh      — ``ssh <target> "..."`` per call

Selection is read lazily from ``~/.openprogram/config.json`` (via
``setup._read_config``) so ``--profile`` and live config
edits take effect without restarting anything.

## Files in this directory

- **`_gui_child.py`** — Copied into the isolated scratch directory; never imported by the host
- **`base.py`** — Backend ABC + shared RunResult type
- **`docker.py`** — Docker backend
- **`gui.py`** — Mandatory local isolation for the persistent GUI interpreter
- **`gui_agent.py`** — Host-owned isolated GUI tool for the existing standard Agent runtime
- **`gui_broker.py`** — Host-owned GUI primitive admission and durable receipts
- **`gui_browser.py`** — Bind an existing owned WebUseSession to the isolated GUI interpreter
- **`gui_browser_resources.py`** — Invocation-owned browser capabilities for an isolated GUI script
- **`gui_runner.py`** — Persistent isolated Python transport, without host or GUI capabilities
- **`local.py`** — Local backend
- **`process.py`** — Collect a local command with execution cancellation and child cleanup
- **`ssh.py`** — SSH backend

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
