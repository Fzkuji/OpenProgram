# `openprogram/agents/`

> Multi-agent support — one OpenProgram install can host many agents.

## Overview

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

## Files in this directory

- **`gating.py`** — Unified extension gating
- **`manager.py`** — Agent registry + on-disk storage
- **`runtime_registry.py`** — Per-agent runtime cache
- **`session_aliases.py`** — Session aliases
- **`workspace.py`** — Per-agent workspace directory

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `_gen_dir_readmes.py` to refresh._
