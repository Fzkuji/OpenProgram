# `openprogram/operations/self_update/`

> Conversational self-update state protocol.

## Overview

App activation and rollback deliberately live outside the worker process. This
package exposes the durable request/state contract and the dispatcher handoff
that releases a prepared request only after its origin turn is durable.

## Files in this directory

- **`store.py`** — Crash-safe file store for conversational self-update state
- **`types.py`** — Durable data contract for conversational self-update

## Sub-packages

- **`control/`** — Control implementation
- **`delivery/`** — Delivery implementation
- **`github_release/`** — Shared helpers for OpenProgram's explicit update commands
- **`official/`** — Official releases for an installation that runs from the version store
- **`repair/`** — Repair implementation
- **`trial/`** — Trial: a candidate version run on a sanitized copy of the data, before it can go live
- **`verification/`** — Verification implementation
- **`versions/`** — Sealed versions: the on-disk store a managed install runs from

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
