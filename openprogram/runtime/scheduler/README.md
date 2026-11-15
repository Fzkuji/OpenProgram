# `openprogram/runtime/scheduler/`

> User-facing scheduled tasks built on OpenProgram's signed cron executor.

## Files in this directory

- **`hooks.py`** — Webhook tasks: an outside service starts a Scheduler task with a POST
- **`migration.py`** — One-time migration from Memory Commitments into Scheduler tasks
- **`reminders.py`** — Reminders: a scheduled moment the user hears about, or the agent acts on
- **`run_finish.py`** — How a run of a scheduled task comes to its end
- **`service.py`** — Scheduler CRUD shared by the Agent tool, REST API, and worker
- **`task_branch.py`** — The branch a scheduled task works on
- **`task_runs.py`** — What each run of a scheduled task came to
- **`time_limits.py`** — How long an unattended run may go on before it is stopped

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
