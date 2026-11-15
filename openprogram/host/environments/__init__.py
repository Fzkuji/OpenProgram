"""Execution environments: where a session's commands and files live.

An environment is a named place a conversation runs in. ``local`` is
built in and always present: commands run on this machine and file
tools act on its disk. Users add more in ``config.json``::

    "environments": {
      "default": "local",
      "items": [
        {"id": "devbox", "name": "devbox", "kind": "ssh",
         "ssh_target": "me@devbox", "workdir": "/home/me/work"},
        {"id": "ubuntu", "name": "Ubuntu", "kind": "docker",
         "image": "ubuntu:24.04"}
      ]
    }

A session picks its environment once, when it is created, and keeps it
(``environment_id`` on the session meta). Channels are a separate axis:
a channel decides where a conversation's messages are mirrored
(WeChat, Telegram, ...), never where its tools execute.

Kinds:

* ``local`` — host shell and host filesystem.
* ``ssh`` — commands run over SSH; file tools read and write the remote
  disk through the same connection. ``workdir`` is the remote folder a
  session starts in.
* ``docker`` — one long-lived container per session. The session's
  project folder and extra working folders are bind-mounted at their
  host paths, so file tools keep working on the host while commands run
  inside the container.

The legacy ``backend.terminal`` setting (one global backend) is read as
an environment named after its kind and made the default, so an
existing ``ssh``/``docker`` setup keeps behaving the same.
"""
from __future__ import annotations

import contextlib
import re
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from openprogram.state import config as user_config


LOCAL_ID = "local"
KINDS = ("local", "ssh", "docker")
DEFAULT_DOCKER_IMAGE = "ubuntu:24.04"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")


@dataclass(frozen=True)
class Environment:
    id: str
    name: str
    kind: str
    ssh_target: str = ""
    workdir: str = ""
    image: str = ""
    legacy: bool = field(default=False, compare=False)

    @property
    def is_local(self) -> bool:
        return self.kind == "local"

    @property
    def remote_files(self) -> bool:
        """File tools must go through the environment, not the host disk."""
        return self.kind == "ssh"

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("legacy", None)
        if self.kind != "ssh":
            data.pop("ssh_target", None)
            data.pop("workdir", None)
        if self.kind != "docker":
            data.pop("image", None)
        if self.legacy:
            data["legacy"] = True
        return data


LOCAL = Environment(id=LOCAL_ID, name="Local", kind="local")


class EnvironmentError(ValueError):
    """A request names an environment that is invalid or missing."""


# ---- config ----------------------------------------------------------------


def _section(cfg: dict[str, Any]) -> dict[str, Any]:
    section = cfg.get("environments")
    return section if isinstance(section, dict) else {}


def _from_item(raw: Any) -> Optional[Environment]:
    if not isinstance(raw, dict):
        return None
    env_id = str(raw.get("id") or "").strip()
    kind = str(raw.get("kind") or "").strip().lower()
    if not _ID_RE.match(env_id) or env_id == LOCAL_ID or kind not in ("ssh", "docker"):
        return None
    name = str(raw.get("name") or env_id).strip() or env_id
    if kind == "ssh":
        target = str(raw.get("ssh_target") or "").strip()
        if not target:
            return None
        return Environment(env_id, name, kind, ssh_target=target,
                           workdir=str(raw.get("workdir") or "").strip())
    return Environment(env_id, name, kind,
                       image=str(raw.get("image") or "").strip() or DEFAULT_DOCKER_IMAGE)


def _legacy(cfg: dict[str, Any]) -> Optional[Environment]:
    backend = cfg.get("backend")
    if not isinstance(backend, dict):
        return None
    kind = str(backend.get("terminal") or "local").lower()
    if kind == "ssh":
        target = str(backend.get("ssh_target") or "").strip()
        if not target:
            return None
        return Environment("ssh", "SSH", "ssh", ssh_target=target, legacy=True)
    if kind == "docker":
        return Environment("docker", "Docker", "docker",
                           image=str(backend.get("docker_image") or DEFAULT_DOCKER_IMAGE),
                           legacy=True)
    return None


def list_environments(cfg: Optional[dict[str, Any]] = None) -> list[Environment]:
    """Local first, then configured environments in config order."""
    cfg = user_config.read_config() if cfg is None else cfg
    items: list[Environment] = [LOCAL]
    seen = {LOCAL_ID}
    for raw in _section(cfg).get("items") or []:
        env = _from_item(raw)
        if env is not None and env.id not in seen:
            items.append(env)
            seen.add(env.id)
    legacy = _legacy(cfg)
    if legacy is not None and legacy.id not in seen and "environments" not in cfg:
        items.append(legacy)
    return items


def get_environment(env_id: Optional[str], cfg: Optional[dict[str, Any]] = None) -> Optional[Environment]:
    if not env_id:
        return None
    for env in list_environments(cfg):
        if env.id == env_id:
            return env
    return None


def default_environment_id(cfg: Optional[dict[str, Any]] = None) -> str:
    cfg = user_config.read_config() if cfg is None else cfg
    wanted = str(_section(cfg).get("default") or "").strip()
    if not wanted and "environments" not in cfg:
        legacy = _legacy(cfg)
        wanted = legacy.id if legacy is not None else ""
    if wanted and get_environment(wanted, cfg) is not None:
        return wanted
    return LOCAL_ID


def default_environment(cfg: Optional[dict[str, Any]] = None) -> Environment:
    cfg = user_config.read_config() if cfg is None else cfg
    return get_environment(default_environment_id(cfg), cfg) or LOCAL


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    env_id = str(payload.get("id") or "").strip().lower()
    kind = str(payload.get("kind") or "").strip().lower()
    name = str(payload.get("name") or "").strip()
    if env_id == LOCAL_ID:
        raise EnvironmentError("'local' is built in and cannot be changed")
    if not _ID_RE.match(env_id):
        raise EnvironmentError(
            "id must be 1-48 characters: lowercase letters, digits, '-' or '_'")
    if kind not in ("ssh", "docker"):
        raise EnvironmentError("kind must be 'ssh' or 'docker'")
    item: dict[str, Any] = {"id": env_id, "name": name or env_id, "kind": kind}
    if kind == "ssh":
        target = str(payload.get("ssh_target") or "").strip()
        if not target or any(ch.isspace() for ch in target) or target.startswith("-"):
            raise EnvironmentError("ssh_target must be user@host or a host from ~/.ssh/config")
        workdir = str(payload.get("workdir") or "").strip()
        if workdir and not (workdir.startswith("/") or workdir.startswith("~")):
            raise EnvironmentError("workdir must be an absolute remote path")
        item["ssh_target"] = target
        if workdir:
            item["workdir"] = workdir
    else:
        image = str(payload.get("image") or "").strip() or DEFAULT_DOCKER_IMAGE
        if any(ch.isspace() for ch in image) or image.startswith("-"):
            raise EnvironmentError("image must be a Docker image reference")
        item["image"] = image
    return item


def _materialize(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return the writable ``environments`` section, folding in the legacy
    ``backend`` entry the first time the section is created."""
    section = cfg.get("environments")
    if isinstance(section, dict):
        section.setdefault("items", [])
        return section
    section = {"default": LOCAL_ID, "items": []}
    legacy = _legacy(cfg)
    if legacy is not None:
        section["items"].append({k: v for k, v in legacy.public().items() if k != "legacy"})
        section["default"] = legacy.id
    cfg["environments"] = section
    return section


def save_environment(payload: dict[str, Any]) -> Environment:
    """Create or replace one environment (matched by id)."""
    item = _normalize_payload(payload)

    def mutate(cfg: dict[str, Any]) -> None:
        section = _materialize(cfg)
        items = [x for x in section["items"]
                 if not (isinstance(x, dict) and x.get("id") == item["id"])]
        items.append(item)
        section["items"] = items

    cfg = user_config.update_config(mutate)
    env = get_environment(item["id"], cfg)
    assert env is not None
    return env


def delete_environment(env_id: str) -> None:
    if env_id == LOCAL_ID:
        raise EnvironmentError("'local' is built in and cannot be removed")

    def mutate(cfg: dict[str, Any]) -> None:
        section = _materialize(cfg)
        section["items"] = [x for x in section["items"]
                            if not (isinstance(x, dict) and x.get("id") == env_id)]
        if section.get("default") == env_id:
            section["default"] = LOCAL_ID

    user_config.update_config(mutate)


def set_default_environment(env_id: str) -> None:
    if get_environment(env_id) is None:
        raise EnvironmentError(f"unknown environment {env_id!r}")
    user_config.update_config(lambda cfg: _materialize(cfg).__setitem__("default", env_id))


# ---- sessions --------------------------------------------------------------


def session_environment_id(session_id: Optional[str]) -> Optional[str]:
    """The id stored on the session, or None for sessions created before
    environments existed (they follow the configured default)."""
    if not session_id:
        return None
    try:
        from openprogram.state.store.current import current_store
        row = current_store().get_session(session_id)
    except Exception:
        return None
    value = (row or {}).get("environment_id")
    return value if isinstance(value, str) and value else None


def resolve_for_session(session_id: Optional[str]) -> Environment:
    """The environment a session's tools run in.

    Raises :class:`EnvironmentError` when the session names an
    environment that has since been removed: running its commands
    somewhere else would be worse than refusing.
    """
    env_id = session_environment_id(session_id)
    if env_id is None:
        return default_environment()
    env = get_environment(env_id)
    if env is None:
        raise EnvironmentError(
            f"this conversation runs in environment {env_id!r}, which no longer "
            "exists. Add it back in Settings > Environments or start a new conversation.")
    return env


def choose_for_new_session(requested: Optional[str]) -> str:
    """Validate the composer's pick for a brand-new session."""
    if requested:
        if get_environment(requested) is None:
            raise EnvironmentError(f"unknown environment {requested!r}")
        return requested
    return default_environment_id()


def assign_new_session_environment(db, session_id: str, requested: Optional[str]) -> Optional[str]:
    """Pin the environment on a conversation's first message.

    A conversation that already has history keeps what it has: one that
    already stored an id keeps it, and one created before environments
    existed keeps following the default rather than being moved by a
    later composer pick. Returns the id now in force (None = default).
    """
    current = session_environment_id(session_id)
    if current is not None:
        return current
    try:
        has_history = bool(db.get_branch(session_id))
    except Exception:
        has_history = False
    if has_history:
        return None
    chosen = choose_for_new_session(requested)
    db.update_session(session_id, environment_id=chosen)
    return chosen


# ---- per-turn binding ------------------------------------------------------


_current_environment: ContextVar[Optional[Environment]] = ContextVar(
    "_current_environment", default=None)


def bind_turn_environment(env: Optional[Environment]):
    return _current_environment.set(env)


def reset_turn_environment(token) -> None:
    with contextlib.suppress(Exception):
        _current_environment.reset(token)


def current_environment() -> Environment:
    """The environment the running tool call belongs to.

    A turn binds its session's environment; outside a turn the current
    session (if any) decides, else the configured default.
    """
    env = _current_environment.get()
    if env is not None:
        return env
    from openprogram.state.execution_facts import current_session_id
    return resolve_for_session(current_session_id())


__all__ = [
    "DEFAULT_DOCKER_IMAGE",
    "Environment",
    "EnvironmentError",
    "KINDS",
    "LOCAL",
    "LOCAL_ID",
    "assign_new_session_environment",
    "bind_turn_environment",
    "choose_for_new_session",
    "current_environment",
    "default_environment",
    "default_environment_id",
    "delete_environment",
    "get_environment",
    "list_environments",
    "reset_turn_environment",
    "resolve_for_session",
    "save_environment",
    "session_environment_id",
    "set_default_environment",
]
