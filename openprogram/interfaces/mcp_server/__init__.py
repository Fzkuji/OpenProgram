"""The authenticated MCP server: how outside programs (an editor, another agent) talk to OpenProgram over MCP."""

from openprogram.interfaces.mcp_server.server import build_server, serve, serve_stdio

__all__ = ["build_server", "serve", "serve_stdio"]
