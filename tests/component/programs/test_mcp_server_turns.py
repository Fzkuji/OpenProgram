from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace

import mcp.types as mcp_types
import pytest
from mcp.shared.exceptions import McpError

from openprogram.agent.authority import mcp_client_authority
from openprogram.agent.dispatcher import TurnResult
from openprogram.events import create_event_bus, make_event
from openprogram.mcp.server.service import MCPClientContext, MCPService


class FakeSessionDB:
    def __init__(self) -> None:
        self.sessions = {
            "existing": {"id": "existing", "agent_id": "researcher"},
            "second": {"id": "second", "agent_id": "main"},
            "malformed": {"id": "malformed", "agent_id": ""},
        }
        self.created: list[tuple] = []

    def get_session(self, session_id):
        return self.sessions.get(session_id)

    def create_session(self, session_id, agent_id, **kwargs):
        self.created.append((session_id, agent_id, kwargs))
        self.sessions[session_id] = {"id": session_id, "agent_id": agent_id}


class CanonicalSessionDB(FakeSessionDB):
    def __init__(self, nodes=None) -> None:
        super().__init__()
        self.nodes = list(nodes or [])

    def get_nodes(self, session_id):
        return list(self.nodes)


class FakeQuestions:
    def __init__(self) -> None:
        self.resolved: list[tuple] = []
        self.cancelled: list[tuple[str, str]] = []
        self.pending: dict[str, str] = {}

    def resolve(self, question_id, outcome, value=None):
        self.resolved.append((question_id, outcome, value))
        return True

    def list_pending(self, session_id):
        return [
            SimpleNamespace(id=qid, execution_id=execution_id)
            for qid, execution_id in self.pending.items()
        ]

    def cancel_execution(self, session_id, execution_id):
        self.cancelled.append((session_id, execution_id))


def _context(client_id="0123456789abcdef"):
    return MCPClientContext(client_id, mcp_client_authority(client_id))


def _payload(result):
    assert len(result.content) == 1
    return json.loads(result.content[0].text)


def _active(service):
    with service._active_lock:
        return tuple(service._active_by_request.values())


def _service(
    *, db=None, process=None, bus=None, questions=None, calls=None, context=None,
    cancel=None,
):
    calls = calls if calls is not None else []
    bus = bus or create_event_bus()
    questions = questions or FakeQuestions()
    current_events = {}
    cleanup_leases = {}

    def record(name, value=None):
        calls.append((name, value))

    def register(session_id, event, *, execution_id):
        record("register", (session_id, event, execution_id))
        current_events[(session_id, execution_id)] = event

    def unregister(session_id, event, *, execution_id):
        record("unregister", (session_id, event, execution_id))
        if current_events.get((session_id, execution_id)) is event:
            current_events.pop((session_id, execution_id), None)

    def cancel_execution(execution_id):
        record("cancel", execution_id)
        for key, event in current_events.items():
            if key[1] == execution_id:
                event.set()

    def acquire_cleanup(session_id, event):
        if (
            not any(
                key[0] == session_id and candidate is event
                for key, candidate in current_events.items()
            )
            or session_id in cleanup_leases
        ):
            return False
        cleanup_leases[session_id] = event
        return True

    def release_cleanup(session_id, event):
        if cleanup_leases.get(session_id) is event:
            cleanup_leases.pop(session_id, None)

    return MCPService(
        context or _context(),
        session_db=db or FakeSessionDB(),
        process_user_turn=process
        or (lambda req, *, cancel_event: TurnResult("ok", "u", "a")),
        register_cancel_event=register,
        unregister_cancel_event=unregister,
        current_cancel_event=lambda session_id, *, execution_id: current_events.get(
            (session_id, execution_id)
        ),
        acquire_cancel_cleanup=acquire_cleanup,
        release_cancel_cleanup=release_cleanup,
        cancel_execution=cancel or cancel_execution,
        question_registry_getter=lambda: questions,
        event_bus_getter=lambda: bus,
    )


def test_prompt_send_creates_mcp_session_and_returns_exact_payload() -> None:
    db = FakeSessionDB()
    captured = []

    def process(req, *, cancel_event):
        captured.append((req, cancel_event))
        return TurnResult("回答", "user-1", "assistant-1", failed=False)

    result = asyncio.run(
        _service(db=db, process=process).prompt_send(
            "问题", session_id=None, request_id="request-1"
        )
    )

    assert result.is_error is False
    assert _payload(result) == {
        "session_id": captured[0][0].session_id,
        "text": "回答",
        "assistant_msg_id": "assistant-1",
        "failed": False,
    }
    session_id = captured[0][0].session_id
    assert session_id.startswith("mcp_") and len(session_id) == 36
    assert db.created == [(session_id, "main", {"source": "mcp"})]


def test_prompt_send_existing_session_uses_fixed_request_and_exact_events() -> None:
    calls = []
    captured = []

    def process(req, *, cancel_event):
        captured.append((req, cancel_event))
        return TurnResult("done", "u", "a", failed=True)

    service = _service(process=process, calls=calls)
    result = asyncio.run(
        service.prompt_send("prompt", session_id="existing", request_id="request-1")
    )

    req, passed_event = captured[0]
    registered = next(value for name, value in calls if name == "register")
    unregistered = next(value for name, value in calls if name == "unregister")
    assert req.session_id == "existing"
    assert req.agent_id == "researcher"
    assert req.user_text == "prompt"
    assert req.source == "mcp"
    assert req.permission_mode == "ask"
    assert req.user_msg_id
    assert registered[2].startswith("exec_")
    assert {key: getattr(req, key) for key in service.context.authority} == dict(
        service.context.authority
    )
    assert req.interaction == "non-interactive"
    assert registered[0] == "existing"
    assert registered[2].startswith("exec_")
    assert unregistered == registered
    assert _active(service) == ()
    assert _payload(result)["failed"] is True


def test_prompt_send_registers_exact_execution_before_dispatch() -> None:
    captured = []
    entered = threading.Event()

    def process(req, *, cancel_event):
        captured.append(req)
        with service._active_lock:
            active = service._active_by_request["request-1"]
        assert active.execution_id.startswith("exec_")
        entered.set()
        return TurnResult("done", req.user_msg_id, req.user_msg_id + "_reply")

    service = _service(process=process)
    result = asyncio.run(
        service.prompt_send("prompt", session_id="existing", request_id="request-1")
    )

    assert entered.is_set()
    assert result.is_error is False
    request = captured[0]
    assert request.user_msg_id


def test_cancel_barrier_before_admission_never_activates_prompt(monkeypatch) -> None:
    """A cancel racing the worker admission is consumed before activation."""
    from openprogram.agent.production_driver import CanonicalAgentAdmission

    admitted = threading.Event()
    release = threading.Event()
    activated = threading.Event()
    failed: list[tuple[str, str]] = []

    class Adapter:
        def __init__(self, **_kwargs):
            pass

        def admit(self, *_args, **_kwargs):
            admitted.set()
            assert release.wait(2)
            return CanonicalAgentAdmission("exec-barrier", "existing", 0)

        def fail_admission(self, admission, *, reason_code, target=None):
            failed.append((admission.execution_id, reason_code))

        async def activate(self, _admission):
            activated.set()

    monkeypatch.setattr(
        "openprogram.agent.production_driver.CanonicalAgentAdapter", Adapter,
    )
    service = _service()

    async def scenario():
        task = asyncio.create_task(
            service.prompt_send("prompt", session_id="existing", request_id="r1")
        )
        await asyncio.to_thread(admitted.wait, 1)
        assert service.prompt_cancel("existing").content[0].text == (
            '{"cancelled":true,"session_id":"existing"}'
        )
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert activated.is_set() is False
    assert failed == [("exec-barrier", "prompt_cancel")]
    assert _active(service) == ()


@pytest.mark.parametrize("session_id", ["unknown", "malformed"])
def test_prompt_send_rejects_unknown_or_malformed_supplied_session(session_id) -> None:
    db = FakeSessionDB()
    dispatched = []
    service = _service(db=db, process=lambda *args, **kwargs: dispatched.append(args))

    with pytest.raises(McpError) as caught:
        asyncio.run(
            service.prompt_send("prompt", session_id=session_id, request_id="request-1")
        )

    assert caught.value.error.code == mcp_types.INVALID_PARAMS
    assert caught.value.error.message == "invalid MCP prompt session"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert db.created == []
    assert dispatched == []


def test_prompt_send_rejects_duplicate_request_id_without_dispatch() -> None:
    entered = threading.Event()
    release = threading.Event()

    def process(req, *, cancel_event):
        entered.set()
        release.wait(2)
        return TurnResult("first", "u", "a")

    service = _service(process=process)

    async def scenario():
        first = asyncio.create_task(
            service.prompt_send("first", session_id="existing", request_id="same")
        )
        await asyncio.to_thread(entered.wait, 1)
        second = await service.prompt_send(
            "second", session_id="existing", request_id="same"
        )
        release.set()
        await first
        return second

    second = asyncio.run(scenario())
    assert second.is_error is True
    assert _payload(second) == {"error": "prompt execution failed"}


@pytest.mark.parametrize("rejection", ["closed", "duplicate"])
def test_rejected_omitted_session_does_not_create_orphan(rejection) -> None:
    db = FakeSessionDB()
    entered = threading.Event()
    release = threading.Event()

    def process(req, *, cancel_event):
        entered.set()
        release.wait(2)
        return TurnResult("first", "u", "a")

    service = _service(db=db, process=process)

    async def scenario():
        first = None
        if rejection == "closed":
            service.close()
        else:
            first = asyncio.create_task(
                service.prompt_send("first", session_id="existing", request_id="same")
            )
            await asyncio.to_thread(entered.wait, 1)
        before = list(db.created)
        rejected = await service.prompt_send(
            "rejected", session_id=None, request_id="same"
        )
        assert db.created == before
        if first is not None:
            release.set()
            await first
        return rejected

    rejected = asyncio.run(scenario())
    assert rejected.is_error is True
    assert _payload(rejected) == {"error": "prompt execution failed"}


def test_concurrent_requests_are_isolated_and_completion_removes_only_owner() -> None:
    entered = {name: threading.Event() for name in ("one", "two")}
    release = {name: threading.Event() for name in ("one", "two")}

    def process(req, *, cancel_event):
        entered[req.user_text].set()
        release[req.user_text].wait(2)
        return TurnResult(req.user_text, "u", f"a-{req.user_text}")

    service = _service(process=process)

    async def scenario():
        one = asyncio.create_task(
            service.prompt_send("one", session_id="existing", request_id="r1")
        )
        two = asyncio.create_task(
            service.prompt_send("two", session_id="second", request_id="r2")
        )
        await asyncio.gather(
            asyncio.to_thread(entered["one"].wait, 1),
            asyncio.to_thread(entered["two"].wait, 1),
        )
        assert {item.request_id for item in _active(service)} == {"r1", "r2"}
        release["one"].set()
        await one
        assert tuple(item.request_id for item in _active(service)) == ("r2",)
        release["two"].set()
        await two

    asyncio.run(scenario())
    assert _active(service) == ()


def test_prompt_cancel_owned_request_performs_complete_idempotent_cleanup() -> None:
    calls = []
    questions = FakeQuestions()
    bus = create_event_bus()
    audit = []
    bus.subscribe(lambda event: audit.append(event), types={"mcp.request.cancelled"})
    entered = threading.Event()
    release = threading.Event()

    def process(req, *, cancel_event):
        entered.set()
        release.wait(2)
        return TurnResult("late", "u", "a")

    service = _service(calls=calls, questions=questions, bus=bus, process=process)

    async def scenario():
        task = asyncio.create_task(
            service.prompt_send("prompt", session_id="existing", request_id="r1")
        )
        await asyncio.to_thread(entered.wait, 1)
        record = _active(service)[0]
        first = service.prompt_cancel("existing")
        second = service.prompt_cancel("existing")
        service.cancel_request("r1", reason="secret-repeat")
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        return record, first, second

    record, first, second = asyncio.run(scenario())

    assert _payload(first) == {"session_id": "existing", "cancelled": True}
    assert _payload(second) == {"session_id": "existing", "cancelled": False}
    assert record.thread_cancel.is_set() and record.tool_cancel.is_set()
    assert calls == [
        ("register", ("existing", record.thread_cancel, record.execution_id)),
        ("cancel", record.execution_id),
        ("unregister", ("existing", record.thread_cancel, record.execution_id)),
    ]
    bus.emit(
        make_event(
            "question.asked",
            "agent",
            {
                "id": "late-question",
                "session_id": "existing",
                "execution_id": record.execution_id,
            },
        )
    )
    assert questions.resolved == []
    assert questions.cancelled == [("existing", record.execution_id)]
    assert len(audit) == 1
    assert audit[0].payload == {
        "request_id": "r1",
        "execution_id": record.execution_id,
        "session_id": "existing",
        "client_id": "0123456789abcdef",
        "reason": "prompt_cancel",
    }
    assert "secret" not in repr(audit[0])


@pytest.mark.parametrize("error", ["terminal", "unexpected"])
def test_prompt_cancel_service_failure_preserves_live_turn(error) -> None:
    from openprogram.agent import run_control

    entered = threading.Event()
    release = threading.Event()
    audit = []
    questions = FakeQuestions()
    bus = create_event_bus()
    bus.subscribe(lambda event: audit.append(event), types={"mcp.request.cancelled"})

    def process(req, *, cancel_event):
        entered.set()
        release.wait(2)
        return TurnResult("normal", "u", "a")

    def cancel(execution_id):
        if error == "terminal":
            raise run_control.ExecutionNotCancellable(
                execution_id, {"execution_id": execution_id, "status": "completed"}
            )
        raise RuntimeError("store failure")

    service = _service(
        process=process, questions=questions, bus=bus, cancel=cancel,
    )

    async def scenario():
        task = asyncio.create_task(
            service.prompt_send("prompt", session_id="existing", request_id="r1")
        )
        await asyncio.to_thread(entered.wait, 1)
        record = _active(service)[0]
        result = service.prompt_cancel("existing")
        assert _payload(result) == {"session_id": "existing", "cancelled": False}
        assert not record.thread_cancel.is_set()
        assert not record.tool_cancel.is_set()
        assert _active(service) == (record,)
        release.set()
        normal = await task
        assert _payload(normal)["text"] == "normal"

    asyncio.run(scenario())
    assert questions.cancelled == []
    assert audit == []


def test_prompt_cancel_not_found_allows_only_verified_pre_placeholder() -> None:
    from openprogram.agent import run_control

    entered = threading.Event()
    release = threading.Event()
    db = CanonicalSessionDB()
    questions = FakeQuestions()
    audit = []
    bus = create_event_bus()
    bus.subscribe(lambda event: audit.append(event), types={"mcp.request.cancelled"})

    def process(req, *, cancel_event):
        entered.set()
        release.wait(2)
        return TurnResult("late", "u", "a")

    service = _service(
        db=db, process=process, questions=questions, bus=bus,
        cancel=lambda execution_id: (_ for _ in ()).throw(
            run_control.ExecutionNotFound(execution_id)
        ),
    )

    async def scenario():
        task = asyncio.create_task(
            service.prompt_send("prompt", session_id="existing", request_id="r1")
        )
        await asyncio.to_thread(entered.wait, 1)
        record = _active(service)[0]
        result = service.prompt_cancel("existing")
        assert _payload(result)["cancelled"] is False
        assert not record.thread_cancel.is_set()
        release.set()
        await task
        return record

    record = asyncio.run(scenario())
    assert questions.cancelled == []
    assert audit == []


@pytest.mark.parametrize("lookup", ["placeholder", "store_failure", "retired"])
def test_prompt_cancel_not_found_guards(lookup) -> None:
    from openprogram.agent import run_control

    entered = threading.Event()
    release = threading.Event()
    execution_nodes = []
    db = CanonicalSessionDB(execution_nodes)
    questions = FakeQuestions()

    def process(req, *, cancel_event):
        entered.set()
        release.wait(2)
        return TurnResult("normal", "u", "a")

    def cancel(execution_id):
        raise run_control.ExecutionNotFound(execution_id)

    service = _service(db=db, process=process, questions=questions, cancel=cancel)

    async def scenario():
        task = asyncio.create_task(
            service.prompt_send("prompt", session_id="existing", request_id="r1")
        )
        await asyncio.to_thread(entered.wait, 1)
        record = _active(service)[0]
        if lookup == "placeholder":
            db.nodes.append(SimpleNamespace(id=record.execution_id))
        elif lookup == "store_failure":
            db.get_nodes = lambda _session_id: (_ for _ in ()).throw(
                RuntimeError("read failed")
            )
        else:
            service._current_cancel_event = (
                lambda _session_id, *, execution_id: None
            )
        result = service.prompt_cancel("existing")
        assert _payload(result) == {"session_id": "existing", "cancelled": False}
        assert not record.thread_cancel.is_set()
        assert _active(service) == (record,)
        release.set()
        await task

    asyncio.run(scenario())
    assert questions.cancelled == []


def test_abandoned_outer_cancel_keeps_owner_until_late_worker_returns() -> None:
    entered = threading.Event()
    release = threading.Event()
    worker_done = threading.Event()

    def process(req, *, cancel_event):
        entered.set()
        release.wait(2)
        worker_done.set()
        return TurnResult(req.user_text, "u", "a")

    def fail(_execution_id):
        raise RuntimeError("cancel service unavailable")

    service = _service(process=process, cancel=fail)

    async def scenario():
        old = asyncio.create_task(
            service.prompt_send("old", session_id="existing", request_id="old-r")
        )
        await asyncio.to_thread(entered.wait, 1)
        old_record = _active(service)[0]
        old.cancel()
        with pytest.raises(asyncio.CancelledError):
            await old
        assert _active(service) == (old_record,)
        assert not old_record.thread_cancel.is_set()

        blocked = await service.prompt_send(
            "successor", session_id="existing", request_id="new-r"
        )
        assert _payload(blocked) == {"error": "prompt execution failed"}
        assert not worker_done.is_set()

        release.set()
        await asyncio.to_thread(worker_done.wait, 1)
        for _ in range(20):
            if not _active(service):
                break
            await asyncio.sleep(0.01)
        assert _active(service) == ()

        result = await service.prompt_send(
            "successor", session_id="existing", request_id="new-r"
        )
        assert _payload(result)["text"] == "successor"

    asyncio.run(scenario())


def test_prompt_cancel_foreign_completed_and_stale_records_have_no_effect() -> None:
    calls = []
    own = _service(calls=calls)
    entered = threading.Event()
    release = threading.Event()

    def process(req, *, cancel_event):
        entered.set()
        release.wait(2)
        return TurnResult("done", "u", "a")

    foreign = _service(calls=calls, process=process)

    async def scenario():
        task = asyncio.create_task(
            foreign.prompt_send("prompt", session_id="existing", request_id="foreign-r")
        )
        await asyncio.to_thread(entered.wait, 1)
        record = _active(foreign)[0]
        assert _payload(own.prompt_cancel("existing"))["cancelled"] is False
        assert _payload(own.prompt_cancel("unknown"))["cancelled"] is False
        assert not record.thread_cancel.is_set() and not record.tool_cancel.is_set()
        assert len(_active(foreign)) == 1
        release.set()
        await task

    asyncio.run(scenario())
    assert not any(name == "cancel" for name, _ in calls)


def test_two_services_cannot_share_or_cross_cancel_one_process_session() -> None:
    session_id = "shared-mcp-session"
    db = FakeSessionDB()
    db.sessions[session_id] = {"id": session_id, "agent_id": "main"}
    entered = {"a": threading.Event(), "b": threading.Event()}
    release = {"a": threading.Event(), "b": threading.Event()}
    cleanup = []
    questions_a = FakeQuestions()
    questions_b = FakeQuestions()

    def process(label):
        def run(req, *, cancel_event):
            entered[label].set()
            release[label].wait(2)
            return TurnResult(label, "u", f"assistant-{label}")

        return run

    def service(label, questions, client_id):
        return MCPService(
            _context(client_id),
            session_db=db,
            process_user_turn=process(label),
            cancel_execution=lambda execution_id: cleanup.append(
                (label, "cancel", execution_id)
            ),
            question_registry_getter=lambda: questions,
            event_bus_getter=create_event_bus,
        )

    service_a = service("a", questions_a, "0123456789abcdef")
    service_b = service("b", questions_b, "fedcba9876543210")

    async def scenario():
        task_a = asyncio.create_task(
            service_a.prompt_send("a", session_id=session_id, request_id="request-a")
        )
        await asyncio.to_thread(entered["a"].wait, 1)
        record_a = _active(service_a)[0]

        result_b = await asyncio.wait_for(
            service_b.prompt_send(
                "b", session_id=session_id, request_id="request-b-rejected"
            ),
            0.5,
        )
        assert result_b.is_error is True
        assert _payload(result_b) == {"error": "prompt execution failed"}
        assert not entered["b"].is_set()

        assert _payload(service_a.prompt_cancel(session_id))["cancelled"] is True
        assert record_a.thread_cancel.is_set()
        assert questions_a.cancelled == [(session_id, record_a.execution_id)]
        assert questions_b.cancelled == []
        assert cleanup == [("a", "cancel", record_a.execution_id)]
        release["a"].set()
        with pytest.raises(asyncio.CancelledError):
            await task_a

        task_b = asyncio.create_task(
            service_b.prompt_send("b", session_id=session_id, request_id="request-b")
        )
        await asyncio.to_thread(entered["b"].wait, 1)
        record_b = _active(service_b)[0]

        service_a.close()
        assert not record_b.thread_cancel.is_set()
        assert not record_b.tool_cancel.is_set()
        assert questions_b.cancelled == []
        assert cleanup == [("a", "cancel", record_a.execution_id)]

        assert _payload(service_b.prompt_cancel(session_id))["cancelled"] is True
        release["b"].set()
        with pytest.raises(asyncio.CancelledError):
            await task_b

    try:
        asyncio.run(scenario())
    finally:
        release["a"].set()
        release["b"].set()
        service_a.close()
        service_b.close()


@pytest.mark.parametrize("operation", ["prompt_cancel", "close"])
def test_old_owner_cleanup_cannot_cross_concurrent_session_handover(operation) -> None:
    from openprogram.agent.questions import PendingQuestion, QuestionRegistry
    session_id = f"handover-{operation}"
    db = FakeSessionDB()
    db.sessions[session_id] = {"id": session_id, "agent_id": "main"}
    turn_entered = threading.Event()
    release_turn = threading.Event()
    owner_sampled = threading.Event()
    release_owner_sample = threading.Event()
    cleanup = []
    questions = QuestionRegistry()

    def process(req, *, cancel_event):
        turn_entered.set()
        release_turn.wait(2)
        return TurnResult("old", "u", "a")

    def acquire_cleanup(selected_session_id, event):
        del selected_session_id, event
        owner_sampled.set()
        release_owner_sample.wait(2)
        return True

    service = MCPService(
        _context(),
        session_db=db,
        process_user_turn=process,
        acquire_cancel_cleanup=acquire_cleanup,
        release_cancel_cleanup=lambda selected_session_id, event: None,
        cancel_execution=lambda execution_id: cleanup.append(
            ("cancel", execution_id)
        ),
        question_registry_getter=lambda: questions,
        event_bus_getter=create_event_bus,
    )
    foreign_event = threading.Event()

    async def scenario():
        task = asyncio.create_task(
            service.prompt_send(
                "old", session_id=session_id, request_id=f"request-{operation}"
            )
        )
        await asyncio.to_thread(turn_entered.wait, 1)
        record = _active(service)[0]

        result = {}

        def cancel_old_owner():
            if operation == "prompt_cancel":
                result["value"] = _payload(service.prompt_cancel(session_id))
            else:
                service.close()

        cancel_thread = threading.Thread(target=cancel_old_owner)
        cancel_thread.start()
        assert owner_sampled.wait(1)
        release_owner_sample.set()
        cancel_thread.join(1)

        assert not cancel_thread.is_alive()
        if operation == "prompt_cancel":
            assert result["value"] == {"session_id": session_id, "cancelled": True}
        assert record.thread_cancel.is_set()
        assert record.tool_cancel.is_set()
        assert not foreign_event.is_set()
        assert cleanup == [("cancel", record.execution_id)]

        service.close()

        release_turn.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    try:
        asyncio.run(scenario())
    finally:
        release_owner_sample.set()
        release_turn.set()
        service.close()


def test_question_events_decline_only_current_service_active_request() -> None:
    bus = create_event_bus()
    own_questions = FakeQuestions()
    foreign_questions = FakeQuestions()
    entered = {"existing": threading.Event(), "second": threading.Event()}
    release = {"existing": threading.Event(), "second": threading.Event()}

    def process(req, *, cancel_event):
        entered[req.session_id].set()
        release[req.session_id].wait(2)
        return TurnResult("done", "u", "a")

    own = _service(bus=bus, questions=own_questions, process=process)
    foreign = _service(bus=bus, questions=foreign_questions, process=process)

    async def scenario():
        own_task = asyncio.create_task(
            own.prompt_send("prompt", session_id="existing", request_id="r1")
        )
        foreign_task = asyncio.create_task(
            foreign.prompt_send("prompt", session_id="second", request_id="r2")
        )
        await asyncio.gather(
            asyncio.to_thread(entered["existing"].wait, 1),
            asyncio.to_thread(entered["second"].wait, 1),
        )
        own_record = _active(own)[0]
        foreign_record = _active(foreign)[0]
        own_questions.pending["q-own"] = own_record.execution_id
        own_questions.pending["q-sibling"] = foreign_record.execution_id
        own_questions.pending["q-ownerless"] = ""
        foreign_questions.pending["q-foreign"] = foreign_record.execution_id
        bus.emit(
            make_event(
                "question.asked",
                "agent",
                {"id": "q-own", "session_id": "existing"},
            )
        )
        bus.emit(
            make_event(
                "question.asked",
                "agent",
                {"id": "q-foreign", "session_id": "second"},
            )
        )
        for question_id in ("q-sibling", "q-ownerless"):
            bus.emit(
                make_event(
                    "question.asked",
                    "agent",
                    {"id": question_id, "session_id": "existing"},
                )
            )
        bus.emit(make_event("question.asked", "agent", {"bad": "event"}))
        release["existing"].set()
        release["second"].set()
        await asyncio.gather(own_task, foreign_task)
        bus.emit(
            make_event(
                "question.asked",
                "agent",
                {"id": "q-completed", "session_id": "existing"},
            )
        )

    asyncio.run(scenario())

    assert own_questions.resolved == [("q-own", "declined", None)]
    assert foreign_questions.resolved == [("q-foreign", "declined", None)]


def test_question_event_requires_registry_exact_owner() -> None:
    bus = create_event_bus()
    questions = FakeQuestions()
    entered = threading.Event()
    release = threading.Event()

    def process(req, *, cancel_event):
        entered.set()
        release.wait(2)
        return TurnResult("done", "u", "a")

    service = _service(bus=bus, questions=questions, process=process)

    async def scenario():
        task = asyncio.create_task(
            service.prompt_send("prompt", session_id="existing", request_id="r1")
        )
        await asyncio.to_thread(entered.wait, 1)
        execution_id = _active(service)[0].execution_id

        questions.pending["q-ownerless"] = ""
        bus.emit(make_event("question.asked", "agent", {
            "id": "q-ownerless", "session_id": "existing",
            "execution_id": execution_id,
        }))
        questions.pending["q-conflict"] = execution_id
        bus.emit(make_event("question.asked", "agent", {
            "id": "q-conflict", "session_id": "existing",
            "execution_id": "other-execution_reply",
        }))
        questions.pending.pop("q-missing", None)
        bus.emit(make_event("question.asked", "agent", {
            "id": "q-missing", "session_id": "existing",
            "execution_id": execution_id,
        }))
        original_list_pending = questions.list_pending
        questions.list_pending = lambda _session_id: (_ for _ in ()).throw(
            RuntimeError("registry unavailable")
        )
        bus.emit(make_event("question.asked", "agent", {
            "id": "q-registry-failure", "session_id": "existing",
            "execution_id": execution_id,
        }))
        questions.list_pending = original_list_pending
        questions.pending["q-valid-payload"] = execution_id
        bus.emit(make_event("question.asked", "agent", {
            "id": "q-valid-payload", "session_id": "existing",
            "execution_id": execution_id,
        }))
        questions.pending["q-valid-registry"] = execution_id
        bus.emit(make_event("question.asked", "agent", {
            "id": "q-valid-registry", "session_id": "existing",
        }))
        release.set()
        await task

    asyncio.run(scenario())
    assert questions.resolved == [
        ("q-valid-payload", "declined", None),
        ("q-valid-registry", "declined", None),
    ]


def test_question_event_resolves_same_registry_instance_used_for_ownership() -> None:
    bus = create_event_bus()
    owner_registry = FakeQuestions()
    other_registry = FakeQuestions()
    entered = threading.Event()
    release = threading.Event()
    getter_calls = []

    def process(req, *, cancel_event):
        entered.set()
        release.wait(2)
        return TurnResult("done", "u", "a")

    service = _service(bus=bus, questions=owner_registry, process=process)

    def getter():
        getter_calls.append(True)
        return owner_registry if len(getter_calls) == 1 else other_registry

    service._question_registry_getter = getter

    async def scenario():
        task = asyncio.create_task(
            service.prompt_send("prompt", session_id="existing", request_id="r1")
        )
        await asyncio.to_thread(entered.wait, 1)
        execution_id = _active(service)[0].execution_id
        owner_registry.pending["q-exact"] = execution_id
        bus.emit(make_event("question.asked", "agent", {
            "id": "q-exact", "session_id": "existing",
        }))
        release.set()
        await task

    asyncio.run(scenario())
    assert getter_calls == [True]
    assert owner_registry.resolved == [("q-exact", "declined", None)]
    assert other_registry.resolved == []


def test_question_claim_and_cancellation_coordinate_on_active_ownership() -> None:
    question_entered = threading.Event()
    release_question = threading.Event()
    turn_entered = threading.Event()
    release_turn = threading.Event()
    cancel_done = threading.Event()

    class BlockingQuestions(FakeQuestions):
        def resolve(self, question_id, outcome, value=None):
            question_entered.set()
            release_question.wait(2)
            return super().resolve(question_id, outcome, value)

    def process(req, *, cancel_event):
        turn_entered.set()
        release_turn.wait(2)
        return TurnResult("late", "u", "a")

    bus = create_event_bus()
    questions = BlockingQuestions()
    service = _service(bus=bus, questions=questions, process=process)

    async def scenario():
        task = asyncio.create_task(
            service.prompt_send("prompt", session_id="existing", request_id="r1")
        )
        await asyncio.to_thread(turn_entered.wait, 1)
        questions.pending["q-race"] = _active(service)[0].execution_id
        emit_thread = threading.Thread(
            target=lambda: bus.emit(
                make_event(
                    "question.asked",
                    "agent",
                    {"id": "q-race", "session_id": "existing"},
                )
            )
        )
        emit_thread.start()
        await asyncio.to_thread(question_entered.wait, 1)

        def cancel():
            service.prompt_cancel("existing")
            cancel_done.set()

        cancel_thread = threading.Thread(target=cancel)
        cancel_thread.start()
        assert not cancel_done.wait(0.05)
        release_question.set()
        emit_thread.join(1)
        cancel_thread.join(1)
        release_turn.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert questions.resolved == [("q-race", "declined", None)]


def test_close_unsubscribes_once_and_cleans_only_owned_requests() -> None:
    bus = create_event_bus()
    questions = FakeQuestions()
    calls = []
    captured_execution_ids = []
    entered = threading.Event()
    release = threading.Event()

    def process(req, *, cancel_event):
        entered.set()
        release.wait(2)
        return TurnResult("late", "u", "a")

    service = _service(bus=bus, questions=questions, calls=calls, process=process)

    async def scenario():
        task = asyncio.create_task(
            service.prompt_send("prompt", session_id="existing", request_id="r1")
        )
        await asyncio.to_thread(entered.wait, 1)
        captured_execution_ids.append(_active(service)[0].execution_id)
        service.close()
        service.close()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    execution_id = captured_execution_ids[0]
    bus.emit(
        make_event(
            "question.asked",
            "agent",
            {"id": "after-close", "session_id": "existing"},
        )
    )

    assert _active(service) == ()
    assert questions.cancelled == [("existing", execution_id)]
    assert questions.resolved == []
    assert [name for name, _ in calls].count("cancel") == 1


def test_prompt_send_async_cancellation_cleans_then_reraises_and_drops_late_result() -> (
    None
):
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def process(req, *, cancel_event):
        entered.set()
        release.wait(2)
        return TurnResult("late-secret", "u", "late-a")

    service = _service(process=process, calls=calls)

    async def scenario():
        task = asyncio.create_task(
            service.prompt_send("prompt", session_id="existing", request_id="r1")
        )
        await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert _active(service) == ()
        release.set()
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert [name for name, _ in calls].count("cancel") == 1
    assert [name for name, _ in calls].count("unregister") == 1


@pytest.mark.parametrize("operation", ["prompt_cancel", "close"])
def test_cancelled_late_worker_exception_cannot_publish_result(operation) -> None:
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def process(req, *, cancel_event):
        entered.set()
        release.wait(2)
        raise RuntimeError("secret-late-worker")

    service = _service(process=process, calls=calls)

    async def scenario():
        task = asyncio.create_task(
            service.prompt_send("prompt", session_id="existing", request_id="r1")
        )
        await asyncio.to_thread(entered.wait, 1)
        if operation == "prompt_cancel":
            assert _payload(service.prompt_cancel("existing"))["cancelled"] is True
        else:
            service.close()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert [name for name, _ in calls].count("cancel") == 1
    assert [name for name, _ in calls].count("unregister") == 1


def test_cancelled_late_worker_cannot_remove_reused_request_record() -> None:
    entered = {"old": threading.Event(), "new": threading.Event()}
    release = {"old": threading.Event(), "new": threading.Event()}
    calls = []

    def process(req, *, cancel_event):
        entered[req.user_text].set()
        release[req.user_text].wait(2)
        return TurnResult(req.user_text, "u", f"a-{req.user_text}")

    service = _service(process=process, calls=calls)

    async def scenario():
        old = asyncio.create_task(
            service.prompt_send("old", session_id="existing", request_id="reused")
        )
        await asyncio.to_thread(entered["old"].wait, 1)
        assert _payload(service.prompt_cancel("existing"))["cancelled"] is True
        new = asyncio.create_task(
            service.prompt_send("new", session_id="existing", request_id="reused")
        )
        await asyncio.to_thread(entered["new"].wait, 1)
        release["old"].set()
        with pytest.raises(asyncio.CancelledError):
            await old
        assert len(_active(service)) == 1
        assert _active(service)[0].request_id == "reused"
        assert not _active(service)[0].thread_cancel.is_set()
        new_event = _active(service)[0].thread_cancel
        release["new"].set()
        result = await new
        assert _payload(result)["text"] == "new"
        unregistered_events = [
            value[1] for name, value in calls if name == "unregister"
        ]
        assert unregistered_events.count(new_event) == 1

    asyncio.run(scenario())


def test_new_same_session_request_is_rejected_until_old_cleanup_finishes() -> None:
    old_entered = threading.Event()
    old_release = threading.Event()
    cleanup_entered = threading.Event()
    cleanup_release = threading.Event()
    new_entered = threading.Event()

    def process(req, *, cancel_event):
        if req.user_text == "old":
            old_entered.set()
            old_release.wait(2)
        else:
            new_entered.set()
        return TurnResult(req.user_text, "u", f"a-{req.user_text}")

    service = _service(process=process)

    def blocking_cancel(_execution_id):
        cleanup_entered.set()
        cleanup_release.wait(2)

    service._cancel_execution = blocking_cancel

    async def scenario():
        old = asyncio.create_task(
            service.prompt_send("old", session_id="existing", request_id="old-r")
        )
        await asyncio.to_thread(old_entered.wait, 1)
        cancel_thread = threading.Thread(
            target=lambda: service.prompt_cancel("existing")
        )
        cancel_thread.start()
        await asyncio.to_thread(cleanup_entered.wait, 1)
        new = asyncio.create_task(
            service.prompt_send("new", session_id="existing", request_id="new-r")
        )
        blocked = await asyncio.wait_for(new, 0.5)
        assert blocked.is_error is True
        assert _payload(blocked) == {"error": "prompt execution failed"}
        assert not new_entered.is_set()
        cleanup_release.set()
        cancel_thread.join(1)
        old_release.set()
        with pytest.raises(asyncio.CancelledError):
            await old
        later = await service.prompt_send(
            "new", session_id="existing", request_id="later-r"
        )
        assert _payload(later)["text"] == "new"

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["exception", "malformed"])
def test_prompt_send_failures_are_fixed_and_sanitized(kind) -> None:
    secret = "secret-dispatch-value"

    def process(req, *, cancel_event):
        if kind == "exception":
            raise RuntimeError(secret)
        return object()

    result = asyncio.run(
        _service(process=process).prompt_send(
            "prompt", session_id="existing", request_id="r1"
        )
    )
    assert result.is_error is True
    assert _payload(result) == {"error": "prompt execution failed"}
    assert secret not in result.model_dump_json()


def test_prompt_send_session_exception_is_fixed_invalid_params() -> None:
    class BrokenSessionDB(FakeSessionDB):
        def get_session(self, session_id):
            raise RuntimeError("secret-session-value")

    with pytest.raises(McpError) as caught:
        asyncio.run(
            _service(db=BrokenSessionDB()).prompt_send(
                "prompt", session_id="existing", request_id="r1"
            )
        )

    assert caught.value.error.code == mcp_types.INVALID_PARAMS
    assert caught.value.error.message == "invalid MCP prompt session"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "secret-session-value" not in str(caught.value)


def test_cancel_request_cleanup_callback_failures_do_not_retain_ownership() -> None:
    entered = threading.Event()
    release = threading.Event()

    def process(req, *, cancel_event):
        entered.set()
        release.wait(2)
        return TurnResult("late", "u", "a")

    def fail(*_args, **_kwargs):
        raise RuntimeError("secret-cleanup-value")

    bus = create_event_bus()
    current_events = {}
    cleanup_leases = {}

    def register(session_id, event, *, execution_id):
        current_events[(session_id, execution_id)] = event

    def acquire_cleanup(session_id, event):
        if (
            not any(
                key[0] == session_id and candidate is event
                for key, candidate in current_events.items()
            )
            or session_id in cleanup_leases
        ):
            return False
        cleanup_leases[session_id] = event
        return True

    def release_cleanup(session_id, event):
        if cleanup_leases.get(session_id) is event:
            cleanup_leases.pop(session_id, None)

    service = MCPService(
        _context(),
        session_db=FakeSessionDB(),
        process_user_turn=process,
        register_cancel_event=register,
        unregister_cancel_event=fail,
        current_cancel_event=lambda session_id, *, execution_id: current_events.get(
            (session_id, execution_id)
        ),
        acquire_cancel_cleanup=acquire_cleanup,
        release_cancel_cleanup=release_cleanup,
        cancel_execution=fail,
        question_registry_getter=fail,
        event_bus_getter=lambda: bus,
    )

    async def scenario():
        task = asyncio.create_task(
            service.prompt_send("prompt", session_id="existing", request_id="r1")
        )
        await asyncio.to_thread(entered.wait, 1)
        service.cancel_request("r1", reason="secret-caller-reason")
        assert _active(service)
        assert not service._active_by_request["r1"].thread_cancel.is_set()
        release.set()
        result = await task
        assert _payload(result)["text"] == "late"

    asyncio.run(scenario())
    assert cleanup_leases == {}
