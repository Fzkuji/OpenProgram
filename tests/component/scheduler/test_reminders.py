"""Scheduler reminders: the remind tool, the signed reminder spec, the
reminder.fired event the worker emits, and its delivery."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import threading
import time
from types import SimpleNamespace

import pytest


@pytest.fixture
def scheduler_env(tmp_path, monkeypatch):
    import openprogram.state.paths as paths
    from openprogram.runtime.permissions import authority
    from openprogram.runtime.scheduler import store as task_store

    schedule = tmp_path / "scheduler" / "tasks.json"
    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    authority._reset_owner_cache_for_tests()
    monkeypatch.setenv("OPENPROGRAM_SCHEDULER_PATH", str(schedule))
    monkeypatch.delenv(task_store.DEFAULT_CRON_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def in_conversation(monkeypatch):
    """Make task creation see a current conversation and the local owner."""
    from openprogram.runtime.permissions.authority import local_owner_authority
    from openprogram.runtime.scheduler import store as task_store

    monkeypatch.setattr(task_store, "_origin_session", lambda: ("sess-1", "main"))
    monkeypatch.setattr(task_store, "_authorize", lambda _action: (local_owner_authority(), None))


def _reminder(**kw):
    from openprogram.runtime.permissions.authority import local_owner_authority
    from openprogram.runtime.scheduler import service

    return service.create_task(
        title="Stretch", task_type="once", reminder="Stand up and stretch",
        run_at="2026-08-14T10:00:00+00:00", authority=local_owner_authority(), **kw,
    )


def test_reminder_task_signs_its_conversation_and_wake(scheduler_env, in_conversation):
    from openprogram.runtime.scheduler import store as task_store

    task = _reminder(wake=True)
    spec, error = task_store._verify_execution_spec(task["execution"])
    assert error is None
    assert task["reminder"] == "Stand up and stretch" and task["wake"] is True
    assert (spec["kind"], spec["session_id"], spec["wake"]) == ("reminder", "sess-1", True)


def test_waking_reminder_needs_a_conversation(scheduler_env):
    plain = _reminder()
    assert plain["execution"]["session_id"] == "" and plain["wake"] is False
    with pytest.raises(ValueError, match="conversation"):
        _reminder(wake=True)


def test_task_bodies_are_exclusive_and_wake_is_only_for_reminders(scheduler_env):
    from openprogram.runtime.permissions.authority import local_owner_authority
    from openprogram.runtime.scheduler import service

    with pytest.raises(ValueError, match="exactly one"):
        _reminder(prompt="also a prompt")
    with pytest.raises(ValueError, match="wake"):
        service.create_task(title="x", task_type="once", command="echo x", wake=True,
                            run_at="2026-08-14T10:00:00+00:00", authority=local_owner_authority())


def test_update_keeps_the_reminder_home_outside_the_conversation(
    scheduler_env, in_conversation, monkeypatch,
):
    from openprogram.runtime.scheduler import store as task_store
    from openprogram.runtime.scheduler import service

    task = _reminder(wake=True)
    # The Scheduler page edits with no current conversation.
    monkeypatch.setattr(task_store, "_origin_session", lambda: ("", "main"))
    updated = service.update_task(task["id"], {"reminder": "Drink water"})
    assert updated["reminder"] == "Drink water" and updated["wake"] is True
    assert updated["execution"]["session_id"] == "sess-1"
    switched = service.update_task(task["id"], {"prompt": "Summarize the day"})
    assert "reminder" not in switched and "wake" not in switched


def test_worker_fires_a_due_reminder_as_one_event(scheduler_env, in_conversation, monkeypatch):
    from openprogram.runtime.events import get_event_bus
    from openprogram.runtime.scheduler import worker
    from openprogram.runtime.scheduler import reminders

    monkeypatch.setattr(reminders, "_installed", True)  # no real delivery here
    task = _reminder()
    seen = []
    unsubscribe = get_event_bus().subscribe(seen.append, types={reminders.EVENT})
    try:
        state = {}
        assert worker._tick(state, now=datetime(2026, 8, 14, 9, 59, tzinfo=timezone.utc)) == 0
        assert worker._tick(state, now=datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc)) == 1
        assert worker._tick(state, now=datetime(2026, 8, 14, 10, 1, tzinfo=timezone.utc)) == 0
    finally:
        unsubscribe()
    assert len(seen) == 1
    payload = seen[0].payload
    assert payload["task_id"] == task["id"] and payload["message"] == "Stand up and stretch"
    assert payload["session_id"] == "sess-1" and payload["wake"] is False
    assert seen[0].metadata["session"] == "sess-1"


def _event(**payload):
    base = {"task_id": "t1", "title": "Stretch", "message": "Stand up", "session_id": "sess-1",
            "agent_id": "main", "wake": False, "scheduled_for": "", "fired_at": 1_786_000_000.0}
    return SimpleNamespace(id="ev1", payload={**base, **payload})


def _patch_delivery(monkeypatch, session, result=None):
    import openprogram.runtime.events as events
    from openprogram.interfaces.channels import outbound
    from openprogram.runtime.scheduler import reminders

    frames, sent, turns = [], [], []
    done = threading.Event()
    def frame(value):
        frames.append(value)
        # A desktop App answers, so the notify thread ends with the test
        # instead of posting a real notification after the patches are gone.
        reminders.shown(value["data"]["id"])
    monkeypatch.setattr(events, "emit_ws_frame", frame)
    monkeypatch.setattr(reminders, "_session", lambda _sid: session)
    monkeypatch.setattr(reminders, "system_notify", lambda *_a: True)

    def send(channel, account_id, peer_id, text):
        sent.append((channel, account_id, peer_id, text))
        done.set()
        return SimpleNamespace(ok=True)
    monkeypatch.setattr(outbound, "send_full", send)

    def start(session_id, agent_id, text, **kw):
        turns.append((session_id, agent_id, text))
        kw["on_done"](result or SimpleNamespace(final_text="Done stretching."))
        done.set()
        return "exec-1"
    import openprogram.runtime.conversation.turn_admission.owner_turn as owner_turn
    monkeypatch.setattr(owner_turn, "start_owner_turn", start)
    import openprogram.runtime.conversation.run_control as run_control
    monkeypatch.setattr(run_control, "is_turn_running", lambda _sid: False)
    return frames, sent, turns, done


def test_delivery_notifies_clients_and_the_channel_conversation(monkeypatch):
    from openprogram.runtime.scheduler import reminders

    session = {"agent_id": "main", "channel": "wechat", "account_id": "acc", "peer_id": "peer"}
    frames, sent, turns, done = _patch_delivery(monkeypatch, session)
    reminders.deliver(_event())
    assert done.wait(30)
    assert frames[0]["type"] == "reminder"
    assert frames[0]["data"]["message"] == "Stand up" and frames[0]["data"]["session_id"] == "sess-1"
    assert sent == [("wechat", "acc", "peer", "⏰ Stand up")] and turns == []


def test_waking_reminder_starts_a_turn_and_sends_its_answer(monkeypatch):
    from openprogram.runtime.scheduler import reminders

    session = {"agent_id": "writer", "channel": "wechat", "account_id": "acc", "peer_id": "peer"}
    frames, sent, turns, done = _patch_delivery(monkeypatch, session)
    reminders.deliver(_event(wake=True))
    assert done.wait(30)
    assert frames[0]["data"]["wake"] is True
    assert turns[0][:2] == ("sess-1", "writer") and turns[0][2].startswith("⏰ Stand up")
    assert sent == [("wechat", "acc", "peer", "Done stretching.")]


def test_a_paused_reminder_turn_sends_nothing_and_its_resumed_attempt_carries_the_answer(monkeypatch):
    from openprogram.runtime.conversation import bound_reply
    from openprogram.runtime.context import session_db
    from openprogram.runtime.scheduler import reminders

    session = {"agent_id": "writer", "channel": "wechat", "account_id": "acc", "peer_id": "peer"}
    frames, sent, turns, done = _patch_delivery(monkeypatch, session, result=SimpleNamespace(
        final_text="", _execution_safe_point_handoff=True))
    reminders.deliver(_event(wake=True))
    assert done.wait(30)
    assert sent == [], "the pause has no answer to send yet"
    # After the approval the same turn resumes; its text goes out through the
    # chat's bound reply, because a reminder's source is not the channel.
    monkeypatch.setattr(session_db, "default_db", lambda: SimpleNamespace(get_session=lambda _id: session))
    target = bound_reply.ReplyTarget("wechat", "acc", "peer")
    monkeypatch.setattr(bound_reply, "reply_target", lambda *_a, **_k: target)
    request = SimpleNamespace(session_id="sess-1", source="web", background=False, user_msg_id="m1")
    resumed = bound_reply.attach(request, "m1_reply", resumed=True)
    assert resumed is not None and resumed.target == target and resumed.resumed


def test_reminder_without_a_live_conversation_only_notifies(monkeypatch):
    from openprogram.runtime.scheduler import reminders

    frames, sent, turns, _done = _patch_delivery(monkeypatch, None)
    reminders.deliver(_event(wake=True))
    assert frames[0]["data"]["wake"] is False and frames[0]["data"]["session_id"] == ""
    assert sent == [] and turns == []


def test_waking_reminder_in_an_archived_conversation_only_notifies(monkeypatch):
    from openprogram.runtime.scheduler import reminders

    session = {"agent_id": "writer", "channel": "wechat", "account_id": "acc", "peer_id": "peer", "archived": True}
    frames, sent, turns, done = _patch_delivery(monkeypatch, session)
    reminders.deliver(_event(wake=True))
    assert done.wait(30)
    assert frames[0]["data"]["wake"] is False
    assert turns == [] and sent == [("wechat", "acc", "peer", "⏰ Stand up")]


def test_remind_tool_resolves_times_and_creates_reminders(scheduler_env, in_conversation, monkeypatch):
    from openprogram.programs.tools.jobs.cron import remind as tool
    from openprogram.runtime.scheduler import service

    now = datetime(2026, 10, 23, 14, 30, tzinfo=timezone(timedelta(hours=8)))
    monkeypatch.setattr(tool, "_now", lambda: now)

    reply = tool.remind("Call the clinic", in_minutes=20)
    assert "2026-10-23 14:50" in reply and "the user is notified" in reply
    reply = tool.remind("Check CI", at="09:00", wake_agent=True)
    assert "Sat 2026-10-24 09:00" in reply and "you act on it here" in reply
    reply = tool.remind("Standup", cron="0 9 * * 1-5")
    assert "Mon 2026-10-26 09:00" in reply

    tasks = service.list_tasks()
    assert [(t["type"], t["reminder"], t["wake"]) for t in tasks] == [
        ("once", "Call the clinic", False), ("once", "Check CI", True),
        ("recurring", "Standup", False),
    ]
    assert tasks[0]["run_at"] == "2026-10-23T14:50:00+08:00"


@pytest.mark.parametrize("kw, error", [
    ({}, "exactly one"),
    ({"at": "09:00", "in_minutes": 5}, "exactly one"),
    ({"in_minutes": -1}, "positive"),
    ({"at": "2026-10-01 09:00"}, "already passed"),
    ({"at": "soon"}, "HH:MM"),
    ({"cron": "nope"}, "cron"),
])
def test_remind_tool_explains_bad_times(scheduler_env, in_conversation, monkeypatch, kw, error):
    from openprogram.programs.tools.jobs.cron import remind as tool

    monkeypatch.setattr(tool, "_now", lambda: datetime(2026, 10, 23, 14, 30).astimezone())
    reply = tool.remind("x", **kw)
    assert reply.startswith("Error") and error in reply


def test_remind_is_resident_and_needs_schedule_create():
    from openprogram.runtime.permissions.tool_authority import capability_for_tool
    from openprogram.programs import RESIDENT_TOOLS

    assert "remind" in RESIDENT_TOOLS
    assert capability_for_tool("remind", {}) == "schedule.create"


def test_rest_api_creates_a_reminder(scheduler_env):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from openprogram.interfaces.server.routes.execution import scheduler as routes

    app = FastAPI()
    routes.register(app)
    created = TestClient(app).post("/api/scheduler/tasks", json={
        "title": "Invoice", "type": "once", "reminder": "Send the invoice",
        "run_at": "2026-08-14T17:00:00+08:00",
    })
    assert created.status_code == 201, created.text
    assert created.json()["reminder"] == "Send the invoice" and created.json()["wake"] is False


@pytest.mark.parametrize("app_answers", [True, False])
def test_worker_posts_the_system_notification_when_no_desktop_app_did(monkeypatch, app_answers):
    import openprogram.runtime.events as events
    from openprogram.runtime.scheduler import reminders

    posted = threading.Event()
    calls = []

    def frame(value):
        if app_answers:
            reminders.shown(value["data"]["id"])
    monkeypatch.setattr(events, "emit_ws_frame", frame)
    monkeypatch.setattr(reminders, "_session", lambda _sid: None)
    monkeypatch.setattr(reminders, "SHOWN_WAIT_S", 0.2)

    def notify(title, message):
        calls.append((title, message))
        posted.set()
        return True
    monkeypatch.setattr(reminders, "system_notify", notify)
    reminders.deliver(_event())
    assert posted.wait(2) is not app_answers
    assert calls == ([] if app_answers else [("OpenProgram ⏰", "Stand up")])
    deadline = time.monotonic() + 30
    while reminders._shown and time.monotonic() < deadline:
        time.sleep(0.01)
    assert reminders._shown == {}


@pytest.mark.parametrize("in_bundle", [True, False])
def test_macos_notification_comes_from_the_app_bundle_when_there_is_one(monkeypatch, in_bundle, _no_system_notifications):
    from openprogram.runtime.scheduler import reminders

    ran = []
    monkeypatch.setattr(reminders.sys, "platform", "darwin")
    monkeypatch.setattr(reminders, "_bundle_notify", lambda *_a: in_bundle)
    monkeypatch.setattr(reminders.subprocess, "run",
                        lambda cmd, **_kw: ran.append(cmd[0]) or SimpleNamespace(returncode=0))
    assert _no_system_notifications.system_notify("OpenProgram ⏰", "Stand up") is True
    assert ran == ([] if in_bundle else ["osascript"])


def test_a_waking_reminders_turn_is_marked_as_nobody_typing_it(monkeypatch):
    from openprogram.runtime.conversation.turn_admission import owner_turn
    from openprogram.runtime.scheduler import reminders

    seen = {}
    monkeypatch.setattr(owner_turn, "start_owner_turn_when_free", lambda *_a, **kw: seen.update(kw) or "e1")
    reminders._wake({"session_id": "s", "task_id": "t", "message": "Stand up", "agent_id": "main",
                     "fired_at": 1.0}, {"agent_id": "main"})
    assert seen["automation"] == "reminder"


@pytest.mark.parametrize("platform, tool", [("darwin", "osascript"), ("linux", "notify-send")])
def test_a_text_starting_with_a_dash_is_not_read_as_an_option(monkeypatch, platform, tool, _no_system_notifications):
    from openprogram.runtime.scheduler import reminders

    ran = []
    monkeypatch.setattr(reminders.sys, "platform", platform)
    monkeypatch.setattr(reminders, "_bundle_notify", lambda *_a: False)
    monkeypatch.setattr(reminders.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(reminders.subprocess, "run",
                        lambda cmd, **_kw: ran.append(cmd) or SimpleNamespace(returncode=0))
    assert _no_system_notifications.system_notify("-Scout", "-3% revenue") is True
    assert ran[0][0] == tool
    assert ran[0][-3:] == ["--", "-Scout", "-3% revenue"]


def test_windows_notification_is_a_toast_whose_texts_are_never_script(monkeypatch, _no_system_notifications):
    """Not run on Windows: Windows PowerShell is a stand-in at the subprocess boundary."""
    from openprogram.runtime.scheduler import reminders

    ran = []
    monkeypatch.setattr(reminders.sys, "platform", "win32")
    monkeypatch.setattr(reminders.shutil, "which",
                        lambda name: rf"C:\Windows\System32\{name}" if name == "powershell.exe" else None)
    monkeypatch.setattr(reminders.subprocess, "run",
                        lambda cmd, **kw: ran.append((cmd, kw)) or SimpleNamespace(returncode=0))
    title, message = "OpenProgram ⏰", "'); Remove-Item C:\\ -Recurse; ('<b>&"
    assert _no_system_notifications.system_notify(title, message) is True
    command, options = ran[0]
    assert command[0].endswith("powershell.exe") and command[-2] == "-Command"
    assert "ToastNotificationManager" in command[-1]
    assert not any(title in part or message in part for part in command)
    assert options["env"]["OPENPROGRAM_NOTICE_TITLE"] == title
    assert options["env"]["OPENPROGRAM_NOTICE_MESSAGE"] == message
    assert "$env:OPENPROGRAM_NOTICE_MESSAGE" in command[-1] and "CreateTextNode" in command[-1]


def test_windows_without_windows_powershell_posts_nothing(monkeypatch, _no_system_notifications):
    from openprogram.runtime.scheduler import reminders

    monkeypatch.setattr(reminders.sys, "platform", "win32")
    monkeypatch.setattr(reminders.shutil, "which", lambda _name: None)
    monkeypatch.setattr(reminders.subprocess, "run", lambda *_a, **_k: pytest.fail("ran a command"))
    assert _no_system_notifications.system_notify("OpenProgram", "Stand up") is False


def test_a_wake_whose_conversation_stays_locked_tries_again_when_it_is_free(monkeypatch):
    """A long load or a move holding the conversation is not a turn running:
    the wake keeps trying within its wait instead of failing."""
    from openprogram.runtime.conversation.turn_admission import owner_turn
    from openprogram.runtime.conversation import run_control
    from openprogram.state.store.session.files.session_lock import SessionLockTimeout

    attempts = []

    def start(session_id, agent_id, text, **_options):
        attempts.append(session_id)
        if len(attempts) == 1:
            raise SessionLockTimeout(session_id, 60.0, "another process")
        return "exec-2"

    monkeypatch.setattr(run_control, "is_turn_running", lambda _sid: False)
    monkeypatch.setattr(owner_turn, "start_owner_turn", start)
    assert owner_turn.start_owner_turn_when_free("s", "main", "wake", wait_s=5, poll_s=0.01) == "exec-2"
    assert attempts == ["s", "s"]
