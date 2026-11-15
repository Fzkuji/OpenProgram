"""Stored hand-offs move onto send_message: rehearsed on a copy built here.

The file below is a temporary copy shaped like the ``handoffs.json`` the
removed ``handoff`` tool wrote; no real profile is read or written.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openprogram.runtime.conversation.always_on import handoff_migration as migration
from openprogram.runtime.conversation.always_on import store

CHAINS = {
    # A message still waiting for its busy target.
    "m-waiting": {"chain": "c1", "hop": 2, "origin": "planner", "handoff_id": "h1", "session_id": "docs",
                  "at": 1.0, "boot": "old", "phase": "waiting", "reply_to": "planner",
                  "message": "Check the links",
                  "text": "📨 Message from “Planner” (planner):\n\nCheck the links\n\n(...)"},
    # A reply still waiting for its busy sender.
    "r-waiting": {"chain": "c2", "hop": 1, "origin": "planner", "handoff_id": "h2", "session_id": "planner",
                  "at": 1.0, "boot": "old", "phase": "waiting",
                  "text": "↩️ Reply from “Docs bot” (docs) to the message you sent it:\n\nAll links work."},
    # A turn already running owes its reply when it ends: the sender watches it.
    "m-running": {"chain": "c3", "hop": 1, "origin": "planner", "session_id": "docs", "at": 1.0,
                  "boot": "old", "phase": "running", "reply_to": "planner"},
    "m-done": {"chain": "c4", "hop": 1, "origin": "planner", "session_id": "docs", "at": 1.0,
               "boot": "old", "phase": "done"},
    # A reply whose text names no conversation: kept in the record only.
    "r-unnamed": {"chain": "c5", "hop": 1, "session_id": "planner", "at": 1.0, "boot": "old",
                  "phase": "waiting", "text": "(no sender named)"},
}


@pytest.fixture
def copy(tmp_path, monkeypatch):
    path = tmp_path / "always_on" / "handoffs.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(CHAINS), encoding="utf-8")
    monkeypatch.setattr(store, "_handoffs_path", lambda: path)
    return path


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def watches(monkeypatch):
    """Watches and relays recorded instead of written to a sender's notices."""
    seen = {"watch": [], "relay": []}

    def watch(delivery):
        seen["watch"].append(delivery)
        return "delivered", delivery["watch_id"], "watching"

    monkeypatch.setattr(migration, "watch_with_notices", watch)
    monkeypatch.setattr(migration, "hold_relays", lambda delivery: seen["relay"].append(delivery))
    return seen


def test_a_dry_run_reports_and_changes_nothing(copy):
    before = copy.read_bytes()
    report = migration.migrate()
    assert report == {"messages": 1, "replies": 1, "watches": 1, "retired": 2, "pending": 0,
                      "applied": False}
    assert copy.read_bytes() == before
    assert not copy.with_name(migration.MIGRATED_NAME).exists()


def test_every_entry_moves_and_the_old_file_is_emptied(copy, watches):
    sent = []

    def send(delivery):
        sent.append(delivery)
        return "delivered", delivery["job_id"], "[delivered]"

    report = migration.migrate(apply=True, send=send)
    assert (report["messages"], report["replies"], report["watches"], report["delivered"]) == (1, 1, 1, 3)
    by_kind = {item["kind"]: item for item in sent}
    assert by_kind["message"] | {} == {**by_kind["message"], "from": "planner", "to": "docs",
                                       "message": "Check the links", "hop": 2,
                                       "ancestors": ["planner"]}
    # The reply goes from the conversation that answered to the one that asked.
    assert (by_kind["reply"]["from"], by_kind["reply"]["to"]) == ("docs", "planner")
    assert "All links work." in by_kind["reply"]["message"]
    # The running turn's sender watches its conversation for the reply.
    (watch,) = watches["watch"]
    assert (watch["from"], watch["to"], watch["watch_id"]) == ("planner", "docs", migration.watch_id_for("m-running"))
    # Nothing is left for a replay: the old file is empty.
    assert _read(copy) == {}
    # Every entry stays readable, and the file was copied first.
    record = _read(copy.with_name(migration.MIGRATED_NAME))
    assert record["original"] == CHAINS
    assert {key: value["status"] for key, value in record["deliveries"].items()} == {
        "m-waiting": "delivered", "r-waiting": "delivered", "m-running": "delivered"}
    assert record["deliveries"]["m-waiting"]["execution_id"] == migration.job_id_for("m-waiting")
    assert _read(copy.with_name(migration.BACKUP_NAME)) == CHAINS


def test_applying_again_delivers_nothing_twice(copy):
    calls = []
    migration.migrate(apply=True, send=lambda d: (calls.append(d) or ("delivered", d["job_id"], "")))
    again = migration.migrate(apply=True, send=lambda d: (calls.append(d) or ("delivered", d["job_id"], "")))
    assert len(calls) == 2
    assert (again["messages"], again["replies"], again["delivered"]) == (0, 0, 0)


def test_a_stop_midway_delivers_each_entry_once_on_the_next_start(copy):
    # The Worker moved the entries and stopped before sending them.
    migration.migrate(apply=True, deliver=False)
    # It also stopped before the old file was shortened: the entry is in both.
    shortened = _read(copy)
    copy.write_text(json.dumps({**shortened, "m-waiting": CHAINS["m-waiting"]}), encoding="utf-8")
    calls = []

    def flaky(delivery):
        calls.append(delivery["job_id"])
        if delivery["kind"] == "reply" and calls.count(delivery["job_id"]) == 1:
            raise OSError("worker going down")
        return "delivered", delivery["job_id"], ""

    first = migration.migrate(apply=True, send=flaky)
    assert (first["delivered"], first["failed"]) == (2, 0)
    second = migration.deliver_pending(send=flaky)
    assert second == {"delivered": 1, "failed": 0}
    assert sorted(calls) == sorted([migration.job_id_for("m-waiting"), migration.job_id_for("r-waiting"),
                                    migration.job_id_for("r-waiting")])
    assert "m-waiting" not in _read(copy)


def test_delivery_uses_send_messages_path_with_a_fixed_job(copy, monkeypatch):
    from openprogram.programs.tools.agents.send_message.send_message import dispatch
    from openprogram.runtime.context import session_db
    from openprogram.runtime import jobs
    from openprogram.runtime.permissions import authority

    sessions = {"planner": {"id": "planner", "head_id": "p-head", "agent_id": "assistant"},
                "docs": {"id": "docs", "head_id": "d-head", "agent_id": "butler"}}
    monkeypatch.setattr(session_db, "default_db", lambda: SimpleNamespace(get_session=sessions.get))
    admitted = {}
    monkeypatch.setattr(jobs, "get_runner", lambda: SimpleNamespace(get_job=admitted.get))
    monkeypatch.setattr(authority, "authority_from_message", lambda sid, mid: {"principal_id": "owner"})
    seen = []

    def deliver(**kwargs):
        seen.append(kwargs)
        admitted[kwargs["job_id"]] = object()
        return f"[delivered] execution_id={kwargs['job_id']} target=docs:d-head\nmore"

    monkeypatch.setattr(dispatch, "deliver", deliver)
    report = migration.migrate(apply=True)
    assert report["delivered"] == 3
    message = next(item for item in seen if item["run_session"] == "docs")
    assert message == {
        "message": "Check the links", "sid": "planner", "aid": "p-head", "parent_agent": "assistant",
        "chosen_agent": "butler", "run_session": "docs", "target_tip": "d-head",
        "caller_authority": {"principal_id": "owner"}, "messages": 2, "generations": 0,
        "job_id": migration.job_id_for("m-waiting"), "ancestors": ["planner"]}
    # A Job that already exists is not admitted again.
    record_path = copy.with_name(migration.MIGRATED_NAME)
    record = _read(record_path)
    record["deliveries"]["m-waiting"]["status"] = "pending"
    record_path.write_text(json.dumps(record), encoding="utf-8")
    assert migration.deliver_pending() == {"delivered": 1, "failed": 0}
    assert len(seen) == 2
    # A conversation that is gone is reported, not retried forever.
    sessions.pop("docs")
    assert migration.send_with_send_message({"job_id": "j_new", "from": "planner", "to": "docs",
                                             "message": "x", "hop": 0})[0] == "failed"


def test_a_refused_delivery_is_tried_again_under_a_new_job(copy, monkeypatch):
    from openprogram.programs.tools.agents.send_message.send_message import dispatch
    from openprogram.runtime.context import session_db
    from openprogram.runtime import jobs
    from openprogram.runtime.permissions import authority

    sessions = {"planner": {"id": "planner", "head_id": "p-head"}, "docs": {"id": "docs", "head_id": "d-head"}}
    monkeypatch.setattr(session_db, "default_db", lambda: SimpleNamespace(get_session=sessions.get))
    admitted = {}
    monkeypatch.setattr(jobs, "get_runner", lambda: SimpleNamespace(get_job=admitted.get))
    monkeypatch.setattr(authority, "authority_from_message", lambda sid, mid: {"principal_id": "owner"})
    refuse = {"on": True}

    def deliver(**kwargs):
        # Admitted, then cancelled because the inbox write failed.
        admitted[kwargs["job_id"]] = object()
        if refuse["on"]:
            return "[send_message error] OSError: inbox unavailable"
        return f"[delivered] execution_id={kwargs['job_id']} target=x"

    monkeypatch.setattr(dispatch, "deliver", deliver)
    first = migration.migrate(apply=True)
    # Only the watch went; both sends were refused and wait for the next start.
    assert (first["delivered"], first["failed"]) == (1, 0)
    record = _read(copy.with_name(migration.MIGRATED_NAME))["deliveries"]["m-waiting"]
    assert record["status"] == "pending" and record["job_id"] == migration.job_id_for("m-waiting#1")
    # The cancelled Job under the first id is not taken for a delivery.
    refuse["on"] = False
    assert migration.deliver_pending() == {"delivered": 2, "failed": 0}
    record = _read(copy.with_name(migration.MIGRATED_NAME))["deliveries"]["m-waiting"]
    assert record["execution_id"] == migration.job_id_for("m-waiting#1")


def test_a_message_that_owes_earlier_conversations_carries_the_answer_on(copy, watches):
    relay = {**CHAINS["m-waiting"], "owed_after": ["origin-conv", "middle-conv"]}
    copy.write_text(json.dumps({"m-waiting": relay}), encoding="utf-8")
    report = migration.migrate(apply=True, send=lambda d: ("delivered", d["job_id"], ""))
    assert report["messages"] == 1 and _read(copy) == {}
    (delivery,) = watches["relay"]
    job = migration.job_id_for("m-waiting")
    # The nearest earlier conversation waits on docs' answer to planner; the
    # first one waits on what the nearest passes on.
    nearest, first = delivery["relays"]
    assert (nearest["to"], nearest["from"], nearest["waits_on"]) == ("middle-conv", "planner",
                                                                     ["planner", job, "docs"])
    assert (first["to"], first["from"], first["waits_on"]) == ("origin-conv", "middle-conv",
                                                               ["middle-conv", nearest["id"], ""])
