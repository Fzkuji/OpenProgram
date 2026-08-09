"""The background writer's silent-loss paths.

Everything memory writes goes through a cursor: turns are handed to the
writer, then the cursor moves past them and they are never offered
again. So every failure on this path has the same shape — the cursor
advances over turns that never reached a file, and nothing says so.

Four of them lived here at once. The session store stamps Unix seconds
and the memory layer parses ISO 8601, so every write raised before it
reached a model. The idle flush took one batch and reported the session
finished. A flush that raised was reported to the watcher as success. A
repair commit that was rejected a second time was reported as ``ok``.

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


# -- 2. The idle flush finishes the backlog --------------------------------


def test_the_idle_flush_writes_every_pending_turn(memory_root, written):
    """A session that ends with more backlog than one call can hold.

    The flush used to take the leading batch and stop; the watcher then
    marked the session processed and the rest was never offered again."""
    from openprogram.memory.scriptorium import writing

    messages = [_turn(i, "user" if i % 2 == 0 else "assistant", f"turn {i} text")
                for i in range(6)]

    assert writing.flush("s2", messages, token_threshold=8) is True

    sent = "\n".join(written)
    assert len(written) == 6, "one call per batch, six batches of one turn"
    for i in range(6):
        assert f"turn {i} text" in sent
    assert writing._pending("s2", messages) == [], "the cursor covers all six"


def test_a_flush_that_cannot_finish_says_so(memory_root, written, monkeypatch):
    """A pass that writes nothing — another writer holds the workspace —
    leaves backlog behind, and the caller has to hear about it."""
    from openprogram.memory.scriptorium import writing

    messages = [_turn(i, "user", f"turn {i} text") for i in range(3)]
    monkeypatch.setattr(writing, "write_session", lambda *a, **kw: False)

    assert writing.flush("s3", messages, token_threshold=8) is False


# -- 3. A failed write reaches the watcher ---------------------------------


def _watch(session_id: str = "s4") -> bool:
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


def test_a_raising_flush_is_not_a_processed_session(memory_root, provider, monkeypatch):
    from openprogram.memory.scriptorium import writing

    def _boom(*_a, **_kw):
        raise RuntimeError("model unreachable")

    monkeypatch.setattr(writing, "flush", _boom)
    assert _watch() is False, "the watcher marks a session done on True"


def test_an_unfinished_flush_is_not_a_processed_session(memory_root, provider, monkeypatch):
    from openprogram.memory.scriptorium import writing

    monkeypatch.setattr(writing, "flush", lambda *a, **kw: False)
    assert _watch() is False


def test_a_finished_flush_is(memory_root, provider, monkeypatch):
    from openprogram.memory.scriptorium import writing

    monkeypatch.setattr(writing, "flush", lambda *a, **kw: True)
    assert _watch() is True


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
