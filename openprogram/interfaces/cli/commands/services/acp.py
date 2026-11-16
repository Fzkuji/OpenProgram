"""`openprogram acp` — serve ACP on stdio for an editor like Zed."""
from __future__ import annotations


def _cmd_acp(agent: str = "main", permission: str | None = None) -> int:
    from openprogram.interfaces.acp import serve_stdio

    return serve_stdio(agent_id=agent, permission_mode=permission)
