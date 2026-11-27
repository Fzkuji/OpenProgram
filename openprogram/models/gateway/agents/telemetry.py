"""An agent's own switch for the usage data it sends its vendor.

After yetone/magpie ``internal/agent/telemetry.go`` (MIT License, Copyright
(c) yetone). It is the user's own setting, not wiring, so it is a kept
choice (``Field.kept``, ``kept_picks.py``): set whether the agent is
connected or not, left as it is by connecting and disconnecting. "Off"
writes the agent's documented keys that turn it off; a key the user set
already is theirs and stays as it is. Back to the agent's own default
takes out only what OpenProgram wrote, while it still says what was
written; what was written is kept by agent in
``<state>/gateway/agent-telemetry.json``.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable

from .base import Field, Write

#: The kept field's key.
KEY = "telemetry"
_lock = threading.Lock()


def field() -> Field:
    """The row's choice: the agent's own default, or off."""
    return Field(KEY, "Telemetry", quiet=True, options=lambda: [("off", "Off")], kept=True)


def _path() -> Path:
    from ..keys import gateway_dir
    return gateway_dir() / "agent-telemetry.json"


def _load() -> dict[str, dict[str, dict[str, Any]]]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict[str, Any]) -> None:
    from ..edit import write_atomic
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(data, indent=2, sort_keys=True) + "\n", keep_mode=False)


def _set(agent_id: str, writes: list[Write]) -> None:
    from .. import edit
    from ..edit import ConfigText, get_path
    data = _load()
    wrote: dict[str, dict[str, Any]] = data.get(agent_id) or {}
    for write in writes:
        text = ConfigText.read(write.path)
        doc = edit.load(write.format, text.text, str(write.path)) if text.exists else {}
        sets = {key: value for key, value in write.sets.items() if not get_path(doc, key)[0]}
        if not sets:
            continue  # set already, to off or by the user to on: theirs
        new = edit.edit(write.format, text.text, sets=sets, where=str(write.path))
        if new != text.text or not text.exists:
            write.path.parent.mkdir(parents=True, exist_ok=True)
            text.write(new)
        entry = wrote.setdefault(str(write.path), {"format": write.format, "keys": {}, "made": []})
        entry["keys"].update(sets)
        # the objects it made to hold them go with them (``options`` of Crush's)
        for key in sets:
            parts = edit.split_path(key)
            for depth in range(1, len(parts)):
                parent = ".".join(part.replace(".", "\\.") for part in parts[:depth])
                if not get_path(doc, parent)[0] and parent not in entry["made"]:
                    entry["made"].append(parent)
    if wrote:
        data[agent_id] = wrote
        _save(data)


def _unset(agent_id: str) -> None:
    from .. import edit
    from ..edit import ConfigText, get_path
    data = _load()
    for path, entry in (data.pop(agent_id, None) or {}).items():
        fmt, keys = entry.get("format") or "json", entry.get("keys") or {}
        text = ConfigText.read(Path(path))
        if not text.exists:
            continue
        doc = edit.load(fmt, text.text, path)
        deletes = [key for key, value in keys.items() if get_path(doc, key) == (True, value)]
        after = dict(doc)
        for key in deletes:
            edit.delete_path(after, key)
        for parent in sorted(entry.get("made") or [], key=len, reverse=True):
            present, value = get_path(after, parent)
            if present and value == {}:
                deletes.append(parent)
                edit.delete_path(after, parent)
        if deletes:
            new = edit.edit(fmt, text.text, deletes=deletes, where=path)
            if new != text.text:
                text.write(new)
    _save(data)


def switch(agent_id: str, writes: Callable[[], list[Write]]) -> dict[str, Any]:
    """The ``Agent`` arguments that carry out its telemetry choice:
    ``apply_kept`` as it is set, and ``after`` a connect that picked it."""
    from ..edit import ConfigUnreadable

    def apply(picks: dict[str, str]) -> None:
        with _lock:
            try:
                if picks.get(KEY) == "off":
                    _set(agent_id, writes())
                elif agent_id in _load():
                    _unset(agent_id)
            except (ConfigUnreadable, OSError) as exc:
                from .engine import AgentConfigError
                raise AgentConfigError(f"{exc}; telemetry was left as it was") from exc

    def after(written: bool, record: dict[str, Any]) -> None:
        if written:
            from ..kept_picks import kept
            from .engine import AgentConfigError
            try:
                apply(kept(agent_id))
            except AgentConfigError:
                pass  # the connect stands; the choice is carried out when it is set again

    return {"apply_kept": apply, "after": after}
