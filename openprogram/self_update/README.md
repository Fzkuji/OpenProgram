# `openprogram/self_update/`

> Conversational self-update state protocol.

## Overview

App activation and rollback deliberately live outside the worker process.  This
package currently exposes only the durable request/state contract used by that
future controller.

## Files in this directory

- **`store.py`** — Crash-safe file store for conversational self-update state
- **`types.py`** — Durable data contract for conversational self-update

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
