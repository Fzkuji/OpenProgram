"""Always-on end to end: a real conversation joins, wakes, checks in through
the production turn path with a scripted model, reports, and the report
reaches a channel conversation."""
from __future__ import annotations

from types import SimpleNamespace
import uuid

import pytest

from tests.support.waiting import wait_until

_API = "always-on-e2e-api"


@pytest.fixture
def world(tmp_path, monkeypatch):
    import openprogram.state.paths as paths
    from openprogram.models.providers.catalog import api_registry
    from openprogram.models.providers.types import Model
    from openprogram.runtime import channel_link, interface_link
    from openprogram.runtime.conversation import turn_runtime as D
    from openprogram.runtime.permissions import authority
    from openprogram.state.store.session import session_store
    from tests.component.providers.scripted_provider import ScriptedProvider

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    authority._reset_owner_cache_for_tests()
    session_store.shared._default_store = None

    provider = ScriptedProvider()
    previous = api_registry._registry.get(_API)
    api_registry.register_api_provider(_API, provider)
    model = Model(id="scripted", name="scripted", api=_API, provider="scripted-e2e",
                  base_url="http://scripted.invalid")
    monkeypatch.setattr(D, "_resolve_model", lambda *a, **kw: model)

    # The server registers its interface link on import; import it before
    # the patch below so undoing the patch leaves that link in place.
    from openprogram.interfaces.server import server as _server  # noqa: F401

    frames: list[dict] = []

    class Link:
        def broadcast(self, frame):
            frames.append(frame)

        def __getattr__(self, _name):
            return lambda *a, **kw: None
    monkeypatch.setattr(interface_link, "_link", Link())

    sent: list[tuple] = []

    class Channels:
        def flatten_tables(self, text):
            return text

        def send_message(self, channel, account_id, peer_id, text):
            sent.append((channel, account_id, peer_id, text))
            return SimpleNamespace(ok=True, error_kind=None, error_detail=None)

        def peer_bindings(self, _agent_id):
            return []

        def account_ids(self, _channel):
            return []
    monkeypatch.setattr(channel_link, "require", lambda: Channels())
    monkeypatch.setattr(channel_link, "peer_bindings", lambda agent_id: [], raising=False)

    from openprogram.runtime.agent_registry import manager
    agent_id = "e2e" + uuid.uuid4().hex[:8]
    manager.create(agent_id, name="Scout")
    try:
        yield SimpleNamespace(agent_id=agent_id, provider=provider, frames=frames, sent=sent,
                              manager=manager)
    finally:
        manager.delete(agent_id)
        if previous is None:
            api_registry._registry.pop(_API, None)
        else:
            api_registry.register_api_provider(_API, previous)


def _idle(session_id):
    from openprogram.interfaces.server import server as _s
    from openprogram.runtime.conversation.run_control import is_turn_running
    return not is_turn_running(session_id) and not _s._is_run_active(session_id)


def _said(session_id, text):
    """The owner said something in the conversation: it has a head."""
    import time
    from openprogram.runtime.context.session_db import default_db
    default_db().append_message(session_id, {"id": "u-" + uuid.uuid4().hex[:8], "role": "user",
                                             "content": text, "timestamp": time.time()})


def _wake_ended(session_id):
    from openprogram.runtime.conversation.always_on import store
    state = store.load_state(session_id)
    outcome = (state.get("last_wake") or {}).get("outcome")
    return outcome not in (None, "check_in") and not state.get("check_in") and _idle(session_id)


def test_check_in_reports_and_delivers_to_a_channel(world):
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.conversation import always_on
    from openprogram.runtime.conversation.always_on import store
    from tests.component.providers.scripted_provider import ScriptedText, ScriptedToolCall

    target = {"channel": "telegram", "account_id": "bot", "peer_id": "42", "label": "Me"}
    world.manager.update(world.agent_id, {"always_on": {
        "enabled": True, "research": True, "channels": [target]}})
    sid = "ao-" + uuid.uuid4().hex[:8]
    default_db().create_session(sid, world.agent_id)
    joined = always_on.join(sid)
    assert joined["next_wake_at"] > joined["joined_at"]
    assert always_on.agent_for_session(sid) == world.agent_id
    _said(sid, "Watch CI for me")

    world.provider.add_response(ScriptedToolCall(
        "always_on_report", {"title": "CI is red", "body": "main fails since 10:00",
                             "links": ["https://ci.invalid/1"], "kind": "approval"}, "call-1"))
    world.provider.add_response(ScriptedText("Reported the CI failure."))

    result = always_on.wake(sid, reason="manual")
    assert result["outcome"] == "check_in"
    wait_until(lambda: world.provider.call_count >= 2 and _wake_ended(sid), timeout=20)

    # The check-in turn ran with the read-only tool list.
    tools = {tool.name for tool in world.provider.calls[0].context.tools or []}
    assert {"always_on_report", "always_on_schedule"} <= tools and "always_on_preference" not in tools
    assert "bash" not in tools and "write" not in tools
    assert "Always-on check-in" in str(world.provider.calls[0].context.messages)

    reports = store.load_reports(sid, 10)
    assert [(r["title"], r["kind"]) for r in reports] == [("CI is red", "approval")]
    assert store.load_state(sid)["last_wake"]["outcome"] == "reported"
    wait_until(lambda: (store.load_reports(sid, 10)[0].get("deliveries") or [{}])[0].get("status") == "sent",
               timeout=10)
    assert world.sent and world.sent[0][:3] == ("telegram", "bot", "42")
    assert "Scout · CI is red" in world.sent[0][3]
    kinds = [frame["type"] for frame in world.frames]
    report_frame = world.frames[kinds.index("always_on_report")]
    assert report_frame["data"]["kind"] == "approval" and report_frame["data"]["notify_desktop"] is True
    # The report itself gives the conversation the sidebar's unread dot.
    after = world.frames[kinds.index("always_on_report") + 1]
    assert after == {"type": "session_updated", "data": {"id": sid, "unread": True}}
    assert default_db().get_session(sid)["unread"]
    # The conversation shows the report as the Agent's message and nothing
    # of the check-in itself.
    wait_until(lambda: len(default_db().get_branch(sid)) == 2, timeout=10)
    branch = default_db().get_branch(sid)
    assert [m["role"] for m in branch] == ["user", "assistant"]
    assert branch[1]["always_on_report"] == reports[0]["id"] and branch[1]["kind"] == "approval"
    assert branch[1]["report_title"] == "CI is red" and "**CI is red**" not in branch[1]["content"]
    assert branch[1]["report_source"] == "check_in"
    assert default_db().get_session(sid)["head_id"] == branch[1]["id"]
    assert "Always-on check-in" not in str(branch)
    assert default_db().get_session(sid).get("status") != "running"


def test_a_check_in_that_finds_nothing_stays_out_of_sight(world):
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.conversation import always_on
    from openprogram.runtime.conversation.owner_turn import start_owner_turn
    from tests.component.providers.scripted_provider import ScriptedText

    world.manager.update(world.agent_id, {"always_on": {"enabled": True, "research": True}})
    sid = "ao-" + uuid.uuid4().hex[:8]
    default_db().create_session(sid, world.agent_id)
    always_on.join(sid)
    _said(sid, "Keep an eye on the release")
    head = default_db().get_session(sid)["head_id"]
    world.provider.add_response(ScriptedText("Nothing new since the last check-in."))

    assert always_on.wake(sid, reason="schedule")["outcome"] == "check_in"
    wait_until(lambda: world.provider.call_count >= 1 and _wake_ended(sid), timeout=20)
    assert always_on.status(sid)["last_wake"]["outcome"] == "nothing_new"
    session = default_db().get_session(sid)
    assert not session.get("unread") and session.get("status") != "running"
    assert session["head_id"] == head and len(default_db().get_branch(sid)) == 1
    assert not any(frame["type"] == "session_updated" for frame in world.frames)
    assert not any(frame["type"] == "chat_response" for frame in world.frames)

    # An ordinary turn in the same conversation still marks it unread.
    world.provider.add_response(ScriptedText("Hi."))
    start_owner_turn(sid, world.agent_id, "hello")
    wait_until(lambda: world.provider.call_count >= 2 and _idle(sid), timeout=20)
    wait_until(lambda: default_db().get_session(sid).get("unread"), timeout=10)
    # Its context is the conversation alone, without the hidden check-in.
    assert "Always-on check-in" not in str(world.provider.calls[1].context.messages)


def test_standing_goal_starts_once_as_a_goal(world):
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.conversation import always_on
    from openprogram.runtime.conversation.always_on import store
    import openprogram.programs.tools.planning.goal as goals
    from tests.component.providers.scripted_provider import ScriptedText

    world.manager.update(world.agent_id, {"always_on": {"enabled": True, "goal": "Watch the docs"}})
    sid = "ao-" + uuid.uuid4().hex[:8]
    default_db().create_session(sid, world.agent_id)
    always_on.join(sid)
    for _ in range(8):
        world.provider.add_response(ScriptedText("Working on it."))

    assert always_on.wake(sid, reason="manual")["outcome"] == "goal_started"
    goal = goals.load_goal(sid)
    assert goal and goal["text"] == "Watch the docs" and goal["origin"] == "always_on"
    assert store.load_state(sid)["applied_goal"]["text"] == "Watch the docs"
    wait_until(lambda: world.provider.call_count >= 1, timeout=20)
    wait_until(lambda: _idle(sid), timeout=30)
    # The goal is not started twice.
    # ("busy": the Goal's own next turn is already running.)
    assert always_on.wake(sid, reason="manual")["outcome"] in {"goal_in_progress", "goal_paused", "check_in", "busy"}
    assert store.load_state(sid)["applied_goal"]["text"] == "Watch the docs"


class _RoutedProvider:
    """Answers by what the conversation was last told, so turns of two
    conversations may interleave."""

    def __init__(self, route):
        self.route = route
        self.seen: list[str] = []
        self.tool_results: list[str] = []

    def stream(self, model, context, options=None):
        return self.stream_simple(model, context, options)

    async def stream_simple(self, model, context, options=None):
        from tests.component.providers.scripted_provider import ScriptedProvider, ScriptedText
        last = context.messages[-1]
        users = [m for m in context.messages if getattr(m, "role", "") == "user"]
        told = str(getattr(users[-1], "content", "")) if users else ""
        self.seen.append(told)
        scripted = ScriptedProvider()
        if getattr(last, "role", "") == "toolResult":
            self.tool_results.append(last.content[0].text)
            scripted.add_response(ScriptedText(f"tool said: {last.content[0].text}"))
        else:
            scripted.add_response(*self.route(told))
        async for event in scripted.stream_simple(model, context, options):
            yield event


def test_send_message_reaches_the_other_conversation_by_its_title(world, monkeypatch):
    """The always-on hand-off is send_message now: a conversation reaches
    another by its title, and the message is admitted there as a Job whose
    reply routes back to the sender (the reply notice itself is covered by
    the job notice tests; this world runs no Job dispatcher)."""
    from openprogram.models.providers.catalog import api_registry
    from openprogram.runtime.permissions import classifier

    async def allow(*_a, **_kw):
        # Auto mode reviews a message like any action; the reviewer approves here.
        return classifier.ReviewResult(False, "", "")
    monkeypatch.setattr(classifier, "auto_classify_tool", allow)
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.conversation.owner_turn import start_owner_turn
    from openprogram.runtime.jobs.store import list_jobs
    from tests.component.providers.scripted_provider import ScriptedToolCall

    planner, docs = "ao-" + uuid.uuid4().hex[:8], "ao-" + uuid.uuid4().hex[:8]

    def route(told):
        if "LOOP" in told:
            return (ScriptedToolCall("send_message", {"to": planner, "message": "me again"}, "self-1"),)
        return (ScriptedToolCall("send_message", {"to": "docs bot", "message": "Check the links"}, "h-1"),)
    provider = _RoutedProvider(route)
    api_registry.register_api_provider(_API, provider)
    world.manager.update(world.agent_id, {"always_on": {"enabled": True}})
    default_db().create_session(planner, world.agent_id, title="Planner")
    default_db().create_session(docs, "main", title="Docs bot")

    start_owner_turn(planner, world.agent_id, "PING: ask the docs bot")
    wait_until(lambda: _idle(planner) and provider.tool_results, timeout=30)

    assert provider.tool_results[0].startswith("[delivered] execution_id=")
    (job,) = list_jobs(docs)
    assert job.caller_session_id == planner and job.agent_id == "main"
    assert "Check the links" in job.prompt and "not from your user" in job.prompt
    assert job.context_mode == "clean"  # the first turn of an empty conversation

    # A conversation cannot message its own running turn.
    provider.tool_results.clear()
    start_owner_turn(planner, world.agent_id, "LOOP")
    wait_until(lambda: _idle(planner) and provider.tool_results, timeout=30)
    assert provider.tool_results[0].startswith("[send_message refused]")
    assert len(list_jobs(docs)) == 1


def test_the_owner_can_type_while_a_check_in_runs(world):
    import asyncio
    import threading
    from openprogram.interfaces.server import server as _s
    from openprogram.models.providers.catalog import api_registry
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.conversation import always_on
    from openprogram.runtime.conversation.always_on import store
    from openprogram.runtime.conversation.run_control import is_turn_running, run_slot
    from tests.component.providers.scripted_provider import ScriptedProvider, ScriptedText

    gate = threading.Event()

    class Held:
        def stream(self, model, context, options=None):
            return self.stream_simple(model, context, options)

        async def stream_simple(self, model, context, options=None):
            await asyncio.to_thread(gate.wait, 20)
            scripted = ScriptedProvider()
            scripted.add_response(ScriptedText("Nothing new."))
            async for event in scripted.stream_simple(model, context, options):
                yield event
    api_registry.register_api_provider(_API, Held())
    world.manager.update(world.agent_id, {"always_on": {"enabled": True}})
    sid = "ao-" + uuid.uuid4().hex[:8]
    default_db().create_session(sid, world.agent_id)
    _said(sid, "Watch the release")
    try:
        assert always_on.wake(sid, reason="manual")["outcome"] == "check_in"
        wait_until(lambda: is_turn_running(sid), timeout=20)
        assert store.load_state(sid)["check_in"]
        # The check-in holds no run slot: a typed message would be admitted.
        assert not _s._is_run_active(sid)
        assert run_slot.reserve(sid, "probe")
        run_slot.release(sid, "probe")
        assert always_on.status(sid)["running"] is True
        # A scheduled wake meanwhile finds it busy.
        assert always_on.wake(sid, reason="schedule")["outcome"] == "busy"
    finally:
        gate.set()
    wait_until(lambda: _wake_ended(sid), timeout=20)
    assert store.load_state(sid)["last_wake"]["outcome"] == "nothing_new"
