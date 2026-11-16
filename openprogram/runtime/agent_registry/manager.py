"""Agent registry + on-disk storage.

Each agent is a folder under ``<state>/agents/<id>/``; the agent
configuration itself lives in ``agent.json``. A top-level
``<state>/agents.json`` holds ordering and the default-agent pointer.

Mirrors OpenClaw's ``agents.list`` / ``agents.defaults`` config
shape, simplified — we keep:

  id, name, description, revision, default, model (provider+id), thinking_effort, system_prompt,
  skills {disabled: []}, tools {disabled: []}, identity {name, mention_patterns},
  always_on {enabled, goal, wake_minutes, research, autonomy, channels,
             quiet_hours, wake_days, check_in_model, notify, daily_budget_usd},
  avatar {kind, shape, color, style, seed, file, letter, bg},
  created_at, updated_at.

Anything else the UI wants to surface can be added to the dataclass
and schema without breaking older configs (missing fields fall back
to defaults).
"""
from __future__ import annotations

import contextlib

import json
import math
import os
import re
import shutil
import tempfile
import threading
import unicodedata
from contextlib import contextmanager

from openprogram.host._platform import locking
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional
from openprogram.host._platform.filesystem import replace_file


class AgentNotFound(KeyError):
    """Raised when an agent id isn't in the registry."""


class AgentRevisionConflict(ValueError):
    """A conditional write no longer matches the saved configuration."""

    def __init__(self, current: "AgentSpec") -> None:
        super().__init__("agent configuration changed; reload before saving")
        self.current = current


class AgentPreconditionRequired(ValueError):
    """An HTTP configuration update must identify the version it edits."""


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


def validate_memory_config(raw: object, *, partial: bool = False) -> dict[str, Any]:
    """Validate stored memory settings; partial patches are merged before use."""
    if not isinstance(raw, dict):
        raise ValueError("memory must be an object")
    if set(raw) - {"mode", "read_spaces", "write_space", "required"}:
        raise ValueError("memory contains unsupported fields")
    out: dict[str, Any] = {} if partial else {
        "mode": "off", "read_spaces": ["self"],
        "write_space": "self", "required": False,
    }
    if "mode" in raw:
        mode = raw["mode"]
        if not isinstance(mode, str) or mode not in {"off", "read_only", "read_write"}:
            raise ValueError("memory.mode must be off, read_only, or read_write")
        out["mode"] = mode
    if "read_spaces" in raw:
        spaces = raw["read_spaces"]
        if not isinstance(spaces, list) or any(
            not isinstance(space, str) or space not in {"self", "legacy_global"}
            for space in spaces
        ):
            raise ValueError("memory.read_spaces must list self or legacy_global")
        out["read_spaces"] = list(dict.fromkeys(spaces))
    if "write_space" in raw:
        space = raw["write_space"]
        if not isinstance(space, str) or space not in {"self", "legacy_global"}:
            raise ValueError("memory.write_space must be self or legacy_global")
        out["write_space"] = space
    if "required" in raw:
        if not isinstance(raw["required"], bool):
            raise ValueError("memory.required must be a boolean")
        out["required"] = raw["required"]
    if out.get("mode") in {"read_only", "read_write"}:
        reads = out.get("read_spaces")
        if reads is not None and not reads:
            raise ValueError("memory requires at least one readable space")
        if out["mode"] == "read_write" and reads is not None and "write_space" in out:
            if out["write_space"] not in reads:
                raise ValueError("memory.write_space must also be readable")
    return out


ALWAYS_ON_FIELDS = ("enabled", "goal", "wake_minutes", "research", "autonomy", "channels",
                    "quiet_hours", "wake_days", "check_in_model", "notify", "daily_budget_usd")
ALWAYS_ON_GOAL_MAX = 4000
ALWAYS_ON_WAKE_RANGE = (5, 10080)
ALWAYS_ON_CHANNELS = ("telegram", "slack", "discord", "wechat")
ALWAYS_ON_CHANNELS_MAX = 10
ALWAYS_ON_DAYS = (0, 1, 2, 3, 4, 5, 6)  # Monday is 0, as datetime.weekday() counts
# Report kinds and where each may reach the user beyond the conversation.
ALWAYS_ON_REPORT_KINDS = ("finding", "approval", "done")
ALWAYS_ON_NOTIFY_ROUTES = ("desktop", "channels")
ALWAYS_ON_MODEL_MAX = 300


def default_notify() -> dict[str, dict[str, bool]]:
    return {kind: {route: True for route in ALWAYS_ON_NOTIFY_ROUTES} for kind in ALWAYS_ON_REPORT_KINDS}


def _notify(raw: object) -> dict[str, dict[str, bool]]:
    """Per report kind, whether it raises a desktop notification and goes to channels.

    Kinds and routes left out stay on."""
    if not isinstance(raw, dict) or set(raw) - set(ALWAYS_ON_REPORT_KINDS):
        raise ValueError("always_on.notify must map finding, approval and done to {desktop, channels}")
    out = default_notify()
    for kind, routes in raw.items():
        if (not isinstance(routes, dict) or set(routes) - set(ALWAYS_ON_NOTIFY_ROUTES)
                or any(not isinstance(value, bool) for value in routes.values())):
            raise ValueError(f"always_on.notify.{kind} must be {{desktop, channels}} booleans")
        out[kind].update(routes)
    return out


def _check_in_model(raw: object) -> str:
    """``""`` (the conversation's model) or ``"provider/model"``."""
    if not isinstance(raw, str) or len(raw) > ALWAYS_ON_MODEL_MAX:
        raise ValueError('always_on.check_in_model must be "" or "provider/model"')
    value = raw.strip()
    if value and ("/" not in value or not all(part.strip() for part in value.split("/", 1))):
        raise ValueError('always_on.check_in_model must be "" or "provider/model"')
    return value


def _daily_budget(raw: object) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw) or raw <= 0:
        raise ValueError("always_on.daily_budget_usd must be a number above 0, or null")
    return float(raw)


def _clock(value: object) -> str:
    """A local "HH:MM" time of day."""
    if isinstance(value, str) and len(value) == 5 and value[2] == ":":
        hours, minutes = value[:2], value[3:]
        if hours.isdigit() and minutes.isdigit() and int(hours) < 24 and int(minutes) < 60:
            return value
    raise ValueError("always_on.quiet_hours start and end must be HH:MM times")


def _quiet_hours(raw: object) -> dict[str, str] | None:
    """No scheduled wakes from ``start`` until ``end``, local time; may cross midnight."""
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != {"start", "end"}:
        raise ValueError("always_on.quiet_hours must be null or {start, end}")
    start, end = _clock(raw["start"]), _clock(raw["end"])
    if start == end:
        raise ValueError("always_on.quiet_hours start and end must differ")
    return {"start": start, "end": end}


def _wake_days(raw: object) -> list[int]:
    """The weekdays scheduled wakes may happen on."""
    if (not isinstance(raw, list) or not raw
            or any(isinstance(day, bool) or day not in ALWAYS_ON_DAYS for day in raw)):
        raise ValueError("always_on.wake_days must list at least one weekday from 0 (Monday) to 6")
    return sorted(set(raw))


def _always_on_channel_targets(raw: object) -> list[dict[str, str]]:
    """Channel conversations that receive reports: (channel, account, peer)."""
    if not isinstance(raw, list) or len(raw) > ALWAYS_ON_CHANNELS_MAX:
        raise ValueError(f"always_on.channels must be a list of at most {ALWAYS_ON_CHANNELS_MAX} targets")
    targets: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in raw:
        if not isinstance(item, dict) or set(item) - {"channel", "account_id", "peer_id", "label"}:
            raise ValueError("always_on.channels entries need channel, account_id and peer_id")
        channel, account, peer = (item.get(key) for key in ("channel", "account_id", "peer_id"))
        label = item.get("label", "")
        if channel not in ALWAYS_ON_CHANNELS:
            raise ValueError(f"always_on.channels: unsupported channel {channel!r}")
        if not all(isinstance(value, str) and value.strip() and len(value) <= 500 for value in (account, peer)):
            raise ValueError("always_on.channels entries need an account_id and a peer_id")
        if not isinstance(label, str):
            raise ValueError("always_on.channels label must be text")
        key = (channel, account.strip(), peer.strip())
        if key in seen:
            continue
        seen.add(key)
        targets.append({"channel": key[0], "account_id": key[1], "peer_id": key[2], "label": label.strip()[:200]})
    return targets


def validate_always_on(raw: object, *, partial: bool = False) -> dict[str, Any]:
    """Validate the always-on section; partial patches are merged before use."""
    if not isinstance(raw, dict):
        raise ValueError("always_on must be an object")
    if set(raw) - set(ALWAYS_ON_FIELDS):
        raise ValueError("always_on contains unsupported fields")
    out: dict[str, Any] = {} if partial else {
        "enabled": False, "goal": "", "wake_minutes": None,
        "research": True, "autonomy": "report", "channels": [],
        "quiet_hours": None, "wake_days": list(ALWAYS_ON_DAYS),
        "check_in_model": "", "notify": default_notify(), "daily_budget_usd": None,
    }
    for key in ("enabled", "research"):
        if key in raw:
            if not isinstance(raw[key], bool):
                raise ValueError(f"always_on.{key} must be a boolean")
            out[key] = raw[key]
    if "goal" in raw:
        goal = raw["goal"]
        if not isinstance(goal, str) or len(goal) > ALWAYS_ON_GOAL_MAX:
            raise ValueError(f"always_on.goal must be text of at most {ALWAYS_ON_GOAL_MAX} characters")
        out["goal"] = goal.strip()
    if "wake_minutes" in raw:
        # null: no fixed interval; the agent schedules its own wakes.
        minutes = raw["wake_minutes"]
        low, high = ALWAYS_ON_WAKE_RANGE
        if minutes is not None and (isinstance(minutes, bool) or not isinstance(minutes, int)
                                    or not low <= minutes <= high):
            raise ValueError(f"always_on.wake_minutes must be null or between {low} and {high}")
        out["wake_minutes"] = minutes
    if "autonomy" in raw:
        if raw["autonomy"] not in {"report", "act"}:
            raise ValueError("always_on.autonomy must be report or act")
        out["autonomy"] = raw["autonomy"]
    if "channels" in raw:
        out["channels"] = _always_on_channel_targets(raw["channels"])
    if "quiet_hours" in raw:
        out["quiet_hours"] = _quiet_hours(raw["quiet_hours"])
    if "wake_days" in raw:
        out["wake_days"] = _wake_days(raw["wake_days"])
    if "check_in_model" in raw:
        out["check_in_model"] = _check_in_model(raw["check_in_model"])
    if "notify" in raw:
        out["notify"] = _notify(raw["notify"])
    if "daily_budget_usd" in raw:
        out["daily_budget_usd"] = _daily_budget(raw["daily_budget_usd"])
    return out


AVATAR_KINDS = ("bot", "dicebear", "upload", "letter")
AVATAR_FIELDS = ("kind", "shape", "color", "style", "seed", "file", "letter", "bg")
# An uploaded picture travels in every Agent listing, so it stays small:
# about 256 KB of image once base64 has grown it by a third.
AVATAR_FILE_MAX = 350_000
_AVATAR_SLUG = re.compile(r"^[a-z][A-Za-z0-9-]{0,39}$")
_AVATAR_TEXT_MAX = {"seed": 120, "letter": 2, "bg": 40}


def validate_avatar(raw: object) -> dict[str, str]:
    """Validate how an Agent is pictured. An empty value means the picture
    the interface derives from the Agent's id.

    ``kind`` chooses the picture: ``bot`` is a character (``shape`` and
    ``color`` name entries of the interface's own tables), ``dicebear`` a
    generated one (``style`` and ``seed``), ``upload`` an image data URL in
    ``file``, ``letter`` an initial on a ``bg`` colour.
    """
    if raw is None or raw == {}:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("avatar must be an object")
    if set(raw) - set(AVATAR_FIELDS):
        raise ValueError("avatar contains unsupported fields")
    kind = raw.get("kind")
    if kind not in AVATAR_KINDS:
        raise ValueError("avatar.kind must be one of " + ", ".join(AVATAR_KINDS))
    out: dict[str, str] = {"kind": kind}
    for key in ("shape", "color", "style"):
        value = raw.get(key)
        if value is None or value == "":
            continue
        if not isinstance(value, str) or not _AVATAR_SLUG.match(value):
            raise ValueError(f"avatar.{key} must be a short name")
        out[key] = value
    for key, limit in _AVATAR_TEXT_MAX.items():
        value = raw.get(key)
        if value is None or value == "":
            continue
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError(f"avatar.{key} must be text of at most {limit} characters")
        out[key] = value
    file = raw.get("file")
    if file is not None and file != "":
        if not isinstance(file, str) or not file.startswith("data:image/"):
            raise ValueError("avatar.file must be an image data URL")
        if len(file) > AVATAR_FILE_MAX:
            raise ValueError(f"avatar.file must be at most {AVATAR_FILE_MAX} characters")
        out["file"] = file
    return out


def _stored_avatar(raw: object) -> dict[str, str]:
    """The avatar of a stored Agent. One that no longer validates falls back
    to the derived picture, so the Agent itself still loads."""
    try:
        return validate_avatar(raw)
    except ValueError:
        return {}


DELEGATION_VALUES = ("", "on", "off")
ISOLATION_VALUES = ("", "worktree", "none")


def _choice(raw: object, allowed: tuple[str, ...], key: str) -> str:
    value = "" if raw is None else raw
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{key} must be one of " + ", ".join(repr(v) for v in allowed))
    return value


def validate_permissions(raw: object) -> dict[str, Any]:
    """An Agent's ``permissions`` block, keeping only the keys it sets.

    Each key can only narrow what the agent that starts this one allows
    (``runtime/permissions/delegation.py``): ``mode`` (a permission mode),
    ``allow``/``deny``/``ask`` (rule strings), ``additional_directories``
    (folders) and ``sandbox`` (true turns it on). An empty block, the
    default, narrows nothing.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("permissions must be an object")
    from openprogram.runtime.permissions.delegation import OWN_KEYS
    from openprogram.runtime.permissions.modes import normalize_permission
    unknown = set(raw) - set(OWN_KEYS)
    if unknown:
        raise ValueError("permissions has unknown keys: " + ", ".join(sorted(unknown)))
    out: dict[str, Any] = {}
    if raw.get("mode") not in (None, ""):
        mode = normalize_permission(raw["mode"])
        if mode is None:
            raise ValueError("permissions.mode must be one of plan, ask, acceptEdits, auto, bypass")
        out["mode"] = mode
    for key in ("allow", "deny", "ask", "additional_directories"):
        if key not in raw or raw[key] is None:
            continue
        value = raw[key]
        if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
            raise ValueError(f"permissions.{key} must be a list of strings")
        out[key] = list(dict.fromkeys(item.strip() for item in value))
    if raw.get("sandbox") is not None:
        if not isinstance(raw["sandbox"], bool):
            raise ValueError("permissions.sandbox must be true or false")
        out["sandbox"] = raw["sandbox"]
    return out


# The fields that make up the agent tool's roster of Agents.
_ROSTER_FIELDS = ("name", "description", "delegation", "tools")


def _roster_changed(before: "AgentSpec | None", after: "AgentSpec | None") -> None:
    """The agent tool's description lists Agents by name and description, so
    an edit to those changes every conversation's prompt prefix once; say
    so, so the cache report counts it as intended."""
    if before is not None and after is not None and all(
        getattr(before, f) == getattr(after, f) for f in _ROSTER_FIELDS
    ):
        return
    with contextlib.suppress(Exception):
        from openprogram.models.usage.prefix_fingerprint import expect_break_everywhere
        expect_break_everywhere("agent_roster")


@dataclass
class AgentSpec:
    """One agent record. Serialises round-trip to ``agent.json``."""
    id: str
    name: str = ""
    default: bool = False
    model: AgentModelRef = field(default_factory=AgentModelRef)
    thinking_effort: str = "medium"
    system_prompt: str = ""
    # Unified extension gating. Each block has ``disabled`` / ``allowed``
    # name-pattern lists (fnmatch syntax — exact names are the trivial
    # case). Skills carry an extra ``categories`` filter; MCP carries
    # ``required`` (must-have server patterns; agent unavailable if
    # missing).
    skills: dict[str, Any] = field(default_factory=lambda: {
        "disabled": [], "allowed": [], "categories": [],
    })
    tools: dict[str, Any] = field(default_factory=lambda: {
        "mode": "automatic",
    })
    mcp: dict[str, Any] = field(default_factory=lambda: {
        "disabled": [], "allowed": [], "required": [],
    })
    identity: AgentIdentity = field(default_factory=AgentIdentity)
    # Session routing policy (see agents/context_engine.py and
    # channels/inbound/_conversation.py). Values mirror OpenClaw's dmScope:
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
    description: str = ""
    revision: int = 1
    memory: dict[str, Any] = field(default_factory=lambda: validate_memory_config({}))
    memory_policy_epoch: int = 1
    always_on: dict[str, Any] = field(default_factory=lambda: validate_always_on({}))
    # Hold what the Agent sends for the user (an email, a message, a post
    # through a connected service) as a draft the user approves, one call at
    # a time, in every permission mode. See permissions/policy.py.
    hold_outbound: bool = False
    # How the interface pictures this Agent; empty means the picture derived
    # from its id. See validate_avatar.
    avatar: dict[str, str] = field(default_factory=dict)
    # Whether other agents may start it with agent(agent=…): "on", "off",
    # or "" for the default (see agent_registry/delegation.py). Listed
    # Agents appear in the agent tool's description.
    delegation: str = ""
    # The isolation it gets when another agent starts it: "worktree",
    # "none", or "" for the default rule (agent_registry/delegation.py).
    isolation: str = ""
    # What it may do when another agent starts it, narrowing that agent's
    # permission (validate_permissions); empty narrows nothing.
    permissions: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AgentSpec":
        model = raw.get("model") or {}
        identity = raw.get("identity") or {}
        memory = validate_memory_config(raw["memory"] if "memory" in raw else {
            "mode": "read_write", "read_spaces": ["legacy_global"],
            "write_space": "legacy_global", "required": False,
        })
        memory_epoch = raw.get("memory_policy_epoch", 1)
        if isinstance(memory_epoch, bool) or not isinstance(memory_epoch, int) or memory_epoch < 1:
            raise ValueError("memory_policy_epoch must be a positive integer")
        return cls(
            id=str(raw.get("id") or "").strip(),
            name=str(raw.get("name") or ""),
            description=str(raw.get("description") or ""),
            revision=int(raw.get("revision") or 1),
            memory=memory,
            memory_policy_epoch=memory_epoch,
            always_on=validate_always_on(raw.get("always_on") or {}),
            hold_outbound=raw.get("hold_outbound") is True,
            avatar=_stored_avatar(raw.get("avatar")),
            delegation=_choice(raw.get("delegation"), DELEGATION_VALUES, "delegation"),
            isolation=_choice(raw.get("isolation"), ISOLATION_VALUES, "isolation"),
            permissions=validate_permissions(raw.get("permissions")),
            default=bool(raw.get("default") or False),
            model=AgentModelRef(
                provider=str(model.get("provider") or ""),
                id=str(model.get("id") or ""),
            ),
            thinking_effort=str(
                raw["thinking_effort"]
                if raw.get("thinking_effort") is not None else "medium"
            ),
            system_prompt=str(raw.get("system_prompt") or ""),
            skills=dict(raw.get("skills") or {
                "disabled": [], "allowed": [], "categories": [],
            }),
            tools=dict(raw.get("tools") or {"mode": "automatic"}),
            mcp=dict(raw.get("mcp") or {"disabled": [], "allowed": [], "required": []}),
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
    from openprogram.state.paths import get_state_dir
    root = get_state_dir()
    (root / "agents").mkdir(parents=True, exist_ok=True)
    return root


def agent_dir(agent_id: str) -> Path:
    return _state_root() / "agents" / agent_id


def sessions_dir(agent_id: str) -> Path:
    d = agent_dir(agent_id) / "sessions"
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
_writer_state = threading.local()


def _file_lock(path: Path):
    """Yield a locking.LOCK_EX'd file handle on ``path``. Caller must
    close it to release the lock. Used for cross-process synchronization
    around the single agents.json and each agent.json."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    fh = open(lock_path, "a+", encoding="utf-8")
    try:
        locking.flock(fh.fileno(), locking.LOCK_EX)
    except BaseException:
        fh.close()
        raise
    return fh


@contextmanager
def configuration_lock():
    """Hold the configuration writer lock, including across processes.

    Nested manager operations in the same thread reuse the file lock.
    This also lets a short policy-checked commit keep its check atomic
    with respect to configuration changes.
    """
    with _lock:
        if getattr(_writer_state, "held", False):
            yield
            return
        with _file_lock(_index_file()):
            _writer_state.held = True
            try:
                yield
            finally:
                _writer_state.held = False


# ---------------------------------------------------------------------------
# Low-level I/O
# ---------------------------------------------------------------------------

def _read_index() -> dict[str, Any]:
    path = _index_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"v": 1, "default_id": "", "order": [], "builtins": []}
    data.setdefault("v", 1)
    data.setdefault("default_id", "")
    data.setdefault("order", [])
    data.setdefault("builtins", [])
    return data


def installed_builtins() -> set[str]:
    """Ids of the built-in Agents this instance has installed at some point.

    A built-in stays on this list after the user deletes it, which is how
    ``builtin_agents.create_builtin_agents`` knows not to recreate it.
    """
    with _lock:
        return {str(agent_id) for agent_id in _read_index().get("builtins") or []}


def record_builtins(agent_ids) -> None:
    """Remember ``agent_ids`` as installed built-ins (idempotent)."""
    with _lock:
        idx = _read_index()
        known = {str(agent_id) for agent_id in idx.get("builtins") or []}
        merged = sorted(known | {str(agent_id) for agent_id in agent_ids})
        if merged != sorted(known):
            idx["builtins"] = merged
            _write_index(idx)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            tmp = Path(stream.name)
            json.dump(data, stream, indent=2, sort_keys=True)
        replace_file(tmp, path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def _write_index(data: dict[str, Any]) -> None:
    _write_json(_index_file(), data)


def _read_agent(agent_id: str) -> Optional[AgentSpec]:
    path = _agent_file(agent_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    spec = AgentSpec.from_dict(raw)
    spec.id = agent_id  # directory name wins if they drift
    return spec


def _write_agent(spec: AgentSpec) -> None:
    path = _agent_file(spec.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = _read_agent(spec.id)
    spec.revision = previous.revision + 1 if previous is not None else 1
    now = time.time()
    spec.updated_at = max(now, math.nextafter(previous.updated_at, math.inf)) if previous else now
    if not spec.created_at:
        spec.created_at = spec.updated_at
    _write_json(path, spec.to_dict())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_all() -> list[AgentSpec]:
    """Every agent, ordered by the registry's ``order`` list.

    Falls back to disk scan if the index is missing entries (defensive
    — e.g. if a user copies an agent folder in by hand).
    """
    with _lock:
        specs = (_read_agent(agent_id) for agent_id in list_ids())
        return [spec for spec in specs if spec is not None]


def list_ids() -> list[str]:
    """Every agent's id, in ``list_all`` order, without opening its file.

    For a loop that must go on past one agent whose file cannot be read:
    it loads each with ``get`` and handles that agent's failure itself.
    """
    with _lock:
        known = list(_read_index().get("order") or [])
        agents_root = _state_root() / "agents"
        entries = agents_root.iterdir() if agents_root.is_dir() else []
        return known + [entry.name for entry in entries
                        if entry.is_dir() and entry.name not in known]


def get(agent_id: str) -> Optional[AgentSpec]:
    """The saved Agent, or the read-only Agent a Markdown file defines
    (``md:<name>``, ``markdown_imports``). Writes read saved records only,
    so an imported Agent cannot be changed here."""
    if isinstance(agent_id, str) and agent_id.startswith("md:"):
        from openprogram.runtime.agent_registry.markdown_imports import get as imported
        return imported(agent_id)
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


def new_record(agent_id: str, **fields: Any) -> AgentSpec:
    """The record ``create`` starts from, built in memory and saved nowhere."""
    return AgentSpec(id=agent_id, skills={"disabled": []}, tools={"mode": "automatic"}, **fields)


def create(
    agent_id: str,
    *,
    name: str = "",
    provider: str = "",
    model_id: str = "",
    thinking_effort: str | None = None,
    system_prompt: str = "",
    identity_name: str = "",
    mention_patterns: Optional[list[str]] = None,
    make_default: bool = False,
    configuration: Optional[dict[str, Any]] = None,
) -> AgentSpec:
    """Create a new agent. ``agent_id`` must match
    ``^[a-z][a-z0-9_-]{0,39}$``. Raises ``ValueError`` on bad id or
    if one already exists with that id.

    ``configuration`` is an ``update`` patch, with a complete tool policy,
    that is part of the first save: nothing is written if it is invalid,
    and no half-configured record can be left behind.
    """
    if not _VALID_ID.match(agent_id):
        raise ValueError(
            f"Invalid agent id {agent_id!r} — must start with a letter "
            f"and contain only [a-z0-9_-], ≤40 chars."
        )
    with configuration_lock():
        if _read_agent(agent_id) is not None:
            raise ValueError(f"Agent {agent_id!r} already exists.")
        idx = _read_index()
        now = time.time()
        spec = new_record(
            agent_id,
            name=name or agent_id.replace("_", " ").replace("-", " ").title(),
            default=make_default or not idx.get("default_id"),
            model=AgentModelRef(provider=provider, id=model_id),
            thinking_effort=thinking_effort if thinking_effort is not None else "medium",
            system_prompt=system_prompt,
            identity=AgentIdentity(
                name=identity_name or name or agent_id,
                mention_patterns=list(mention_patterns or []),
            ),
            created_at=now,
            updated_at=now,
        )
        if configuration:
            spec = merge_patch(spec, configuration, replace_tool_policy=True)
        agent_dir(agent_id).mkdir(parents=True, exist_ok=True)
        sessions_dir(agent_id)
        _write_agent(spec)
        _roster_changed(None, spec)

        order = list(idx.get("order") or [])
        if agent_id not in order:
            order.append(agent_id)
        idx["order"] = order
        if make_default or not idx.get("default_id"):
            idx["default_id"] = agent_id
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


def create_from_name(name: str, *, provider: str = "",
                     model_id: str = "", thinking_effort: str | None = None) -> AgentSpec:
    """Create an agent whose internal id is derived from its display name."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    if not base or not base[0].isalpha():
        base = f"agent-{base}".rstrip("-")
    base = base[:40].rstrip("-") or "agent"
    agent_id = base
    suffix = 2
    while True:
        try:
            return create(agent_id, name=name,
                          provider=provider, model_id=model_id,
                          thinking_effort=thinking_effort)
        except ValueError:
            if _read_agent(agent_id) is None:
                raise
            tail = f"-{suffix}"
            agent_id = f"{base[:40 - len(tail)].rstrip('-')}{tail}"
            suffix += 1


def duplicate(agent_id: str, *, target_id: str = "", name: str = "") -> AgentSpec:
    """Copy one Agent's configuration without copying sessions."""
    if target_id and not _VALID_ID.match(target_id):
        raise ValueError(
            f"Invalid agent id {target_id!r} — must start with a letter "
            f"and contain only [a-z0-9_-], ≤40 chars."
        )
    with configuration_lock():
        # An Agent from a Markdown file is copied into a saved one to edit it.
        source = _read_agent(agent_id) if not agent_id.startswith("md:") else get(agent_id)
        if source is None:
            raise AgentNotFound(agent_id)
        if target_id:
            candidate = target_id
            if _read_agent(candidate) is not None or agent_dir(candidate).exists():
                raise ValueError(f"Agent {candidate!r} already exists.")
        else:
            ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
            base = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
            if not base or not base[0].isalpha():
                base = f"agent-{base}".rstrip("-")
            base = base[:40].rstrip("-") or "agent"
            candidate = base
            suffix = 2
            while _read_agent(candidate) is not None or agent_dir(candidate).exists():
                tail = f"-{suffix}"
                candidate = f"{base[:40 - len(tail)].rstrip('-')}{tail}"
                suffix += 1

        now = time.time()
        raw = source.to_dict()
        raw.update({
            "id": candidate,
            "name": name or candidate.replace("_", " ").replace("-", " ").title(),
            "default": False,
            "created_at": now,
            "updated_at": now,
            "memory_policy_epoch": 1,
            # A copy never starts waking on its own.
            "always_on": {**(raw.get("always_on") or {}), "enabled": False},
        })
        spec = AgentSpec.from_dict(raw)
        folder = agent_dir(candidate)
        folder_created = False
        try:
            folder.mkdir(parents=True, exist_ok=False)
            folder_created = True
            sessions_dir(candidate)
            _write_agent(spec)
            idx = _read_index()
            order = list(idx.get("order") or [])
            order.append(candidate)
            idx["order"] = order
            _write_index(idx)
        except Exception:
            if folder_created:
                shutil.rmtree(folder, ignore_errors=True)
            raise
        return spec


def merge_patch(spec: AgentSpec, patch: dict[str, Any], *, replace_tool_policy: bool = False) -> AgentSpec:
    """``spec`` with ``patch`` merged in and validated; nothing is saved."""
    raw = spec.to_dict()
    _deep_merge(raw, patch)
    if replace_tool_policy and "tools" in patch:
        raw["tools"] = dict(patch["tools"])
    if "avatar" in patch:
        # One picture replaces another whole: a character keeps nothing
        # of the uploaded image it replaced.
        raw["avatar"] = validate_avatar(patch["avatar"])
    if "permissions" in patch:
        # The block is replaced whole, so a key left out stops narrowing.
        raw["permissions"] = validate_permissions(patch["permissions"])
    raw["id"] = spec.id  # can't rename via update
    return AgentSpec.from_dict(raw)


def update(
    agent_id: str,
    patch: dict[str, Any],
    *,
    expected_revision: int | None = None,
    expected_updated_at: float | None = None,
    require_precondition: bool = False,
    replace_tool_policy: bool = False,
) -> AgentSpec:
    """Merge ``patch`` into an existing agent's spec and save.

    The patch uses the same shape as ``AgentSpec.to_dict()``. Fields
    not mentioned are preserved; nested dicts (model / identity / etc.)
    are merged recursively, not replaced. HTTP callers replace a complete
    tool policy and require a revision (or legacy timestamp). Comparison
    and the complete write share the cross-process registry writer lock.
    """
    if "memory_policy_epoch" in patch:
        raise ValueError("memory_policy_epoch is managed by configuration updates")
    with configuration_lock():
        spec = _read_agent(agent_id)
        if spec is None:
            raise AgentNotFound(agent_id)
        if require_precondition and expected_revision is None and expected_updated_at is None:
            raise AgentPreconditionRequired("expected_revision or updated_at is required")
        if (
            expected_revision is not None and expected_revision != spec.revision
            or expected_updated_at is not None and expected_updated_at != spec.updated_at
        ):
            raise AgentRevisionConflict(spec)
        if not patch:
            return spec
        new_spec = merge_patch(spec, patch, replace_tool_policy=replace_tool_policy)
        new_spec.memory_policy_epoch = spec.memory_policy_epoch + (new_spec.memory != spec.memory)
        _write_agent(new_spec)
        _roster_changed(spec, new_spec)
        return new_spec


def delete(agent_id: str) -> None:
    """Remove an agent and everything under its folder (including
    sessions + workspace). Updates the default pointer if necessary.
    """
    with configuration_lock():
        spec = _read_agent(agent_id)
        if spec is None:
            return
        folder = agent_dir(agent_id)
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
        _roster_changed(spec, None)
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
    with configuration_lock():
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
