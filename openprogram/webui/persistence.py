"""
Per-session persistence.

Sessions belong to agents. On disk:

    ~/.agentic/agents/<agent_id>/sessions/<session_key>/
        meta.json       — provider/model/title/usage/channel metadata
        messages.json   — user + assistant messages (DAG-of-branches via
                          attempts[].subsequent_messages)
        trees/
          <func_idx>/
            <attempt_idx>.jsonl — append-only Context events

Every function here takes ``agent_id`` as the first argument so the
caller is always explicit about which agent's store it's touching.
``resolve_agent_for_conv`` scans every agent's dir to find the owner
of an existing session key when the caller didn't stash it.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path
from typing import Optional


def _sessions_root(agent_id: str) -> Path:
    from openprogram.agents.manager import sessions_dir
    return sessions_dir(agent_id)


def sessions_root(agent_id: str) -> Path:
    """Public alias."""
    return _sessions_root(agent_id)


def conv_dir(agent_id: str, conv_id: str) -> Path:
    return _sessions_root(agent_id) / conv_id


def trees_dir(agent_id: str, conv_id: str) -> Path:
    return conv_dir(agent_id, conv_id) / "trees"


def tree_path(agent_id: str, conv_id: str,
              func_idx: int, attempt_idx: int) -> Path:
    return trees_dir(agent_id, conv_id) / str(func_idx) / f"{attempt_idx}.jsonl"


def resolve_agent_for_conv(conv_id: str) -> Optional[str]:
    """Which agent's sessions dir contains ``conv_id``? None if
    nobody. Small O(agent_count) scan — fine for UI lookups.
    """
    try:
        from openprogram.agents.manager import list_all
        for spec in list_all():
            if (_sessions_root(spec.id) / conv_id).is_dir():
                return spec.id
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Locking — per-conversation so unrelated writes don't serialize each other.
# ---------------------------------------------------------------------------

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_key(agent_id: str, conv_id: str) -> str:
    return f"{agent_id}/{conv_id}"


def _lock_for(agent_id: str, conv_id: str) -> threading.Lock:
    key = _lock_key(agent_id, conv_id)
    with _locks_guard:
        lk = _locks.get(key)
        if lk is None:
            lk = threading.Lock()
            _locks[key] = lk
        return lk


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _ensure_conv_dir(agent_id: str, conv_id: str) -> Path:
    d = conv_dir(agent_id, conv_id)
    (d / "trees").mkdir(parents=True, exist_ok=True)
    return d


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _json_dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str, indent=2)


# ---------------------------------------------------------------------------
# Meta + messages (low-frequency, whole-file overwrite)
# ---------------------------------------------------------------------------

def save_meta(agent_id: str, conv_id: str, meta: dict) -> None:
    """Persist conversation metadata into SessionDB.

    Was a meta.json atomic-write; now it's an UPDATE on the sessions
    row with overflow into extra_meta JSON for fields SessionDB
    doesn't model explicitly. We still mkdir the conv folder because
    the function-tree code (`init_tree` / `append_tree_event`) drops
    JSONLs into `<conv>/trees/` and that path stays file-based.
    """
    from openprogram.agent.session_db import default_db
    _ensure_conv_dir(agent_id, conv_id)
    db = default_db()
    meta_fields = dict(meta)
    meta_fields.pop("id", None)
    meta_fields.pop("agent_id", None)
    # `session_id` in meta is the LLM runtime's session identifier, not
    # the SessionDB primary key. Rename it before forwarding so the
    # **kwargs expansion doesn't collide with update_session's first
    # positional parameter (also called `session_id`).
    if "session_id" in meta_fields:
        meta_fields["llm_session_id"] = meta_fields.pop("session_id")
    if db.get_session(conv_id) is None:
        db.create_session(conv_id, agent_id, **meta_fields)
    else:
        db.update_session(conv_id, agent_id=agent_id, **meta_fields)


def save_messages(agent_id: str, conv_id: str, messages: list) -> None:
    """Sync the message log to SessionDB. Skips messages whose ids are
    already persisted, so callers can keep passing the full in-memory
    list without rewriting the whole transcript every turn."""
    from openprogram.agent.session_db import default_db
    _ensure_conv_dir(agent_id, conv_id)
    db = default_db()
    if db.get_session(conv_id) is None:
        db.create_session(conv_id, agent_id)
    existing_ids = {m["id"] for m in db.get_messages(conv_id)}
    new_msgs = [m for m in messages if m.get("id") and m["id"] not in existing_ids]
    if new_msgs:
        db.append_messages(conv_id, new_msgs)


def save_conversation(agent_id: str, conv_id: str,
                      meta: dict, messages: list) -> None:
    """Save both meta and messages in one call."""
    save_meta(agent_id, conv_id, meta)
    save_messages(agent_id, conv_id, messages)


# ---------------------------------------------------------------------------
# Tree JSONL (high-frequency, append-only)
# ---------------------------------------------------------------------------

def init_tree(agent_id: str, conv_id: str,
              func_idx: int, attempt_idx: int) -> Path:
    """Create (or truncate) an empty tree jsonl file."""
    _ensure_conv_dir(agent_id, conv_id)
    p = tree_path(agent_id, conv_id, func_idx, attempt_idx)
    p.parent.mkdir(parents=True, exist_ok=True)
    with _lock_for(agent_id, conv_id):
        p.write_text("", encoding="utf-8")
    return p


def append_tree_event(agent_id: str, conv_id: str,
                      func_idx: int, attempt_idx: int,
                      record: dict) -> None:
    p = tree_path(agent_id, conv_id, func_idx, attempt_idx)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
    with _lock_for(agent_id, conv_id):
        with open(p, "a", encoding="utf-8") as f:
            f.write(line)


def write_tree_from_dict(agent_id: str, conv_id: str,
                         func_idx: int, attempt_idx: int,
                         tree_dict: dict) -> None:
    init_tree(agent_id, conv_id, func_idx, attempt_idx)
    for rec in _tree_dict_to_records(tree_dict):
        append_tree_event(agent_id, conv_id, func_idx, attempt_idx, rec)


def load_tree(agent_id: str, conv_id: str,
              func_idx: int, attempt_idx: int) -> Optional[dict]:
    p = tree_path(agent_id, conv_id, func_idx, attempt_idx)
    if not p.exists() or p.stat().st_size == 0:
        return None
    from openprogram.agentic_programming.context import Context
    try:
        return Context.from_jsonl(p)._to_dict()
    except Exception:
        return None


def list_func_indexes(agent_id: str, conv_id: str) -> list[int]:
    d = trees_dir(agent_id, conv_id)
    if not d.is_dir():
        return []
    out = []
    for child in d.iterdir():
        if child.is_dir() and child.name.isdigit():
            out.append(int(child.name))
    return sorted(out)


def list_attempt_indexes(agent_id: str, conv_id: str,
                         func_idx: int) -> list[int]:
    d = trees_dir(agent_id, conv_id) / str(func_idx)
    if not d.is_dir():
        return []
    out = []
    for f in d.iterdir():
        if f.is_file() and f.suffix == ".jsonl":
            stem = f.stem
            if stem.isdigit():
                out.append(int(stem))
    return sorted(out)


# ---------------------------------------------------------------------------
# Event records — built from a live Context
# ---------------------------------------------------------------------------

def ctx_to_enter_record(ctx) -> dict:
    params = ctx.params or {}
    sanitized = {
        k: (getattr(v, "__name__", None) or str(v)) if callable(v) else v
        for k, v in params.items()
        if k not in ("runtime", "callback")
    }
    return {
        "event": "enter",
        "path": ctx.path,
        "name": ctx.name,
        "node_type": getattr(ctx, "node_type", "function"),
        "prompt": getattr(ctx, "prompt", ""),
        "params": sanitized,
        "render": getattr(ctx, "render", "summary"),
        "compress": getattr(ctx, "compress", False),
        "ts": ctx.start_time,
    }


def ctx_to_exit_record(ctx) -> dict:
    return {
        "event": "exit",
        "path": ctx.path,
        "status": ctx.status,
        "output": ctx.output,
        "raw_reply": ctx.raw_reply,
        "attempts": ctx.attempts,
        "error": ctx.error or "",
        "duration_ms": ctx.duration_ms,
        "ts": ctx.end_time,
    }


def _tree_dict_to_records(node: dict) -> list[dict]:
    """Convert a serialized Context dict back into enter/exit records
    (pre-order for enter, post-order for exit)."""
    enter = {
        "event": "enter",
        "path": node.get("path"),
        "name": node.get("name"),
        "node_type": node.get("node_type", "function"),
        "prompt": node.get("prompt", ""),
        "params": node.get("params") or {},
        "render": node.get("render", "summary"),
        "compress": node.get("compress", False),
        "ts": node.get("start_time"),
    }
    records = [enter]
    for child in node.get("children") or []:
        records.extend(_tree_dict_to_records(child))
    records.append({
        "event": "exit",
        "path": node.get("path"),
        "status": node.get("status"),
        "output": node.get("output"),
        "raw_reply": node.get("raw_reply"),
        "attempts": node.get("attempts", []),
        "error": node.get("error", ""),
        "duration_ms": node.get("duration_ms"),
        "ts": node.get("end_time"),
    })
    return records


# ---------------------------------------------------------------------------
# Whole-conversation I/O
# ---------------------------------------------------------------------------

def list_conversations(agent_id: str = "") -> list[tuple[str, str]]:
    """Return ``[(agent_id, conv_id), ...]`` across SessionDB.

    With ``agent_id`` empty (default) we list every agent; otherwise
    just that agent. Was a filesystem walk; now an indexed query —
    O(log n) instead of O(n × dirs).
    """
    from openprogram.agent.session_db import default_db
    db = default_db()
    rows = db.list_sessions(agent_id=agent_id or None, limit=10_000)
    return [(r["agent_id"], r["id"]) for r in rows]


def load_conversation(agent_id: str, conv_id: str) -> Optional[dict]:
    """Return the conversation state dict (meta + messages + function_trees).

    Reads SessionDB for meta + messages; function_trees still come
    from the per-conv `trees/` JSONL files because that's a separate
    high-frequency event log used only during webui function execution.
    """
    from openprogram.agent.session_db import default_db
    db = default_db()
    sess = db.get_session(conv_id)
    if sess is None:
        return None
    if sess.get("agent_id") != agent_id:
        return None

    messages = db.get_messages(conv_id)

    function_trees: list = []
    for func_idx in list_func_indexes(agent_id, conv_id):
        attempts = list_attempt_indexes(agent_id, conv_id, func_idx)
        if not attempts:
            continue
        latest = attempts[-1]
        tree = load_tree(agent_id, conv_id, func_idx, latest)
        if tree is not None:
            function_trees.append(tree)

    result = dict(sess)
    result["id"] = conv_id
    result["agent_id"] = agent_id
    result["messages"] = messages
    result["function_trees"] = function_trees
    return result


def delete_conversation(agent_id: str, conv_id: str) -> None:
    from openprogram.agent.session_db import default_db
    default_db().delete_session(conv_id)
    d = conv_dir(agent_id, conv_id)
    if d.is_dir():
        shutil.rmtree(d)
    with _locks_guard:
        _locks.pop(_lock_key(agent_id, conv_id), None)


# The legacy-file migration shim is retired — fresh installs never had
# a visualizer_sessions.json, and users who used to have one were
# already migrated before this refactor.

def migrate_legacy_file() -> int:
    return 0
