# `openprogram/models/auth/credentials/`

> Which state files hold secrets, how a backup treats them, and checking them.
>
> The owner-only file I/O they are written through is
> ``openprogram.state.private_files``: config, Program directories and
> machines below the models layer write through it too.

## Files in this directory

- **`audit.py`**
- **`inventory.py`**
- **`redaction.py`**

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/generators/gen_dir_readmes.py` from the repo root to refresh._
