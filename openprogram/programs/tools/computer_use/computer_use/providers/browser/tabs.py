"""Stable tab ids for the user's browsers, kept per conversation, and the tab each agent used last."""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import threading
from typing import Any


# Ids of pages no longer seen, kept to give back to a page that comes back at the same address.
_GONE_KEPT = 50


@dataclass
class _Table:
    """One conversation's tab ids in one browser."""
    ids: dict[str, str] = field(default_factory=dict)
    urls: dict[str, str] = field(default_factory=dict)
    # The ids in the latest listing: a gone tab keeps its id for a reconnect, but no window is claimed for it.
    present: set[str] = field(default_factory=set)


_guard = threading.Lock()
_tables: dict[tuple[str, str], _Table] = {}
# (conversation, browser, agent) -> the id of the tab that agent used last.
_current: dict[tuple[str, str, str], str] = {}
# A key's lock is never dropped: a call may be waiting on it, and a fresh one would let two calls hold the turn.
_turns: dict[tuple[str, str], threading.Lock] = {}
# (conversation, browser) -> the agents that have used the connection; it ends with the last of them.
_users: dict[tuple[str, str], set[str]] = {}


def _turn(conversation: str, browser: str) -> threading.Lock:
    with _guard:
        return _turns.setdefault((conversation, browser), threading.Lock())


def _table(conversation: str, browser: str) -> _Table:
    with _guard:
        return _tables.setdefault((conversation, browser), _Table())


def _name(browser: str, key: str, taken: set[str]) -> str:
    digest = base64.b32encode(hashlib.sha1(f"{browser}\0{key}".encode()).digest()).decode().lower()
    return next(f"t{digest[:size]}" for size in range(4, len(digest)) if f"t{digest[:size]}" not in taken)


def identify(table: _Table, browser: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The listed tabs with their ids and their numbers in Playwright MCP's list."""
    present = {str(row.get("key") or "") for row in rows}
    taken = set(table.ids.values())
    gone = {tab: table.urls.get(tab, "") for key, tab in table.ids.items() if key not in present}
    out = []
    for index, row in enumerate(rows):
        key, url = str(row.get("key") or ""), str(row.get("url") or "")
        tab = table.ids.get(key)
        if tab is None:
            same = [old for old, seen in gone.items() if seen and seen == url]
            if len(same) == 1:
                tab = same[0]
                gone.pop(tab)
                for old_key in [old_key for old_key, old in table.ids.items() if old == tab]:
                    table.ids.pop(old_key)
            else:
                tab = _name(browser, key, taken)
            table.ids[key] = tab
            taken.add(tab)
        table.urls[tab] = url
        out.append({**row, "id": tab, "index": index})
    stale = [key for key, tab in table.ids.items() if key not in present and tab in gone][:-_GONE_KEPT or None]
    for key in stale:
        table.urls.pop(table.ids.pop(key), None)
    table.present = {row["id"] for row in out}
    return out


def _public(rows: list[dict[str, Any]], current: str | None) -> list[dict[str, Any]]:
    return [{"tab": row["id"], "title": row.get("title") or "", "url": row.get("url") or "",
             **({"current": True} if row["id"] == current else {})} for row in rows]
