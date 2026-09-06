"""Actual isolated interpreter to trusted broker, with real durable effects."""
import json
import sys
import threading
import time

import pytest

from openprogram.backend.gui_broker import GuiBroker
from openprogram.backend.gui_runner import GuiPythonRunner, GuiRunnerError
from openprogram.execution.attempts import AttemptStore
from openprogram.execution.effects import EffectStatus, EffectStore
from openprogram.execution.model import CapabilitySet
from openprogram.execution.store import ExecutionStore

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="strict macOS sandbox")


@pytest.fixture
def environment(tmp_path):
    executions = ExecutionStore(tmp_path / "effects.db")
    revision = executions.create_revision(manifest={"entrypoint": "owned-gui-fixture"})
    execution = executions.create_execution(execution_id="exec", run_id="run", session_id="session",
                                            revision_id=revision.revision_id, capabilities=CapabilitySet())
    attempts = AttemptStore(executions)
    leased, reserved = attempts.lease("exec", expected_version=execution.status_version,
                                      owner_id="worker", ttl_seconds=30, attempt_id="attempt")
    active, running = attempts.activate("attempt", generation=leased.generation,
                                        expected_execution_version=reserved.status_version)
    effects = EffectStore(executions)
    broker = GuiBroker(effects=effects, execution_id="exec", attempt_id="attempt",
                       generation=active.generation, invocation_id="owned-invocation")
    yield broker, effects, executions
    broker.revoke()


def test_rpc_persists_receipt_before_return_and_keeps_handle(environment):
    broker, effects, _ = environment
    state = []
    def apply(args, observation, context):
        context.check()
        unresolved = effects.list_unresolved("exec")
        assert len(unresolved) == 1 and unresolved[0].status is EffectStatus.DISPATCHED
        state.append(args["value"])
        return {"count": len(state)}
    handle = broker.register(target="owned-window", methods={"append": apply}, validate=lambda *args: None)
    with GuiPythonRunner(broker=broker) as runner:
        first = runner.execute(f"handle = {handle!r}\nreply = await ui.call(handle, 'append', {{'value': 1}})\nprint(reply['effect_id'])")
        effect = effects.get(first.stdout.strip())
        assert effect.status is EffectStatus.COMMITTED
        assert effect.receipt["value"] == {"count": 1}
        assert runner.execute("print((await ui.call(handle, 'append', {'value': 2}))['value']['count'])").stdout == "2\n"
    assert state == [1, 2]


def test_revoked_grant_never_reaches_adapter(environment):
    broker, effects, _ = environment
    allowed = [True]
    applied = []
    def validate(method, args, observation):
        if not allowed[0] or observation != "frame-1":
            raise PermissionError("grant or observation stale")
    handle = broker.register(target="owned-window", methods={"click": lambda *args: applied.append(1)}, validate=validate)
    with GuiPythonRunner(broker=broker) as runner:
        result = runner.execute(f"await ui.call({handle!r}, 'click', observation='old-frame')")
        assert "stale" in result.error
        allowed[0] = False
        assert "stale" in runner.execute(f"await ui.call({handle!r}, 'click', observation='frame-1')").error
    assert applied == [] and effects.list_unresolved("exec") == []


def test_adapter_failure_stays_uncertain(environment):
    broker, effects, _ = environment
    def apply(*args):
        raise RuntimeError("owned adapter failed after dispatch")
    handle = broker.register(target="owned-window", methods={"click": apply}, validate=lambda *args: None)
    with GuiPythonRunner(broker=broker) as runner:
        assert "adapter failed" in runner.execute(f"await ui.call({handle!r}, 'click')").error
    assert effects.list_unresolved("exec")[0].status is EffectStatus.UNCERTAIN


def test_rpc_without_broker_fails_closed():
    with GuiPythonRunner() as runner:
        with pytest.raises(GuiRunnerError, match="broker"):
            runner.execute("await ui.call('invented', 'click')")


@pytest.mark.parametrize("which", ["resource", "method", "revoked"])
def test_unknown_or_revoked_target_never_reaches_adapter(environment, which):
    broker, effects, _ = environment
    applied = []
    handle = broker.register(target="owned", methods={"click": lambda *args: applied.append(1)}, validate=lambda *args: None)
    method = "click"
    if which == "resource": handle = "invented"
    if which == "method": method = "shell"
    if which == "revoked": broker.revoke_resource(handle)
    with GuiPythonRunner(broker=broker) as runner:
        assert "denied" in runner.execute(f"await ui.call({handle!r}, {method!r})").error
    assert applied == [] and effects.list_unresolved("exec") == []


def test_execution_cancel_state_denies_before_adapter(environment):
    from openprogram.execution.model import ExecutionStatus
    broker, effects, executions = environment
    applied = []
    handle = broker.register(target="owned", methods={"click": lambda *args: applied.append(1)}, validate=lambda *args: None)
    execution = executions.get_execution("exec")
    executions.transition_execution("exec", expected_version=execution.status_version, target=ExecutionStatus.CANCELLING)
    with GuiPythonRunner(broker=broker) as runner:
        assert "admission is closed" in runner.execute(f"await ui.call({handle!r}, 'click')").error
    assert applied == [] and effects.list_unresolved("exec") == []


def test_wrong_host_generation_denies_before_adapter(environment):
    _, effects, _ = environment
    broker = GuiBroker(effects=effects, execution_id="exec", attempt_id="attempt", generation=999,
                       invocation_id="owned-invocation")
    applied = []
    handle = broker.register(target="owned", methods={"click": lambda *args: applied.append(1)}, validate=lambda *args: None)
    with GuiPythonRunner(broker=broker) as runner:
        assert "stale" in runner.execute(f"await ui.call({handle!r}, 'click')").error
    assert applied == []


def test_cancel_during_adapter_revokes_before_child_cleanup_and_late_commit(environment):
    broker, effects, _ = environment
    entered, release, cancel = threading.Event(), threading.Event(), threading.Event()
    applied = []
    def apply(args, observation, context):
        entered.set()
        assert release.wait(3)
        context.check()
        applied.append(1)
        return "must not commit"
    handle = broker.register(target="owned", methods={"click": apply}, validate=lambda *args: None)
    def cancel_after_dispatch():
        if entered.wait(2): cancel.set()
    signaler = threading.Thread(target=cancel_after_dispatch)
    signaler.start()
    try:
        with GuiPythonRunner(broker=broker) as runner:
            start = time.monotonic()
            with pytest.raises(GuiRunnerError, match="cancelled"):
                runner.execute(f"await ui.call({handle!r}, 'click')", cancel=cancel)
            assert time.monotonic() - start < 2
            assert broker._revoked.is_set()
            assert runner._process.poll() is not None
    finally:
        release.set()
        signaler.join(3)
        broker._executor.shutdown(wait=True)
    assert applied == []
    assert effects.list_unresolved("exec")[0].status is EffectStatus.UNCERTAIN


def test_operation_budget_limits_actual_dispatch_count(environment):
    broker, _, _ = environment
    applied = []
    def apply(args, observation, context):
        context.check()
        applied.append(1)
        return None
    handle = broker.register(target="owned", methods={"click": apply}, validate=lambda *args: None)
    with GuiPythonRunner(broker=broker) as runner:
        with pytest.raises(GuiRunnerError, match="operation limit"):
            runner.execute(f"for _ in range(101):\n await ui.call({handle!r}, 'click')")
    assert len(applied) == 100


@pytest.mark.parametrize("second_type", ["rpc", "done"])
def test_pipelined_or_premature_completion_revokes_inflight_request(environment, second_type):
    broker, effects, _ = environment
    release = threading.Event()
    applied = []
    def apply(args, observation, context):
        assert release.wait(3)
        context.check()
        applied.append(1)
    handle = broker.register(target="owned", methods={"click": apply}, validate=lambda *args: None)
    request = {"id": 1, "type": "rpc", "request_id": 1,
               "request": {"resource": handle, "method": "click", "arguments": {}, "observation": None}}
    second = {**request, "request_id": 2} if second_type == "rpc" else {"id": 1, "type": "done", "error": None}
    code = f"""
import os, json, struct
messages = { [request, second]!r}
frames = b''
for message in messages:
 payload = json.dumps(message).encode()
 frames += struct.pack('!I', len(payload)) + payload
os.write(1, frames)
while True: pass
"""
    try:
        with GuiPythonRunner(broker=broker) as runner:
            with pytest.raises(GuiRunnerError, match="pipelined|premature"):
                runner.execute(code)
    finally:
        release.set()
        broker._executor.shutdown(wait=True)
    assert applied == []
    assert all(effect.status is EffectStatus.UNCERTAIN for effect in effects.list_unresolved("exec"))


@pytest.mark.parametrize("revocation", ["local", "execution"])
def test_revocation_during_validation_cannot_return_a_valid_context(environment, revocation):
    broker, _, executions = environment
    reached, release = threading.Event(), threading.Event()
    adapter_check = threading.Event()
    applied = []
    def validate(*args):
        if adapter_check.is_set():
            reached.set()
            assert release.wait(3)
    def apply(args, observation, context):
        adapter_check.set()
        context.check()
        applied.append(1)
    handle = broker.register(target="owned", methods={"click": apply}, validate=validate)
    future = broker.submit({"resource": handle, "method": "click", "arguments": {}, "observation": None},
                           deadline=time.monotonic() + 5)
    try:
        assert reached.wait(2)
        if revocation == "local":
            broker.revoke()
        else:
            from openprogram.execution.model import ExecutionStatus
            execution = executions.get_execution("exec")
            executions.transition_execution("exec", expected_version=execution.status_version,
                                            target=ExecutionStatus.CANCELLING)
    finally:
        release.set()
    with pytest.raises(PermissionError, match="revoked|stale"):
        future.result(timeout=2)
    assert applied == []


def test_revoke_during_receipt_materialization_refuses_commit(environment):
    broker, effects, _ = environment
    entered, release = threading.Event(), threading.Event()
    class Receipt(dict):
        def items(self):
            entered.set()
            assert release.wait(3)
            return super().items()
    handle = broker.register(target="owned", methods={"click": lambda *args: Receipt(done=True)}, validate=lambda *args: None)
    future = broker.submit({"resource": handle, "method": "click", "arguments": {}, "observation": None},
                           deadline=time.monotonic() + 5)
    try:
        assert entered.wait(2)
        broker.revoke()
    finally:
        release.set()
    with pytest.raises(PermissionError, match="revoked"):
        future.result(timeout=2)
    assert effects.list_unresolved("exec")[0].status is EffectStatus.UNCERTAIN


def test_admitted_receipt_can_finish_persisting_without_blocking_revoke(environment, monkeypatch):
    broker, effects, _ = environment
    entered, release = threading.Event(), threading.Event()
    original_resolve = effects.resolve
    def delayed_resolve(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original_resolve(*args, **kwargs)
    monkeypatch.setattr(effects, "resolve", delayed_resolve)
    handle = broker.register(target="owned", methods={"click": lambda *args: {"done": True}}, validate=lambda *args: None)
    future = broker.submit({"resource": handle, "method": "click", "arguments": {}, "observation": None},
                           deadline=time.monotonic() + 5)
    try:
        assert entered.wait(2)
        start = time.monotonic()
        broker.revoke()
        assert time.monotonic() - start < 0.5
    finally:
        release.set()
    receipt = future.result(timeout=2)
    assert effects.get(receipt["effect_id"]).status is EffectStatus.COMMITTED


def test_repeated_request_identity_is_not_dispatched_again(environment):
    broker, _, _ = environment
    applied = []
    handle = broker.register(target="owned", methods={"click": lambda *args: applied.append(1)}, validate=lambda *args: None)
    with GuiPythonRunner(broker=broker) as runner:
        assert runner.execute(f"await ui.call({handle!r}, 'click')").error is None
        with pytest.raises(GuiRunnerError, match="request identity"):
            runner.execute(f"ui.call.__func__.__globals__['_rpc_sequence'] = 0\nawait ui.call({handle!r}, 'click')")
    assert applied == [1]


def test_adapter_validation_uses_bound_owner_context_not_thread_defaults(environment):
    from contextvars import ContextVar
    _, effects, _ = environment
    authority = ContextVar("owned_gui_authority", default="permissive default")
    token = authority.set("owner denies")
    try:
        broker = GuiBroker(effects=effects, execution_id="exec", attempt_id="attempt", generation=1,
                           invocation_id="restricted-owner")
    finally:
        authority.reset(token)
    applied = []
    def validate(*args):
        if authority.get() == "owner denies":
            raise PermissionError("owner denies")
    handle = broker.register(target="owned", methods={"click": lambda *args: applied.append(1)}, validate=validate)
    with GuiPythonRunner(broker=broker) as runner:
        result = runner.execute(f"await ui.call({handle!r}, 'click')")
        assert result.error and "owner denies" in result.error
    assert applied == []
