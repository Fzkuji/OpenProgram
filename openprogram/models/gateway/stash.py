"""What an agent's config said before OpenProgram pointed it at the gateway.

After yetone/magpie ``internal/agent/stash.go`` and ``applied.go`` (MIT
License, Copyright (c) yetone). ``<state>/gateway/harness-stash.json`` holds,
per agent:

* ``files``: per file written, each key's value before OpenProgram first
  wrote it (or that it was absent) and what OpenProgram wrote, so a key
  changed since is drift and is never restored over; whether the file was
  there at all (``absent``) and the copy taken of it whole before the first
  write (``copy``, magpie's ``beforeMagpie``), for agents that ask for one;
* ``values``: the models picked, by field;
* ``address``: the gateway address written, so a moved port is rewired;
* ``at``: when OpenProgram last changed the agent's files, for an app that
  reads them only as it starts.

Beside the agents, ``~takeout`` keeps what is still to be taken out of an app
that was uninstalled when its agent was turned off (``AppGone``), until it
is installed and answers again.

The stash is read, changed and written whole under a lock, through a temp
file renamed over it. A stash that does not parse is never read as empty:
:class:`StashUnreadable` is raised and nothing is written or restored until
it is fixed, since "nothing kept" would lose the user's own settings.

A record from before the registry (``{"path", "originals", "wrote"}``, one
file per harness) is read as the one-file record it stands for, so the
values it kept still go back to the user's Claude Code or Codex config.
"""
from __future__ import annotations

import copy as _copy
import json
import os
import secrets
import threading
from typing import Any, Optional

from .keys import gateway_dir

_lock = threading.RLock()


#: The stash's entry for what is still to be taken out of an app that was
#: uninstalled when its agent was turned off (magpie keeps ``alma.takeout``
#: and ``shelley.takeout`` in its own stash); never an agent's id.
TAKEOUT = "~takeout"


class StashUnreadable(RuntimeError):
    """The stash file does not parse; nothing is restored or written over it."""


def _path():
    return gateway_dir() / "harness-stash.json"


def _upgraded(record: Any) -> Any:
    """A pre-registry record as a one-file record."""
    if not isinstance(record, dict) or "files" in record or "path" not in record:
        return record
    path = str(record.get("path") or "")
    wrote = dict(record.get("wrote") or {})
    model = (wrote.get("model") or {}).get("set")
    return {"files": {path: {"format": "toml" if path.endswith(".toml") else "json",
                             "originals": dict(record.get("originals") or {}),
                             "wrote": wrote, "absent": False}},
            "values": {"model": model} if isinstance(model, str) and model else {}}


def load() -> dict[str, Any]:
    with _lock:
        try:
            data = json.loads(_path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (ValueError, OSError) as exc:
            raise StashUnreadable(f"the saved original agent settings cannot be read ({_path()}): {exc}") from exc
        if not isinstance(data, dict):
            raise StashUnreadable(f"the saved original agent settings are not an object ({_path()})")
        return {agent: _upgraded(record) for agent, record in data.items()}


def _save(data: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    try:
        os.write(fd, json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    from openprogram.host._platform.filesystem import replace_file
    replace_file(tmp, path)


def entry(agent: str) -> dict[str, Any] | None:
    return load().get(agent)


def agents() -> list[str]:
    """The agents connected; a record kept beside the connections (what a
    live hook keeps, ``<id>#<name>``: Hermes's bots taken off, DeepSeek
    Harness's pinned searches) is not one."""
    return [agent for agent in load() if _agent(agent)]


def connected() -> dict[str, Any]:
    """Each connected agent's record."""
    return {agent: record for agent, record in load().items() if _agent(agent)}


def _agent(key: str) -> bool:
    return key != TAKEOUT and "#" not in key


def take_outs() -> dict[str, Any]:
    """What a live hook kept of each agent turned off while its app was
    uninstalled, to be taken out of the app once it is back."""
    return dict(load().get(TAKEOUT) or {})


def keep_take_out(agent: str, kept: Optional[dict[str, Any]]) -> None:
    """Keep ``kept`` for :func:`take_outs` (``None`` drops it)."""
    with _lock:
        data = load()
        outs = dict(data.get(TAKEOUT) or {})
        if kept is None:
            if outs.pop(agent, None) is None:
                return
        else:
            outs[agent] = _copy.deepcopy(kept)
        if outs:
            data[TAKEOUT] = outs
        else:
            data.pop(TAKEOUT, None)
        _save(data)


def put(agent: str, record: Optional[dict[str, Any]]) -> None:
    """Set an agent's whole record back (``None`` removes it): the undo of a
    connect whose writes failed."""
    with _lock:
        data = load()
        if record is None:
            if data.pop(agent, None) is None:
                return
        else:
            data[agent] = _copy.deepcopy(record)
        _save(data)


def remember(agent: str, path: str, originals: dict[str, tuple[bool, Any]], *, fmt: str,
             absent: bool = False, copy: str | None = None, parents: list[str] | None = None,
             keep: list[str] | None = None) -> None:
    """Keep each key's original value of ``path`` unless one is already kept.

    ``parents``: objects OpenProgram adds to hold its keys, taken out again
    once empty. ``keep``: keys written and never restored, watched for drift."""
    with _lock:
        data = load()
        record = data.setdefault(agent, {"files": {}, "values": {}})
        files = record.setdefault("files", {})
        kept = files.setdefault(path, {"format": fmt, "originals": {}, "wrote": {}, "absent": absent})
        kept["format"] = fmt
        if copy and "copy" not in kept:
            kept["copy"] = copy
        for key, (had, value) in originals.items():
            kept["originals"].setdefault(key, {"had": had, "value": value} if had else {"had": False})
        if parents:
            kept["parents"] = sorted(set(kept.get("parents") or []) | set(parents))
        if keep is not None:
            kept["keep"] = list(keep)
        _save(data)


def update_file(agent: str, path: str, **fields: Any) -> None:
    """Set fields of one file's record: ``wrote`` (``{key: {"set": value}}`` or
    ``{key: {"deleted": True}}``) and ``kept_set`` (the never-restored keys'
    values, by which this instance knows the file still points at it)."""
    with _lock:
        data = load()
        record = data.get(agent)
        if record is None or path not in record.get("files", {}):
            return
        record["files"][path].update(fields)
        _save(data)


def update(agent: str, **fields: Any) -> None:
    """Set top-level fields of an agent's record (``values``, ``address``, ``at``)."""
    with _lock:
        data = load()
        record = data.get(agent)
        if record is None:
            return
        record.update(fields)
        _save(data)


def forget_file(agent: str, path: str) -> None:
    with _lock:
        data = load()
        record = data.get(agent)
        if record and record.get("files", {}).pop(path, None) is not None:
            _save(data)


def forget(agent: str) -> None:
    with _lock:
        data = load()
        if data.pop(agent, None) is not None:
            _save(data)


# Shared files left behind (engine.disconnect, Agent.shared_used)

def _left_path():
    return gateway_dir() / "left-shared.json"


def _left_load() -> dict[str, Any]:
    try:
        data = json.loads(_left_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (ValueError, OSError) as exc:
        raise StashUnreadable(f"the record of OpenProgram's files left in place cannot be read ({_left_path()}): "
                              f"{exc}") from exc
    return data if isinstance(data, dict) else {}


def _left_save(data: dict[str, Any]) -> None:
    path = _left_path()
    if not data:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    try:
        os.write(fd, json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    from openprogram.host._platform.filesystem import replace_file
    replace_file(tmp, path)


def leave(path: str, kept: dict[str, Any]) -> None:
    """Keep, outside every agent's record, a shared file's record (what was
    there before OpenProgram, the copy of it) when the last disconnect leaves
    the file because an agent of the app still names it."""
    with _lock:
        data = _left_load()
        data[path] = _copy.deepcopy(kept)
        _left_save(data)


def left(path: str) -> dict[str, Any] | None:
    """The record :func:`leave` kept of ``path``, if any."""
    with _lock:
        return _copy.deepcopy(_left_load().get(path))


def forget_left(path: str) -> None:
    with _lock:
        data = _left_load()
        if data.pop(path, None) is not None:
            _left_save(data)
