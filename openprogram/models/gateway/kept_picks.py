"""An agent's choices that outlive disconnecting (magpie ``Field.Own``).

After yetone/magpie ``internal/provider/agenteffort.go`` and the ``Own``
fields of ``internal/agent`` (MIT License, Copyright (c) yetone). Most of an
agent's picks are wiring, kept in its stash record and gone with it on
disconnect. A field marked ``kept`` (agy's effort and fast tools) is the
user's own setting instead: its value is kept by agent in
``<state>/gateway/agent-picks.json``, shown in the agent's row whether it is
connected or not, and given back to the wiring on the next connect.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

_lock = threading.Lock()


def _path() -> Path:
    from .keys import gateway_dir
    return gateway_dir() / "agent-picks.json"


def _load() -> dict[str, dict[str, str]]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(agent): {str(k): str(v) for k, v in picks.items() if isinstance(v, str)}
            for agent, picks in data.items() if isinstance(picks, dict)}


def kept(agent: str) -> dict[str, str]:
    """The kept choices of ``agent``, ``{}`` for none."""
    return dict(_load().get((agent or "").strip().lower(), {}))


def keep(agent: str, picks: dict[str, str]) -> None:
    """Keep ``picks`` for ``agent``: a value ``""`` takes that choice out."""
    agent = (agent or "").strip().lower()
    with _lock:
        data = _load()
        now = {k: v for k, v in {**data.get(agent, {}), **picks}.items() if v}
        if now == data.get(agent, {}):
            return
        if now:
            data[agent] = now
        else:
            data.pop(agent, None)
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        from openprogram.host._platform.filesystem import replace_file
        replace_file(tmp, path)
