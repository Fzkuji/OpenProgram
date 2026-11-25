"""The person's Codex conversations live in OpenProgram, linked to their threads."""
from __future__ import annotations

import json
import sqlite3

import pytest

from openprogram.runtime.harnesses.codex.engine import _synced_since
from openprogram.runtime.harnesses.handoff import plan_thread
from openprogram.state.store import SessionStore
from openprogram.state.store.session.external_import import codex_sync, codex_threads

COLUMNS = ("id TEXT PRIMARY KEY, rollout_path TEXT, cwd TEXT, title TEXT, name TEXT, model TEXT,"
           " model_provider TEXT, updated_at INTEGER, updated_at_ms INTEGER, source TEXT,"
           " originator TEXT, archived INTEGER, has_user_event INTEGER")


def _turn(turn_id: str, ask: str, answer: str, minute: int) -> list[dict]:
    ts = f"2026-10-02T09:{minute:02d}:00Z"
    return [
        {"type": "event_msg", "timestamp": ts, "payload": {"type": "task_started", "turn_id": turn_id}},
        {"type": "turn_context", "timestamp": ts, "payload": {"turn_id": turn_id, "model": "gpt-test"}},
        {"type": "response_item", "timestamp": ts, "payload": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": ask}]}},
        {"type": "response_item", "timestamp": ts, "payload": {
            "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": answer}]}},
    ]


class Codex:
    """A Codex home with a state database and rollouts."""

    def __init__(self, home):
        self.home = home
        home.mkdir()
        self.db = home / "state_5.sqlite"
        with sqlite3.connect(self.db) as connection:
            connection.execute(f"CREATE TABLE threads ({COLUMNS})")

    def thread(self, thread_id: str, turns: list[list[dict]], *, source="vscode", originator="Codex Desktop",
               provider="openai", updated=1000, archived=0, user=1) -> None:
        path = self.home / "sessions" / f"rollout-{thread_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        records = [{"type": "session_meta", "timestamp": "2026-10-02T09:00:00Z",
                    "payload": {"id": thread_id, "cwd": "/work/repo", "originator": originator}}]
        records += [r for turn in turns for r in turn]
        path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
        with sqlite3.connect(self.db) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO threads VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (thread_id, str(path), "/work/repo", f"Thread {thread_id}", None, "gpt-test", provider,
                 updated, updated * 1000, source, originator, archived, user))

    def providers(self) -> dict[str, str]:
        with sqlite3.connect(self.db) as connection:
            return dict(connection.execute("SELECT id, model_provider FROM threads").fetchall())


@pytest.fixture
def codex(tmp_path):
    return Codex(tmp_path / "codex")


@pytest.fixture
def store(tmp_path):
    s = SessionStore(tmp_path / "sessions")
    yield s
    s.close()


def _sync(codex, store, tmp_path):
    return codex_sync.sync(home=codex.home, store=store, ledger_root=tmp_path / "ledger", agent_id="main")


def _texts(store, session_id) -> list[str]:
    head = store.get_session(session_id)["head_id"]
    return [m["content"] for m in store.get_branch(session_id, head) if m["role"] in ("user", "assistant")]


def test_only_conversations_are_listed_and_moved(codex) -> None:
    codex.thread("app", [_turn("t1", "hi", "hello", 1)])
    codex.thread("cli", [_turn("t1", "hi", "hello", 1)], source="cli", updated=2000)
    codex.thread("exec", [_turn("t1", "hi", "hello", 1)], source="exec")
    codex.thread("old", [_turn("t1", "hi", "hello", 1)], archived=1)
    codex.thread("blank", [], user=0)
    assert [t.id for t in codex_threads.threads(codex.home)] == ["cli", "app"]

    # Every app and terminal thread moves, archived ones too; exec runs stay.
    assert codex_threads.move_provider(codex.home, "openprogram", from_provider="openai") == 4
    assert codex.providers() == {"app": "openprogram", "cli": "openprogram", "exec": "openai",
                                 "old": "openprogram", "blank": "openprogram"}
    codex_threads.move_provider(codex.home, "openai", from_provider="openprogram")
    assert set(codex.providers().values()) == {"openai"}


def test_a_thread_becomes_a_session_that_runs_in_codex(codex, store, tmp_path) -> None:
    codex.thread("app", [_turn("t1", "Summarise the repo", "It has a README.", 1)])
    [result] = _sync(codex, store, tmp_path)
    assert result["status"] == "created"
    sid = result["session_id"]
    row = store.get_session(sid)
    assert (row["source"], row["harness"], row["provider_override"], row["model_override"]) == \
        ("codex", "codex", "codex-harness", "gpt-test")
    record = row["harness_threads"]["codex-harness"]["main"]
    assert record["thread_id"] == "app" and record["shared"] and record["seen"] == row["head_id"]
    assert _texts(store, sid) == ["Summarise the repo", "It has a README."]
    # The sidebar's row says which agent runs it.
    listed = next(r for r in store.list_sessions(limit=10) if r["id"] == sid)
    assert listed["harness"] == "codex"
    # The next turn sent here continues the same Codex thread.
    history = store.get_branch(sid, row["head_id"])
    assert plan_thread(row["harness_threads"]["codex-harness"], "main", history).thread_id == "app"

    assert _sync(codex, store, tmp_path) == []  # nothing new: not read again


def test_turns_made_in_the_app_arrive_and_our_own_do_not_twice(codex, store, tmp_path) -> None:
    codex.thread("app", [_turn("t1", "one", "first", 1)])
    sid = _sync(codex, store, tmp_path)[0]["session_id"]

    def own(current):
        records = dict(current["codex-harness"])
        records["main"] = {**records["main"], "own_turns": ["t2"]}
        return {**current, "codex-harness": records}
    store.update_session_dict(sid, "harness_threads", own)

    codex.thread("app", [_turn("t1", "one", "first", 1), _turn("t2", "two", "second", 2),
                         _turn("t3", "three", "third", 3)], updated=2000)
    [result] = _sync(codex, store, tmp_path)
    assert result["status"] == "appended"
    assert _texts(store, sid) == ["one", "first", "three", "third"]
    row = store.get_session(sid)
    record = row["harness_threads"]["codex-harness"]["main"]
    assert record["seen"] == row["head_id"]
    assert _synced_since(row["harness_threads"]["codex-harness"], "app", record["synced_at"] - 1)
    assert not _synced_since(row["harness_threads"]["codex-harness"], "app", record["synced_at"] + 1)


def test_a_thread_started_here_gets_only_the_apps_later_turns(codex, store, tmp_path) -> None:
    store.create_session("local_mine", "main", title="Mine")
    store.update_session_dict("local_mine", "harness_threads", lambda _current: {
        "codex-harness": {"main": {"thread_id": "ours", "seen": None, "at": 1}}})
    codex.thread("ours", [_turn("t1", "asked here", "answered", 1)], originator="OpenProgram")
    [result] = _sync(codex, store, tmp_path)
    assert (result["session_id"], result["status"]) == ("local_mine", "unchanged")

    codex.thread("ours", [_turn("t1", "asked here", "answered", 1), _turn("t2", "from the app", "ok", 2)],
                 originator="OpenProgram", updated=2000)
    [result] = _sync(codex, store, tmp_path)
    assert result["status"] == "appended"
    assert _texts(store, "local_mine") == ["from the app", "ok"]


def test_a_shared_thread_is_resumed_only_when_it_saw_the_latest_message() -> None:
    history = [{"id": "u1", "role": "user"}, {"id": "a1", "role": "assistant"}]
    records = {"main": {"thread_id": "app", "seen": "a1", "shared": True}}
    assert plan_thread(records, "b2", history).action == "resume"
    moved_on = history + [{"id": "u2", "role": "user"}, {"id": "a2", "role": "assistant"}]
    plan = plan_thread(records, "b2", moved_on)
    assert (plan.action, plan.thread_id, [m["id"] for m in plan.missed]) == ("resume", "app", ["u2", "a2"])
