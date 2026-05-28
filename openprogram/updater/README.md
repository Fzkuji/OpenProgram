# `openprogram/updater/`

> Auto-update for OpenProgram.

## Overview

Strategy is install-method aware:

  * git checkout (``pip install -e .`` from a clone) → ``git pull``
  * standalone binary (PyInstaller / similar)        → download + atomic swap
  * pip wheel install                                  → currently a no-op,
                                                         will become "pip install
                                                         --upgrade" once OpenProgram
                                                         ships to PyPI

Public API:

    detect_install_method()      — git_checkout | binary | pip_wheel | unknown
    check_for_update()           — query upstream; returns UpdateInfo or None
    apply_update()               — download / pull / install the new version
    background_check_and_apply() — fire-and-forget thread used at worker start
    is_disabled()                — environment / config kill switch

State files (under ``<state-dir>/``):

    update.last_check    — Unix timestamp of the last upstream query
    update.staged        — JSON: {"version": str, "applied_at": int} written
                           after a successful apply, read at next start to
                           show "updated to X" banner

## Files in this directory

- **`binary.py`** — Binary-distribution updater (placeholder for future Plan D)
- **`detect.py`** — Detect how OpenProgram is installed on this machine
- **`git.py`** — Git-checkout updater: ``git fetch`` then ``git pull`` if behind upstream
- **`github.py`** — GitHub Releases lookup
- **`pip.py`** — PyPI / pip-wheel install update path
- **`runner.py`** — Top-level orchestration for auto-update

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
