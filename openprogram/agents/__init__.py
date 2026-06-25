"""Multi-agent support — one OpenProgram install can host many agents.

An *agent* (in OpenClaw's sense) is the full scope of a single
"persona": its own model pick, reasoning effort, system prompt,
skills + tools allowlists, credentials, and session store. Multiple
agents live side-by-side inside the same process; inbound messages
from channel bots are routed to the appropriate agent through
bindings.

Public API (module-level convenience):

    from openprogram.agents import agents as A

    A.list_all()                # -> [AgentSpec]
    A.get(agent_id)             # -> AgentSpec | None
    A.get_default()             # -> AgentSpec
    A.create(id, **kwargs)      # -> AgentSpec
    A.update(id, patch)         # -> AgentSpec
    A.delete(id)                # removes agent + its sessions
    A.set_default(id)           # flip default flag

Layout on disk:

    <state>/agents.json              # {default_id: "main", order: [...]}
    <state>/agents/<id>/agent.json   # AgentSpec
    <state>/agents/<id>/sessions/... # per-agent session store
    <state>/agents/<id>/workspace/   # optional per-agent workspace
"""
from __future__ import annotations

from openprogram.agents.manager import (
    AgentSpec,
    AgentNotFound,
    list_all,
    get,
    get_default,
    create,
    update,
    delete,
    set_default,
    agent_dir,
    sessions_dir,
    workspace_dir,
)

from openprogram.agents.runtime_registry import (
    get_runtime_for,
    invalidate,
    invalidate_all,
)

__all__ = [
    "AgentSpec",
    "AgentNotFound",
    "list_all",
    "get",
    "get_default",
    "create",
    "update",
    "delete",
    "set_default",
    "agent_dir",
    "sessions_dir",
    "workspace_dir",
    "get_runtime_for",
    "invalidate",
    "invalidate_all",
]
