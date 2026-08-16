# `openprogram/lsp/`

> Language-server clients for agent tools.

## Overview

Language servers answer questions a grep cannot: which call sites are
real, where a symbol is actually defined, and what the type checker
thinks of the file the agent just edited — against the working tree as
it stands, not a pre-built index.

One server per language per workspace, started on first use, cached for
the life of the process, shut down at exit. A missing server binary is
reported as ``unavailable``; the tools stay registered so the model
learns installing it is possible.

## Files in this directory

- **`client.py`** — JSON-RPC-over-stdio client for language servers

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
