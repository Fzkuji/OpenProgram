"""``/stop`` through the whole inbound path, against a real turn: the
chat's placeholder and the command's receipt, with only the provider
faked.
"""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from openprogram.interfaces.channels.inbound import _access, _conversation, _room
from openprogram.interfaces.channels.delivery import _transport
from openprogram.interfaces.channels.inbound._message import ChannelMessage
from openprogram.interfaces.channels.inbound._session_routing import _read_generations
from openprogram.interfaces.channels.delivery._transport import SendResult
from openprogram.interfaces.channels.base import Channel
from openprogram.models.providers.types import (
    AssistantMessage,
    EventStart,
    EventTextDelta,
    EventTextStart,
    Model,
    TextContent,
)
from openprogram.runtime.agent_registry.manager import AgentModelRef
from openprogram.runtime.context.session_db import SessionDB
from openprogram.runtime.conversation import turn_runtime as turns
from openprogram.runtime.execution import default_store


class _Agent:
    id = "main"
    session_scope = "per-account-channel-peer"
    session_daily_reset = ""
    session_idle_minutes = 0
    # Branch settings fall back to the Agent's own model, effort and tool
    # policy; this Agent names none of them.
    model = AgentModelRef()
    thinking_effort = None
    tools = None
    always_on: dict = {}


class _FakeChannel(Channel):
    platform_id = "faketg"

    def __init__(self) -> None:
        super().__init__(account_id="acct1")
        self.sent: list[str] = []

    def run(self, stop: threading.Event) -> None:  # pragma: no cover
        pass

    def send_text_full(self, target: str, text: str) -> SendResult:
        self.sent.append(text)
        return SendResult.success("mid")


def _direct(text: str, message_id: str) -> ChannelMessage:
    return ChannelMessage(
        text=text, chat_id="701", user_id="701", user_display="Ada",
        chat_type="direct", message_id=message_id,
    )


def _partial(text: str = "") -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)] if text else [],
        api="completion", provider="openai", model="stub",
        timestamp=int(time.time() * 1000),
    )


@pytest.fixture
def edits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)
    database = SessionDB(tmp_path / "sessions.sqlite")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: database)
    monkeypatch.setattr(turns, "_resolve_model", lambda profile, override=None: Model(
        id="stub", name="stub", api="completion", provider="openai", base_url="https://x",
    ))
    monkeypatch.setattr(turns, "_load_agent_profile", lambda agent_id: {
        "id": agent_id, "system_prompt": "", "tools": [],
    })
    monkeypatch.setattr("openprogram.interfaces.channels.bindings.route", lambda *args: "main")
    monkeypatch.setattr("openprogram.runtime.agent_registry.manager.get", lambda agent_id: _Agent())
    monkeypatch.setattr(
        "openprogram.runtime.agent_registry.manager.sessions_dir",
        lambda agent_id: tmp_path / "agents" / agent_id,
    )
    monkeypatch.setattr(
        "openprogram.runtime.agent_registry.session_aliases.lookup", lambda *args: None,
    )
    placeholder_edits: list[str] = []
    monkeypatch.setattr(
        _transport, "post_message",
        lambda channel, account, target, text, **kwargs: SendResult.success("9001"),
    )
    monkeypatch.setattr(
        _transport, "patch_message",
        lambda platform, account, target, message_id, text:
            placeholder_edits.append(text) or SendResult.success("9001"),
    )
    _room.reset_state()
    _access.approve_user("faketg", "acct1", "701")
    yield placeholder_edits
    _room.reset_state()


def _stop_a_running_turn(started: threading.Event) -> _FakeChannel:
    channel = _FakeChannel()
    turn = threading.Thread(
        target=channel._dispatch_and_reply, args=(_direct("work", "m1"),), daemon=True,
    )
    turn.start()
    assert started.wait(10)
    channel._dispatch_and_reply(_direct("/stop", "m2"))
    turn.join(10)
    assert not turn.is_alive()
    assert default_store().list_nonterminal() == []
    return channel


def test_stop_interrupts_a_provider_that_is_mid_reply(edits, monkeypatch) -> None:
    started = threading.Event()

    async def stream(model, context, options):
        yield EventStart(partial=_partial())
        yield EventTextStart(content_index=0, partial=_partial())
        yield EventTextDelta(content_index=0, delta="half", partial=_partial("half"))
        started.set()
        await asyncio.sleep(30)

    real_loop = turns._run_loop_blocking

    def run_loop(*, req, history, on_event, cancel_event, **_extra):
        return real_loop(
            req=req, history=history, on_event=on_event,
            cancel_event=cancel_event, stream_fn=stream,
        )

    monkeypatch.setattr(turns, "_run_loop_blocking", run_loop)
    channel = _stop_a_running_turn(started)
    assert channel.sent == ["✓ 已停止正在进行的任务。"]
    # The stopped reply keeps the text it had streamed; the chat app gets it,
    # as the stored reply and CLI --print do.
    assert edits == ["half"]


def test_a_turn_that_fails_after_the_stop_does_not_read_as_an_error(edits, monkeypatch) -> None:
    started = threading.Event()

    def run_loop(*, cancel_event, **_extra):
        started.set()
        assert cancel_event.wait(10)
        return "", {"input_tokens": 1, "output_tokens": 1}, []

    def persist_fails(**_kwargs):
        raise RuntimeError("persist failed")

    monkeypatch.setattr(turns, "_run_loop_blocking", run_loop)
    monkeypatch.setattr(turns, "persist_assistant_message", persist_fails)
    channel = _stop_a_running_turn(started)
    assert channel.sent == ["✓ 已停止正在进行的任务。"]
    assert edits == [_conversation.STOPPED_REPLY]


def test_a_stop_before_the_turn_activates_does_not_read_as_an_error(edits, monkeypatch) -> None:
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    from openprogram.runtime.execution.run_control import cancel_session_executions

    real_activate = CanonicalAgentAdapter.activate

    async def stopped_first(self, admission, **kwargs):
        cancel_session_executions(admission.session_id)
        return await real_activate(self, admission, **kwargs)

    monkeypatch.setattr(CanonicalAgentAdapter, "activate", stopped_first)
    channel = _FakeChannel()
    channel._dispatch_and_reply(_direct("work", "m1"))
    assert channel.sent == []
    assert edits == [_conversation.STOPPED_REPLY]


def test_a_failing_stop_still_answers(edits, monkeypatch) -> None:
    def fails(session_id):
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(
        "openprogram.runtime.execution.run_control.cancel_session_executions", fails,
    )
    channel = _FakeChannel()
    channel._dispatch_and_reply(_direct("/stop", "m1"))
    assert channel.sent == ["[error] RuntimeError: store unavailable"]


def test_stop_on_an_idle_expired_session_starts_no_new_conversation(edits, monkeypatch) -> None:
    monkeypatch.setattr(_Agent, "session_idle_minutes", 1)
    monkeypatch.setattr(
        "openprogram.interfaces.channels.inbound._session_routing._last_activity",
        lambda session_key: time.time() - 3600,
    )
    channel = _FakeChannel()
    channel._dispatch_and_reply(_direct("/stop", "m1"))
    assert channel.sent == ["现在没有正在进行的任务。"]
    assert _read_generations() == {}
