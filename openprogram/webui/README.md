# `openprogram/webui/`

> Compatibility namespace for the OpenProgram Server application.

## Overview

Server source lives in :mod:`openprogram_server`.  This package preserves the
established ``openprogram.webui.*`` module names while loading their single
implementation from the Server application package.

Usage:
    from openprogram.webui import start_web
    start_web(port=18100)

Or from CLI:
    openprogram web
    python -m openprogram.webui

## Files in this directory

- **`__main__.py`** — Allow running the web UI with: python -m openprogram.webui
- **`server.py`** — Compatibility alias for :mod:`openprogram_server.server`

## Sub-packages

- **`routes/`** — Compatibility path for FastAPI route registrations

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
