"""The public events of a ``--print`` run, and the transcript written from them.

Each event has the schema's envelope (version, an unbroken sequence, the
execution it belongs to and that execution's parent); what the stores do
not hold is said with a ``gap`` event; the transcript file is the events'
type and data, one record each.
"""
from __future__ import annotations

from openprogram.interfaces.cli import print_events, print_turn


class _Store:
    def __init__(self, messages, journal=()):
        self.messages = messages
        self.journal = list(journal)

    def get_messages(self, session_id):
        return self.messages

    def read_turn_records(self, session_id, execution_id, record_type=None):
        return self.journal


def _request(ts, execution_id, call_kind="chat"):
    return {"ts": ts, "execution_id": execution_id, "call_kind": call_kind,
            "provider": "p", "model_id": "m", "input_tokens": 1, "cache_read_tokens": 0,
            "cache_write_tokens": 0, "output_tokens": 1}


def _scope(monkeypatch, requests, *, anchors, parents, unread=False):
    monkeypatch.setattr(print_events, "_run_scope", lambda run: {
        "executions": frozenset({*anchors.values(), *parents}), "anchors": dict(anchors),
        "parents": dict(parents), "unread": unread})
    monkeypatch.setattr(print_events, "_request_rows", lambda run, ids: requests)


def _sub_agent_run(monkeypatch):
    """The run (``exec-run``) called a sub-agent (``exec-sub``), whose turn
    compacted its context once before answering."""
    _scope(monkeypatch, [_request(110.0, "exec-run"), _request(121.0, "exec-sub", "compaction"),
                         _request(122.0, "exec-sub"), _request(130.0, "exec-run")],
           anchors={"u": "exec-run", "a": "exec-run", "su": "exec-sub", "sa": "exec-sub"},
           parents={"exec-sub": "exec-run"})
    store = _Store([
        {"role": "user", "id": "u", "timestamp": 100.0, "content": "go"},
        {"role": "user", "id": "su", "timestamp": 120.0, "content": "look"},
        {"role": "assistant", "id": "sa", "timestamp": 120.5, "completed_at": 123.0,
         "content": "seen", "extra": {"blocks": [{"type": "text", "text": "seen"}]}},
        {"role": "tool", "timestamp": 124.0, "content": "seen",
         "extra": {"tool_use": {"tool_call_id": "c1", "arguments": {"prompt": "look"}}}},
        {"role": "assistant", "id": "a", "timestamp": 101.0, "completed_at": 140.0, "content": "done",
         "extra": {"blocks": [{"type": "tool", "tool_call_id": "c1", "tool": "task"},
                              {"type": "text", "text": "done"}]}},
    ])
    run = print_turn.PrintRun(session_id="s", started_at=100.0, execution_id="exec-run")
    return run, store


def test_every_event_carries_the_envelope_with_an_unbroken_sequence(monkeypatch):
    run, store = _sub_agent_run(monkeypatch)

    events = print_events.run_events(run, store=store)

    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert {event["schema_version"] for event in events} == {print_events.SCHEMA_VERSION}
    assert set(events[0]) == {"schema_version", "seq", "execution_id",
                              "parent_execution_id", "type", "data"}


def test_a_sub_agent_s_events_name_it_and_its_parent(monkeypatch):
    run, store = _sub_agent_run(monkeypatch)

    events = print_events.run_events(run, store=store)

    seen = [(e["type"], e["data"].get("call_kind"), e["execution_id"], e["parent_execution_id"])
            for e in events]
    assert seen == [
        ("run", None, "exec-run", None),
        ("user", None, "exec-run", None),
        ("request", "chat", "exec-run", None),
        ("user", None, "exec-sub", "exec-run"),
        # The sub-agent's compaction, then its own round, inside the call.
        ("request", "compaction", "exec-sub", "exec-run"),
        ("request", "chat", "exec-sub", "exec-run"),
        ("assistant_text", None, "exec-sub", "exec-run"),
        ("reply", None, "exec-sub", "exec-run"),
        ("tool_call", None, "exec-run", None),
        ("tool_result", None, "exec-run", None),
        ("request", "chat", "exec-run", None),
        ("assistant_text", None, "exec-run", None),
        ("reply", None, "exec-run", None),
    ]


def test_the_transcript_is_the_events_type_and_data(monkeypatch):
    run, store = _sub_agent_run(monkeypatch)

    events = print_events.run_events(run, store=store)
    records = print_turn.transcript_records(run, store=store)

    assert records == [{"type": e["type"], **e["data"]} for e in events]
    assert all(list(record)[0] == "type" for record in records)
    [result] = [r for r in records if r["type"] == "tool_result"]
    assert (result["content"], result["complete"]) == ("seen", True)


def test_what_the_stores_do_not_hold_is_a_gap(monkeypatch):
    """The ledger could not be read and the run's messages could not all be
    found: both are said, not left out."""
    _scope(monkeypatch, None, anchors={"u": "exec-run"}, parents={}, unread=True)
    store = _Store([{"role": "user", "id": "u", "timestamp": 100.0, "content": "go"}])
    run = print_turn.PrintRun(session_id="s", started_at=100.0, execution_id="exec-run")

    events = print_events.run_events(run, store=store)

    assert [(e["type"], e["data"].get("what")) for e in events] == [
        ("run", None), ("gap", "messages"), ("gap", "requests"), ("user", None)]


def test_a_stopped_run_s_unfinished_call_is_a_gap_after_its_result(monkeypatch):
    _scope(monkeypatch, [_request(110.0, "exec-run")],
           anchors={"u": "exec-run", "a": "exec-run"}, parents={})
    store = _Store([
        {"role": "user", "id": "u", "timestamp": 100.0, "content": "go"},
        {"role": "assistant", "id": "a", "timestamp": 101.0, "status": "cancelled",
         "content": "", "extra": {"blocks": [{"type": "tool", "tool_call_id": "c1", "tool": "bash"}]}},
    ])
    run = print_turn.PrintRun(session_id="s", started_at=100.0, execution_id="exec-run",
                              ended=print_turn.ENDED_CANCELLED)

    records = print_turn.transcript_records(run, store=store)

    assert [(r["type"], r.get("tool_call_id")) for r in records[1:]] == [
        ("user", None), ("request", None), ("tool_call", "c1"), ("tool_result", "c1"),
        ("gap", "c1"), ("reply", None)]
    assert records[0]["ended"] == "cancelled"
