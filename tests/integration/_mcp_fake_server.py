"""Minimal FastMCP server used as the subprocess target in
``test_mcp_client.py`` — exposes two trivial tools so the round-trip
covers both string and integer arg/return marshalling.
"""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("fake-server")


@mcp.tool()
def echo(message: str) -> str:
    """Echo a message back uppercased."""
    return message.upper()


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


if __name__ == "__main__":
    mcp.run(transport="stdio")
