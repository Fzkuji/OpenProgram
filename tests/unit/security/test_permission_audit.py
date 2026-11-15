"""Public permission boundary: private audit records and delegated file targets."""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

import pytest


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    from openprogram.state import paths
    from openprogram.runtime.permissions import authority
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.events import bus as event_bus
    from openprogram.state.store.session.session_store import SessionStore
    from openprogram.state.worktree.context import reset_worktree, set_worktree

    state = tmp_path / "state"
    monkeypatch.setattr(paths, "get_state_dir", lambda: state)
    # The event log routes to a session directory only when it holds a real
    # session, whichever process-wide store an earlier test left cached.
    SessionStore(state / "projects").create_session("audit-session", "worker")
    authority._reset_owner_cache_for_tests()
    req = TurnRequest(
        session_id="audit-session", user_text="", agent_id="worker",
        source="mcp", permission_mode="bypass",
        **authority.local_owner_authority(),
    )
    project = tmp_path / "project"
    process = tmp_path / "process"
    outside = tmp_path / "outside"
    for directory in (project, process, outside):
        directory.mkdir()
    monkeypatch.chdir(process)
    token = set_worktree(str(project))
    events = []
    bus = event_bus.create_event_bus()
    bus.log_events = True
    bus.subscribe(events.append)
    monkeypatch.setattr(event_bus, "get_event_bus", lambda: bus)
    try:
        yield req, events, state, project, outside
    finally:
        reset_worktree(token)
        authority._reset_owner_cache_for_tests()


def execute(req, tool_name, args):
    from openprogram.runtime.permissions.approval import wrap_with_approval
    from openprogram.models.providers.types import AgentTool, AgentToolResult

    calls = []

    async def run(_call_id, arguments, _cancel, _on_update):
        calls.append(arguments)
        return AgentToolResult(content=[], details={"ran": True})

    tool = AgentTool(
        name=tool_name, description="", parameters={}, label=tool_name,
        execute=run,
    )
    wrapped = wrap_with_approval(tool, req, lambda _event: None)
    result = asyncio.run(wrapped.execute("audit-call", args, None, None))
    return result, calls


@pytest.mark.parametrize("mode", ["bypass", "allow-rule"])
@pytest.mark.parametrize("tool_name,payload", [
    ("bash", {"command": "echo SYNTHETIC_AUDIT_CREDENTIAL_7c02"}),
    ("execute_code", {"code": "print('SYNTHETIC_AUDIT_CREDENTIAL_7c02')"}),
])
def test_delegated_audit_never_records_argument_text(boundary, mode, tool_name, payload):
    from openprogram.runtime.permissions.modes import PermissionRules
    from openprogram.runtime.events import ORIGINS

    req, events, state, _, _ = boundary
    req.source = "agent_spawn"
    if mode == "allow-rule":
        req.permission_mode = "ask"
        req.permission_rules = PermissionRules(allow=[tool_name])
    result, calls = execute(req, tool_name, {**payload, "other": "SYNTHETIC_AUDIT_CREDENTIAL_7c02"})
    assert not result.is_error and len(calls) == 1
    audit = [ev for ev in events if ev.type == "security.risky_tool_delegated"]
    assert audit, "successful delegated permission check must be observable"
    serialized = json.dumps([asdict(ev) for ev in audit])
    assert "SYNTHETIC_AUDIT_CREDENTIAL_7c02" not in serialized
    for ev in audit:
        assert ev.origin in ORIGINS
        assert ev.metadata["session"] == req.session_id
        assert ev.payload["tool"] == tool_name
        assert ev.payload["source"] == "agent_spawn"
    log = state / "projects" / "default" / "chats" / req.session_id / "events.jsonl"
    assert log.exists()
    assert "SYNTHETIC_AUDIT_CREDENTIAL_7c02" not in log.read_text()
    assert '"security.risky_tool_delegated"' in log.read_text()


@pytest.mark.parametrize("tool_name,args", [
    ("write", {"content": "do not write"}),
    ("write_file", {"path": "", "content": "do not write"}),
    ("edit", {"file_path": "   ", "new_string": "do not write"}),
    ("edit_file", {"path": 12, "new_string": "do not write"}),
    ("apply_patch", {"patch": "*** Begin Patch\n*** Add File: \n+x\n*** End Patch"}),
])
def test_delegated_missing_target_is_denied_and_audited(boundary, tool_name, args):
    req, events, _, _, _ = boundary
    result, calls = execute(req, tool_name, args)
    assert result.is_error and not calls
    assert result.details["reason_code"] == "IDENTITY_SCOPE"
    audit = [ev for ev in events if ev.type == "security.path_violation"]
    assert audit
    assert audit[-1].payload["tool"] == tool_name
    assert audit[-1].metadata["session"] == req.session_id
    assert "do not write" not in json.dumps([asdict(ev) for ev in audit])


@pytest.mark.parametrize("target,allowed", [
    ("inside.txt", True),
    ("../outside/file.txt", False),
    ("absolute-link", False),
    ("relative-link", False),
    ("directory-link/file.txt", False),
])
def test_permission_entry_preserves_bound_worktree_and_symlink_containment(boundary, target, allowed):
    req, _, _, project, outside = boundary
    (outside / "file.txt").write_text("original")
    (project / "absolute-link").symlink_to(outside / "file.txt")
    (project / "relative-link").symlink_to("../outside/file.txt")
    (project / "directory-link").symlink_to(outside, target_is_directory=True)
    result, calls = execute(req, "write", {"file_path": target, "content": "replacement"})
    assert bool(calls) is allowed
    assert bool(result.is_error) is not allowed
    if not allowed:
        assert result.details["reason_code"] == "IDENTITY_SCOPE"
    assert (outside / "file.txt").read_text() == "original"


def test_explicit_deny_does_not_emit_allowed_risky_event(boundary):
    from openprogram.runtime.permissions.modes import PermissionRules

    req, events, _, _, _ = boundary
    req.permission_rules = PermissionRules(deny=["bash"])
    result, calls = execute(req, "bash", {"command": "echo never"})
    assert result.is_error and not calls
    assert not any(ev.type == "security.risky_tool_delegated" for ev in events)


def test_unsafe_target_is_audited_without_path_or_content(boundary):
    req, events, state, _, outside = boundary
    secret = "SYNTHETIC_PATH_CREDENTIAL_d63f"
    result, calls = execute(req, "write", {
        "file_path": str(outside / secret), "content": secret,
    })
    assert result.is_error and not calls
    audit = [ev for ev in events if ev.type == "security.path_violation"]
    assert audit and audit[-1].payload["reason"] == "unsafe write target"
    assert secret not in json.dumps([asdict(ev) for ev in audit])
    log = state / "projects" / "default" / "chats" / req.session_id / "events.jsonl"
    assert log.exists() and secret not in log.read_text()


@pytest.mark.parametrize("source", ["agent_spawn", "mcp"])
@pytest.mark.parametrize("tool_name", ["write", "apply_patch"])
def test_a_program_file_is_an_ordinary_write_target(boundary, source, tool_name):
    req, events, state, _, _ = boundary
    req.source = source
    secret = "SYNTHETIC_PROGRAM_PATH_4a98"
    target = f"{state}/programs/{secret}.py"
    args = ({"file_path": target, "content": secret} if tool_name == "write"
            else {"patch": f"*** Begin Patch\n*** Add File: {target}\n+{secret}\n*** End Patch"})
    result, _calls = execute(req, tool_name, args)
    assert (result.details or {}).get("reason_code") != "AGENTIC_PYTHON_WRITE"
    assert not [ev for ev in events if ev.type == "security.path_violation"
                and ev.payload.get("reason") == "protected agentic target"]
    audit = [ev for ev in events if ev.type == "security.path_violation"]
    assert secret not in json.dumps([asdict(ev) for ev in audit])
