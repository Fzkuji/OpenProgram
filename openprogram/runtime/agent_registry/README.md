# `openprogram/runtime/agent_registry/`

> Multi-agent support — one OpenProgram install can host many agents.

## Overview

An *agent* (in OpenClaw's sense) is the full scope of a single
"persona": its own model pick, reasoning effort, system prompt,
skills + tools allowlists, credentials, and session store. Multiple
agents live side-by-side inside the same process; inbound messages
from channel bots are routed to the appropriate agent through
bindings.

Public API (module-level convenience):

    from openprogram.runtime import agent_registry as A

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

## Files in this directory

- **`builtin_agents.py`** — The built-in Agents: the saved Agents every instance starts with
- **`delegation.py`** — Which saved Agents other agents may start, and how
- **`manager.py`** — Agent registry + on-disk storage
- **`markdown_imports.py`** — Agents defined as Markdown files by other tools, read as read-only Agents
- **`runtime_registry.py`** — Per-agent runtime cache
- **`session_aliases.py`** — Session aliases
- **`workspace.py`** — Retire the per-agent workspace persona files

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/generators/gen_dir_readmes.py` from the repo root to refresh._
