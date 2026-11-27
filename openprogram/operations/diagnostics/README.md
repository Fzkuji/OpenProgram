# `openprogram/operations/diagnostics/`

> Diagnostics shared by every entry point.

## Overview

``checks`` holds the installation health checks behind ``openprogram
doctor`` and ``/api/doctor``. The scrubber that keeps credential-shaped
values out of a log, an error message or a support bundle is
``openprogram.models.auth.redaction``, with the credentials,
because the layers below operations use it too.

## Files in this directory

- **`checks.py`** — Installation health checks

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/generators/gen_dir_readmes.py` from the repo root to refresh._
