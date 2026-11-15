"""A Worker killed at each durable step of an always-on wake.

Every case runs two real processes on one state directory. The first takes the
Worker lock, drives the production path with a scripted model and is killed
with ``os._exit`` at the named step, so no ``finally`` block and no thread gets
to tidy up. The second takes the lock the way the next Worker does, runs the
startup recovery and the wake tick, and sends back what it found.
"""
from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import time
import traceback
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from tests.support.waiting import wait_until

_API = "always-on-crash-api"
_KILLED = 9
_NEVER_KILLED_S = 20
_WAKE = {"enabled": True, "research": True}
_TARGET = {"channel": "telegram", "account_id": "bot", "peer_id": "42", "label": "Me"}
_REPORT = {"title": "CI is red", "body": "main fails", "kind": "finding"}


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Patches only: each process opens its own stores, as a new Worker does."""
    import openprogram.state.paths as paths
    from openprogram.interfaces.server import server as _server  # noqa: F401
    from openprogram.models.providers.catalog import api_registry
    from openprogram.models.providers.types import Model
    from openprogram.runtime import channel_link, interface_link
    from openprogram.runtime.agent_registry import manager
    from openprogram.runtime.conversation import turn_runtime
    from openprogram.state.store.session import session_store

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    session_store.shared._default_store = None
    model = Model(id="scripted", name="scripted", api=_API, provider="scripted-crash",
                  base_url="http://scripted.invalid")
    monkeypatch.setattr(turn_runtime, "_resolve_model", lambda *a, **kw: model)
    sends = tmp_path / "sends.jsonl"

    class Link:
        def __getattr__(self, _name):
            return lambda *a, **kw: None

    class Channels:
        def send_message(self, channel, account_id, peer_id, text):
            with sends.open("a") as handle:
                handle.write(json.dumps([channel, peer_id, text]) + "\n")
            return SimpleNamespace(ok=True, connect_failed=False, error_kind=None, error_detail=None)

        def __getattr__(self, _name):
            return lambda *a, **kw: []

    monkeypatch.setattr(interface_link, "_link", Link())
    monkeypatch.setattr(channel_link, "require", lambda: Channels())
    monkeypatch.setattr(channel_link, "peer_bindings", lambda agent_id: [], raising=False)
    agent_id = "crash" + uuid.uuid4().hex[:8]
    manager.create(agent_id, name="Scout")
    previous = api_registry._registry.get(_API)
    try:
        yield SimpleNamespace(agent_id=agent_id, session_id="ao-" + uuid.uuid4().hex[:8], sends=sends,
                              manager=manager, api_registry=api_registry)
    finally:
        manager.delete(agent_id)
        if previous is None:
            api_registry._registry.pop(_API, None)
        else:
            api_registry.register_api_provider(_API, previous)


def _kill() -> None:
    os._exit(_KILLED)


def _die_before(owner: Any, name: str, when: Callable[..., bool] = lambda *a, **kw: True) -> None:
    original = getattr(owner, name)

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if when(*args, **kwargs):
            _kill()
        return original(*args, **kwargs)
    setattr(owner, name, wrapped)


def _die_after(owner: Any, name: str, when: Callable[..., bool] = lambda *a, **kw: True) -> None:
    original = getattr(owner, name)

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        if when(*args, **kwargs):
            _kill()
        return result
    setattr(owner, name, wrapped)


def _provider(*responses: Any, on_call: Callable[[int], None] | None = None) -> Any:
    from tests.component.providers.scripted_provider import ScriptedProvider

    scripted = ScriptedProvider()
    for response in responses:
        scripted.add_response(response)

    class Provider:
        calls = 0

        def stream(self, model, context, options=None):
            return self.stream_simple(model, context, options)

        async def stream_simple(self, model, context, options=None):
            Provider.calls += 1
            if on_call is not None:
                await asyncio.to_thread(on_call, Provider.calls)
            async for event in scripted.stream_simple(model, context, options):
                yield event

    return Provider()


def _replies(world, count: int = 4) -> None:
    from tests.component.providers.scripted_provider import ScriptedText

    world.api_registry.register_api_provider(_API, _provider(*[ScriptedText("Nothing new.")] * count))


def _in_a_process(body: Callable[[], Any]) -> SimpleNamespace:
    """Run ``body`` as a Worker in its own process. Returns its exit code and answer."""
    context = multiprocessing.get_context("fork")
    receive, send = context.Pipe(duplex=False)

    def run() -> None:
        from openprogram.operations.worker.lock import WorkerLock
        from openprogram.runtime.conversation.always_on import runtime

        runtime._BOOT = uuid.uuid4().hex[:12]  # forked, so not a new one by itself
        try:
            lock = WorkerLock()
            assert lock.try_acquire(), "the previous Worker still holds the lock"
            send.send({"answer": body()})
        except BaseException:  # noqa: BLE001 - the parent shows this process's failure
            send.send({"error": traceback.format_exc()})
        finally:
            send.close()
        os._exit(0)

    process = context.Process(target=run)
    process.start()
    send.close()
    process.join(90)
    if process.is_alive():
        process.kill()
        process.join(10)
        raise AssertionError("the process never finished")
    try:
        message = receive.recv() if receive.poll(0) else {}
    except EOFError:  # killed before it could answer
        message = {}
    assert "error" not in message, message.get("error")
    return SimpleNamespace(exit_code=process.exitcode, answer=message.get("answer"))


def _join(world, config: dict | None = None) -> None:
    """A conversation with something said, whose next wake has passed."""
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.conversation import always_on
    from openprogram.runtime.conversation.always_on import store

    world.manager.update(world.agent_id, {"always_on": config or _WAKE})
    sid = world.session_id
    default_db().create_session(sid, world.agent_id)
    default_db().append_message(sid, {"id": "u1", "role": "user", "content": "Watch CI",
                                      "timestamp": time.time() - 60})
    default_db().set_head(sid, "u1")
    always_on.join(sid)
    store.update_state(sid, next_wake_at=time.time())


def _dying_wake(world, arrange: Callable[[], None], config: dict | None = None) -> None:
    """The first Worker's scheduled tick, killed where ``arrange`` says."""
    def body() -> None:
        from openprogram.runtime.conversation.always_on import ticker

        _join(world, config)
        arrange()
        ticker.tick()
        time.sleep(_NEVER_KILLED_S)

    assert _in_a_process(body).exit_code == _KILLED


def _next_worker(world, then: Callable[[], Any] | None = None) -> dict[str, Any]:
    """Start up, tick twice, wait for what the tick started, and report the state."""
    _replies(world)

    def body() -> dict[str, Any]:
        from openprogram.runtime.context.session_db import default_db
        from openprogram.runtime.conversation.always_on import runtime, store, ticker
        from openprogram.runtime.execution import default_control_service, default_store, restart
        from openprogram.runtime.execution.startup import recover_execution_startup

        sid = world.session_id
        executions = default_store()
        executions.tables.ensure_session(sid)
        recover_execution_startup()
        extra = then() if then is not None else None
        before = store.load_state(sid)
        restart.reconcile(SimpleNamespace(_execution_store=executions, _execution_control=default_control_service()))
        results = ticker.tick() + ticker.tick()
        wait_until(lambda: not store.load_state(sid).get("check_in"), timeout=30)
        runtime.status(sid)
        rows = executions.list_nonterminal(session_id=sid)
        return {
            "before": before,
            "outcomes": [result.get("outcome") for result in results],
            "state": store.load_state(sid),
            "wakes": store.load_wake_log(sid),
            "reports": store.load_reports(sid),
            "head": (default_db().get_session(sid) or {}).get("head_id"),
            "branch": [(m.get("role"), m.get("id"), m.get("status"), m.get("content"))
                       for m in default_db().get_branch(sid)],
            "unfinished": [(row.execution_id, row.status.value) for row in rows],
            "restart_answers": [event.payload.get("outcome")
                                for event in executions.list_events((before.get("check_in") or {}).get("execution_id") or "")
                                if event.kind == restart._SETTLED],
            "extra": extra,
        }

    return _in_a_process(body).answer


def _sends(world) -> list:
    path = Path(world.sends)
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _cut_off(found: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in found["wakes"] if row.get("outcome") == "interrupted"]


# ── claim ────────────────────────────────────────────────────────────


def _is_claim(*_args: Any, **changes: Any) -> bool:
    return bool(changes.get("check_in"))


def test_killed_just_before_the_claim_the_wake_still_happens(world):
    def arrange() -> None:
        from openprogram.runtime.conversation.always_on import store
        _die_before(store, "update_state", _is_claim)

    _dying_wake(world, arrange)
    found = _next_worker(world)
    assert found["outcomes"][0] == "check_in"
    assert _cut_off(found) == [] and found["state"].get("check_in") is None


def test_killed_right_after_the_claim_the_check_in_is_cut_off_once_and_runs_again(world):
    def arrange() -> None:
        from openprogram.runtime.conversation.always_on import store
        _die_after(store, "update_state", _is_claim)

    _dying_wake(world, arrange)
    found = _next_worker(world)
    assert len(_cut_off(found)) == 1 and _cut_off(found)[0]["error"] == "Cut off by a restart"
    # What was said before the crash was never looked at, so it still is news.
    assert found["outcomes"][0] == "check_in"
    assert found["state"].get("check_in") is None and found["unfinished"] == []


def test_a_cut_off_check_in_counts_from_the_one_before_it(world):
    seen_at = time.time() - 3600

    def arrange() -> None:
        from openprogram.runtime.conversation.always_on import store
        store.update_state(world.session_id, last_check_in_at=seen_at)
        _die_after(store, "update_state", _is_claim)

    def reaped() -> float | None:
        from openprogram.runtime.conversation.always_on import runtime
        return runtime.reap_interrupted(world.session_id).get("last_check_in_at")

    _dying_wake(world, arrange)
    found = _next_worker(world, reaped)
    assert found["extra"] == seen_at and found["outcomes"][0] == "check_in"


def test_killed_after_the_turn_was_admitted_the_turn_and_the_check_in_both_close(world):
    def arrange() -> None:
        world.api_registry.register_api_provider(_API, _provider(on_call=lambda _n: _kill()))

    _dying_wake(world, arrange)
    found = _next_worker(world)
    assert len(_cut_off(found)) == 1 and found["unfinished"] == []
    # A check-in's side branch never becomes the conversation.
    assert found["head"] == "u1" and [row[0] for row in found["branch"]] == ["user"]


# ── restart intent ───────────────────────────────────────────────────


def _dying_restart(world, die: Callable[[Any], None]) -> None:
    """A shutdown request reaches a turn holding a model call, and the Worker dies at ``die``."""
    def request_shutdown(_call: int) -> None:
        from openprogram.runtime.execution import default_control_service, default_store, restart

        runner = SimpleNamespace(_execution_store=default_store(), _execution_control=default_control_service())
        die(restart)
        restart.prepare_shutdown(runner)
        _kill()

    def arrange() -> None:
        world.api_registry.register_api_provider(_API, _provider(on_call=request_shutdown))

    _dying_wake(world, arrange)


def test_killed_before_the_restart_intent_a_check_in_ends_once(world):
    _dying_restart(world, lambda restart: _die_before(restart, "record_intent"))
    found = _next_worker(world)
    assert len(_cut_off(found)) == 1 and found["unfinished"] == []


def test_killed_after_the_restart_intent_a_check_in_is_ended_and_not_continued(world):
    _dying_restart(world, lambda _restart: None)
    found = _next_worker(world)
    # The intent was there and was answered, with no continuation.
    assert found["restart_answers"] in (["execution_ended"], ["check_in_not_resumed"])
    assert found["unfinished"] == [] and len(_cut_off(found)) == 1


# ── report row ───────────────────────────────────────────────────────


def _reporting_check_in(world, **config: Any) -> dict:
    from tests.component.providers.scripted_provider import ScriptedText, ScriptedToolCall

    world.api_registry.register_api_provider(_API, _provider(
        ScriptedToolCall("always_on_report", _REPORT, "call-1"), ScriptedText("Reported.")))
    return {**_WAKE, **config}


def test_killed_before_the_report_row_nothing_was_promised(world):
    config = _reporting_check_in(world, channels=[_TARGET])

    def arrange() -> None:
        from openprogram.runtime.conversation.always_on import store
        _die_before(store, "add_report")

    _dying_wake(world, arrange, config)
    found = _next_worker(world)
    assert found["reports"] == [] and _sends(world) == []
    assert len(_cut_off(found)) == 1


def test_killed_right_after_the_report_row_it_is_kept_whole_and_once(world):
    config = _reporting_check_in(world, channels=[_TARGET])

    def arrange() -> None:
        from openprogram.runtime.conversation.always_on import store
        _die_after(store, "add_report")

    _dying_wake(world, arrange, config)
    found = _next_worker(world)
    assert [row["title"] for row in found["reports"]] == ["CI is red"]
    assert found["reports"][0]["body"] == "main fails"
    assert len(_cut_off(found)) == 1 and found["state"].get("check_in") is None
    # It did look, and said so: that check-in still counts.
    assert found["state"]["last_check_in_at"] == found["before"]["last_check_in_at"]


# ── delivery ─────────────────────────────────────────────────────────


def _killed_after_the_send(world) -> None:
    config = _reporting_check_in(world, channels=[_TARGET])

    def arrange() -> None:
        from openprogram.runtime.conversation.always_on import store
        _die_before(store, "update_report", lambda *a, **changes: "deliveries" in changes)

    _dying_wake(world, arrange, config)


def test_killed_between_the_send_and_its_result_the_report_is_kept(world):
    _killed_after_the_send(world)
    found = _next_worker(world)
    assert len(_sends(world)) == 1 and [row["title"] for row in found["reports"]] == ["CI is red"]
    assert len(_cut_off(found)) == 1


def test_a_send_cut_off_by_a_kill_gets_a_result_from_the_next_worker(world):
    from openprogram.runtime.conversation.always_on import runtime

    if not hasattr(runtime, "recover_reports"):
        pytest.skip("the report recovery of batch 39 is on its own branch")
    _killed_after_the_send(world)

    def recover() -> None:
        from openprogram.runtime.conversation.always_on import runtime as next_runtime, store

        next_runtime.recover_reports(time.time())
        wait_until(lambda: all(row.get("status") != "sending" for report in store.load_reports(world.session_id)
                               for row in report.get("deliveries") or []), timeout=20)

    found = _next_worker(world, recover)
    assert [row["status"] for row in found["reports"][0]["deliveries"]] == ["sent"]


def test_killed_while_posting_the_report_the_conversation_is_not_broken(world):
    config = _reporting_check_in(world)

    def arrange() -> None:
        from openprogram.runtime.context.session_db import default_db
        db = default_db()
        _die_after(type(db), "append_after_head", lambda _self, _sid, message: bool(message.get("always_on_report")))

    _dying_wake(world, arrange, config)
    found = _next_worker(world)
    posted = [row for row in found["branch"] if row[1].startswith("report_")]
    assert [row[0] for row in found["branch"]] == ["user", "assistant"] and len(posted) == 1
    assert posted[0][3] == "main fails"


# ── the two open questions ───────────────────────────────────────────


def _owner_turn(world, text: str, user_id: str) -> Any:
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    from openprogram.runtime.permissions.authority import local_owner_authority

    sid = world.session_id
    head = (default_db().get_session(sid) or {}).get("head_id")
    request = TurnRequest(session_id=sid, user_text=text, agent_id=world.agent_id, source="web",
                          user_msg_id=user_id, branch_from=head, **local_owner_authority())
    adapter = CanonicalAgentAdapter()
    admission = adapter.admit(request, trusted_actor=local_owner_authority(), user_message_id=user_id,
                              assistant_message_id=f"{user_id}_reply", config_snapshot_ref=f"session:{sid}")
    return asyncio.run(adapter.activate(admission))


def test_a_killed_chat_turn_the_owner_stopped_sends_no_empty_reply_to_the_model(world):
    """The placeholder head: killed before its first word, then stopped by the owner."""
    def die_in_a_chat_turn() -> None:
        from openprogram.runtime.context.session_db import default_db

        world.api_registry.register_api_provider(_API, _provider(on_call=lambda _n: _kill()))
        default_db().create_session(world.session_id, world.agent_id)
        _owner_turn(world, "Look at CI", "owner-u")
        time.sleep(_NEVER_KILLED_S)

    assert _in_a_process(die_in_a_chat_turn).exit_code == _KILLED

    def stop_then_type() -> dict[str, Any]:
        from openprogram.runtime.context.session_db import default_db
        from openprogram.runtime.execution.run_control.cancellation import cancel_session_executions
        from tests.component.providers.scripted_provider import ScriptedText

        cancel_session_executions(world.session_id)
        head = default_db().get_branch(world.session_id)[-1]
        sent: list[list[tuple[str, str]]] = []

        class Recording:
            def stream(self, model, context, options=None):
                return self.stream_simple(model, context, options)

            async def stream_simple(self, model, context, options=None):
                sent.append([(message.role, "".join(getattr(block, "text", "") or "" for block in message.content))
                             for message in context.messages])
                async for event in _provider(ScriptedText("Looking.")).stream_simple(model, context, options):
                    yield event

        world.api_registry.register_api_provider(_API, Recording())
        _owner_turn(world, "Try again", "owner-v")
        return {"head": (head["id"], head["status"], head["content"]), "sent": sent}

    found = _next_worker(world, stop_then_type)["extra"]
    # The empty stopped head is reachable, so what matters is what it does next.
    assert found["head"] == ("owner-u_reply", "cancelled", "")
    assert [role for role, _text in found["sent"][0]] == ["user", "user"]


def test_a_scheduled_tasks_sub_agent_finishes_and_a_written_over_turn_recovers(tmp_path):
    """The stuck run's shape: a scheduled turn that starts a sub-agent, then a
    Worker restart writing over the row of a turn that had already ended."""
    from tests.integration.execution.test_scheduled_run_processes import _run

    report = _run(tmp_path, "sub_agent")
    assert report["jobs"] == [{"status": "completed", "result": "the screen is clear"}]
    assert report["worker_job_rows"] > 0 and report["unfinished"] == []
    written = _run(tmp_path, "leave_written_over")
    assert written["status"] == "pausing"
    recovered = _run(tmp_path, "recover_written_over", written["execution_id"])
    assert recovered["after"] == "completed" and recovered["failures"] == []
