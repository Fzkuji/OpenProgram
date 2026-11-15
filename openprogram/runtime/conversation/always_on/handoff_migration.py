"""Move everything the removed ``handoff`` tool left onto ``send_message``.

The always-on hand-off saved every message and reply turn in
``<state>/always_on/handoffs.json``. The model now reaches another
conversation with ``send_message``, whose delivery is a tracked Job and
whose reply comes back as a notice with no timeout, and the hand-off's own
replay is gone. Every entry moves:

* a waiting message (``reply_to`` set) is delivered by ``send_message``
  from its sender to its target, carrying its hop count;
* a waiting reply is delivered by ``send_message`` from the conversation
  that answered (named in the reply's own text) to the one that asked;
* a turn already running or paused that owes its sender a reply becomes a
  watch (``notices.watch``): the sender gets the conversation's latest
  reply once it is idle, which is what the old replay sent when the turn
  ended;
* the earlier conversations an entry also owes (``owed_after``, nearest
  last) each get the answer of the one below them once it has answered
  (``notices.hold_reply``), the chain the old replay carried on;
* a delivery ``send_message`` refuses (``[send_message error]``) stays
  pending under a new Job id and is tried again at the next Worker start;
* finished entries, and waiting ones with nothing to deliver or nobody to
  deliver it from, are only kept in the record.

Every entry is kept, as it was, in ``handoffs.migrated.json`` beside its
delivery's state and ``execution_id``, and ``handoffs.json`` is copied
once to ``handoffs.before-migration.json`` before anything changes, so old
hand-offs stay readable. The transcripts' 📨 and ↩️ rows are not touched.

A dry run (the default of :func:`migrate`) only reports. Applying is
repeatable: each delivery admits a Job with an id fixed by its entry, and a
Job that already exists is not admitted again, so a Worker that stops
midway delivers each entry once on its next start.

Design: docs/reference/design/runtime/collaboration/agent-collaboration.html#stored-handoffs
"""
from __future__ import annotations

import hashlib
import logging
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from openprogram.runtime.conversation.always_on import store

_log = logging.getLogger(__name__)

MIGRATED_NAME = "handoffs.migrated.json"
BACKUP_NAME = "handoffs.before-migration.json"
#: ``“Title” (conversation id)``, as every ↩️ text names the conversation it came from.
_FROM = re.compile(r"“[^”]*” \(([^()\s]+)\)")

Send = Callable[[dict[str, Any]], tuple[str, Optional[str], str]]


def _paths() -> tuple[Path, Path, Path]:
    current = store._handoffs_path()  # noqa: SLF001 - the hand-off file this module retires
    return current, current.with_name(MIGRATED_NAME), current.with_name(BACKUP_NAME)


def job_id_for(msg_id: str) -> str:
    """The delivery Job's id for one stored entry, the same on every run."""
    return "j_" + hashlib.sha256(f"handoff:{msg_id}".encode("utf-8")).hexdigest()[:14]


def watch_id_for(msg_id: str) -> str:
    """The watch's id for one stored running turn, the same on every run."""
    return "watch_" + hashlib.sha256(f"handoff:{msg_id}".encode("utf-8")).hexdigest()[:12]


def _relays(msg_id: str, entry: dict[str, Any], base: str, base_from: str,
            base_run: str) -> list[dict[str, Any]]:
    """The earlier conversations ``entry`` owes (``owed_after``, nearest
    last), each waiting on the answer of the one below it: the nearest on
    ``base`` answered in ``base_from``, the next on that relay, and so on."""
    out: list[dict[str, Any]] = []
    passer, waits_on, run = base_from, base, base_run
    for k, earlier in enumerate(reversed([str(e) for e in entry.get("owed_after") or [] if e])):
        relay_id = f"relay:handoff:{msg_id}:{k}"
        out.append({"to": earlier, "from": passer, "id": relay_id,
                    "waits_on": [passer, waits_on, run]})
        passer, waits_on, run = earlier, relay_id, ""
    return out


def plan(chains: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """``(deliveries, retired)``: what moves to ``send_message`` and what is
    only kept in the record (finished, or nothing left to deliver)."""
    deliveries: dict[str, dict[str, Any]] = {}
    retired: dict[str, Any] = {}
    for msg_id, entry in chains.items():
        if not isinstance(entry, dict):
            retired[msg_id] = entry
            continue
        phase = entry.get("phase")
        hop = int(entry.get("hop") or 0)
        if phase in ("running", "paused"):
            # The turn runs on (or is continued after this restart); what it
            # owed is its latest reply once it is idle. A reply turn that
            # carried an answer on owes the nearest earlier conversation.
            owed = [str(e) for e in entry.get("owed_after") or [] if e]
            if entry.get("reply_to"):
                watcher, rest = str(entry["reply_to"]), owed
            elif owed:
                watcher, rest = owed[-1], owed[:-1]
            else:
                retired[msg_id] = entry
                continue
            watch = watch_id_for(msg_id)
            deliveries[msg_id] = {
                "kind": "watch", "from": watcher, "to": str(entry["session_id"]), "hop": hop,
                "watch_id": watch,
                "relays": _relays(msg_id, {"owed_after": rest}, watch, watcher, ""),
            }
            continue
        if phase != "waiting" or not entry.get("text"):
            retired[msg_id] = entry
            continue
        if entry.get("reply_to"):
            job_id = job_id_for(msg_id)
            deliveries[msg_id] = {
                "kind": "message", "from": str(entry["reply_to"]), "to": str(entry["session_id"]),
                "message": str(entry.get("message") or entry["text"]), "hop": hop,
                # Who waited above it, the sender included: send_message
                # refuses a message back to one of them.
                "ancestors": [str(a) for a in entry.get("ancestors") or [entry["reply_to"]] if a],
                "relays": _relays(msg_id, entry, job_id, str(entry["reply_to"]), str(entry["session_id"])),
            }
            continue
        answered_by = _FROM.search(str(entry["text"]))
        if answered_by is None:
            # No conversation to send it from.
            retired[msg_id] = entry
            continue
        deliveries[msg_id] = {
            "kind": "reply", "from": answered_by.group(1), "to": str(entry["session_id"]),
            "message": str(entry["text"]), "hop": hop, "relays": [],
        }
    for msg_id, delivery in deliveries.items():
        delivery.update(job_id=job_id_for(msg_id), status="pending")
        if not delivery["relays"]:
            delivery.pop("relays")
    return deliveries, retired


def _record(path: Path) -> dict[str, Any]:
    record = store._read_json(path)  # noqa: SLF001
    return {"version": 1, "original": dict(record.get("original") or {}),
            "deliveries": dict(record.get("deliveries") or {})}


def migrate(*, apply: bool = False, deliver: bool = True, send: Send | None = None) -> dict[str, Any]:
    """Report, and with ``apply`` carry out, the move. Returns the counts.

    ``deliver=False`` moves the entries without sending them yet (the Worker
    sends them on a thread, :func:`deliver_pending`).
    """
    current, migrated, backup = _paths()
    with store.lock("handoffs"):
        chains = store._read_json(current)  # noqa: SLF001
        record = _record(migrated)
        deliveries, retired = plan(chains)
        report = {"messages": sum(d["kind"] == "message" for d in deliveries.values()),
                  "replies": sum(d["kind"] == "reply" for d in deliveries.values()),
                  "watches": sum(d["kind"] == "watch" for d in deliveries.values()),
                  "retired": len(retired),
                  "pending": sum(d.get("status") == "pending" for d in record["deliveries"].values()),
                  "applied": False}
        if not apply:
            return report
        if chains:
            if current.exists() and not backup.exists():
                shutil.copy2(current, backup)
            for msg_id, entry in chains.items():
                record["original"].setdefault(msg_id, entry)
            for msg_id, delivery in deliveries.items():
                record["deliveries"].setdefault(msg_id, delivery)
            # The record first: a stop before the next write leaves the entry
            # in both files, and the next run moves it once.
            store._write_json(migrated, record)  # noqa: SLF001
            store.save_handoffs({})
        report["applied"] = True
    if deliver:
        report.update(deliver_pending(send=send))
    return report


def deliver_pending(*, send: Send | None = None, settle: Callable[[str], None] | None = None) -> dict[str, int]:
    """Send every moved entry not delivered yet; returns how many went and how many could not."""
    _current, migrated, _backup = _paths()
    send = send or send_with_send_message
    with store.lock("handoffs"):
        pending = {msg_id: dict(delivery) for msg_id, delivery in _record(migrated)["deliveries"].items()
                   if delivery.get("status") == "pending"}
    done = failed = 0
    for msg_id, delivery in pending.items():
        if settle is not None:
            settle(delivery["to"])
        try:
            status, execution_id, note = (watch_with_notices(delivery) if delivery["kind"] == "watch"
                                          else send(delivery))
            if status == "delivered":
                hold_relays(delivery)
        except Exception as exc:  # noqa: BLE001 - one entry never stops the others
            _log.warning("hand-off %s not delivered: %s: %s", msg_id, type(exc).__name__, exc)
            status, execution_id, note = "pending", None, f"{type(exc).__name__}: {exc}"
        with store.lock("handoffs"):
            record = _record(migrated)
            entry = record["deliveries"].get(msg_id)
            if entry is None:
                continue
            entry.update(status=status, note=note, at=time.time())
            if execution_id:
                entry["execution_id"] = execution_id
            if status == "retry":
                # send_message refused it this time (it may have admitted and
                # cancelled its Job): the next Worker start tries again under
                # a new id, so that Job is not taken for a delivery.
                attempts = int(entry.get("attempts") or 0) + 1
                entry.update(status="pending", attempts=attempts,
                             job_id=job_id_for(f"{msg_id}#{attempts}"))
            store._write_json(migrated, record)  # noqa: SLF001
        done += status == "delivered"
        failed += status == "failed"
    return {"delivered": done, "failed": failed}


_EXECUTION_ID = re.compile(r"execution_id=(\S+)")


def send_with_send_message(delivery: dict[str, Any]) -> tuple[str, Optional[str], str]:
    """Deliver one moved entry as ``send_message`` does: a tracked Job on
    the target, its reply back to the sender as a notice."""
    from openprogram.programs.tools.agents.send_message.send_message.dispatch import deliver
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.jobs import get_runner
    from openprogram.runtime.permissions.authority import authority_from_message, local_owner_authority

    job_id = delivery["job_id"]
    if get_runner().get_job(job_id) is not None:
        return "delivered", job_id, "admitted by an earlier run"
    db = default_db()
    sender, target = db.get_session(delivery["from"]), db.get_session(delivery["to"])
    if sender is None or target is None:
        gone = delivery["from"] if sender is None else delivery["to"]
        return "failed", None, f"conversation {gone} no longer exists"
    anchor = str(sender.get("head_id") or "")
    authority = authority_from_message(delivery["from"], anchor) or local_owner_authority()
    text = deliver(
        message=delivery["message"], sid=delivery["from"], aid=anchor,
        parent_agent=sender.get("agent_id"), chosen_agent=str(target.get("agent_id") or "main"),
        run_session=delivery["to"], target_tip=str(target.get("head_id") or ""),
        caller_authority=authority, messages=int(delivery.get("hop") or 0), generations=0,
        job_id=job_id, ancestors=list(delivery.get("ancestors") or []) or None,
    )
    found = _EXECUTION_ID.search(text)
    if found is None:
        if text.startswith("[send_message error]"):
            return "retry", None, text[:500]
        if "duplicate message ignored" in text:
            return "delivered", None, text[:200]
        return "failed", None, text[:500]
    return "delivered", found.group(1), text.splitlines()[0][:200]


def _authority_of(session_id: str) -> dict[str, Any]:
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.permissions.authority import authority_from_message, local_owner_authority
    head = str((default_db().get_session(session_id) or {}).get("head_id") or "")
    return authority_from_message(session_id, head) or local_owner_authority()


def watch_with_notices(delivery: dict[str, Any]) -> tuple[str, Optional[str], str]:
    """A turn still running that owed its sender a reply: the sender gets
    that conversation's latest reply once it is idle."""
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.jobs import notices
    db = default_db()
    sender, target = db.get_session(delivery["from"]), db.get_session(delivery["to"])
    if sender is None or target is None:
        gone = delivery["from"] if sender is None else delivery["to"]
        return "failed", None, f"conversation {gone} no longer exists"
    if not notices.watch(
        sender_session_id=delivery["from"], target_session_id=delivery["to"],
        target_head_id=str(target.get("head_id") or ""), label=str(target.get("title") or ""),
        agent_id=str(sender.get("agent_id") or "main"), chain_messages=int(delivery.get("hop") or 0),
        chain_generations=0, authority=_authority_of(delivery["from"]), watch_id=delivery["watch_id"],
    ):
        return "retry", None, "the sender cannot hold a watch now"
    return "delivered", delivery["watch_id"], "watching"


def hold_relays(delivery: dict[str, Any]) -> None:
    """Each earlier conversation the entry owed gets the answer below it."""
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.jobs import notices
    db = default_db()
    for k, relay in enumerate(delivery.get("relays") or []):
        if db.get_session(relay["to"]) is None:
            continue
        waits_on = list(relay["waits_on"])
        if k == 0 and delivery["kind"] == "message":
            # A refused attempt delivered later under a new Job id.
            waits_on[1] = delivery["job_id"]
        source = db.get_session(relay["from"]) or {}
        notices.hold_reply(
            sender_session_id=relay["to"], entry_id=relay["id"], from_session_id=relay["from"],
            waits_on=waits_on, label=str(source.get("title") or ""),
            agent_id=str(source.get("agent_id") or "main"), authority=_authority_of(relay["to"]),
        )


def wait_for_restart_to_settle(session_id: str) -> None:
    """Let the restart continue this conversation's own turns first: a new
    turn here would read as the user having moved on from them."""
    from openprogram.runtime.conversation.owner_turn import WAKE_POLL_S, WAKE_WAIT_S
    from openprogram.runtime.execution.restart import conversation_settling
    from openprogram.runtime.execution.store import default_store
    deadline = time.monotonic() + WAKE_WAIT_S
    while conversation_settling(default_store(), session_id) and time.monotonic() < deadline:
        time.sleep(WAKE_POLL_S)


def migrate_in_worker() -> None:
    """At Worker start: move every stored entry, and send the deliveries
    once each conversation's restart settled."""
    report = migrate(apply=True, deliver=False)
    if not (report["messages"] or report["replies"] or report["watches"] or report["pending"]):
        return
    threading.Thread(target=deliver_pending, kwargs={"settle": wait_for_restart_to_settle},
                     name="handoff-migration", daemon=True).start()


__all__ = ["deliver_pending", "hold_relays", "job_id_for", "migrate", "migrate_in_worker", "plan",
           "send_with_send_message", "watch_id_for", "watch_with_notices"]
