"""Agent registry + on-disk storage.

Each agent is a folder under ``<state>/agents/<id>/``; the agent
configuration itself lives in ``agent.json``. A top-level
``<state>/agents.json`` holds ordering and the default-agent pointer.

Mirrors OpenClaw's ``agents.list`` / ``agents.defaults`` config
shape, simplified — we keep:

  id, name, default, model (provider+id), thinking_effort, system_prompt,
  skills {disabled: []}, tools {disabled: []}, identity {name, mention_patterns},
  created_at, updated_at.

Anything else the UI wants to surface can be added to the dataclass
and schema without breaking older configs (missing fields fall back
to defaults).
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional


class AgentNotFound(KeyError):
    """Raised when an agent id isn't in the registry."""


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

DEFAULT_AGENT_ID = "main"
_INDEX_FILE = "agents.json"
_AGENT_FILE = "agent.json"
_VALID_ID = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")


@dataclass
class AgentModelRef:
    """Which LLM backs this agent."""
    provider: str = ""          # "anthropic", "openai-codex", ...
    id: str = ""                # "claude-sonnet-4-6", "gpt-4o", ...


@dataclass
class AgentIdentity:
    """Human-facing identity — name the bot shows to channel users,
    and mention tokens that cause group messages to route to this
    agent (if it's bound to a group peer).
    """
    name: str = ""
    mention_patterns: list[str] = field(default_factory=list)


@dataclass
class AgentSpec:
    """One agent record. Serialises round-trip to ``agent.json``."""
    id: str
    name: str = ""
    default: bool = False
    model: AgentModelRef = field(default_factory=AgentModelRef)
    thinking_effort: str = "medium"
    system_prompt: str = ""
    skills: dict[str, Any] = field(default_factory=lambda: {"disabled": []})
    tools: dict[str, Any] = field(default_factory=lambda: {"disabled": []})
    identity: AgentIdentity = field(default_factory=AgentIdentity)
    # Session routing policy (see agents/context_engine.py and
    # channels/_conversation.py). Values mirror OpenClaw's dmScope:
    #   "main"                      — one shared session across all DMs
    #   "per-peer"                  — one session per sender (any channel)
    #   "per-channel-peer"          — one per (channel, sender)
    #   "per-account-channel-peer"  — one per (account, channel, sender)
    session_scope: str = "per-account-channel-peer"
    # Idle reset — start a fresh session after N minutes of silence
    # (0 = never). Default 4320 (3 days) so a long-lost contact like
    # "alice who hasn't written in a month" shows up as a new session
    # thread rather than reviving the old context. Independent of
    # daily reset (below).
    session_idle_minutes: int = 4320
    # Daily reset — if non-empty, hour-of-day in local time at which
    # stale sessions get cut (e.g. "04:00"). Empty = no daily reset.
    session_daily_reset: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AgentSpec":
        model = raw.get("model") or {}
        identity = raw.get("identity") or {}
        return cls(
            id=str(raw.get("id") or "").strip(),
            name=str(raw.get("name") or ""),
            default=bool(raw.get("default") or False),
            model=AgentModelRef(
                provider=str(model.get("provider") or ""),
                id=str(model.get("id") or ""),
            ),
            thinking_effort=str(raw.get("thinking_effort") or "medium"),
            system_prompt=str(raw.get("system_prompt") or ""),
            skills=dict(raw.get("skills") or {"disabled": []}),
            tools=dict(raw.get("tools") or {"disabled": []}),
            identity=AgentIdentity(
                name=str(identity.get("name") or ""),
                mention_patterns=list(identity.get("mention_patterns") or []),
            ),
            session_scope=str(
                raw.get("session_scope") or "per-account-channel-peer"
            ),
            session_idle_minutes=int(
                raw.get("session_idle_minutes")
                if raw.get("session_idle_minutes") is not None
                else 4320
            ),
            session_daily_reset=str(raw.get("session_daily_reset") or ""),
            created_at=float(raw.get("created_at") or 0.0),
            updated_at=float(raw.get("updated_at") or 0.0),
        )


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _state_root() -> Path:
    from openprogram.paths import get_state_dir
    root = get_state_dir()
    (root / "agents").mkdir(parents=True, exist_ok=True)
    return root


def agent_dir(agent_id: str) -> Path:
    return _state_root() / "agents" / agent_id


def sessions_dir(agent_id: str) -> Path:
    d = agent_dir(agent_id) / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def workspace_dir(agent_id: str) -> Path:
    d = agent_dir(agent_id) / "workspace"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _agent_file(agent_id: str) -> Path:
    return agent_dir(agent_id) / _AGENT_FILE


def _index_file() -> Path:
    return _state_root() / _INDEX_FILE


# ---------------------------------------------------------------------------
# Locking — one fcntl lock covers both agents.json and the per-agent files.
# Agent mutations are infrequent; a single big lock is simpler than
# per-agent locks and avoids deadlock windows.
# ---------------------------------------------------------------------------

_lock = threading.RLock()


def _file_lock(path: Path):
    """Yield an fcntl.LOCK_EX'd file handle on ``path``. Caller must
    close it to release the lock. Used for cross-process synchronization
    around the single agents.json and each agent.json."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    fh = open(lock_path, "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    except OSError:
        pass
    return fh


# ---------------------------------------------------------------------------
# Low-level I/O
# ---------------------------------------------------------------------------

def _read_index() -> dict[str, Any]:
    path = _index_file()
    if not path.exists():
        return {"v": 1, "default_id": "", "order": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"v": 1, "default_id": "", "order": []}
    data.setdefault("v", 1)
    data.setdefault("default_id", "")
    data.setdefault("order", [])
    return data


def _write_index(data: dict[str, Any]) -> None:
    path = _index_file()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True),
                   encoding="utf-8")
    os.replace(tmp, path)


def _read_agent(agent_id: str) -> Optional[AgentSpec]:
    path = _agent_file(agent_id)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    spec = AgentSpec.from_dict(raw)
    spec.id = agent_id  # directory name wins if they drift
    return spec


def _write_agent(spec: AgentSpec) -> None:
    path = _agent_file(spec.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    spec.updated_at = time.time()
    if not spec.created_at:
        spec.created_at = spec.updated_at
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(spec.to_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_all() -> list[AgentSpec]:
    """Every agent, ordered by the registry's ``order`` list.

    Falls back to disk scan if the index is missing entries (defensive
    — e.g. if a user copies an agent folder in by hand).
    """
    with _lock:
        idx = _read_index()
        known = list(idx.get("order") or [])
        agents: list[AgentSpec] = []
        for aid in known:
            spec = _read_agent(aid)
            if spec is not None:
                agents.append(spec)
        # Disk scan for stragglers
        agents_root = _state_root() / "agents"
        for entry in agents_root.iterdir() if agents_root.is_dir() else []:
            if not entry.is_dir():
                continue
            if entry.name in known:
                continue
            spec = _read_agent(entry.name)
            if spec is not None:
                agents.append(spec)
        return agents


def get(agent_id: str) -> Optional[AgentSpec]:
    with _lock:
        return _read_agent(agent_id)


def get_default() -> Optional[AgentSpec]:
    """The agent marked as default. Returns ``None`` iff no agents
    exist yet (fresh install); callers should either run setup or
    return an error to the user.
    """
    with _lock:
        idx = _read_index()
        default_id = idx.get("default_id") or ""
        if default_id:
            spec = _read_agent(default_id)
            if spec is not None:
                return spec
        # Try DEFAULT_AGENT_ID, then first known agent.
        spec = _read_agent(DEFAULT_AGENT_ID)
        if spec is not None:
            return spec
        any_list = list_all()
        return any_list[0] if any_list else None


def create(
    agent_id: str,
    *,
    name: str = "",
    provider: str = "",
    model_id: str = "",
    thinking_effort: str = "medium",
    system_prompt: str = "",
    identity_name: str = "",
    mention_patterns: Optional[list[str]] = None,
    make_default: bool = False,
) -> AgentSpec:
    """Create a new agent. ``agent_id`` must match
    ``^[a-z][a-z0-9_-]{0,39}$``. Raises ``ValueError`` on bad id or
    if one already exists with that id.
    """
    if not _VALID_ID.match(agent_id):
        raise ValueError(
            f"Invalid agent id {agent_id!r} — must start with a letter "
            f"and contain only [a-z0-9_-], ≤40 chars."
        )
    with _lock:
        if _read_agent(agent_id) is not None:
            raise ValueError(f"Agent {agent_id!r} already exists.")
        now = time.time()
        spec = AgentSpec(
            id=agent_id,
            name=name or agent_id.replace("_", " ").replace("-", " ").title(),
            default=False,
            model=AgentModelRef(provider=provider, id=model_id),
            thinking_effort=thinking_effort,
            system_prompt=system_prompt,
            skills={"disabled": []},
            tools={"disabled": []},
            identity=AgentIdentity(
                name=identity_name or name or agent_id,
                mention_patterns=list(mention_patterns or []),
            ),
            created_at=now,
            updated_at=now,
        )
        agent_dir(agent_id).mkdir(parents=True, exist_ok=True)
        sessions_dir(agent_id)
        workspace_dir(agent_id)
        # Seed AGENTS.md / SOUL.md / USER.md placeholders so the
        # persona pipeline has something to load on the first turn.
        _write_agent(spec)
        try:
            from openprogram.agents.workspace import bootstrap as _ws_bootstrap
            _ws_bootstrap(agent_id)
        except Exception:
            pass

        idx = _read_index()
        order = list(idx.get("order") or [])
        if agent_id not in order:
            order.append(agent_id)
        idx["order"] = order
        if make_default or not idx.get("default_id"):
            idx["default_id"] = agent_id
            spec.default = True
            _write_agent(spec)
            # Ensure only this agent has default=True
            for other_id in order:
                if other_id == agent_id:
                    continue
                other = _read_agent(other_id)
                if other and other.default:
                    other.default = False
                    _write_agent(other)
        _write_index(idx)
        return spec


def update(agent_id: str, patch: dict[str, Any]) -> AgentSpec:
    """Merge ``patch`` into an existing agent's spec and save.

    The patch uses the same shape as ``AgentSpec.to_dict()``. Fields
    not mentioned are preserved; nested dicts (model / identity / etc.)
    are merged shallowly, not replaced.
    """
    with _lock:
        spec = _read_agent(agent_id)
        if spec is None:
            raise AgentNotFound(agent_id)
        raw = spec.to_dict()
        _deep_merge(raw, patch)
        raw["id"] = agent_id  # can't rename via update
        new_spec = AgentSpec.from_dict(raw)
        _write_agent(new_spec)
        return new_spec


def delete(agent_id: str) -> None:
    """Remove an agent and everything under its folder (including
    sessions + workspace). Updates the default pointer if necessary.
    """
    with _lock:
        spec = _read_agent(agent_id)
        if spec is None:
            return
        folder = agent_dir(agent_id)
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
        idx = _read_index()
        order = [a for a in (idx.get("order") or []) if a != agent_id]
        idx["order"] = order
        if idx.get("default_id") == agent_id:
            idx["default_id"] = order[0] if order else ""
            if order:
                next_spec = _read_agent(order[0])
                if next_spec is not None:
                    next_spec.default = True
                    _write_agent(next_spec)
        _write_index(idx)


def set_default(agent_id: str) -> AgentSpec:
    """Mark ``agent_id`` as the default agent; clear ``default=True``
    on all others. Raises ``AgentNotFound`` if the id isn't known.
    """
    with _lock:
        spec = _read_agent(agent_id)
        if spec is None:
            raise AgentNotFound(agent_id)
        idx = _read_index()
        idx["default_id"] = agent_id
        _write_index(idx)
        for other in list_all():
            want_default = (other.id == agent_id)
            if other.default != want_default:
                other.default = want_default
                _write_agent(other)
        return get(agent_id) or spec


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> None:
    """Mutate ``base`` in place: dicts merge, everything else replaces."""
    for k, v in patch.items():
        if (isinstance(v, dict) and isinstance(base.get(k), dict)):
            _deep_merge(base[k], v)
        else:
            base[k] = v
