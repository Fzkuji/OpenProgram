"""The one masked shape every surface shows for a stored credential.

An API key, an MCP server's env value and a channel token all render the
same way, and the mask never contains enough of the value to recover it.
"""
from __future__ import annotations

_SHORT_CREDENTIAL_MASK = "•" * 8


def mask_credential(value: str) -> str:
    """Return the stable, non-retrievable credential presentation."""
    if not value:
        return ""
    visible = value[:3] + value[-4:]
    if len(value) >= 12 and all(ord(char) < 0x80 for char in visible):
        return f"{value[:3]}…{value[-4:]}"
    return _SHORT_CREDENTIAL_MASK
