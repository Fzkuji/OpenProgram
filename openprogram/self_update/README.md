# `openprogram/self_update/`

> Conversational self-update state protocol.

## Overview

App activation and rollback deliberately live outside the worker process. This
package exposes the durable request/state contract and the dispatcher handoff
that releases a prepared request only after its origin turn is durable.

## Files in this directory

- **`controller_bundle.py`** — Freeze the installed controller runtime outside the replaceable App
- **`handoff.py`** — Durable release of a prepared update after its origin turn commits
- **`iteration.py`** — Deterministic authorization checks for a proposed self-update iteration
- **`launcher.py`** — Submit one trusted, one-shot self-update supervisor through launchd
- **`maintenance.py`** — Durable admission gate while an approved update waits for quiescence
- **`recovery.py`** — Dispatch one frozen verifier Job after the supervisor releases system gates
- **`rollback_intent.py`** — Durable rollback intent shared by the controller and worker admission
- **`store.py`** — Crash-safe file store for conversational self-update state
- **`supervisor.py`** — External controller for one durable conversational self-update
- **`system_probe.py`** — Observe the default worker before releasing post-update verification
- **`types.py`** — Durable data contract for conversational self-update
- **`verification.py`** — Validate identity-bound, timestamped self-update acceptance evidence
- **`verifier_config.py`** — Freeze verifier inputs and bind their digest to an immutable update request

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
