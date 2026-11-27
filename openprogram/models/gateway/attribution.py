"""Which agent a gateway request came from, for the usage ledger.

After yetone/magpie ``internal/gateway/agentpath.go``, ``gateway.go``
``agentOf`` and ``usage.AgentOf`` (MIT License, Copyright (c) yetone), in
this order:

1. the path: an agent that can be given neither a key of its own nor a
   User-Agent is given ``/gateway/agent/<id>/…`` as its address; the prefix
   is taken off (:func:`take_agent_path`) and the rest is served as it is.
   A path an agent asks for at its endpoint host's root, where its address
   can't reach (``Agent.root_paths``), is first moved under its own
   (:func:`root_path`);
2. the key: one made by ``keys.token_for`` names its agent;
3. the User-Agent, matched against the registry's prefixes;
4. else the protocol the request speaks.
"""
from __future__ import annotations

import functools
from typing import Any, Optional

AGENT_PATH = "/gateway/agent/"
_STATE_KEY = "gateway_agent"
#: The scope state naming the agent whose own key its path carried.
PATH_KEY_STATE = "gateway_path_key"


def take_agent_path(scope: dict[str, Any]) -> Optional[str]:
    """Rewrite ``/gateway/agent/<id>/rest`` to ``/gateway/rest`` and keep the
    agent in the scope's state; a path naming no known agent is left as it is."""
    path = str(scope.get("path") or "")
    if not path.startswith(AGENT_PATH):
        return None
    agent_id, _, rest = path[len(AGENT_PATH):].partition("/")
    from .agents import find
    from .keys import agent_of_path_key
    keyed = agent_of_path_key(agent_id)
    if keyed:
        # ``/gateway/agent/<id>.<mac>/…``: the agent's own key in its address
        # (``Agent.path_keyed``), which lets it in whatever bearer it sends
        scope.setdefault("state", {})[PATH_KEY_STATE] = keyed
        segment, agent_id = agent_id, keyed
    else:
        segment = agent_id
    agent = find(agent_id) if agent_id else None
    if agent is None or agent.id != agent_id and agent_id not in agent.aliases:
        return None
    scope["path"] = "/gateway/" + rest
    raw = scope.get("raw_path")
    if isinstance(raw, (bytes, bytearray)):
        prefix = (AGENT_PATH + segment).encode("latin-1")
        scope["raw_path"] = b"/gateway" + bytes(raw)[len(prefix):] if bytes(raw).startswith(prefix) else scope["path"].encode("latin-1")
    scope.setdefault("state", {})[_STATE_KEY] = agent.id
    return agent.id


def root_path(scope: dict[str, Any]) -> Optional[str]:
    """Rewrite a path an agent asks for at its endpoint host's root (an
    agent's ``root_paths``: Muse Code's ``/muse-code/models``, magpie
    ``museModels``) to that agent's own path; the agent, else ``None``."""
    path = str(scope.get("path") or "")
    agent_id = root_paths().get(path) if path and not path.startswith("/gateway/") else None
    if agent_id:
        scope["path"] = f"{AGENT_PATH}{agent_id}{path}"
        scope["raw_path"] = scope["path"].encode("latin-1")
    return agent_id


@functools.lru_cache(maxsize=1)
def root_paths() -> dict[str, str]:
    """Each agent's root path and its id, read once: every page the server
    serves outside ``/api/`` and ``/gateway/`` is checked against it."""
    from .agents import all_agents
    return {path: agent.id for agent in all_agents() for path in agent.root_paths}


def _presented_keys(headers: Any, query: Any = None) -> list[str]:
    keys = []
    authorization = headers.get("authorization") or ""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() == "bearer" and token:
        keys.append(token.strip())
    for name in ("x-api-key", "x-goog-api-key"):
        if headers.get(name):
            keys.append(headers[name].strip())
    if query is not None and query.get("key"):
        keys.append(query["key"])
    return keys


def agent_of(request: Any, protocol: str) -> str:
    """The usage label of a request: its agent's id, else the protocol."""
    state = getattr(request, "scope", {}).get("state") or {}
    if state.get(_STATE_KEY):
        return state[_STATE_KEY]
    from .agents import all_agents, agent_of_ua, find
    from .keys import agent_of_key
    agents = all_agents()
    for key in _presented_keys(request.headers, getattr(request, "query_params", None)):
        named = agent_of_key(key)
        if named:
            agent = find(named, agents)
            return agent.id if agent else named
    from .science import SCIENCE_ID, is_science
    if is_science(request.headers) and find(SCIENCE_ID, agents) is not None:
        # Claude Science's client sends an Anthropic SDK's User-Agent,
        # nothing of the app's own: its surface header says it
        return SCIENCE_ID
    by_ua = agent_of_ua(request.headers.get("user-agent") or "", agents)
    return by_ua or protocol
