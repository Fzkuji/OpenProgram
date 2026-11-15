"""The open default at every entry point, and the one-time rewrite of stored data."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from openprogram.models.providers.types import AgentTool, AgentToolResult
from openprogram.operations.migrations import open_permissions
from openprogram.runtime.agent.turn_request import TurnRequest
from openprogram.runtime.permissions.authority import owner_authority, paired_channel_authority, runtime_authority
from openprogram.runtime.permissions.modes import DEFAULT_PERMISSION, resolve_mode
from openprogram.runtime.permissions.policy import permission_decision

OWNER = "owner/install/0123456789abcdef"


async def _run(*_args):
    return AgentToolResult(content=[], details={})


BASH = AgentTool(name="bash", label="bash", description="probe", parameters={"type": "object"}, execute=_run)
BACKGROUND_SOURCES = ["cron", "scheduler", "agent_spawn", "always_on", "job_followup", "merge_turn", "goal"]


@pytest.fixture(autouse=True)
def owner_install(monkeypatch):
    monkeypatch.setattr("openprogram.runtime.permissions.authority.owner_principal_id", lambda: OWNER)
    monkeypatch.setattr("openprogram.runtime.permissions.rule.load_merged_rules", lambda _sid: None)
    monkeypatch.setattr(open_permissions, "_resume_owed", False)


def _background(source, **fields):
    return TurnRequest(session_id="s", user_text="", agent_id="main", source=source,
                       **fields, **runtime_authority(owner_authority(OWNER), source))


def test_the_default_is_unrestricted_and_a_request_starts_there():
    assert DEFAULT_PERMISSION == "bypass"
    assert TurnRequest(session_id="s", user_text="", agent_id="main", source="web").permission_mode == "bypass"


@pytest.mark.parametrize(("session", "project", "mode"), [
    ("ask", "plan", "ask"),
    (None, "plan", "plan"),
    ("not-a-mode", "plan", "plan"),
    ("", "AUTO", "auto"),
    (None, None, "bypass"),
    ("not-a-mode", "also-not", "bypass"),
])
def test_resolve_mode(session, project, mode):
    assert resolve_mode(session, project) == mode


@pytest.mark.parametrize("source", BACKGROUND_SOURCES)
def test_background_owner_work_runs_under_the_default(source):
    verdict = permission_decision(BASH, _background(source), {"command": "ls"})
    assert (verdict.outcome, verdict.code) == ("allow", "MODE_UNRESTRICTED")


@pytest.mark.parametrize("source", BACKGROUND_SOURCES)
def test_background_owner_work_is_refused_under_ask_because_no_one_can_approve(source):
    verdict = permission_decision(BASH, _background(source, permission_mode="ask"), {"command": "ls"})
    # A helper Agent's refusal says to report what it needed to its caller.
    expected = "HELPER_CANNOT_ASK" if source == "agent_spawn" else "NO_ONE_TO_APPROVE"
    assert (verdict.outcome, verdict.code) == ("deny", expected)


def test_an_attended_owner_turn_gets_a_card_under_ask():
    request = TurnRequest(session_id="s", user_text="", agent_id="main", source="web", permission_mode="ask",
                          **owner_authority(OWNER))
    assert permission_decision(BASH, request, {"command": "ls"}).outcome == "ask"


def test_a_paired_peer_stays_limited_under_the_default():
    request = TurnRequest(session_id="s", user_text="", agent_id="main", source="wechat",
                          **paired_channel_authority("wechat", "account", "peer", "Peer"))
    verdict = permission_decision(BASH, request, {"command": "ls"})
    assert verdict.outcome == "deny" and verdict.layer == "identity"


def test_conversation_permission_uses_the_conversations_mode_and_rules(monkeypatch):
    from openprogram.runtime.conversation import session_config
    from openprogram.runtime.permissions.modes import PermissionRules

    rules = PermissionRules(deny=["bash"])
    monkeypatch.setattr(session_config, "load_session_run_config",
                        lambda _sid: session_config.SessionRunConfig(permission_mode=None))
    monkeypatch.setattr(session_config, "project_defaults", lambda _sid: {"permission_mode": "ask"})
    monkeypatch.setattr("openprogram.runtime.permissions.rule.load_merged_rules", lambda _sid: rules)
    assert session_config.conversation_permission("s") == {"permission_mode": "ask", "permission_rules": rules}


def test_standalone_python_is_bound_as_the_owner_in_the_default_mode():
    from openprogram.runtime.agent.turn_request import inner_turn_request
    from openprogram.runtime.agent.runtime_scope import execution_scope

    with execution_scope(runtime=object()):
        inner = inner_turn_request("program")
    assert inner is not None
    assert (inner.permission_mode, inner.authority_tier) == ("bypass", "owner")


def test_acp_starts_in_the_default_and_a_client_can_set_a_mode():
    from openprogram.interfaces.acp.server import ACPServer
    from openprogram.interfaces.cli.parser import build_parser

    assert ACPServer(reader=object(), writer=object())._permission_mode == "bypass"
    assert ACPServer(reader=object(), writer=object(), permission_mode="ask")._permission_mode == "ask"
    assert build_parser().parse_args(["acp"]).permission is None


class _Store:
    def __init__(self, sessions):
        self.sessions = sessions
        self.scans = []

    def list_sessions(self, *, include_archived=False, limit=100):
        self.scans.append(include_archived)
        return [{"id": sid} for sid in self.sessions][:limit]

    def read_disk_meta(self, session_id):
        return self.sessions.get(session_id)

    def clear_session_field(self, session_id, field, *, when):
        meta = self.sessions[session_id]
        if not when(meta):
            return False
        del meta[field]
        return True


@pytest.fixture
def profile(tmp_path, monkeypatch):
    store = _Store({
        "old-default": {"permission_mode": "auto"},
        "owner-chose-auto": {"permission_mode": "auto", "permission_state": {"mode": "auto", "version": 2}},
        "owner-chose-ask": {"permission_mode": "ask"},
        "plain-bypass": {"permission_mode": "bypass"},
        "nothing": {},
    })
    projects = {"a": {"permission_mode": "auto", "thinking_effort": "low"},
                "b": {"permission_mode": "ask"}, "c": {}}
    saved = []

    def save(project_id, settings):
        saved.append(project_id)
        projects[project_id] = settings

    monkeypatch.setattr(open_permissions, "get_state_dir", lambda: tmp_path)
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: store)
    project_store = "openprogram.state.store.project.project_store"
    monkeypatch.setattr(f"{project_store}.list_projects", lambda: [SimpleNamespace(id=pid) for pid in projects])
    monkeypatch.setattr(f"{project_store}.load_project_settings", lambda pid: dict(projects[pid]))
    monkeypatch.setattr(f"{project_store}.save_project_settings", save)
    return SimpleNamespace(state=tmp_path, store=store, projects=projects, saved=saved,
                           marker=tmp_path / "migrations" / "open-permissions.done")


def _open_waits(monkeypatch, waits, *, fail=()):
    """``waits`` is a list of (kind, execution's session or None); returns the forced calls."""
    executions = {f"e{i}": session for i, (_kind, session) in enumerate(waits)}
    records = [SimpleNamespace(kind=kind, execution_id=f"e{i}") for i, (kind, _session) in enumerate(waits)]

    def get_execution(execution_id):
        session = executions[execution_id]
        return SimpleNamespace(session_id=session) if session else None

    service = SimpleNamespace(executions=SimpleNamespace(get_execution=get_execution))
    calls = []

    async def reconcile(session_id, *, service, force):
        calls.append((session_id, service, force))
        if session_id in fail:
            raise RuntimeError("no")

    monkeypatch.setattr("openprogram.runtime.execution.default_control_service", lambda: service)
    monkeypatch.setattr("openprogram.runtime.execution.waits.DurableWaitStore",
                        lambda _store: SimpleNamespace(list_open=lambda: records))
    monkeypatch.setattr("openprogram.runtime.permissions.reconcile_permission_waits", reconcile)
    return service, calls


def test_only_the_old_default_is_cleared_from_stored_data(profile):
    assert open_permissions.run() is True

    assert "permission_mode" not in profile.store.sessions["old-default"]
    assert profile.store.sessions["owner-chose-auto"]["permission_mode"] == "auto"
    assert profile.store.sessions["owner-chose-ask"] == {"permission_mode": "ask"}
    assert profile.store.sessions["plain-bypass"] == {"permission_mode": "bypass"}
    assert profile.store.sessions["nothing"] == {}
    assert profile.store.scans == [True]

    assert profile.projects == {"a": {"thinking_effort": "low"}, "b": {"permission_mode": "ask"}, "c": {}}
    assert profile.saved == ["a"]


def test_the_marker_waits_for_the_approvals_and_then_nothing_runs_again(profile, monkeypatch):
    service, calls = _open_waits(monkeypatch, [
        ("approval", "two"), ("approval", "one"), ("approval", "two"), ("ask", "not-an-approval"), ("approval", None),
    ])
    open_permissions.run()
    assert not profile.marker.exists()

    asyncio.run(open_permissions.resume_open_approvals())
    assert calls == [("one", service, True), ("two", service, True)]
    assert profile.marker.exists()

    profile.projects["a"]["permission_mode"] = "auto"
    assert open_permissions.run() is False
    asyncio.run(open_permissions.resume_open_approvals())
    assert profile.projects["a"]["permission_mode"] == "auto"
    assert len(calls) == 2


def test_a_session_that_cannot_resume_keeps_its_card_and_does_not_hold_the_marker(profile, monkeypatch):
    _service, calls = _open_waits(monkeypatch, [("approval", "bad"), ("approval", "good")], fail={"bad"})
    open_permissions.run()
    asyncio.run(open_permissions.resume_open_approvals())
    assert [call[0] for call in calls] == ["bad", "good"]
    assert profile.marker.exists()


def test_an_interrupted_run_starts_over_without_harm(profile, monkeypatch):
    agent = profile.state / "agents" / "main" / "agent.json"
    agent.parent.mkdir(parents=True)
    agent.write_text(json.dumps({"id": "main", "hold_outbound": True}), encoding="utf-8")
    open_permissions.run()
    # The process stopped before the server finished the run.
    assert open_permissions.run() is True
    assert json.loads(agent.read_text(encoding="utf-8"))["hold_outbound"] is False
    assert "permission_mode" not in profile.store.sessions["old-default"]


def test_a_server_that_did_not_rewrite_the_data_writes_no_marker(profile, monkeypatch):
    _service, calls = _open_waits(monkeypatch, [("approval", "one")])
    asyncio.run(open_permissions.resume_open_approvals())
    assert calls == [] and not profile.marker.exists()
