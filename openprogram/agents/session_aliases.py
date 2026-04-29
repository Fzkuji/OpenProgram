"""Session aliases — override the "who gets this message" default.

Normally inbound channel messages land in a session whose key is
computed from ``session_scope``. That's fine for fresh contacts.

This module lets a user say "when alice DMs me on WeChat, put her
messages into the session I already have open (conv_id
``local_abc123``)" — without changing the global scope setting.

Storage:

    <state>/session_aliases.json
    {
      "v": 1,
      "aliases": [
        {
          "channel": "wechat",
          "account_id": "default",
          "peer": {"kind": "direct", "id": "alice_openid"},
          "agent_id": "main",
          "session_id": "local_abc123def0"
        }
      ]
    }

Lookup first checks exact (channel, account_id, peer.kind, peer.id)
aliases, then falls back to a catch-all row whose peer.id is "*".
One target per inbound envelope — no fan-out. Writes are fcntl-locked
so the Web UI server and the channels worker can both mutate the file
safely.
"""
from __future__ import annotations

import fcntl
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional


_SCHEMA_VERSION = 1
_FILE = "session_aliases.json"
_lock = threading.RLock()


def _path() -> Path:
    from openprogram.paths import get_state_dir
    root = get_state_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root / _FILE


def _read() -> list[dict[str, Any]]:
    path = _path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(raw, dict):
        items = raw.get("aliases") or []
    elif isinstance(raw, list):
        items = raw
    else:
        items = []
    out: list[dict[str, Any]] = []
    for r in items:
        if not isinstance(r, dict):
            continue
        if not r.get("session_id") or not r.get("channel") \
                or not r.get("peer"):
            continue
        out.append({
            "channel": str(r.get("channel")),
            "account_id": str(r.get("account_id") or "default"),
            "peer": {
                "kind": str((r.get("peer") or {}).get("kind") or "direct"),
                "id": str((r.get("peer") or {}).get("id") or ""),
            },
            "agent_id": str(r.get("agent_id") or ""),
            "session_id": str(r.get("session_id")),
            "created_at": float(r.get("created_at") or 0.0),
        })
    return out


def _write(rows: list[dict[str, Any]]) -> None:
    path = _path()
    lock_path = path.with_suffix(path.suffix + ".lock")
    with open(lock_path, "a+") as lock_fh:
        try:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        except OSError:
            pass
        payload = {"v": _SCHEMA_VERSION, "aliases": rows}
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp, path)


def _match_account(row: dict, channel: str, account_id: str) -> bool:
    if row["channel"] != channel:
        return False
    if row["account_id"] != (account_id or "default"):
        return False
    return True


def _match_exact(row: dict, channel: str, account_id: str,
                 peer: dict) -> bool:
    if not _match_account(row, channel, account_id):
        return False
    if row["peer"]["id"] != str(peer.get("id") or ""):
        return False
    rk = row["peer"].get("kind") or "direct"
    pk = peer.get("kind") or "direct"
    return rk == pk


def _match_catch_all(row: dict, channel: str, account_id: str) -> bool:
    if not _match_account(row, channel, account_id):
        return False
    return row.get("peer", {}).get("id") == "*"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def lookup(channel: str, account_id: str,
           peer: dict) -> Optional[tuple[str, str]]:
    """Resolve (channel, account_id, peer) → (agent_id, session_id).

    Returns None if no alias matches. Most-recent-wins if the file
    somehow accumulates duplicates — shouldn't happen because
    ``attach`` dedupes, but defensive.
    """
    with _lock:
        rows = _read()
    for row in reversed(rows):
        if _match_exact(row, channel, account_id, peer):
            return (row["agent_id"], row["session_id"])
    for row in reversed(rows):
        if _match_catch_all(row, channel, account_id):
            return (row["agent_id"], row["session_id"])
    return None


def list_all() -> list[dict[str, Any]]:
    with _lock:
        return [dict(r) for r in _read()]


def list_for_session(session_id: str) -> list[dict[str, Any]]:
    with _lock:
        return [dict(r) for r in _read() if r["session_id"] == session_id]


def attach(*, channel: str, account_id: str, peer_kind: str,
           peer_id: str, agent_id: str,
           session_id: str) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    """Create or replace an alias.

    Returns ``(new_row, replaced_row)``:

    * ``new_row`` — the alias just written.
    * ``replaced_row`` — the previous alias that lived under the same
      ``(channel, account_id, peer)`` key, or ``None`` if there wasn't
      one. Surfacing it forces every caller to **decide what to do
      about overwrites** — show a "replaced X" hint, prompt for
      confirmation, etc. — instead of silently discarding the
      previous binding.

    Also stamps the target session's meta.json with the binding so
    the Web UI outbound path (``_load_agent_session_meta`` in
    server.py) can resolve ``(channel, account_id, peer)`` from the
    session meta when the user replies from the browser.
    """
    peer = {"kind": peer_kind or "direct", "id": str(peer_id)}
    row = {
        "channel": channel,
        "account_id": account_id or "default",
        "peer": peer,
        "agent_id": agent_id,
        "session_id": session_id,
        "created_at": time.time(),
    }
    with _lock:
        rows = _read()
        replaced: Optional[dict[str, Any]] = None
        kept: list[dict[str, Any]] = []
        for r in rows:
            if replaced is None and _match_exact(r, channel,
                                                 account_id or "default", peer):
                replaced = r
                continue
            kept.append(r)
        kept.append(row)
        _write(kept)
    _stamp_session_meta(agent_id, session_id, row)
    # Self-replacement (re-binding the same peer to the same session)
    # isn't really an overwrite — drop the replaced flag in that case
    # so callers don't display a misleading "replaced X" hint.
    if replaced is not None and replaced.get("session_id") == session_id \
            and replaced.get("agent_id") == agent_id:
        replaced = None
    return row, replaced


def detach(*, channel: str, account_id: str, peer_kind: str,
           peer_id: str) -> Optional[dict[str, Any]]:
    """Remove the alias matching (channel, account, peer)."""
    peer = {"kind": peer_kind or "direct", "id": str(peer_id)}
    with _lock:
        rows = _read()
        kept, removed = [], None
        for r in rows:
            if removed is None and _match_exact(r, channel,
                                                account_id or "default", peer):
                removed = r
                continue
            kept.append(r)
        if removed is None:
            return None
        _write(kept)
    return removed


def detach_session(session_id: str) -> int:
    """Remove every alias pointing at ``session_id``."""
    with _lock:
        rows = _read()
        kept = [r for r in rows if r["session_id"] != session_id]
        n = len(rows) - len(kept)
        if n:
            _write(kept)
    return n


def _stamp_session_meta(agent_id: str, session_id: str,
                        row: dict[str, Any]) -> None:
    """Best-effort: poke the session's meta.json so UI queries that
    read the session directly (without going through this module)
    still see the attached channel/peer."""
    try:
        from openprogram.webui import persistence as _persist
        meta_p = _persist.conv_dir(agent_id, session_id) / "meta.json"
        if not meta_p.exists():
            return
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        meta["channel"] = row["channel"]
        meta["account_id"] = row["account_id"]
        meta["peer"] = dict(row["peer"])
        meta_p.write_text(
            json.dumps(meta, indent=2, default=str),
            encoding="utf-8",
        )
    except Exception:
        pass
