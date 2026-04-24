"""Channel → Agent routing.

A *binding* answers "when a message arrives on (channel, account,
peer), which agent should handle it?" Mirrors OpenClaw's
``bindings[]`` config shape: each entry is ``{agent_id, match}`` where
``match`` is a set of constraints the inbound envelope must satisfy.

    {
      "v": 1,
      "bindings": [
        {
          "agent_id": "family",
          "match": {
            "channel": "wechat",
            "account_id": "default",
            "peer": {"kind": "group", "id": "12345@chatroom"}
          }
        },
        {
          "agent_id": "work",
          "match": {"channel": "telegram", "account_id": "alerts"}
        }
      ]
    }

Routing rules (first-match-wins, most-specific-first):

  1. peer match (exact DM or group id)
  2. account_id match
  3. channel-level match (no peer, no account)
  4. fallback to the default agent from the agents registry

If a binding has multiple fields, all specified fields must match
(AND). Fields left out are wildcards.
"""
from __future__ import annotations

import fcntl
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional


_FILE = "bindings.json"
_SCHEMA_VERSION = 1
_lock = threading.RLock()


# ---------------------------------------------------------------------------
# Paths + IO
# ---------------------------------------------------------------------------

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
        bindings = raw.get("bindings") or []
    elif isinstance(raw, list):
        bindings = raw
    else:
        bindings = []
    out: list[dict[str, Any]] = []
    for row in bindings:
        if not isinstance(row, dict):
            continue
        aid = row.get("agent_id") or row.get("agentId")
        if not aid:
            continue
        match = row.get("match") or {}
        if not isinstance(match, dict):
            continue
        out.append({
            "id": row.get("id") or _new_binding_id(),
            "agent_id": str(aid),
            "match": _normalize_match(match),
            "created_at": row.get("created_at") or 0.0,
        })
    return out


def _write(bindings: list[dict[str, Any]]) -> None:
    path = _path()
    lock_path = path.with_suffix(path.suffix + ".lock")
    with open(lock_path, "a+") as lock_fh:
        try:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        except OSError:
            pass
        payload = {"v": _SCHEMA_VERSION, "bindings": bindings}
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp, path)


def _normalize_match(match: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    ch = match.get("channel")
    if ch:
        out["channel"] = str(ch)
    acct = match.get("account_id") or match.get("accountId")
    if acct:
        out["account_id"] = str(acct)
    peer = match.get("peer")
    if isinstance(peer, dict):
        kind = peer.get("kind") or "direct"
        pid = peer.get("id") or peer.get("user_id") or peer.get("chat_id")
        if pid:
            out["peer"] = {"kind": str(kind), "id": str(pid)}
    return out


def _new_binding_id() -> str:
    return "bnd_" + uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_all() -> list[dict[str, Any]]:
    with _lock:
        return [dict(b) for b in _read()]


def add(agent_id: str, match: dict[str, Any]) -> dict[str, Any]:
    """Append a new binding. If an identical (agent_id, match) already
    exists, returns the existing one without duplicating.
    """
    normalized = _normalize_match(match)
    if not normalized.get("channel"):
        raise ValueError("match must include `channel`")
    with _lock:
        rows = _read()
        for r in rows:
            if r["agent_id"] == agent_id and r["match"] == normalized:
                return dict(r)
        row = {
            "id": _new_binding_id(),
            "agent_id": str(agent_id),
            "match": normalized,
            "created_at": time.time(),
        }
        rows.append(row)
        _write(rows)
        return dict(row)


def remove(binding_id: str) -> Optional[dict[str, Any]]:
    """Delete by id. Returns the removed entry or None."""
    with _lock:
        rows = _read()
        out = [r for r in rows if r["id"] != binding_id]
        if len(out) == len(rows):
            return None
        removed = next(r for r in rows if r["id"] == binding_id)
        _write(out)
        return dict(removed)


def remove_for_agent(agent_id: str) -> int:
    """Drop every binding that points at ``agent_id``. Returns count."""
    with _lock:
        rows = _read()
        kept = [r for r in rows if r["agent_id"] != agent_id]
        n = len(rows) - len(kept)
        if n:
            _write(kept)
        return n


def remove_for_account(channel: str, account_id: str) -> int:
    """Drop every binding scoped to (channel, account_id)."""
    with _lock:
        rows = _read()
        kept = [
            r for r in rows
            if not (r["match"].get("channel") == channel
                    and r["match"].get("account_id") == account_id)
        ]
        n = len(rows) - len(kept)
        if n:
            _write(kept)
        return n


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def _specificity(match: dict[str, Any]) -> int:
    """Higher score = more specific, tried first."""
    score = 0
    if match.get("peer"):
        score += 100
    if match.get("account_id"):
        score += 10
    if match.get("channel"):
        score += 1
    return score


def route(channel: str, account_id: str, peer: dict[str, Any]) -> str:
    """Resolve an inbound envelope to an agent id.

    ``peer`` must be ``{"kind": "direct"|"group"|..., "id": "..."}``.
    Returns the default agent id if no binding matches.
    """
    with _lock:
        rows = _read()
    # Sort by specificity desc; tie-break by creation order.
    rows_sorted = sorted(
        rows,
        key=lambda r: (-_specificity(r["match"]), r.get("created_at") or 0),
    )
    for r in rows_sorted:
        m = r["match"]
        if m.get("channel") and m["channel"] != channel:
            continue
        if m.get("account_id") and m["account_id"] != account_id:
            continue
        mp = m.get("peer")
        if mp:
            if mp.get("id") != peer.get("id"):
                continue
            if mp.get("kind") and peer.get("kind") \
                    and mp["kind"] != peer["kind"]:
                continue
        return r["agent_id"]
    # No match — fall back to the default agent.
    from openprogram.agents import manager as _agents
    default = _agents.get_default()
    if default is None:
        # No agents configured yet.
        return ""
    return default.id


def list_for_agent(agent_id: str) -> list[dict[str, Any]]:
    """All bindings that currently route to ``agent_id``."""
    with _lock:
        return [dict(r) for r in _read() if r["agent_id"] == agent_id]


def list_for_account(channel: str, account_id: str) -> list[dict[str, Any]]:
    with _lock:
        return [
            dict(r) for r in _read()
            if r["match"].get("channel") == channel
            and r["match"].get("account_id") == account_id
        ]


# Back-compat shim: the old conversation-centric callers used
# auto_bind / attach / detach. Those move onto the new "session"
# concept inside agents; this module is now pure routing.
