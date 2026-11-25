"""Keep the person's Codex conversations in OpenProgram, linked both ways.

Every conversation the Codex app or the Codex CLI has (``codex_threads``)
becomes an OpenProgram session that runs in Codex: its harness is Codex,
its model the thread's, and its thread record (``harness_threads``) names
the Codex thread with the last message the thread saw, so the next turn
sent here continues the same Codex thread (``thread/resume``) and the app
shows it too. Turns made in the app later are appended here; turns
OpenProgram itself ran on the thread, which Codex also writes to the
rollout, are recognised by their Codex turn id (kept in the record as
``own_turns``) and skipped. Switching the session's harness to OpenProgram
moves the conversation here; what is said in the app still arrives.

A thread OpenProgram's engine started is already a session here: only the
turns made in the app after it was first seen are appended to it.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Optional

from . import codex, codex_threads
from .ledger import locked
from .model import CODEX, ExternalSession
from .sanitize import remove_secret_values, remove_secrets
from .writer import node_prefix, reply_id, write_turns

_log = logging.getLogger(__name__)

HARNESS_PROVIDER = "codex-harness"
HARNESS_ID = "codex"
THREADS_FIELD = "harness_threads"
#: Where a gateway model's thread runs, and the prefix of its model here.
GATEWAY_PROVIDER = "openprogram"
GATEWAY_PREFIX = "openprogram:"
#: The record a synced thread is kept under before any turn runs here.
SHARED_KEY = "main"
SOURCE = "codex"


def model_ref(thread: codex_threads.Thread) -> Optional[str]:
    """The session's model for a thread: Codex's own, or the gateway's."""
    if not thread.model:
        return None
    return GATEWAY_PREFIX + thread.model if thread.provider == GATEWAY_PROVIDER else thread.model


def thread_records(row: dict, thread_id: str, *, provider: str = HARNESS_PROVIDER) -> list[dict]:
    threads = (row.get(THREADS_FIELD) or {}).get(provider) or {}
    return [r for r in threads.values() if isinstance(r, dict) and r.get("thread_id") == thread_id]


def _tip(records: list[dict], fallback: Optional[str]) -> Optional[str]:
    """The last message the thread saw here: its newest record's."""
    seen = [r for r in records if r.get("seen")]
    if not seen:
        return fallback
    return max(seen, key=lambda r: float(r.get("at") or 0))["seen"]


def _after(store, session_id: str, row: dict, tip: Optional[str]) -> tuple[str, bool]:
    """Where the app's new turns go, and whether they become what the conversation shows:
    after the head when the thread's last turn here is on its branch (it went on here, or
    not at all); after that turn, leaving the head where it is, when another branch is shown."""
    head = row.get("head_id")
    if not head or not tip or head == tip:
        return head or tip or "ROOT", True
    if any(m.get("id") == tip for m in store.get_branch(session_id, head)):
        return head, True
    return tip, False


def _write_records(store, session_id: str, thread_id: str, old_tip: Optional[str], new_tip: str,
                   *, provider: str = HARNESS_PROVIDER) -> None:
    """Point the thread's records at ``new_tip``: each that was at the old tip, or the shared one."""
    def update(current: dict) -> dict:
        threads = dict(current or {})
        records = dict(threads.get(provider) or {})
        moved = False
        for key, record in list(records.items()):
            if isinstance(record, dict) and record.get("thread_id") == thread_id and record.get("seen") == old_tip:
                records[key] = {**record, "seen": new_tip, "at": time.time(), "synced_at": time.time()}
                moved = True
        if not moved:
            old = records.get(SHARED_KEY)
            key = SHARED_KEY
            if isinstance(old, dict) and old.get("thread_id") not in (None, thread_id):
                # The main branch runs another thread: this one's record stands beside it.
                key = f"shared:{thread_id}"
                old = records.get(key)
            records[key] = {**(old if isinstance(old, dict) and old.get("thread_id") == thread_id else {}),
                            "thread_id": thread_id, "seen": new_tip, "shared": True,
                            "at": time.time(), "synced_at": time.time()}
        threads[provider] = records
        return threads

    store.update_session_dict(session_id, THREADS_FIELD, update)


def _parse(thread: codex_threads.Thread) -> ExternalSession:
    session = codex.parse(thread.path, {thread.id: thread.title} if thread.title else None)
    session.cwd = session.cwd or thread.cwd
    return remove_secrets(session)


def _link(store, session_id: str, thread: codex_threads.Thread, tip: str) -> None:
    values: dict[str, Any] = {"provider_override": HARNESS_PROVIDER, "harness": HARNESS_ID}
    ref = model_ref(thread)
    if ref:
        values["model_override"] = ref
    store.update_branch_config(session_id, None, values)
    _write_records(store, session_id, thread.id, None, tip)


def _project_of(cwd: Optional[str]) -> Optional[str]:
    """The registered project whose folder holds ``cwd``. Unlike an import,
    none is made: the Codex app starts a folder for every chat."""
    if not cwd:
        return None
    try:
        from openprogram.state.store.project.project_store import list_projects
        target = Path(cwd).expanduser().resolve()
        best, depth = None, -1
        for project in list_projects():
            if project.is_default or not project.path:
                continue
            root = Path(project.path).expanduser().resolve()
            if (target == root or root in target.parents) and len(root.parts) > depth:
                best, depth = project.id, len(root.parts)
        return best
    except Exception:  # noqa: BLE001 - the default project then
        _log.debug("project lookup failed for %s", cwd, exc_info=True)
        return None


def _create(store, thread: codex_threads.Thread, session: ExternalSession, agent_id: str) -> str:
    import uuid
    session_id = f"local_{uuid.uuid4()}"
    fields: dict[str, Any] = {"imported_from": {
        "agent": CODEX, "source_id": thread.id, "path": str(thread.path),
        "cwd": session.cwd, "imported_at": time.time(),
    }}
    project_id = _project_of(session.cwd)
    if project_id:
        fields["project_id"] = project_id
    store.create_session(session_id, agent_id, title=session.title or "Codex", source=SOURCE,
                         created_at=session.started_at or time.time(), **fields)
    last = write_turns(store, session_id, session)
    if last:
        store.set_head(session_id, last)
        _link(store, session_id, thread, last)
    store.update_session(session_id, updated_at=thread.updated_at or session.updated_at or time.time())
    store.commit_turn(session_id, "sync from Codex")
    return session_id


def _append(store, session_id: str, thread: codex_threads.Thread, session: ExternalSession,
            entry: dict, since: int = 0) -> int:
    """Write the turns made outside OpenProgram since the last sync; how many."""
    row = store.get_session(session_id) or {}
    records = thread_records(row, thread.id)
    own = {t for r in records for t in (r.get("own_turns") or [])}
    done = int(entry.get("turns") or 0)
    tip = _tip(records, entry.get("last_node_id"))
    prefix = node_prefix(CODEX, thread.id)
    indices = [i for i in range(done, len(session.turns)) if session.turns[i].turn_id not in own]
    # After what the conversation shows now: when it moved to OpenProgram
    # and went on there, the app's new turns follow those.
    after, shown = _after(store, session_id, row, tip)
    # The last turn written may still have been running in Codex then; when another
    # branch is shown, the thread's tip is its own last turn, rewritten in place.
    if (0 < done <= len(session.turns) and tip == reply_id(prefix, done - 1)
            and (row.get("head_id") in (tip, None) or not shown)):
        indices.insert(0, done - 1)
    if not indices:
        return 0
    from .engine_turns import Busy, unchanged
    # Written only if no engine turn started since the reading: its turn ids are not kept yet.
    with unchanged((HARNESS_PROVIDER, thread.id), since) as still:
        if not still:
            raise Busy()
        last = write_turns(store, session_id, session, indices=indices, predecessor=after)
    if not last:
        return 0
    if shown:
        store.set_head(session_id, last)
    if last != tip:
        _write_records(store, session_id, thread.id, tip, last)
    store.update_session(session_id, updated_at=thread.updated_at or time.time())
    store.commit_turn(session_id, "sync from Codex")
    return len([i for i in indices if i >= done])


def _sessions_by_thread(store) -> dict[str, str]:
    """Thread id → the session whose Codex record names it."""
    found: dict[str, str] = {}
    for row in store.list_sessions(limit=100_000, include_archived=True):
        meta = store.get_session(row["id"]) or {}
        for record in ((meta.get(THREADS_FIELD) or {}).get(HARNESS_PROVIDER) or {}).values():
            if isinstance(record, dict) and record.get("thread_id"):
                found.setdefault(record["thread_id"], row["id"])
    return found


def sync(*, home: Optional[Path] = None, store=None, ledger_root: Optional[Path] = None,
         agent_id: Optional[str] = None) -> list[dict[str, Any]]:
    """Bring every changed Codex conversation in; one row per thread that changed."""
    from .service import _default_agent_id, _default_store
    home = Path(home or codex.default_home())
    store = store or _default_store()
    results: list[dict[str, Any]] = []
    found = codex_threads.threads(home)
    if not found:
        return results
    started_here: Optional[dict[str, str]] = None
    with locked(ledger_root) as ledger:
        for thread in found:
            entry = ledger.get(CODEX, thread.id) or {}
            seen_at = float(entry.get("last_message_at") or 0)
            session_id = entry.get("session_id")
            alive = bool(session_id) and store.get_session(session_id) is not None
            if entry.get("linked") and alive and thread.updated_at <= seen_at + 0.5:
                continue
            if entry.get("empty") and thread.updated_at <= seen_at + 0.5:
                continue
            from .engine_turns import Busy, is_running, started
            since = started((HARNESS_PROVIDER, thread.id))
            if is_running((HARNESS_PROVIDER, thread.id)):
                # An engine turn here is writing it: read once that turn ends and records its ids.
                results.append({"thread_id": thread.id, "status": "deferred"})
                continue
            try:
                session = _parse(thread)
                if not any(t.blocks for t in session.turns):
                    ledger.put(CODEX, thread.id, {"empty": True, "path": str(thread.path),
                                                  "last_message_at": thread.updated_at})
                    continue
                status, added = "appended", 0
                if not alive and (thread.originator or codex_threads.originator(thread.path)) == codex_threads.OPENPROGRAM_ORIGINATOR:
                    # Started here: what it had when first seen is here already.
                    if started_here is None:
                        started_here = _sessions_by_thread(store)
                    session_id = started_here.get(thread.id)
                    if session_id is None:
                        ledger.put(CODEX, thread.id, {"empty": True, "path": str(thread.path),
                                                      "last_message_at": thread.updated_at})
                        continue
                    entry = {"turns": len(session.turns)}
                    status = "unchanged"
                elif not alive:
                    session_id = _create(store, thread, session, agent_id or _default_agent_id())
                    status, added = "created", len(session.turns)
                else:
                    if not entry.get("linked"):
                        row = store.get_session(session_id) or {}
                        _link(store, session_id, thread, _tip([], entry.get("last_node_id")) or row.get("head_id") or "ROOT")
                    try:
                        added = _append(store, session_id, thread, session, entry, since)
                    except Busy:
                        # An engine turn started in it since it was read: read it again after.
                        results.append({"thread_id": thread.id, "status": "deferred"})
                        continue
                    status = "appended" if added else "unchanged"
                now = store.get_session(session_id) or {}
                # The thread's last turn here (not the head, when another branch is shown).
                head = _tip(thread_records(now, thread.id), now.get("head_id"))
                ledger.put(CODEX, thread.id, {
                    "session_id": session_id, "linked": True, "path": str(thread.path),
                    "turns": len(session.turns), "last_node_id": head,
                    "last_fingerprint": session.turns[-1].fingerprint(),
                    "last_digest": session.turns[-1].digest(),
                    "last_message_at": thread.updated_at, "imported_at": time.time(),
                })
                results.append({"thread_id": thread.id, "session_id": session_id, "status": status,
                                "turns_added": added, "title": remove_secret_values(session.title)})
            except Exception as exc:  # noqa: BLE001 - one unreadable thread must not stop the rest
                _log.warning("Codex thread %s not synced", thread.id, exc_info=True)
                results.append({"thread_id": thread.id, "status": "failed", "error": str(exc)})
    return results


#: How often the Codex state database is checked for a change.
POLL_SECONDS = 10.0


def _stamp(home: Path) -> tuple:
    db = codex_threads.state_db(home)
    if db is None:
        return ()
    stamps = []
    for path in (db, db.with_name(db.name + "-wal")):
        try:
            stat = path.stat()
            stamps.append((stat.st_mtime_ns, stat.st_size))
        except OSError:
            stamps.append(None)
    return tuple(stamps)


async def run_sync(stop, notify, *, home: Optional[Path] = None) -> None:
    """Sync at start, then whenever Codex writes its state database, until ``stop``.

    ``notify`` is called after a sync that changed a session."""
    import asyncio
    import os
    if home is None and os.environ.get("PYTEST_CURRENT_TEST"):
        return  # a test's Worker never reads the person's own Codex
    home = Path(home or codex.default_home())
    seen: tuple = ()
    while not stop.is_set():
        stamp = _stamp(home)
        if stamp and stamp != seen:
            seen = stamp
            try:
                results = await asyncio.to_thread(sync, home=home)
                if any(r.get("status") in ("created", "appended") for r in results):
                    notify()
                if any(r.get("status") == "deferred" for r in results):
                    seen = ()  # a thread left while its engine turn ran is read on the next poll
            except Exception:  # noqa: BLE001 - the next change tries again
                _log.warning("Codex conversations not synced", exc_info=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_SECONDS)
        except asyncio.TimeoutError:
            pass
