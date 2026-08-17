# `openprogram/acp/`

> ACP (Agent Client Protocol) server — editors drive OpenProgram sessions.

## Overview

ACP is the editor-agnostic standard (agentclientprotocol.com, protocol
version 1) for an editor like Zed to drive an external agent over JSON-RPC
2.0 on stdio. This package is the *agent* side: the editor is the client.

Design: docs/reference/design/integrations/editor-integration.md.
User-facing setup: docs/interfaces/acp.md.

The adapter sits on ``agent.dispatcher.process_user_turn`` — the same
non-HTTP entry point the webui thread, sub-agents and the job runner use.
Nothing about tool gating, authority or persistence is re-implemented here;
the ACP layer only translates protocol shapes in both directions.

## Files in this directory

- **`jsonrpc.py`** — JSON-RPC 2.0 over newline-delimited JSON on a byte stream
- **`server.py`** — ACP agent-side server: protocol methods mapped onto the turn dispatcher

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
