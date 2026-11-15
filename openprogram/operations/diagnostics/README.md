# `openprogram/operations/diagnostics/`

> Diagnostics shared by every entry point.

## Overview

``checks`` holds the installation health checks behind ``openprogram
doctor`` and ``/api/doctor``; ``redaction`` scrubs credential-shaped values
from text and JSON before they reach a log, an error message or a support
bundle.

## Files in this directory

- **`checks.py`** — Installation health checks
- **`redaction.py`** — Scrub credential-shaped values out of text and JSON-like data

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
