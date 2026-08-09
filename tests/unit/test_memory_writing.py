"""The background writer's silent-loss paths.

Everything memory writes goes through a cursor: turns are handed to the
writer, then the cursor moves past them and they are never offered
again. So every failure on this path has the same shape — the cursor
advances over turns that never reached a file, and nothing says so.

Four of them lived here at once. The session store stamps Unix seconds
and the memory layer parses ISO 8601, so every write raised before it
reached a model. The idle write took one batch and reported the session
finished. A write that raised was reported to the watcher as success. A
repair commit that was rejected a second time was reported as ``ok``.

The fifth is the opposite failure, and it costs money rather than
turns: a session nothing will ever write was retried on every poll.
So the outcome ``write`` hands back says both whether the session is
written and whether coming back could change that.

No model is called: the writer, the token counter and the organiser are
all replaced, and what would have been sent to the model is captured
instead.
"""
from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest


@pytest.fixture
def memory_root(tmp_path, monkeypatch):
    """An empty memory workspace under tmp. Returns its root."""
    import openprogram.paths as paths

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    from openprogram.memory import store

    return store.ensure()


@pytest.fixture
def written(monkeypatch):
    """Capture writer prompts instead of running one. Returns the list.

    Also stands in for the token counter (one token per character, so no
    tokenizer is needed) and for the reorganiser, which the batch and
    commit counters would otherwise trigger into a real agent run.
    """
    from openprogram.memory.scriptorium import writing

    prompts: list[str] = []

    def _write(memory_dir, *, agent, task, stage=None, **_kw):
        prompts.append(task)
        return []

    monkeypatch.setattr(writing, "_counter", lambda: len)
    monkeypatch.setattr(writing, "_agent", lambda model=None: object())
    monkeypatch.setattr(writing, "_run_agent", _write)
    monkeypatch.setattr(writing, "organize_topics", lambda *a, **kw: [])
    return prompts


def _turn(index: int, role: str, text: str) -> dict:
    return {"id": f"m{index}", "role": role, "content": text,
            "timestamp": 1786281306.005367 + index}


# -- 1. The timestamp the session store actually writes --------------------


def test_the_stores_own_timestamp_survives_the_trip(tmp_path, memory_root, written):
    """The session store stamps Unix seconds; ``fromisoformat`` in
    ``runtime/online`` used to be handed that float as a string and
    raised ``Invalid isoformat string`` before any model was called."""
    from openprogram.agent.session_db import SessionDB
    from openprogram.memory.scriptorium import writing

    db = SessionDB(tmp_path / "sessions")
    db.append_message("s1", {"id": "u1", "role": "user", "content": "who is dave"})
    db.append_message("s1", {"id": "a1", "role": "assistant",
                             "content": "your neighbour", "predecessor": "u1"})
    branch = db.get_branch("s1")
    assert isinstance(branch[0]["timestamp"], float), (
        "this test is only meaningful against the store's real stamp"
    )

    records = writing._records("s1", branch)
    stamps = [datetime.fromisoformat(r.timestamp) for r in records]
    assert [s.date() for s in stamps] == [date.today(), date.today()]

    assert writing.write_session("s1", branch, token_threshold=1, force=True)
    assert f"## Observed {date.today().isoformat()}" in written[0], (
        "the writer dates a batch by slicing the stamp, so it has to be "
        "a calendar time and not an epoch"
    )


def test_a_written_date_is_left_alone():
    """``archive_sessions`` builds records from an observation date. It
    is already what the memory layer stores, so it passes through."""
    from openprogram.memory.scriptorium.writing import _observed_at

    assert _observed_at("2023-03-15") == "2023-03-15"
    assert _observed_at(None) is None
    assert _observed_at("") is None


# -- 2. The idle write finishes the backlog --------------------------------


def test_a_forced_write_finishes_every_pending_turn(memory_root, written):
    """A session that ends with more backlog than one call can hold.

    It used to take the leading batch and stop; the watcher then marked
    the session processed and the rest was never offered again."""
    from openprogram.memory.scriptorium import writing

    messages = [_turn(i, "user" if i % 2 == 0 else "assistant", f"turn {i} text")
                for i in range(6)]

    assert writing.write("s2", messages, token_threshold=8, force=True) is None

    sent = "\n".join(written)
    assert len(written) == 6, "one call per batch, six batches of one turn"
    for i in range(6):
        assert f"turn {i} text" in sent
    assert writing._pending("s2", messages) == [], "the cursor covers all six"


def test_a_write_that_cannot_finish_says_so(memory_root, written, monkeypatch):
    """A forced pass that writes nothing leaves backlog behind, and the
    caller has to hear about it."""
    from openprogram.memory.scriptorium import writing

    messages = [_turn(i, "user", f"turn {i} text") for i in range(3)]
    monkeypatch.setattr(writing, "write_session", lambda *a, **kw: False)

    left = writing.write("s3", messages, token_threshold=8, force=True)
    assert left is not None and left.retryable, "the backlog is still owed"


# -- 2b. The per-turn call is the same method, one flag apart --------------


def test_below_the_threshold_is_not_a_failure(memory_root, written):
    """The ordinary per-turn outcome: too little to be worth a call, and
    nothing owed yet, so nothing is reported.

    The turn is stamped now — turns left sitting for an hour get written
    whatever their size, which is the other half of the same rule."""
    import time

    from openprogram.memory.scriptorium import writing

    messages = [{"id": "m0", "role": "user", "content": "hi",
                 "timestamp": time.time()}]

    assert writing.write("s6", messages, token_threshold=100_000) is None
    assert written == [], "no model call below the threshold"
    assert writing._pending("s6", messages), "and the turn is still owed"


def test_a_busy_workspace_is_reported_on_the_per_turn_call(
    memory_root, provider, monkeypatch,
):
    """The lock used to become a bare False here, indistinguishable from
    'not enough yet', so a turn that never got written said nothing."""
    from openprogram.memory.scriptorium import writing
    from openprogram.memory.scriptorium.management import transaction

    def _busy(*_a, **_kw):
        raise transaction.TransactionError(
            "CONCURRENT_UPDATE", "another writer holds it"
        )

    monkeypatch.setattr(writing, "workspace_write_lock", _busy)

    from openprogram.memory import get_provider

    left = get_provider().write(
        [_turn(i, "user", f"turn {i} text") for i in range(3)],
        session_id="s7",
    )
    assert left is not None and left.retryable
    assert "CONCURRENT_UPDATE" in left.reason


# -- 3. A failed write reaches the watcher ---------------------------------


def _watch(session_id: str = "s4"):
    from openprogram.memory import session_watcher

    return session_watcher._process_session(
        session_id, [{"id": "u1", "role": "user", "content": "hi"}]
    )


@pytest.fixture
def provider(monkeypatch):
    from openprogram.memory.scriptorium.provider import ScriptoriumMemoryProvider

    monkeypatch.setattr(
        "openprogram.memory.get_provider", lambda: ScriptoriumMemoryProvider()
    )


def test_a_raising_write_is_retryable(memory_root, provider, monkeypatch):
    from openprogram.memory.scriptorium import writing

    def _boom(*_a, **_kw):
        raise RuntimeError("model unreachable")

    monkeypatch.setattr(writing, "write", _boom)
    left = _watch()
    assert left is not None and left.retryable
    assert "model unreachable" in left.reason


def test_a_rejected_batch_is_not_retryable(memory_root, provider, monkeypatch):
    """The writer produced content the transaction refused. The same
    content next poll gets the same answer, so it must not come back."""
    from openprogram.memory.scriptorium import writing
    from openprogram.memory.scriptorium.management.transaction import (
        TransactionError,
    )

    def _rejected(*_a, **_kw):
        raise TransactionError("COMMIT_REJECTED", "block ID must not be removed")

    monkeypatch.setattr(writing, "write", _rejected)
    left = _watch()
    assert left is not None and not left.retryable
    assert "COMMIT_REJECTED" in left.reason


def test_a_held_lock_is_retryable(memory_root, provider, monkeypatch):
    from openprogram.memory.scriptorium import writing
    from openprogram.memory.scriptorium.management.transaction import (
        TransactionError,
    )

    def _busy(*_a, **_kw):
        raise TransactionError("CONCURRENT_UPDATE", "another writer holds it")

    monkeypatch.setattr(writing, "write", _busy)
    left = _watch()
    assert left is not None and left.retryable


def test_a_finished_write_says_nothing(memory_root, provider, monkeypatch):
    from openprogram.memory.scriptorium import writing

    monkeypatch.setattr(writing, "write", lambda *a, **kw: None)
    assert _watch() is None


# -- 3b. What the watcher does with each of the three ----------------------


class _Provider:
    """Returns whatever it was handed. No model, no workspace."""

    def __init__(self, outcome) -> None:
        self._outcome = outcome

    def write(self, _messages=None, *, session_id="", force=False):
        assert force, "the idle watcher has no later pass to leave it to"
        return self._outcome


@pytest.fixture
def watched(tmp_path, monkeypatch):
    """One idle session in the DB, and the events the scan emits.

    Returns ``(run, events)`` — call ``run(outcome)`` with what the
    provider should hand back and read the processed-session state and
    the emitted events out of the result.
    """
    import openprogram.paths as paths

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")

    from openprogram.agent.session_db import SessionDB
    from openprogram.memory import session_watcher

    db = SessionDB(tmp_path / "sessions")
    db.append_message("idle1", {"id": "u1", "role": "user", "content": "hi"})
    monkeypatch.setattr(
        "openprogram.agent.session_db.default_db", lambda: db
    )
    # Old enough that the scan treats it as idle.
    monkeypatch.setattr(
        db, "list_sessions",
        lambda **_kw: [{"id": "idle1", "updated_at": 1.0}],
    )

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "openprogram.events.emit_safe",
        lambda name, actor, payload, meta=None: events.append((name, payload)),
    )

    def run(outcome):
        monkeypatch.setattr(
            "openprogram.memory.get_provider", lambda: _Provider(outcome)
        )
        n = session_watcher._scan(idle_minutes=1)
        return n, session_watcher._load_processed()

    return run, events


def test_nothing_returned_marks_the_session_handled(watched):
    """The Claude Code hook contract: saying nothing is saying fine."""
    run, events = watched
    n_done, processed = run(None)

    assert n_done == 1
    assert "idle1" in processed
    ended = [p for name, p in events if name == "memory.ingest_ended"]
    assert ended == [{"ok": True, "retryable": False, "reason": ""}]


def test_a_retryable_failure_leaves_it_for_the_next_poll(watched):
    from openprogram.memory.provider import WriteIncomplete

    run, events = watched
    n_done, processed = run(WriteIncomplete("model unreachable"))

    assert n_done == 0
    assert "idle1" not in processed, "unmarked, so the next poll retries"
    ended = [p for name, p in events if name == "memory.ingest_ended"]
    assert ended == [{
        "ok": False, "retryable": True, "reason": "model unreachable",
    }]


def test_a_hopeless_failure_is_marked_and_reported(watched):
    """Marked handled so the loop stops burning quota, and the reason
    goes out on the bus rather than only into the log."""
    from openprogram.memory.provider import WriteIncomplete

    run, events = watched
    n_done, processed = run(
        WriteIncomplete("COMMIT_REJECTED: block ID removed", retryable=False)
    )

    assert n_done == 0, "it was never written"
    assert "idle1" in processed, "but it must not be offered again"
    ended = [p for name, p in events if name == "memory.ingest_ended"]
    assert ended == [{
        "ok": False, "retryable": False,
        "reason": "COMMIT_REJECTED: block ID removed",
    }]


# -- 4. A rejected repair is a failure -------------------------------------


class _FakeAgent:
    """Runs no model. Counts how many times the writer was asked."""

    def __init__(self) -> None:
        self.runs = 0

    def run(self, **_kwargs):
        self.runs += 1
        return SimpleNamespace(
            turns=[], reply="done", text="done", num_turns=1,
            input_tokens=10, output_tokens=5, stop_reason="end_turn",
            anthropic_equivalent_cost_usd=0.0,
        )


@pytest.fixture
def no_tools(monkeypatch):
    """The management tools need the agent SDK; a fake agent needs none."""
    from openprogram.memory.scriptorium.management import agent as agent_module

    monkeypatch.setattr(agent_module, "management_tools", lambda ws, audit: [])


def test_a_second_rejected_commit_is_reported(tmp_path, no_tools, monkeypatch):
    """Two invalid turns install nothing. Returning an ``ok`` audit lets
    the caller advance its cursor past turns that reached no file."""
    from openprogram.memory.scriptorium.management import agent as agent_module
    from openprogram.memory.scriptorium.management.transaction import TransactionError

    monkeypatch.setattr(
        agent_module, "_commit_turn",
        lambda ws, base, audit: "block ID must not be removed",
    )
    agent = _FakeAgent()

    with pytest.raises(TransactionError) as caught:
        agent_module._run_agent(tmp_path / "mem", agent=agent, task="write it up")

    assert caught.value.code == "COMMIT_REJECTED"
    assert agent.runs == 2, "it still gets the one repair attempt"


def test_a_repaired_commit_is_a_success(tmp_path, no_tools, monkeypatch):
    """The rejection path itself still works: rejected once, repaired."""
    from openprogram.memory.scriptorium.management import agent as agent_module

    outcomes = ["block ID must not be removed", None]
    monkeypatch.setattr(
        agent_module, "_commit_turn",
        lambda ws, base, audit: outcomes.pop(0),
    )
    agent = _FakeAgent()

    audit = agent_module._run_agent(
        tmp_path / "mem", agent=agent, task="write it up"
    )

    assert agent.runs == 2
    assert [e for e in audit if e.get("tool") == "agent"][0]["status"] == "ok"


# -- 5. Internal scheduling is not conversation ----------------------------


def test_the_runtimes_own_turns_are_not_conversation():
    """``task_followup`` and ``merge_turn`` rows are written by the
    dispatcher so the model has a turn to answer (``dispatcher/prep.py``
    marks them ``display="runtime"``, and the reply carries the same
    ``source``). Nobody said them, so they are not evidence."""
    from openprogram.memory.scriptorium import writing

    messages = [
        _turn(0, "user", "who is dave"),
        _turn(1, "assistant", "your neighbour"),
        {**_turn(2, "user", "[系统消息] the sub-agent finished"),
         "source": "task_followup", "display": "runtime"},
        {**_turn(3, "assistant", "noted, I will read it"),
         "source": "task_followup"},
        {**_turn(4, "user", "merge the branch"),
         "source": "merge_turn", "display": "runtime"},
        {**_turn(5, "assistant", "merged"), "source": "merge_turn"},
        _turn(6, "user", "what did dave say"),
    ]

    records = writing._records("s5", messages)

    assert [r.message_id for r in records] == ["m0", "m1", "m6"]
    assert [r.ordinal for r in records] == [0, 1, 6], (
        "the ordinal is the row's position in the branch — the cursor is "
        "compared against it, so skipping a row must not renumber"
    )


# -- 6. Where the watcher keeps its own bookkeeping ------------------------


def test_the_watcher_can_find_where_to_keep_its_state(memory_root):
    """``store.state_dir`` imported ``workspace_layout`` from the wrong
    package, so every call raised and the whole idle watcher was inert —
    the outer handler in ``_loop`` swallowed it, and the path never ran
    once in production. Calling it is the test."""
    from openprogram.memory import session_watcher, store

    path = session_watcher._processed_path()

    assert path.parent == store.state_dir()
    assert path.parent.parent == memory_root
    assert path.parent.is_dir(), "the runtime directory is created on demand"
    assert path.name == "session-end.json"


def test_the_watchers_state_survives_a_memory_write(memory_root, written):
    """The processed-session file sits inside the runtime directory, so a
    write transaction installing a staged workspace must leave it alone —
    and a file rewritten every poll must not read as a concurrent write."""
    from openprogram.memory import session_watcher
    from openprogram.memory.scriptorium import writing
    from openprogram.memory.scriptorium.management.transaction import (
        workspace_revision,
    )

    session_watcher._save_processed({"s8": 1786288829.9})

    messages = [_turn(i, "user", f"turn {i} text") for i in range(3)]
    assert writing.write("s9", messages, token_threshold=8, force=True) is None
    assert session_watcher._load_processed() == {"s8": 1786288829.9}, (
        "installing a staged workspace must not take the bookkeeping with it"
    )

    written_revision = workspace_revision(memory_root)
    session_watcher._save_processed({"s8": 1786288829.9, "s9": 2.0})
    assert workspace_revision(memory_root) == written_revision, (
        "bookkeeping is not memory, so writing it must not move the revision"
    )


def test_a_session_that_owes_nothing_costs_no_model_call(memory_root, written):
    """A conversation of nothing but the runtime's own scheduling turns.

    The watcher offers it like any other idle session; there is no
    evidence in it, so the forced write reports success without asking a
    model anything."""
    from openprogram.memory.scriptorium import writing

    messages = [
        {**_turn(0, "user", "the sub-agent finished"),
         "source": "task_followup", "display": "runtime"},
        {**_turn(1, "assistant", "noted"), "source": "task_followup"},
        _turn(2, "assistant", ""),
    ]

    assert writing.write("s10", messages, token_threshold=8, force=True) is None
    assert written == [], "nothing anybody said, so nothing to write up"
