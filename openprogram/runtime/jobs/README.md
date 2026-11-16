# `openprogram/runtime/jobs/`

> Async job lifecycle — explicit Job entity, worker pool, cancel.

## Overview

See ``docs/design/runtime/async-job-lifecycle.md`` for the full design.

Public surface:

  * :class:`Job` / :class:`JobStatus` — entity + state machine
  * :func:`get_runner` — process-wide singleton JobRunner
  * :class:`JobRunner` — thread-pool executor with cancel + persistence

Status transitions (one-way):

  pending → queued → running → completed
                            ↘ cancelled
                            ↘ errored

The runner submits work to a ``ThreadPoolExecutor`` and keeps a
parallel ``threading.Event`` per job for cancel signalling. Each
state transition rewrites ``jobs.json``, a line in the session's log.

## Files in this directory

- **`agent_result.py`** — The one shape every agent result comes back in
- **`agent_status.py`** — What Activity shows for one agent: its status and what its work cost
- **`delegation_watch.py`** — Default bounds on delegated agents: spend, stall and runtime
- **`execution_hooks.py`** — The Jobs engine's registrations with the execution control plane
- **`handoff.py`** — Hand-offs between a parent and the sub-agent it asked for, kept across a crash
- **`input.py`** — A background Agent Job's admission input, built from and turned into live objects
- **`notices.py`** — Tell an agent when work it handed off in the background ends
- **`store.py`** — Job persistence
- **`types.py`** — Job entity + state machine

## Sub-packages

- **`resource_governance/`** — Resource admission, limits and job diagnostics
- **`runner/`** — JobRunner

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/generators/gen_dir_readmes.py` from the repo root to refresh._
