# `openprogram/webui/`

> OpenProgram Web API and static-interface host.

## Overview

This package exposes the worker's HTTP and WebSocket surface and serves the
prebuilt Next.js export. Core runtime modules do not eagerly import the server;
the CLI and worker load it only when the Web service is requested.

Usage:
    from openprogram.webui import start_web
    start_web(port=18100)

Or from CLI:
    openprogram web
    python -m openprogram.webui

## Files in this directory

- **`__main__.py`** — Allow running the web UI with: python -m openprogram.webui
- **`_auth_routes.py`** — REST + SSE routes for auth v2
- **`_chat_helpers.py`** — Chat-input parsing
- **`_chat_routes.py`** — REST routes for ContextGit chat operations
- **`_exec_dag.py`** — Execution-DAG: reconstruction, live streaming, run-state repair
- **`_functions.py`** — Function discovery, metadata extraction, loading, and result formatting
- **`_pause_stop.py`** — Pause / resume / cancel / kill-runtime primitives used by the web UI
- **`_runtime_management.py`** — Runtime / provider management for the web UI
- **`_stream_bridge.py`** — Bridge between runtime's ``on_stream(event: dict)`` callback and the v2
- **`_thinking.py`** — Thinking / reasoning-effort picker config + runtime apply helpers
- **`frontend.py`** — Serve the Next.js static export (``apps/web/out/``) from the worker itself
- **`graph_builder.py`** — Unified graph builder for the DAG viewport
- **`messages.py`** — v2 message model + authoritative in-memory store
- **`owner_auth.py`** — Single-owner authentication and request policy for the Web server
- **`persistence.py`** — Per-session persistence
- **`server.py`** — Visualization server

## Sub-packages

- **`_execute/`** — execute_in_context
- **`_model_listing/`** — Unified provider + model listing for the webui
- **`graph_layout/`** — DAG layout pipeline
- **`routes/`** — FastAPI route registrations split out from server.py by topic
- **`static/`**
- **`ws_actions/`** — WebSocket action handlers, split out from server._handle_ws_command

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
