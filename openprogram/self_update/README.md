# `openprogram/self_update/`

> Conversational self-update state protocol.

## Overview

App activation and rollback deliberately live outside the worker process. This
package exposes the durable request/state contract and the dispatcher handoff
that releases a prepared request only after its origin turn is durable.

## Files in this directory

- **`handoff.py`** — Durable release of a prepared update after its origin turn commits
- **`iteration.py`** — Deterministic authorization checks for a proposed self-update iteration
- **`launcher.py`** — Submit one trusted, one-shot self-update supervisor through launchd
- **`maintenance.py`** — Durable admission gate while an approved update waits for quiescence
- **`store.py`** — Crash-safe file store for conversational self-update state
- **`supervisor.py`** — External controller for one durable conversational self-update
- **`types.py`** — Durable data contract for conversational self-update
- **`verification.py`** — Validate identity-bound, timestamped self-update acceptance evidence

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
