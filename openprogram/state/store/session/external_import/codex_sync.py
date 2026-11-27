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
from typing import Any, Callable, Optional

from . import codex, codex_threads
from .ledger import locked
from .model import CODEX, ExternalSession
from .sanitize import remove_secret_values, remove_secrets
from .writer import node_prefix, reply_id, user_id, with_pictures, write_turns

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
                # The thread went on in the app: a failed turn's retry position no longer holds.
                kept = {k: v for k, v in record.items() if k not in ("prior", "held_text")}
                records[key] = {**kept, "seen": new_tip, "at": time.time(), "synced_at": time.time()}
                moved = True
        if not moved:
            old = records.get(SHARED_KEY)
            key = SHARED_KEY
            if isinstance(old, dict) and old.get("thread_id") not in (None, thread_id):
                # The main branch runs another thread: this one's record stands beside it.
                key = f"shared:{thread_id}"
                old = records.get(key)
            kept = old if isinstance(old, dict) and old.get("thread_id") == thread_id else {}
            records[key] = {**{k: v for k, v in kept.items() if k not in ("prior", "held_text")},
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


def _folder_project(cwd: Optional[str]) -> Optional[str]:
    """The project a synced conversation's folder belongs in: the registered one that holds it,
    else a new one, as an import makes. None (the default project) for no folder, the home
    folder, one that is gone, one an app makes for each chat (the Codex app's
    ``~/Documents/Codex/<day>/…``, the Claude app's scratch workspaces), a temporary one, a
    folder whose project the person removed, or one in a project that runs on another machine. Raises when the projects cannot be read or made."""
    if not cwd or _chat_folder(cwd):
        return None
    from openprogram.state.store.project.identity import read_marker
    from openprogram.state.store.project.project_store import (
        LOCAL_MACHINE_ID, folder_is_here, list_projects, resolve_project,
    )
    target = Path(cwd).expanduser().resolve()
    # Projects whose folders are on this computer's disk; one that runs on another machine (a
    # Docker one) keeps its folder's conversations out: a synced one stays in the default project.
    registered = [p for p in list_projects() if not p.is_default and p.path]
    projects = [p for p in registered if folder_is_here(p)]

    def elsewhere(project) -> bool:
        return project.hidden or (project.machine_id or LOCAL_MACHINE_ID) != LOCAL_MACHINE_ID
    best, depth = None, -1
    for project in projects:
        root = Path(project.path).expanduser().resolve()
        if (target == root or root in target.parents) and len(root.parts) > depth:
            best, depth = project, len(root.parts)
    if best is not None:
        return None if elsewhere(best) else best.id
    home = Path.home().resolve()
    if target == home or not target.is_dir():
        return None
    # A folder in, or under, one carrying the marker of a removed project or of another
    # machine's (the project moved since) is left to the person to open; a sync files nothing there.
    for folder in (target, *target.parents):
        if folder == home or folder == folder.parent:
            break
        marker = read_marker(folder)
        owner = next((p for p in registered if p.directory_identity == marker), None) if marker else None
        if owner is not None and elsewhere(owner):
            return None
    return resolve_project(target).id


#: How many times a conversation whose placement failed is tried, and the first wait before another try.
_PLACE_TRIES = 5
_PLACE_RETRY_SECONDS = 60.0


def _project_of(cwd: Optional[str]) -> tuple[Optional[str], bool]:
    """A new synced conversation's project, and whether that is settled (not a failed read)."""
    try:
        return _folder_project(cwd), True
    except Exception:  # noqa: BLE001 - the default project for now; placed on a later sync
        _log.warning("project lookup failed for %s", cwd, exc_info=True)
        return None, False


def _place_once(store, session_id: str, row: dict, cwd: Optional[str]) -> None:
    """Move a conversation synced before its folder got a project into that project, once.
    One the person put in a project stays; one with a turn running, or whose move failed,
    is tried again on a later sync."""
    imported = row.get("imported_from")
    if not isinstance(imported, dict) or imported.get("placed"):
        return
    try:
        if float(imported.get("place_after") or 0) > time.time():
            return  # it failed lately: tried again after a while, not on every poll
        from openprogram.state.execution_facts import turn_running
        from openprogram.state.store.project import project_store as _projects
        from openprogram.state.store.project.relocate_node import record_move
        current = _projects.project_for_session(session_id)
        if current is None or current.is_default:
            project_id = _folder_project(cwd)
            target = _projects.get_project(project_id) if project_id else None
            if target is not None:
                if turn_running(session_id):
                    return  # tried again once the turn ends
                if not store.move_session(session_id, project_id).get("moved"):
                    raise RuntimeError(f"not moved to {project_id}")
                record_move(store, session_id, old_project_id=current.id if current else "",
                            new_project_id=project_id, old_name=current.name if current else "",
                            new_name=target.name)
        store.update_session(session_id, imported_from={**imported, "placed": True})
    except Exception:  # noqa: BLE001 - tried again later, and given up after a few tries
        tries = int(imported.get("place_tries") or 0) + 1
        _log.warning("synced conversation %s not placed in its folder's project (try %d)", session_id, tries,
                     exc_info=True)
        try:
            store.update_session(session_id, imported_from={
                **imported, "place_tries": tries, "place_after": time.time() + _PLACE_RETRY_SECONDS * 2 ** (tries - 1),
                **({"placed": True} if tries >= _PLACE_TRIES else {})})
        except Exception:  # noqa: BLE001
            _log.debug("placement retry not recorded for %s", session_id, exc_info=True)


#: How many conversations synced before a sync kept them as typed (``_unwrap_once``)
#: or kept their pictures (``_picture_once``) get that on one sync.
REPAIRS_PER_SYNC = 20
#: How often a conversation's repair is tried before it is left as it is.
_REPAIR_TRIES = 3
#: What a conversation synced since both repairs existed is marked with when made.
REPAIRED = {"unwrapped": True, "pictured": True}


def _repair_once(store, session_id: str, flag: str, budget: list[int], repair: Callable[[dict, dict], dict]) -> None:
    """Run ``repair(row, imported_from)`` on a conversation once, ``flag`` marking it
    done in ``imported_from``; a failure is tried again on a later sync, a few times.
    ``budget`` is how many more conversations this sync may repair."""
    row = store.get_session(session_id) or {}
    imported = row.get("imported_from")
    if not isinstance(imported, dict) or imported.get(flag) or budget[0] <= 0:
        return
    budget[0] -= 1
    try:
        fields = repair(row, imported)
        done = {flag: True}
    except Exception:  # noqa: BLE001 - tried again on a later sync
        tries = int(imported.get(f"{flag}_tries") or 0) + 1
        _log.warning("synced conversation %s: %s repair failed (try %d)", session_id, flag, tries, exc_info=True)
        fields, done = {}, {f"{flag}_tries": tries, **({flag: True} if tries >= _REPAIR_TRIES else {})}
    try:
        # Read again: the sync may have written other fields of it (``placed``) since.
        now = (store.get_session(session_id) or {}).get("imported_from")
        now = now if isinstance(now, dict) else imported
        synced = fields.pop("imported_from", {})
        store.update_session(session_id, **fields, imported_from={**now, **synced, **done})
    except Exception:  # noqa: BLE001
        _log.debug("repair mark not recorded for %s", session_id, exc_info=True)


def _unwrap_once(store, session_id: str, budget: list[int]) -> None:
    """Show the messages OpenProgram once sent to the agent in its speaker envelope, and a
    title named after one, as the person typed them; once per conversation."""
    from openprogram.state.authority import unwrap_owner_input
    from .sanitize import ENVELOPE_TITLE
    from .timeparse import fallback_title

    def repair(row: dict, imported: dict) -> dict:
        first = None
        for message in store.get_messages(session_id):
            text = message.get("content")
            if message.get("role") != "user" or not isinstance(text, str):
                continue
            plain = unwrap_owner_input(text)
            first = plain if first is None else first
            if plain != text:
                store.merge_node(session_id, message["id"], lambda node, old=text, new=plain:
                                 {"output": new} if node.output == old else None)
        title = str(row.get("title") or "")
        if not (title.lstrip().startswith(ENVELOPE_TITLE) and first):
            return {}
        renamed = remove_secret_values(fallback_title(first))
        synced = {"imported_from": {"title": renamed}} if imported.get("title") == title else {}
        return {"title": renamed, **synced}

    _repair_once(store, session_id, "unwrapped", budget, repair)


def _picture_once(store, session_id: str, entry: dict, agent: str, source_id: str,
                  read: Callable[[], ExternalSession], budget: list[int]) -> None:
    """Show the pictures of a conversation synced while they were left as ``[image]``,
    once: each message this sync wrote, still as it was, gets them."""
    def repair(_row: dict, _imported: dict) -> dict:
        session = read()
        prefix = node_prefix(agent, source_id)
        offset = int(entry.get("offset") or 0)
        for index, turn in enumerate(session.turns):
            if not any(data for data, _mime, _place in turn.images):
                continue
            node_id = user_id(prefix, index + offset)
            found = store.get_messages_by_id(session_id, [node_id])
            if not found or found[0].get("import_fingerprint") != turn.fingerprint():
                continue  # not the message this turn wrote
            stored = found[0].get("content")
            if not isinstance(stored, str) or found[0].get("media"):
                continue  # written with its pictures already
            # Placed in the text as it is stored (unwrapped, redacted when it was written).
            shown, media = with_pictures(store, session_id, turn, stored)
            if media:
                store.merge_node(session_id, node_id, lambda node, was=stored, shown=shown, media=media:
                                 {"output": shown, "metadata": {**(node.metadata or {}), "media": media}}
                                 if node.output == was else None)
        return {}

    _repair_once(store, session_id, "pictured", budget, repair)


def _chat_folder(cwd: str) -> bool:
    """A folder an app made for one chat, or a temporary one."""
    import tempfile
    try:
        target = Path(cwd).expanduser().resolve()
        home = Path.home().resolve()
        roots = [home / "Documents" / "Codex", home / "Library" / "Application Support" / "Claude",
                 Path(tempfile.gettempdir()).resolve(), Path("/private/tmp"), Path("/tmp").resolve()]
    except (OSError, RuntimeError):
        return True
    return any(target == root or root in target.parents for root in roots)


def _create(store, thread: codex_threads.Thread, session: ExternalSession, agent_id: str) -> str:
    import uuid
    session_id = f"local_{uuid.uuid4()}"
    fields: dict[str, Any] = {"imported_from": {
        "agent": CODEX, "source_id": thread.id, "path": str(thread.path),
        "cwd": session.cwd, "imported_at": time.time(),
    }}
    project_id, settled = _project_of(session.cwd)
    fields["imported_from"].update(placed=settled, **REPAIRED)  # written as typed, with its pictures
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
        # The head and record move with the turns, so no engine turn starts between them.
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
    unwraps, pictures = [REPAIRS_PER_SYNC], [REPAIRS_PER_SYNC]
    with locked(ledger_root) as ledger:
        for thread in found:
            entry = ledger.get(CODEX, thread.id) or {}
            seen_at = float(entry.get("last_message_at") or 0)
            session_id = entry.get("session_id")
            row = store.get_session(session_id) if session_id else None
            alive = row is not None
            if alive:
                _place_once(store, session_id, row, thread.cwd)
                _unwrap_once(store, session_id, unwraps)
                _picture_once(store, session_id, entry, CODEX, thread.id, lambda thread=thread: _parse(thread), pictures)
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
