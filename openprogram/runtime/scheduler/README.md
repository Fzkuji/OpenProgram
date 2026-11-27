# `openprogram/runtime/scheduler/`

> User-facing scheduled tasks built on OpenProgram's signed cron executor.

## Overview

Removing a task: ``service.delete_task(task_id, by=...)`` is the one way a task
leaves ``<state>/scheduler/tasks.json``. It logs the task, its title and what
asked (the Scheduler API, the cron tool, a routine that could not be saved), so
the worker log says when a task went. A save that leaves fewer tasks first
copies the list to ``tasks.json.previous``; a task removed by mistake is read
back from there.

## Files in this directory

- **`hooks.py`** — Webhook tasks: an outside service starts a Scheduler task with a POST
- **`migration.py`** — One-time migration from Memory Commitments into Scheduler tasks
- **`reminders.py`** — Reminders: a scheduled moment the user hears about, or the agent acts on
- **`run_finish.py`** — How a run of a scheduled task comes to its end
- **`service.py`** — Scheduler CRUD shared by the Agent tool, REST API, and worker
- **`store.py`** — The Scheduler's task store: where tasks are kept, and the signed spec each one runs
- **`task_activity.py`** — What a conversation's scheduled tasks did, for its Activity panel
- **`task_branch.py`** — The branch a scheduled task works on
- **`task_runs.py`** — What each run of a scheduled task came to
- **`time_limits.py`** — How long an unattended run may go on before it is stopped
- **`worker.py`** — Scheduler worker for one-time, recurring, and monitor tasks

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/generators/gen_dir_readmes.py` from the repo root to refresh._
