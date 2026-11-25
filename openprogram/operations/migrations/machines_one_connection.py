"""One-time rewrite of a profile's stored machines to one connection each.

A machine was stored with up to five connections, one per slot
(``shell``, ``window``, ``screen``, ``browser``, ``openprogram``). Now it
has exactly one, ``connection``: how it is reached. The meaningful one is
kept: the ``openprogram`` connection when there is one (a paired computer),
else the shell's (``ssh``, ``docker``), else a screen's, window's or
browser's that reaches a machine of its own (``osworld``). This computer's
own window, screen and browser (``machines.local``) are dropped: this
computer is the method ``local`` and stores nothing. A machine with nothing
left is dropped.

Each machine's secrets (``auth/machines/<id>.json``) were keyed
``<slot>.<setting>``; the kept connection's keep their setting name, the
others go. The configuration is rewritten first and the marker last, so an
interrupted run starts over; a machine already in the new shape is left
alone.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from openprogram.state import paths as state_paths

_log = logging.getLogger(__name__)

_SLOTS = ("shell", "window", "screen", "browser", "openprogram")
# What only this computer's slots named: its own desktop and OpenProgram's own browser.
_THIS_COMPUTER = frozenset({"local", "desktop", "built_in"})


def _marker(state: Path) -> Path:
    return state / "migrations" / "machines-one-connection.done"


def _kind(raw: Any) -> str:
    return str(raw.get("kind") or "").strip().lower() if isinstance(raw, dict) else ""


def chosen_slot(item: dict) -> str:
    """The slot whose connection a stored machine keeps, or "" when none reaches a machine of its own."""
    if _kind(item.get("openprogram")):
        return "openprogram"
    if _kind(item.get("shell")) and _kind(item.get("shell")) not in _THIS_COMPUTER:
        return "shell"
    for slot in ("screen", "window", "browser"):
        kind = _kind(item.get(slot))
        if kind and kind not in _THIS_COMPUTER:
            return slot
    return ""


def rewrite_item(item: Any) -> tuple[dict | None, str]:
    """``(item in the new shape or None to drop it, slot kept)``; an item already in the new shape is kept."""
    if not isinstance(item, dict):
        return None, ""
    if isinstance(item.get("connection"), dict):
        return {key: value for key, value in item.items() if key not in _SLOTS}, ""
    slot = chosen_slot(item)
    if not slot:
        return None, ""
    kept = {key: value for key, value in item.items() if key not in _SLOTS}
    kept["connection"] = dict(item[slot])
    return kept, slot


def rewrite_section(section: Any) -> tuple[dict, dict[str, str], list[str]]:
    """The machines section in the new shape, the slot kept per machine id, and the ids dropped."""
    if not isinstance(section, dict):
        return {}, {}, []
    out = {key: value for key, value in section.items() if key not in ("local", "items")}
    kept_slots: dict[str, str] = {}
    dropped: list[str] = []
    items = []
    for raw in section.get("items") or []:
        item, slot = rewrite_item(raw)
        if item is None:
            dropped.append(str(raw.get("id") or "?") if isinstance(raw, dict) else "?")
            continue
        if slot:
            kept_slots[str(item.get("id") or "")] = slot
        items.append(item)
    out["items"] = items
    return out, kept_slots, dropped


def rewrite_secrets(values: Any, slot: str) -> dict[str, str]:
    """A machine's secrets keyed by setting name: the kept slot's, without its prefix."""
    if not isinstance(values, dict):
        return {}
    prefix = f"{slot}."
    out: dict[str, str] = {}
    for key, value in values.items():
        if not isinstance(key, str):
            continue
        if key.startswith(prefix):
            out[key[len(prefix):]] = value
        elif "." not in key:
            out[key] = value
    return out


def _rewrite_secret_files(state: Path, kept_slots: dict[str, str], dropped: list[str]) -> None:
    folder = state / "auth" / "machines"
    if not folder.is_dir():
        return
    from openprogram.models.auth.credentials.io import _private_atomic_write

    for path in sorted(folder.glob("*.json")):
        machine_id = path.stem
        if machine_id in dropped:
            path.unlink(missing_ok=True)
            continue
        slot = kept_slots.get(machine_id)
        if not slot:
            continue
        try:
            values = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rewritten = rewrite_secrets(values, slot)
        if rewritten == values:
            continue
        if not rewritten:
            path.unlink(missing_ok=True)
            continue
        data = json.dumps(rewritten).encode("utf-8")
        _private_atomic_write(path, lambda handle: handle.write(data), root=path.parent)


def run() -> bool:
    """Rewrite the stored machines if this profile has not been rewritten. Returns whether anything changed."""
    state = state_paths.get_state_dir()
    marker = _marker(state)
    if marker.exists():
        return False
    from openprogram.state.config import read_config_with_revision, write_config

    cfg, revision = read_config_with_revision()
    section = cfg.get("machines") if isinstance(cfg, dict) else None
    changed = False
    if isinstance(section, dict):
        rewritten, kept_slots, dropped = rewrite_section(section)
        if rewritten != section:
            write_config({**cfg, "machines": rewritten}, expected_revision=revision)
            changed = True
        for machine_id in dropped:
            _log.warning("machines: %s reached nothing but this computer and was dropped", machine_id)
        _rewrite_secret_files(state, kept_slots, dropped)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("done\n", encoding="utf-8")
    return changed


__all__ = ["chosen_slot", "rewrite_item", "rewrite_secrets", "rewrite_section", "run"]
