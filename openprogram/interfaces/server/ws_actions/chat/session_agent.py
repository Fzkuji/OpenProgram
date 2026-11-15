"""The agent a session is bound to."""
from __future__ import annotations


def _db_agent_id(session_id: str) -> str:
    """Read agent_id from SessionStore, falling back to default."""
    from openprogram.runtime.context.session_db import default_db
    from openprogram.interfaces.server.server import _default_agent_id
    return (default_db().get_session(session_id) or {}).get("agent_id") or _default_agent_id()
