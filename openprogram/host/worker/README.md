# `openprogram/host/worker/`

> This profile's worker as the machine sees it: its state files, its lock,

## Overview

its port and the interpreter it runs as.

Every layer reads these facts — the runtime checks who holds the worker
lock, the gateway address names the worker's port, the native checks start
the worker's interpreter — so they live here, below the runtime, and
``openprogram.operations.worker`` starts and stops the worker on top of them.

## Files in this directory

- **`lock.py`** — Single-holder file lock for the persistent worker
- **`paths.py`** — State file paths for the persistent worker
- **`ports.py`** — Unified port probing + ownership diagnostics
- **`process.py`** — Which process is this profile's worker, the port it listens on, the

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/generators/gen_dir_readmes.py` from the repo root to refresh._
