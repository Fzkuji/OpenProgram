# `openprogram/runtime/harnesses/`

> Outside coding agents that run a conversation's turns (Codex now; Claude Code and ACP agents next).

## Overview

A conversation whose model belongs to a harness provider (``codex-harness``)
hands each turn to that agent's engine instead of OpenProgram's agent loop:
the agent makes the model calls and runs the tools, OpenProgram streams what
it does, asks the person its approvals and keeps the record.

## Files in this directory

- **`handoff.py`** — Which harness thread a turn continues, and the turns that thread has not seen
- **`models.py`** — The outside agents installed on this computer, their sign-in and models (``GET /api/harnesses``)

## Sub-packages

- **`codex/`** — Codex (OpenAI's coding agent) driven through ``codex app-server``

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/generators/gen_dir_readmes.py` from the repo root to refresh._
