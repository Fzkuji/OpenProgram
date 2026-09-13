# `openprogram/processes/`

> Persistent managed background processes, scoped by trusted runtime identity.

## Files in this directory

- **`group_guard.py`** — Keep a POSIX process-group identity alive until its ordinary children exit
- **`presentation.py`** — Conservative command labels for process inspection, never execution
- **`store.py`** — Durable managed-process records, bounded output and supervisor commands
- **`supervisor.py`** — Detached owner of one managed process and its pipes

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
