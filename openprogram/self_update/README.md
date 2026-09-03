# `openprogram/self_update/`

> Conversational self-update state protocol.

## Overview

App activation and rollback deliberately live outside the worker process. This
package exposes the durable request/state contract and the dispatcher handoff
that releases a prepared request only after its origin turn is durable.

## Files in this directory

- **`handoff.py`** — Durable release of a prepared update after its origin turn commits
- **`store.py`** — Crash-safe file store for conversational self-update state
- **`types.py`** — Durable data contract for conversational self-update

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
