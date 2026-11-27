"""The gateway key: what a harness config authenticates to ``/gateway/`` with.

The Worker's owner token changes with every start, so a config written into
``~/.codex/config.toml`` or ``~/.claude/settings.json`` could not use it.
The gateway key is random, kept in ``<state>/gateway/key`` (owner-only) and
survives restarts. It is accepted only on ``/gateway/`` paths
(``interfaces/server/owner_auth.py``), never for the rest of the API. An
agent that cannot be told apart by its User-Agent is given a key of its own
(:func:`token_for`): its id and an HMAC of it under the gateway key, which
opens the gateway the same way, names the agent in usage and does not reveal
the gateway key.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
from pathlib import Path

#: The state directory entry the gateway keeps its key and stash in.
STATE_ENTRY = "gateway"
_PREFIX = "opgw-"
#: An agent's own key: ``opgw-agent-<id>.<mac>`` (:func:`token_for`).
_AGENT_PREFIX = "opgw-agent-"
_lock = threading.Lock()


def gateway_dir() -> Path:
    from openprogram.state.paths import get_state_dir
    return get_state_dir() / STATE_ENTRY


def _key_path() -> Path:
    return gateway_dir() / "key"


def gateway_key() -> str:
    """This profile's gateway key, created on first use."""
    path = _key_path()
    with _lock:
        try:
            value = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            value = ""
        if value.startswith(_PREFIX):
            return value
        value = _PREFIX + secrets.token_urlsafe(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
        tmp = path.with_name(f".key.{secrets.token_hex(6)}.tmp")
        fd = os.open(tmp, flags, 0o600)
        try:
            os.write(fd, value.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        from openprogram.host._platform.filesystem import replace_file, restrict_to_user
        restrict_to_user(tmp)
        replace_file(tmp, path)
        return value


def verify_gateway_key(value: str | None) -> bool:
    """Whether ``value`` is this profile's gateway key (constant time)."""
    if not value or not value.startswith(_PREFIX):
        return False
    stored = _stored()
    if stored and hmac.compare_digest(stored.encode("utf-8"), value.encode("utf-8")):
        return True
    # an agent's own key (token_for) opens the gateway as the key does
    return agent_of_key(value) is not None


def _mac(master: str, agent: str) -> str:
    return hmac.new(master.encode("utf-8"), f"agent:{agent}".encode("utf-8"), hashlib.sha256).hexdigest()[:40]


def token_for(agent: str) -> str:
    """The key an agent that says nothing of itself in its User-Agent is given.

    After magpie's ``gateway.TokenFor`` (MIT), which is the gateway key, a
    dash and the agent's id: here the id with an HMAC of it under the gateway
    key, so the agent's config does not hold the gateway key itself and its
    requests are still known as the agent's (:func:`agent_of_key`)."""
    return f"{_AGENT_PREFIX}{agent}.{_mac(gateway_key(), agent)}"


def _stored() -> str:
    try:
        return _key_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def agent_of_key(value: str | None) -> str | None:
    """The registered agent a key made by :func:`token_for` names; ``None`` for any other key."""
    if not value or not value.startswith(_AGENT_PREFIX):
        return None
    agent, dot, mac = value[len(_AGENT_PREFIX):].rpartition(".")
    stored = _stored()
    if not dot or not agent or not stored.startswith(_PREFIX):
        return None
    if not hmac.compare_digest(_mac(stored, agent).encode("utf-8"), mac.encode("utf-8")):
        return None
    from .agents import all_agents
    return agent if any(a.id == agent for a in all_agents()) else None


def path_key(key: str) -> str:
    """An agent's own key (:func:`token_for`) as a path segment: ``<id>.<mac>``."""
    return key[len(_AGENT_PREFIX):] if key.startswith(_AGENT_PREFIX) else key


def agent_of_path_key(segment: str | None) -> str | None:
    """The agent a path segment made by :func:`path_key` names; ``None`` for any other."""
    if not segment or "." not in segment:
        return None
    return agent_of_key(_AGENT_PREFIX + segment)
