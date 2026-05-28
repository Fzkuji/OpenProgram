# `openprogram/webui/`

> agentic_web — real-time web UI for Agentic Programming.

## Overview

Top-level package, decoupled from the `agentic` framework core. Depends on
agentic (framework) one-way; nothing in agentic imports from openprogram.webui
except via lazy imports in the CLI.

Usage:
    from openprogram.webui import start_web
    start_web(port=8109)

Or from CLI:
    agentic web
    python -m agentic_web

## Files in this directory

- **`__main__.py`** — Allow running the web UI with: python -m agentic_web
- **`_auth_routes.py`** — REST + SSE routes for auth v2
- **`_chat_helpers.py`** — Chat-input parsing and MessageStore → WebSocket bridge
- **`_chat_routes.py`** — REST routes for ContextGit chat operations
- **`_exec_dag.py`** — Execution-DAG: reconstruction, live streaming, run-state repair
- **`_functions.py`** — Function discovery, metadata extraction, loading, and result formatting
- **`_pause_stop.py`** — Pause / resume / cancel / kill-runtime primitives used by the web UI
- **`_runtime_management.py`** — Runtime / provider management for the web UI
- **`_stream_bridge.py`** — Bridge between runtime's ``on_stream(event: dict)`` callback and the v2
- **`_thinking.py`** — Thinking / reasoning-effort picker config + runtime apply helpers
- **`messages.py`** — v2 message model + authoritative in-memory store
- **`persistence.py`** — Per-session persistence
- **`server.py`** — Visualization server

## Sub-packages

- **`_execute/`** — execute_in_context
- **`_model_catalog/`** — Unified provider + model catalog for the webui
- **`graph_layout/`** — DAG layout pipeline
- **`routes/`** — FastAPI route registrations split out from server.py by topic
- **`static/`**
- **`ws_actions/`** — WebSocket action handlers, split out from server._handle_ws_command

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
