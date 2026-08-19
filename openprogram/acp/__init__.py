"""ACP (Agent Client Protocol) server — editors drive OpenProgram sessions.

ACP is the editor-agnostic standard (agentclientprotocol.com, protocol
version 1) for an editor like Zed to drive an external agent over JSON-RPC
2.0 on stdio. This package is the *agent* side: the editor is the client.

Design: docs/reference/design/integrations/editor-integration.md.
User-facing setup: docs/interfaces/acp.md.

The adapter sits on ``agent.dispatcher.process_user_turn`` — the same
non-HTTP entry point the webui thread, sub-agents and the job runner use.
Nothing about tool gating, authority or persistence is re-implemented here;
the ACP layer only translates protocol shapes in both directions.
"""
from openprogram.acp.server import ACPServer, PROTOCOL_VERSION, serve_stdio

__all__ = ["ACPServer", "PROTOCOL_VERSION", "serve_stdio"]
