"""``/stop`` against a turn that is really running: the canonical path a
channel turn takes stops it, and the command does not wait for the turn's
session lock.
"""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from openprogram.interfaces.channels import _conversation, _room
from openprogram.interfaces.channels._message import ChannelMessage
from openprogram.interfaces.channels._transport import SendResult
from openprogram.interfaces.channels.base import Channel
from openprogram.runtime.execution import AttemptStore, ExecutionStore
from openprogram.runtime.execution.control import RuntimeControlService
from openprogram.runtime.execution.driver import DriverRegistry
from openprogram.runtime.execution.model import ExecutionStatus

SESSION = "faketg_session"


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch: pytest.MonkeyPatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)
    monkeypatch.setattr(
        "openprogram.interfaces.channels._conversation._route",
        lambda channel, account_id, peer, *, rotate=True: ("main", SESSION, "base"),
    )
    _room.reset_state()
    from openprogram.interfaces.channels import _access
    _access.approve_user("faketg", "acct1", "701")
    yield
    _room.reset_state()


class _FakeChannel(Channel):
    platform_id = "faketg"

    def __init__(self) -> None:
        super().__init__(account_id="acct1")
        self.sent: list[tuple[str, str]] = []

    def run(self, stop: threading.Event) -> None:  # pragma: no cover
        pass

    def send_text_full(self, target: str, text: str) -> SendResult:
        self.sent.append((target, text))
        return SendResult.success("mid")


def _direct(text: str) -> ChannelMessage:
    return ChannelMessage(
        text=text, chat_id="701", user_id="701", user_display="Ada",
        chat_type="direct",
    )


def _no_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(**_kwargs):
        raise AssertionError("a command must not start a turn")

    monkeypatch.setattr(_conversation, "dispatch_inbound", refuse)


def test_cancel_session_executions_stops_a_channel_turn(tmp_path, monkeypatch) -> None:
    """The real canonical path a channel turn takes, with only the model faked."""
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    from openprogram.runtime.execution.run_control import cancel_session_executions
    from openprogram.state.store.session.session_store import SessionStore

    store = ExecutionStore(tmp_path / "executions.sqlite3")
    control = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    monkeypatch.setattr("openprogram.runtime.execution.default_store", lambda: store)
    monkeypatch.setattr("openprogram.runtime.execution.default_control_service", lambda: control)
    monkeypatch.setattr(
        "openprogram.runtime.context.session_db.default_db",
        lambda: SessionStore(tmp_path / "sessions"),
    )
    entered, cancelled = threading.Event(), threading.Event()

    def run_turn(*, request, cancel_event):
        entered.set()
        if cancel_event.wait(10):
            cancelled.set()
        return SimpleNamespace(failed=False, error=None, final_text="")

    adapter = CanonicalAgentAdapter(store=store, turn_runner=run_turn)
    request = TurnRequest(
        session_id=SESSION, user_text="work", agent_id="main", source="faketg",
    )
    admission = adapter.admit(
        request, trusted_actor={"subject": "owner"},
        user_message_id=request.user_msg_id, config_snapshot_ref="channel:faketg",
    )
    outcome: dict = {}
    thread = threading.Thread(
        target=lambda: outcome.update(done=asyncio.run(adapter.activate(admission))),
        daemon=True,
    )
    thread.start()
    assert entered.wait(5)

    results = cancel_session_executions(SESSION)

    assert [item["execution_id"] for item in results] == [admission.execution_id]
    assert cancelled.wait(5)
    thread.join(5)
    assert not thread.is_alive()
    assert store.get_execution(admission.execution_id).status is ExecutionStatus.CANCELLED
    assert cancel_session_executions(SESSION) == []


def test_stop_during_a_turn_acts_without_the_session_lock(monkeypatch) -> None:
    _no_dispatch(monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(
        "openprogram.runtime.execution.run_control.cancel_session_executions",
        lambda session_id: calls.append(session_id) or [{"execution_id": "e1"}],
    )
    lock = _conversation._session_lock_for(SESSION)
    channel = _FakeChannel()
    with lock:
        worker = threading.Thread(
            target=channel._dispatch_and_reply, args=(_direct("/stop"),), daemon=True,
        )
        worker.start()
        worker.join(5)
        assert not worker.is_alive()

    assert calls == [SESSION]
    assert channel.sent == [("701", "✓ 已停止正在进行的任务。")]
