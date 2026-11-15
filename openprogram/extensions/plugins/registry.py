"""What each loaded plugin contributed, for the HTTP API and the commands registry.

The loader records a plugin's contributions here when it mounts them
(``_commands``, the skills directory) and clears them when the plugin is
disabled. The module holds data only; mounting goes through the plugin's
``ProgramContext``.
"""
from __future__ import annotations

from threading import RLock
from typing import Any


_lock = RLock()
# {name: {"_commands": [...], "skills": "/abs/path"}}
_contrib: dict[str, dict[str, Any]] = {}


def set_plugin_contrib(name: str, contrib: dict[str, Any]) -> None:
    with _lock:
        _contrib[name] = dict(contrib)


def clear_plugin_contrib(name: str) -> None:
    with _lock:
        _contrib.pop(name, None)
