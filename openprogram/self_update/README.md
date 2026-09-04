# `openprogram/self_update/`

> Conversational self-update state protocol.

## Overview

App activation and rollback deliberately live outside the worker process. This
package exposes the durable request/state contract and the dispatcher handoff
that releases a prepared request only after its origin turn is durable.

## Files in this directory

- **`bootstrap.py`** — Recover an existing update from its saved runtime, independently of the App
- **`commit_intent.py`** — Preserve an accepted commit decision across irreversible App finalization
- **`controller_bundle.py`** — Freeze the installed controller runtime outside the replaceable App
- **`delivery.py`** — Deterministic original-session notifications from durable update results
- **`diagnosis.py`** — One bounded, read-only diagnostic Job after a verified rollback
- **`handoff.py`** — Durable release of a prepared update after its origin turn commits
- **`iteration.py`** — Deterministic authorization checks for a proposed self-update iteration
- **`launcher.py`** — Submit one trusted, one-shot self-update supervisor through launchd
- **`maintenance.py`** — Durable admission gate while an approved update waits for quiescence
- **`next_candidate.py`** — Durable, evidence-bound submission of a repaired self-update candidate
- **`owner_repair.py`** — Explicit, bounded owner recovery through the original trusted controller
- **`projection.py`** — Read-only, session-scoped status shared by tools and user interfaces
- **`recovery.py`** — Dispatch one frozen verifier Job after the supervisor releases system gates
- **`reopen.py`** — Controller-owned Desktop recovery intent; never update/verifier authority
- **`repair_candidate.py`** — Apply bounded model edits and test a new isolated candidate without installing it
- **`rollback_intent.py`** — Durable rollback intent shared by the controller and worker admission
- **`source_repair.py`** — Continue a verified rollback with one bounded source-repair Job and candidate
- **`store.py`** — Crash-safe file store for conversational self-update state
- **`supervisor.py`** — External controller for one durable conversational self-update
- **`system_probe.py`** — Observe the default worker before releasing post-update verification
- **`types.py`** — Durable data contract for conversational self-update
- **`verification.py`** — Validate identity-bound, timestamped self-update acceptance evidence
- **`verification_channel.py`** — Bind live observations and durable Job results to one verifier authorization
- **`verifier_config.py`** — Freeze verifier inputs and bind their digest to an immutable update request

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
