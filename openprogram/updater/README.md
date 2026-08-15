# `openprogram/updater/`

> Release metadata and installation-type helpers for explicit updates.

## Overview

Product updates are install-method aware:

  * managed release runtime → latest stable GitHub Release and versioned installer
  * source checkout → the gated source pipeline in `_cli_cmds/upgrade.py`
  * unknown installation → no product update path

Worker startup never checks or applies product updates. `openprogram upgrade`
is the public command, and `openprogram update` is its compatibility alias.

    detect_install_method() — managed_release | source_checkout | unknown

## Files in this directory

- **`detect.py`** — Detect how OpenProgram is installed on this machine
- **`github.py`** — GitHub Releases lookup

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
