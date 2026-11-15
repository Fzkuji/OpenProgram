# `openprogram/operations/self_update/github_release/`

> Shared helpers for OpenProgram's explicit update commands.

## Overview

Strategy is install-method aware:

  * managed release runtime → formal GitHub Release updater
  * source checkout         → explicit gated source upgrade
  * anything else           → no product update path

``openprogram upgrade`` (the CLI) calls these helpers; channel names live in
:mod:`openprogram.state.update_channels`.
Nothing in this package applies an update during worker startup.

## Files in this directory

- **`detect.py`** — Detect how OpenProgram is installed on this machine
- **`github.py`** — Validated metadata and small files from formal GitHub Releases

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
