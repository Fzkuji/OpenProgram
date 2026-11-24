# `openprogram/host/processes/`

> Persistent managed background processes, scoped by trusted runtime identity.

## Files in this directory

- **`channel.py`** — The doorbell between a command's supervisor and whoever waits on it
- **`command.py`** — Shared subprocess execution utilities
- **`group_guard.py`** — Keep a POSIX process-group identity alive until its ordinary children exit
- **`presentation.py`** — Conservative command labels for process inspection, never execution
- **`progress.py`** — What a managed process is doing, read from its output
- **`record_changes.py`** — Telling windows the process records changed, and the Worker a supervisor ended
- **`store.py`** — Durable managed-process records, bounded output and supervisor commands
- **`supervisor.py`** — Detached owner of one managed process and its pipes

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/generators/gen_dir_readmes.py` from the repo root to refresh._
