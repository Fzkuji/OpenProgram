"""Keep the Claude app's and DeepSeek Harness's conversations in OpenProgram.

The conversations of the Claude app's Code tab (its session list,
``claude-code-sessions/**/local_*.json``, each naming a Claude Code
transcript) and of the DeepSeek Harness app (``deepseek_harness``) are
OpenProgram conversations too, mixed with its own: each becomes a session
whose ``source`` names the app, and turns made in the app later are
appended to it, after whatever was said here since. OpenProgram has no
engine for these agents yet, so a turn sent here runs in OpenProgram: the
conversation moves here and the app keeps its own copy. Codex, which has
an engine, has its own two-way sync (``codex_sync``).

A conversation is read again only when the app records newer activity;
an archived one is left out. The import ledger is shared with
``openprogram sessions import``, so a conversation imported by hand
earlier is the one the app's new turns go to.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from . import claude_code, deepseek_harness
from .codex_sync import _project_of
from .ledger import locked
from .model import AGENT_LABELS, CLAUDE_CODE, DEEPSEEK_HARNESS, ExternalSession
from .sanitize import remove_secret_values, remove_secrets
from .writer import node_prefix, reply_id, write_turns

_log = logging.getLogger(__name__)


@dataclass
class Item:
    """One conversation an app lists."""
    agent: str
    id: str
    title: str
    cwd: Optional[str]
    updated_at: float
    read: Callable[[], ExternalSession]


def claude_app_dir() -> Path:
    """Where the Claude desktop app keeps its data."""
    if os.environ.get("CLAUDE_APP_DIR"):
        return Path(os.environ["CLAUDE_APP_DIR"])
    return Path.home() / "Library" / "Application Support" / "Claude"


def _claude_items(app_dir: Path, claude_home: Path) -> list[Item]:
    items = []
    for meta_path in (app_dir / "claude-code-sessions").glob("*/*/local_*.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        cli_id = meta.get("cliSessionId")
        if not cli_id or meta.get("isArchived"):
            continue
        transcript = next((claude_home / "projects").glob(f"*/{cli_id}.jsonl"), None)
        if transcript is None:
            continue
        title = str(meta.get("title") or "")
        model = str(meta.get("model") or "")

        def read(path=transcript, title=title, model=model, cwd=meta.get("cwd")) -> ExternalSession:
            session = claude_code.parse(path)
            session.title = title or session.title
            session.cwd = session.cwd or cwd
            session.model = session.model or model
            return session

        updated = float(meta.get("lastActivityAt") or 0) / 1000 or transcript.stat().st_mtime
        items.append(Item(CLAUDE_CODE, cli_id, title, meta.get("cwd"), updated, read))
    return items


def _deepseek_items(home: Path) -> list[Item]:
    return [Item(DEEPSEEK_HARNESS, c.id, "", c.cwd, c.updated_at, lambda c=c: deepseek_harness.parse(c))
            for c in deepseek_harness.conversations(home)]


def items(*, claude_app: Optional[Path] = None, claude_home: Optional[Path] = None,
          dsh_home: Optional[Path] = None) -> list[Item]:
    found = _claude_items(Path(claude_app or claude_app_dir()), Path(claude_home or claude_code.default_home()))
    found += _deepseek_items(Path(dsh_home or deepseek_harness.default_home()))
    return found


def _create(store, item: Item, session: ExternalSession, agent_id: str) -> str:
    import uuid
    session_id = f"local_{uuid.uuid4()}"
    fields: dict[str, Any] = {"imported_from": {
        "agent": item.agent, "source_id": item.id, "path": str(session.path),
        "cwd": session.cwd, "imported_at": time.time(),
    }}
    project_id = _project_of(session.cwd)
    if project_id:
        fields["project_id"] = project_id
    store.create_session(session_id, agent_id, title=session.title or AGENT_LABELS[item.agent],
                         source=item.agent, created_at=session.started_at or time.time(), **fields)
    if session.model:
        store.update_branch_config(session_id, None, {"model_override": session.model})
    last = write_turns(store, session_id, session)
    if last:
        store.set_head(session_id, last)
    store.update_session(session_id, updated_at=item.updated_at or session.updated_at or time.time())
    store.commit_turn(session_id, f"sync from {AGENT_LABELS[item.agent]}")
    return session_id


def _append(store, session_id: str, item: Item, session: ExternalSession, entry: dict) -> int:
    """Write the turns the app added since the last sync; how many."""
    done = int(entry.get("turns") or 0)
    if done and (done > len(session.turns)
                 or session.turns[done - 1].fingerprint() != entry.get("last_fingerprint")):
        return 0  # rewound in the app: what is here stays as it is
    row = store.get_session(session_id) or {}
    tip = entry.get("last_node_id")
    indices = list(range(done, len(session.turns)))
    prefix = node_prefix(item.agent, item.id)
    # The last turn written may still have been running in the app then.
    if done and tip == reply_id(prefix, done - 1) and row.get("head_id") in (tip, None):
        if session.turns[done - 1].digest() != entry.get("last_digest"):
            indices.insert(0, done - 1)
    if not indices:
        return 0
    # After what the conversation shows now: when it went on here, the
    # app's new turns follow that. A turn written before keeps its place.
    head = row.get("head_id") or tip or "ROOT"
    last =write_turns(store, session_id, session, indices=indices, predecessor=head)
    if not last:
        return 0
    store.set_head(session_id, last)
    store.update_session(session_id, updated_at=item.updated_at or time.time())
    store.commit_turn(session_id, f"sync from {AGENT_LABELS[item.agent]}")
    return len([i for i in indices if i >= done])


def sync(*, store=None, ledger_root: Optional[Path] = None, agent_id: Optional[str] = None,
         found: Optional[list[Item]] = None) -> list[dict[str, Any]]:
    """Bring every changed conversation in; one row per conversation that changed."""
    from .service import _default_agent_id, _default_store
    store = store or _default_store()
    found = items() if found is None else found
    results: list[dict[str, Any]] = []
    if not found:
        return results
    with locked(ledger_root) as ledger:
        for item in found:
            entry = ledger.get(item.agent, item.id) or {}
            seen_at = float(entry.get("last_message_at") or 0)
            session_id = entry.get("session_id")
            alive = bool(session_id) and store.get_session(session_id) is not None
            if (alive or entry.get("empty")) and item.updated_at <= seen_at + 0.5:
                continue
            try:
                session = remove_secrets(item.read())
                if not any(t.blocks for t in session.turns):
                    ledger.put(item.agent, item.id, {"empty": True, "path": str(session.path),
                                                     "last_message_at": item.updated_at})
                    continue
                if alive:
                    added = _append(store, session_id, item, session, entry)
                    status = "appended" if added else "unchanged"
                else:
                    session_id = _create(store, item, session, agent_id or _default_agent_id())
                    status, added = "created", len(session.turns)
                head = (store.get_session(session_id) or {}).get("head_id")
                ledger.put(item.agent, item.id, {
                    "session_id": session_id, "path": str(session.path),
                    "turns": len(session.turns), "last_node_id": head,
                    "last_fingerprint": session.turns[-1].fingerprint(),
                    "last_digest": session.turns[-1].digest(),
                    "last_message_at": item.updated_at, "imported_at": time.time(),
                })
                results.append({"agent": item.agent, "source_id": item.id, "session_id": session_id,
                                "status": status, "turns_added": added,
                                "title": remove_secret_values(session.title)})
            except Exception as exc:  # noqa: BLE001 - one unreadable conversation must not stop the rest
                _log.warning("%s conversation %s not synced", item.agent, item.id, exc_info=True)
                results.append({"agent": item.agent, "source_id": item.id, "status": "failed", "error": str(exc)})
    return results


#: How often the apps' lists are checked for a change.
POLL_SECONDS = 10.0


def _stamp(found: list[Item]) -> tuple:
    return tuple(sorted((item.agent, item.id, round(item.updated_at, 3)) for item in found))


async def run_sync(stop, notify) -> None:
    """Sync at start, then whenever an app's list moves on, until ``stop``.

    ``notify`` is called after a sync that changed a session."""
    import asyncio
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return  # a test's Worker never reads the person's own apps
    seen: tuple = ()
    while not stop.is_set():
        try:
            found = await asyncio.to_thread(items)
            stamp = _stamp(found)
            if stamp != seen:
                seen = stamp
                results = await asyncio.to_thread(sync, found=found)
                if any(r.get("status") in ("created", "appended") for r in results):
                    notify()
        except Exception:  # noqa: BLE001 - the next change tries again
            _log.warning("app conversations not synced", exc_info=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_SECONDS)
        except asyncio.TimeoutError:
            pass
