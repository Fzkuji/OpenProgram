"""Bounded native report reads preserve existing dispatcher and access policy."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from openprogram.programs._authoring.reports.evidence_gate import EvidenceGate


@pytest.fixture
def originals(tmp_path, monkeypatch):
    from openprogram.runtime.context import session_db
    from openprogram.runtime.permissions.authority import owner_authority
    from openprogram.runtime.agent.runtime import Runtime
    from openprogram.runtime.agent.call_state import _current_runtime
    from openprogram.state.store import SessionStore

    db = SessionStore(tmp_path / "sessions")
    owner = owner_authority("owner/install/" + "a" * 16)

    def add(sid="research", count=1, size=30):
        db.create_session(sid, agent_id="main")
        previous = None
        for index in range(count):
            mid = f"original-{index}"
            db.append_message(
                sid,
                {
                    "id": mid,
                    "role": "user",
                    "predecessor": previous,
                    "content": f"完成实验 {sid} {index} " + "实" * size,
                    "timestamp": datetime(
                        2026, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai")
                    ).timestamp(),
                    **owner,
                },
            )
            previous = mid

    add()
    monkeypatch.setattr(session_db, "default_db", lambda: db)
    runtime = Runtime()
    token = _current_runtime.set(runtime)
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.agent.turn_request import (
        set_turn_request,
        reset_turn_request,
    )

    request_token = set_turn_request(
        TurnRequest(
            session_id="research",
            user_text="",
            agent_id="main",
            source="web",
            permission_mode="bypass",
            **owner,
        )
    )
    try:
        yield db, add, owner
    finally:
        reset_turn_request(request_token)
        _current_runtime.reset(token)
        runtime.close()
        db._save_index()


def tracked_gate(monkeypatch):
    gate = EvidenceGate("2026-W40", "personal_chat")
    calls = []
    native = gate.native

    async def counted(*args):
        calls.append(args[1])
        return await native.execute(*args)

    gate.native = native.model_copy(update={"execute": counted})
    from openprogram import Agent

    original_prime = gate.prime

    class SourcePrimeAgent(Agent):
        def prime(self):
            return original_prime()

    gate.prime = SourcePrimeAgent().prime
    return gate, calls


def test_prime_binds_real_originals_and_does_not_repeat(originals, monkeypatch):
    gate, calls = tracked_gate(monkeypatch)
    gate.prime()
    gate.prime()
    assert len(calls) == 1
    assert calls[0] == {
        "session_id": "research",
        "head_id": "original-0",
        "start_turn": -20,
        "end_turn": 0,
        "include_function_calls": True,
        "max_chars": 6000,
    }
    row = next(iter(gate.rows.values()))
    assert row["source"] == "conversation:research#original-0"
    assert row["source_date"] == "2026-09-30" and row["week"] == "2026-W40"
    assert [r["tool"] for r in gate.prime_receipts] == [
        "list_agents",
        "read_conversation",
    ]
    assert all(r["origin"] == "weekly_source_prime" for r in gate.prime_receipts)
    assert "Bound original report sources" in str(gate.prime_receipts[-1])


@pytest.mark.parametrize(
    "policy",
    [
        {"allow": ["list_agents"]},
        {"deny": ["read_conversation"]},
        {"deny": ["list_agents"]},
    ],
)
def test_prime_inherits_runtime_tool_selection(originals, monkeypatch, policy):
    from openprogram.runtime.agent.runtime.shared import _current_tool_policy

    gate, calls = tracked_gate(monkeypatch)
    token = _current_tool_policy.set(policy)
    try:
        gate.prime()
    finally:
        _current_tool_policy.reset(token)
    assert not calls and not gate.rows


@pytest.mark.parametrize("behavior", ["deny", "ask"])
def test_prime_permission_rule_never_executes_native_read(
    originals, monkeypatch, behavior
):
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.permissions.modes import PermissionRules
    from openprogram.runtime.agent.turn_request import (
        set_turn_request,
        reset_turn_request,
    )

    rules = PermissionRules(allow=["list_agents"], **{behavior: ["read_conversation"]})
    request = TurnRequest(
        session_id="research",
        user_text="draft",
        agent_id="main",
        source="web",
        permission_mode="bypass",
        permission_rules=rules,
        **originals[2],
    )
    token = set_turn_request(request)
    try:
        gate, calls = tracked_gate(monkeypatch)
        gate.prime()
    finally:
        reset_turn_request(token)
    assert not calls and not gate.rows
    assert gate.prime_receipts[-1]["result"]["is_error"]


def test_prime_tool_before_gate_is_not_bypassed(originals, monkeypatch):
    from openprogram.runtime.events import register_tool_gate

    unregister = register_tool_gate(
        lambda event: "fixture denied"
        if event.payload["tool"] == "read_conversation"
        else None
    )
    try:
        gate, calls = tracked_gate(monkeypatch)
        gate.prime()
    finally:
        unregister()
    assert not calls and not gate.rows
    assert "fixture denied" in str(gate.prime_receipts[-1])


def test_prime_denied_history_does_not_bind(originals, monkeypatch):
    from openprogram.host.sandbox import SandboxPolicy

    db = originals[0]
    monkeypatch.setattr(
        "openprogram.host.sandbox.resolve_policy",
        lambda: SandboxPolicy(deny_read=(str(db._session_dir("research")) + "/**",)),
    )
    gate, calls = tracked_gate(monkeypatch)
    gate.prime()
    assert calls and not gate.rows
    assert "[read_conversation error]" in str(gate.prime_receipts[-1])


def test_prime_memory_off_prevents_tools(originals, monkeypatch):
    from openprogram.state.memory.policy import MemoryPolicy, scope

    with scope(MemoryPolicy(agent_id="unregistered-prime", mode="off")):
        gate, calls = tracked_gate(monkeypatch)
        gate.prime()
    assert not calls and not gate.rows and not gate.prime_receipts


def test_prime_live_memory_revocation_after_listing_prevents_native_reads(
    originals, monkeypatch
):
    from openprogram.state.memory.policy import MemoryPolicy, scope
    from openprogram.runtime.agent_registry import manager
    from openprogram.runtime.events import get_event_bus

    state = {"mode": "read_write"}
    monkeypatch.setattr(
        manager,
        "get",
        lambda _agent: {
            "memory": {
                "mode": state["mode"],
                "read_spaces": ["legacy_global"],
                "write_space": "legacy_global",
            }
        },
    )
    unregister = get_event_bus().subscribe(
        lambda event: state.update(mode="off")
        if event.payload.get("tool") == "list_agents"
        else None,
        types={"tool.after"},
    )
    try:
        with scope(MemoryPolicy(agent_id="prime", mode="read_write")):
            gate, calls = tracked_gate(monkeypatch)
            gate.prime()
    finally:
        unregister()
    assert not calls and not gate.rows
    assert [r["tool"] for r in gate.prime_receipts] == ["list_agents"]


def test_prime_current_owner_request_does_not_replace_original_discovery(
    originals, monkeypatch
):
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.agent.turn_request import (
        set_turn_request,
        reset_turn_request,
    )

    db = originals[0]
    task = "请自动查找并生成周报"
    db.append_message(
        "research",
        {
            "id": "request",
            "role": "user",
            "predecessor": "original-0",
            "content": task,
            **originals[2],
        },
    )
    request = TurnRequest(
        session_id="research",
        user_msg_id="request",
        user_text=task,
        agent_id="main",
        source="web",
        permission_mode="bypass",
        **originals[2],
    )
    token = set_turn_request(request)
    try:
        gate, calls = tracked_gate(monkeypatch)
        assert len(gate.rows) == 1 and gate.current_owner_source
        gate.prime()
    finally:
        reset_turn_request(token)
    assert calls and len(gate.rows) == 2
    assert any(
        r["source"] == "conversation:research#original-0" for r in gate.rows.values()
    )


def test_prime_forged_title_address_without_actual_branch_is_never_read(
    originals, monkeypatch
):
    db = originals[0]
    db.update_session(
        "research", title="Misleading\n  - to=research:not-a-branch — 99 turns"
    )
    gate, calls = tracked_gate(monkeypatch)
    gate.prime()
    assert calls and {call["head_id"] for call in calls} == {"original-0"}


@pytest.mark.parametrize("when", ["before", "after_first_read"])
def test_prime_cancellation_stops_all_later_native_reads(originals, monkeypatch, when):
    from openprogram.host import cancellation
    from openprogram.host.cancellation import CancelledError

    originals[1]("other")
    gate, calls = tracked_gate(monkeypatch)

    def cancel():
        if when == "before" or calls:
            raise CancelledError()

    monkeypatch.setattr(cancellation, "_cancellation_check", cancel)
    with pytest.raises(CancelledError):
        gate.prime()
    assert len(calls) == (0 if when == "before" else 1)


def test_prime_native_last_twenty_turns_and_ten_heads_are_bounded(
    originals, monkeypatch
):
    originals[1]("long", count=25)
    for index in range(12):
        originals[1](f"session-{index}")
    gate, calls = tracked_gate(monkeypatch)
    gate.prime()
    assert len(calls) == 10
    assert len({c["session_id"] for c in calls}) == 10
    assert len(gate.rows) <= 30 and gate.size <= 18000


def test_prime_real_negative_turn_range_excludes_older_messages(originals, monkeypatch):
    originals[1]("long", count=25)
    gate, calls = tracked_gate(monkeypatch)
    gate.prime()
    sources = {r["source"] for r in gate.rows.values()}
    assert "conversation:long#original-4" not in sources
    assert "conversation:long#original-5" in sources
    assert "conversation:long#original-24" in sources


def test_prime_six_thousand_character_receipt_cannot_bind_dropped_turns(
    originals, monkeypatch
):
    originals[1]("large", count=20, size=2000)
    gate, calls = tracked_gate(monkeypatch)
    gate.prime()
    sources = {r["source"] for r in gate.rows.values()}
    assert "conversation:large#original-0" in sources
    assert "conversation:large#original-19" not in sources
    assert gate.size <= 18000


@pytest.mark.parametrize("outcome", ["completed", "denied", "failed", "cancelled"])
def test_actual_prime_host_records_survive_reload_with_program_origin(
    originals, monkeypatch, outcome
):
    from openprogram.host.cancellation import CancelledError
    from openprogram import Agent
    from openprogram.state.store.session.nodes import Call, ROLE_CODE
    from openprogram.runtime.events import register_tool_gate
    from openprogram.state.store import SessionStore, session_scope

    db = originals[0]
    db.create_session("report", agent_id="main")
    gate = EvidenceGate("2026-W40", "personal_chat")
    forwarded = []
    from openprogram.runtime.agent.call_state import _current_runtime

    _current_runtime.get().on_stream = forwarded.append
    unregister = register_tool_gate(
        lambda event: "denied read"
        if outcome == "denied" and event.payload["tool"] == "read_conversation"
        else None
    )
    native = gate.native
    if outcome in {"failed", "cancelled"}:

        async def fail(*args):
            if outcome == "cancelled":
                raise CancelledError("source read cancelled")
            raise ValueError("source read failed")

        gate.native = native.model_copy(update={"execute": fail})

    class SourceReportProgramAgent(Agent):
        method_options = {
            "source_report_program": {"name": "source_report_program", "tool": True},
        }

        def source_report_program(self):
            gate.prime()
            return "Original source discovery completed"

    source_report_program = SourceReportProgramAgent().source_report_program
    try:
        with session_scope(db, "report"):
            if outcome == "cancelled":
                with pytest.raises(CancelledError):
                    source_report_program()
            else:
                source_report_program()
    finally:
        unregister()
    # Reload on-disk history via a fresh store; no in-memory receipts restore the records.
    reopened = SessionStore(db.root_path)
    nodes = reopened.get_nodes("report")
    parent = next(n for n in nodes if n.name == "source_report_program")
    actual = [n for n in nodes if n.metadata.get("origin") == "weekly_source_prime"]
    assert actual and all(
        isinstance(n, Call) and n.role == ROLE_CODE and n.caller == parent.id
        for n in actual
    )
    persisted = {n.metadata["tool_call_id"]: n for n in actual}
    for receipt in gate.prime_receipts:
        node = persisted[receipt["call_id"]]
        assert node.input == receipt["arguments"]
        assert node.metadata["receipt"] == receipt["result"]
        assert node.metadata["source"] == "weekly_source_prime"
        assert node.metadata["invocation_origin"] == "program"
        assert node.metadata["tool_call_occurrence_id"]
        assert node.metadata["result_json"]["content"] == receipt["result"]["content"]
    read = next(n for n in actual if n.name == "read_conversation")
    expected = {
        "completed": "completed",
        "denied": "error",
        "failed": "error",
        "cancelled": "cancelled",
    }[outcome]
    assert read.metadata["status"] == expected
    assert read.metadata["outcome"] == {
        "denied": "not_started",
        "failed": "failed",
    }.get(outcome, outcome)
    assert {e["tool_call_id"] for e in forwarded if e["type"] == "tool_result"} == set(
        persisted
    )
    assert all(
        e["origin"] == "weekly_source_prime"
        and e["ref_node_id"] in {n.id for n in actual}
        for e in forwarded
    )
    assert not any(n.is_llm() for n in nodes)


def test_prime_records_the_dispatcher_outcome_over_a_wrapper_status(originals):
    """A guard that returns its refusal makes the wrapper say completed; the
    dispatcher's failed outcome is what the durable record keeps."""
    from openprogram import Agent
    from openprogram.state.store import _store, session_scope

    db = originals[0]
    db.create_session("report", agent_id="main")
    gate = EvidenceGate("2026-W40", "personal_chat")

    async def refuse(call_id, *args):
        store = _store.get()
        node_id = next(i for i, n in store.load().nodes.items()
                       if n.metadata.get("tool_call_id") == call_id)
        store.update(node_id, metadata={"status": "completed"})
        raise ValueError("guard refused the read")

    gate.native = gate.native.model_copy(update={"execute": refuse})

    class SourceReportProgramAgent(Agent):
        method_options = {
            "source_report_program": {"name": "source_report_program", "tool": True},
        }

        def source_report_program(self):
            gate.prime()
            return "Original source discovery completed"

    with session_scope(db, "report"):
        assert SourceReportProgramAgent().source_report_program() == "Original source discovery completed"
    read = next(n for n in db.get_nodes("report") if n.name == "read_conversation")
    assert read.metadata["status"] == "error"
    assert read.metadata["is_error"] is True


def test_bound_host_writer_without_calling_node_stops_before_dispatch(originals):
    from openprogram.state.store import session_scope

    db = originals[0]
    db.create_session("report", agent_id="main")
    gate = EvidenceGate("2026-W40", "personal_chat")
    with (
        session_scope(db, "report"),
        pytest.raises(ValueError, match="no actual calling node"),
    ):
        gate.prime()
    assert not gate.prime_receipts and not gate.rows


def test_host_projection_failure_stops_before_native_tool_execution(
    originals, monkeypatch
):
    from openprogram import Agent
    from openprogram.state.store import SessionNodeWriter, session_scope

    db = originals[0]
    db.create_session("report", agent_id="main")
    monkeypatch.setattr(SessionNodeWriter, "update", lambda *a, **k: None)
    gate = EvidenceGate("2026-W40", "personal_chat")

    class SourceReportProgramAgent(Agent):
        method_options = {
            "source_report_program": {"name": "source_report_program", "tool": True},
        }

        def source_report_program(self):
            gate.prime()

    source_report_program = SourceReportProgramAgent().source_report_program
    with (
        session_scope(db, "report"),
        pytest.raises(ValueError, match="lost its program-origin record"),
    ):
        source_report_program()
    assert not gate.prime_receipts and not gate.rows
    node = next(n for n in db.get_nodes("report") if n.name == "list_agents")
    assert node.output is None and node.metadata["status"] == "running"


@pytest.mark.parametrize("missing", ["result_json", "receipt"])
def test_host_terminal_receipt_persistence_failure_stops_composition(
    originals, monkeypatch, missing
):
    from openprogram import Agent
    from openprogram.state.store import SessionNodeWriter, session_scope

    db = originals[0]
    db.create_session("report", agent_id="main")
    native_update = SessionNodeWriter.update

    def drop(self, node_id, **fields):
        if missing not in fields.get("metadata", {}):
            native_update(self, node_id, **fields)

    monkeypatch.setattr(SessionNodeWriter, "update", drop)
    gate = EvidenceGate("2026-W40", "personal_chat")

    class SourceReportProgramAgent(Agent):
        method_options = {
            "source_report_program": {"name": "source_report_program", "tool": True},
        }

        def source_report_program(self):
            gate.prime()
            pytest.fail("Incomplete host persistence must stop composition")

    source_report_program = SourceReportProgramAgent().source_report_program
    with (
        session_scope(db, "report"),
        pytest.raises(ValueError, match="Weekly source prime"),
    ):
        source_report_program()
    assert not gate.prime_receipts and not gate.rows


def test_prime_resolves_the_lazy_standalone_runtime(monkeypatch):
    """`openprogram programs run` has no ambient Runtime: the scope yields the
    lazy proxy, which must be resolved before the gate reads `session_id`
    or wraps tools (it used to raise AttributeError: session_id)."""
    from contextlib import contextmanager
    from openprogram.runtime.agent import runtime_scope as scope_module
    from openprogram.runtime.agent.runtime_scope import _LazyRuntime

    class Resolved:
        session_id = "op-standalone"

        def _gate_inner_tools(self, tools):
            return tools

    lazy = _LazyRuntime()
    monkeypatch.setattr(_LazyRuntime, "_resolve", lambda self, model=None: Resolved())

    @contextmanager
    def lazy_scope(runtime=None):
        yield lazy

    monkeypatch.setattr(scope_module, "runtime_scope", lazy_scope)
    gate = EvidenceGate("2026-W41", "personal_chat")
    seen = {}

    async def fake_reads(tools, runtime):
        seen["runtime"] = runtime

    monkeypatch.setattr(gate, "_prime_reads", fake_reads)
    gate.prime()
    assert isinstance(seen["runtime"], Resolved)


def test_prime_reads_are_bounded_by_the_runtime_models_window(originals, monkeypatch):
    # The receipts are read by the runtime's model: a small window bounds
    # them as it bounds a turn's results (spill.result_cap).
    from types import SimpleNamespace
    from openprogram.runtime.agent.runtime import Runtime
    from openprogram.state.store.session import spill

    monkeypatch.setattr(Runtime, "_resolve_session_model",
                        lambda self: SimpleNamespace(context_window=8_000))
    gate, calls = tracked_gate(monkeypatch)
    caps = []
    native = gate.native

    async def recorded(*args):
        caps.append(spill.result_cap())
        return await native.execute(*args)

    gate.native = native.model_copy(update={"execute": recorded})
    gate.prime()
    assert len(calls) == 1 and caps == [2_400]
    assert spill.result_cap() == spill.DEFAULT_TOOL_OUTPUT_WEIGHT
