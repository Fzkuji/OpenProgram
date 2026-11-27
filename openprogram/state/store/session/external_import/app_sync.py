"""Keep the Claude app's and DeepSeek Harness's conversations in OpenProgram.

The conversations of the Claude app's Code tab (its session list,
``claude-code-sessions/**/local_*.json``, each naming a Claude Code
transcript) and of the DeepSeek Harness app (``deepseek_harness``) are
OpenProgram conversations too, mixed with its own: each becomes a session
whose ``source`` names the app, and turns made in the app later are
appended to it, after whatever was said here since. A DeepSeek Harness
or Claude conversation goes on in its own session, on that agent's engine
(``runtime.harnesses.deepseek``, ``runtime.harnesses.claude_code``),
whose record of the session is written here; the turns the engine ran are
known by their ids (``own_turns``) and not written twice. Conversations
started with ``claude`` in a terminal come in too. Codex has its own
two-way sync (``codex_sync``).

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
from .codex_sync import REPAIRED, REPAIRS_PER_SYNC, _after, _picture_once, _place_once, _project_of, _unwrap_once, _tip, _write_records, thread_records
from .ledger import locked
from .model import AGENT_LABELS, CLAUDE_CODE, DEEPSEEK_HARNESS, ExternalSession
from .sanitize import remove_secret_values, remove_secrets
from .writer import node_prefix, reply_id, user_id, write_turns

_log = logging.getLogger(__name__)

#: The engine each app's conversations go on in: (provider, harness setting).
ENGINES = {
    DEEPSEEK_HARNESS: ("deepseek-harness", "deepseek-harness"),
    CLAUDE_CODE: ("claude-code-harness", "claude-code"),
}


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


def _transcripts(claude_home: Path) -> dict[str, Path]:
    """Every Claude Code transcript by session id; of copies in several project folders, the newest."""
    found: dict[str, tuple[float, Path]] = {}
    for path in (claude_home / "projects").glob("*/*.jsonl"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if path.stem not in found or mtime > found[path.stem][0]:
            found[path.stem] = (mtime, path)
    return {stem: path for stem, (_, path) in found.items()}


def _claude_items(app_dir: Path, transcripts: dict[str, Path], archived: set[str]) -> list[Item]:
    items = []
    for meta_path in (app_dir / "claude-code-sessions").glob("*/*/local_*.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        cli_id = meta.get("cliSessionId")
        if not cli_id:
            continue
        if meta.get("isArchived"):
            archived.add(str(cli_id))
            continue
        transcript = transcripts.get(cli_id)
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


#: A transcript's ``entrypoint``, by path: once written it never changes.
_entrypoints: dict[str, str] = {}


def _entrypoint(path: Path) -> Optional[str]:
    """The ``entrypoint`` of the session's own records: a fork's copied records carry its parent's."""
    key = str(path)
    if key not in _entrypoints:
        found = None
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for _, line in zip(range(200), fh):
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if (isinstance(record, dict) and record.get("entrypoint")
                            and record.get("sessionId", path.stem) == path.stem):
                        found = str(record["entrypoint"])
                        break
        except OSError:
            return None
        if found is None:
            return None  # not written yet: read again next time
        _entrypoints[key] = found
    return _entrypoints[key]


def _claude_cli_items(transcripts: dict[str, Path], listed: set[str]) -> list[Item]:
    """Conversations started in a terminal with ``claude``: their transcripts say ``cli``.

    Those run through the Agent SDK or ``claude -p`` (other programs' and this
    engine's own) are left out, as Codex's ``exec`` threads are, and so are the
    Claude app's own, archived ones too."""
    found = []
    for path in transcripts.values():
        if path.stem in listed or _entrypoint(path) != "cli":
            continue
        try:
            updated = path.stat().st_mtime
        except OSError:
            continue
        found.append(Item(CLAUDE_CODE, path.stem, "", None, updated, lambda path=path: claude_code.parse(path)))
    return found


def items(*, claude_app: Optional[Path] = None, claude_home: Optional[Path] = None,
          dsh_home: Optional[Path] = None) -> list[Item]:
    home = Path(claude_home or claude_code.default_home())
    transcripts = _transcripts(home)
    archived: set[str] = set()
    found = _claude_items(Path(claude_app or claude_app_dir()), transcripts, archived)
    found += _claude_cli_items(transcripts, {item.id for item in found} | archived)
    found += _deepseek_items(Path(dsh_home or deepseek_harness.default_home()))
    return found


def _create(store, item: Item, session: ExternalSession, agent_id: str) -> str:
    import uuid
    session_id = f"local_{uuid.uuid4()}"
    fields: dict[str, Any] = {"imported_from": {
        "agent": item.agent, "source_id": item.id, "path": str(session.path),
        "cwd": session.cwd, "imported_at": time.time(), "title": session.title or "",
    }}
    project_id, settled = _project_of(session.cwd)
    fields["imported_from"].update(placed=settled, **REPAIRED)  # written as typed, with its pictures
    if project_id:
        fields["project_id"] = project_id
    store.create_session(session_id, agent_id, title=session.title or AGENT_LABELS[item.agent],
                         source=item.agent, created_at=session.started_at or time.time(), **fields)
    last = write_turns(store, session_id, session)
    if last:
        store.set_head(session_id, last)
        _link(store, session_id, item, session, last)
    store.update_session(session_id, updated_at=item.updated_at or session.updated_at or time.time())
    store.commit_turn(session_id, f"sync from {AGENT_LABELS[item.agent]}")
    return session_id


def _model_ref(session: ExternalSession) -> Optional[str]:
    if not session.model:
        return None
    return f"{session.model_provider}:{session.model}" if session.model_provider else session.model


def _link(store, session_id: str, item: Item, session: ExternalSession, tip: str) -> None:
    """Run the conversation on its agent's engine, in the agent's own session."""
    provider, harness = ENGINES[item.agent]
    values: dict[str, Any] = {"provider_override": provider, "harness": harness}
    model = _model_ref(session)
    if model:
        values["model_override"] = model
    store.update_branch_config(session_id, None, values)
    _write_records(store, session_id, item.id, None, tip, provider=provider)


def _unlinked(row: dict, item: Item) -> bool:
    provider = ENGINES[item.agent][0]
    return not ((row.get("harness_threads") or {}).get(provider))


#: The branch settings a switch back to OpenProgram puts back (as the Harness chip keeps them).
_RETURN_FIELDS = ("provider_override", "model_override", "thinking_effort")


def _link_existing(store, session_id: str, item: Item, row: dict, entry: dict) -> None:
    """Put a conversation synced before its agent had an engine on that engine.

    Its session has seen the last turn synced from the app, not what was
    said here since: the engine hands it those."""
    from openprogram.state.store.session.branch_config import config_for_branch
    provider, harness = ENGINES[item.agent]
    current = config_for_branch(row, row.get("active_branch_id"))
    values: dict[str, Any] = {"provider_override": provider, "harness": harness,
                              "model_override": _model_ref(item.read()), "thinking_effort": None}
    if not current.get("harness"):
        values["harness_return"] = {key: current.get(key) for key in _RETURN_FIELDS}
    store.update_branch_config(session_id, None, values)
    tip = entry.get("last_node_id") or row.get("head_id")
    if tip:
        _write_records(store, session_id, item.id, None, tip, provider=provider)


def _last_written(store, session_id: str, item: Item, session: ExternalSession,
                  last: int) -> Optional[tuple[int, int]]:
    """(Its node number, its index now) of the newest turn written here that the app still has, when every turn
    written after it was one the app no longer counts as a turn (Claude Code's interruption
    notes, which an earlier reading took for turns); None otherwise, as for a rewind."""
    prefix = node_prefix(item.agent, item.id)
    index_of = {turn.fingerprint(): k for k, turn in enumerate(session.turns)}
    for node in range(last, -1, -1):
        found = store.get_messages_by_id(session_id, [user_id(prefix, node)])
        written = found[0] if found else {}
        index = index_of.get(written.get("import_fingerprint"))
        if index is not None:
            return node, index
        if not str(written.get("content") or "").startswith(claude_code.INTERRUPTED):
            return None  # a turn the app took back: what is here stays as it is
    return None


from .engine_turns import Busy as _Busy  # noqa: E402 - an engine turn started after the reading


def _append(store, session_id: str, item: Item, session: ExternalSession, entry: dict,
            since: int = 0) -> int:
    """Write the turns the app added since the last sync; how many. ``since`` is how many
    engine turns had started in the agent session when it was read (``engine_turns.started``)."""
    done = int(entry.get("turns") or 0)
    offset = int(entry.get("offset") or 0)
    if done and (done > len(session.turns)
                 or session.turns[done - 1].fingerprint() != entry.get("last_fingerprint")):
        # The last turn written, found earlier: an earlier reading counted turns this one
        # does not (Claude Code's interruption notes). New turns are numbered past it.
        last = done - 1 + offset
        written = _last_written(store, session_id, item, session, last)
        if written is None:
            return -1  # rewound in the app: what is here stays as it is
        node, found = written
        # New turns are numbered after every node written, the notes' too. The last turn
        # written may still have been running then, unless it was a note skipped here.
        offset = last - found
        done = found + 1
        rerun = node == last
    else:
        rerun = True
    entry["offset"] = offset
    row = store.get_session(session_id) or {}
    _retitle(store, session_id, row, session)
    tip = entry.get("last_node_id")
    provider = ENGINES[item.agent][0]
    records = thread_records(row, item.id, provider=provider)
    tip = _tip(records, tip)
    own = {t for r in records for t in (r.get("own_turns") or [])}
    # After what the conversation shows now: when it went on here, the app's new turns
    # follow that; when another branch is shown, they follow the thread's own.
    after, on_head = _after(store, session_id, row, tip)

    shown: Optional[list[str]] = None

    def engine_ran(turn) -> bool:
        nonlocal shown
        if own and turn.turn_id in own:
            return True
        if not records or turn.blocks or turn.turn_id:
            return False
        # A message an engine here sent and stopped before any answer: DeepSeek Harness
        # names no id for it, but the conversation shows it already (after any handoff note).
        if shown is None:
            # Messages sent here, as the app's reading has them (secrets taken out).
            shown = [remove_secret_values(str(m.get("content") or "")).strip()
                     for m in store.get_branch(session_id, after)
                     if m.get("role") == "user" and m.get("source") != "import"] if after != "ROOT" else []
        text = turn.user_text.strip()
        for k, sent in enumerate(shown):
            if sent and (text == sent or text.endswith("\n\n" + sent)):
                del shown[k]  # one message sent here stands for one turn
                return True
        return False

    indices = [i for i in range(done, len(session.turns)) if not engine_ran(session.turns[i])]
    prefix = node_prefix(item.agent, item.id)
    # The last turn written may still have been running in the app then.
    # When another branch is shown, the thread's tip is its own last turn: rewritten in place.
    if rerun and done and tip == reply_id(prefix, done - 1 + offset) and (row.get("head_id") in (tip, None) or not on_head):
        if session.turns[done - 1].digest() != entry.get("last_digest"):
            indices.insert(0, done - 1)
    if not indices:
        return 0
    # A turn written before keeps its place.
    from .engine_turns import unchanged
    # Written only if no engine turn started since the reading: one that did may be in it,
    # its ids not yet kept. No turn starts while this writes.
    with unchanged((provider, item.id), since) as still:
        if not still:
            raise _Busy()
        last = write_turns(store, session_id, session, indices=indices, predecessor=after, offset=offset)
        if not last:
            return 0
        if on_head:
            store.set_head(session_id, last)
        if last != tip:
            # The session has these turns: the engine hands it only what follows.
            _write_records(store, session_id, item.id, tip, last, provider=provider)
        store.update_session(session_id, updated_at=item.updated_at or time.time())
        store.commit_turn(session_id, f"sync from {AGENT_LABELS[item.agent]}")
    return len([i for i in indices if i >= done])


def _retitle(store, session_id: str, row: dict, session: ExternalSession) -> None:
    """The app's newer title (it names a conversation after its first turn), unless renamed here."""
    imported = row.get("imported_from") if isinstance(row.get("imported_from"), dict) else {}
    synced = imported.get("title")
    if synced is None or not session.title or session.title == synced or row.get("title") != synced:
        return
    # Read again: the sync may have written other fields of it (``placed``) since ``row`` was read.
    now = (store.get_session(session_id) or {}).get("imported_from")
    imported = now if isinstance(now, dict) else imported
    store.update_session(session_id, title=session.title, imported_from={**imported, "title": session.title})


def _running(item: Item) -> bool:
    """An engine turn is running in the conversation's agent session right now."""
    from .engine_turns import is_running
    return is_running((ENGINES[item.agent][0], item.id))


def _engine_threads(store, agent: str) -> dict[str, str]:
    """Every agent session an engine ran a conversation in → the conversation here that holds it."""
    provider = ENGINES[agent][0]
    threads: dict[str, str] = {}
    try:
        # Every conversation: one switched back to OpenProgram still owns its agent sessions.
        rows = store.list_sessions(limit=1_000_000, include_archived=True)
    except Exception:  # noqa: BLE001
        return threads
    for listed in rows or []:
        row = store.get_session(listed.get("id")) or {}
        records = (row.get("harness_threads") or {}).get(provider) or {}
        for record in records.values():
            if isinstance(record, dict) and record.get("thread_id"):
                threads.setdefault(str(record["thread_id"]), str(listed.get("id")))
    return threads


def sync(*, store=None, ledger_root: Optional[Path] = None, agent_id: Optional[str] = None,
         found: Optional[list[Item]] = None, settle: float = 0.0) -> list[dict[str, Any]]:
    """Bring every changed conversation in; one row per conversation that changed."""
    from .service import _default_agent_id, _default_store
    store = store or _default_store()
    found = items() if found is None else found
    results: list[dict[str, Any]] = []
    if not found:
        return results
    engine_threads: dict[str, dict[str, str]] = {}
    unwraps, pictures = [REPAIRS_PER_SYNC], [REPAIRS_PER_SYNC]
    with locked(ledger_root) as ledger:
        for item in found:
            entry = ledger.get(item.agent, item.id) or {}
            seen_at = float(entry.get("last_message_at") or 0)
            session_id = entry.get("session_id")
            row = store.get_session(session_id) if session_id else None
            alive = row is not None
            if not alive and (not entry or (entry.get("engine") and not session_id and item.agent == DEEPSEEK_HARNESS)):
                # A session an engine started or forked here is that conversation's, not a new one:
                # turns made in the app later go to it, past those the engine ran.
                if item.agent not in engine_threads:
                    engine_threads[item.agent] = _engine_threads(store, item.agent)
                owner = engine_threads[item.agent].get(item.id)
                if owner and item.agent != DEEPSEEK_HARNESS:
                    # A Claude Code fork starts with copies of its parent's records, turns already
                    # here: its later turns are not read (its own app does not list it either).
                    ledger.put(item.agent, item.id, {"engine": True})
                    continue
                if owner:
                    entry = {"engine": True, "session_id": owner, "turns": 0}
                    ledger.put(item.agent, item.id, entry)  # known from now on, even once deleted
                    session_id = owner
                    row = store.get_session(owner)
                    alive = row is not None
            if entry.get("engine") and not alive:
                continue  # its conversation here was deleted: never brought back as a new one
            engine = {"engine": True, "session_id": session_id} if entry.get("engine") else {}
            if alive:
                _place_once(store, session_id, row, item.cwd)
                _unwrap_once(store, session_id, unwraps)
                _picture_once(store, session_id, entry, item.agent, item.id,
                              lambda item=item: remove_secrets(item.read()), pictures)
            if alive and _unlinked(row, item):
                try:
                    _link_existing(store, session_id, item, row, entry)  # synced before its engine existed
                except Exception:  # noqa: BLE001 - tried again next time
                    _log.warning("%s conversation %s not put on its engine", item.agent, item.id, exc_info=True)
            if (alive or entry.get("empty")) and item.updated_at <= seen_at + 0.5:
                continue
            if alive and _running(item):
                continue  # its turn is still being written by an engine here: read it once that ends
            if settle and time.time() - item.updated_at < settle:
                # Still being written: read once it rests, not on every poll while it streams.
                results.append({"agent": item.agent, "source_id": item.id, "status": "deferred"})
                continue
            from .engine_turns import started
            since = started((ENGINES[item.agent][0], item.id))
            try:
                session = remove_secrets(item.read())
                # A last message with no answer and no id yet is read once it has one; one
                # left like that is a message stopped before any answer, written as such.
                waiting = False
                if time.time() - item.updated_at < UNANSWERED_SECONDS:
                    written = int(entry.get("turns") or 0) if alive else 0
                    while (len(session.turns) > written and not session.turns[-1].blocks
                           and not session.turns[-1].turn_id):
                        session.turns.pop()
                        waiting = True
                if not any(t.blocks for t in session.turns):
                    ledger.put(item.agent, item.id, {**engine, "empty": True, "path": str(session.path),
                                                     "last_message_at": item.updated_at - 1 if waiting else item.updated_at})
                    if waiting:
                        results.append({"agent": item.agent, "source_id": item.id, "status": "deferred"})
                    continue
                if alive:
                    try:
                        added = _append(store, session_id, item, session, entry, since)
                    except _Busy:
                        # An engine turn started there since it was read: read it again after.
                        results.append({"agent": item.agent, "source_id": item.id, "status": "deferred"})
                        continue
                    if added < 0:
                        # Rewound in the app: the ledger stays at what was written, so turns
                        # added later are never put after ones the app took back.
                        ledger.put(item.agent, item.id, {**entry, "last_message_at": item.updated_at})
                        continue
                    status = "appended" if added else "unchanged"
                else:
                    session_id = _create(store, item, session, agent_id or _default_agent_id())
                    status, added = "created", len(session.turns)
                now = store.get_session(session_id) or {}
                # The thread's last turn here (not the head, when another branch is shown).
                head = _tip(thread_records(now, item.id, provider=ENGINES[item.agent][0]), now.get("head_id"))
                ledger.put(item.agent, item.id, {
                    **engine, "session_id": session_id, "path": str(session.path),
                    "turns": len(session.turns), "last_node_id": head,
                    "last_fingerprint": session.turns[-1].fingerprint(),
                    "last_digest": session.turns[-1].digest(),
                    # One waiting for its answer is read again until it has one or stops waiting.
                    "last_message_at": item.updated_at - 1 if waiting else item.updated_at,
                    "imported_at": time.time(),
                    "offset": int(entry.get("offset") or 0) if alive else 0,
                })
                results.append({"agent": item.agent, "source_id": item.id, "session_id": session_id,
                                "status": status, "turns_added": added, "waiting": waiting,
                                "title": remove_secret_values(session.title)})
            except Exception as exc:  # noqa: BLE001 - one unreadable conversation must not stop the rest
                _log.warning("%s conversation %s not synced", item.agent, item.id, exc_info=True)
                results.append({"agent": item.agent, "source_id": item.id, "status": "failed", "error": str(exc)})
    return results


#: How often the apps' lists are checked for a change.
POLL_SECONDS = 10.0
#: How long a conversation's file rests before it is read: a session that is
#: streaming is parsed once it pauses, not on every poll.
SETTLE_SECONDS = 4.0
#: How long a last message with no answer and no id waits for one before it is written as stopped.
UNANSWERED_SECONDS = 60.0


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
                results = await asyncio.to_thread(sync, found=found, settle=SETTLE_SECONDS)
                if any(r.get("status") in ("created", "appended") for r in results):
                    notify()
                if any(r.get("status") == "deferred" or r.get("waiting") for r in results):
                    seen = ()  # what was left until it rests is read on the next poll
        except Exception:  # noqa: BLE001 - the next change tries again
            _log.warning("app conversations not synced", exc_info=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_SECONDS)
        except asyncio.TimeoutError:
            pass
