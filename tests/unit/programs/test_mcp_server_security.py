from __future__ import annotations

import asyncio

import pytest


def _mcp_request(authority: dict, **extra):
    from openprogram.agent.dispatcher import TurnRequest
    from openprogram.agent.session_config import PermissionRules

    return TurnRequest(
        session_id="mcp-session",
        user_text="",
        agent_id="main",
        source="mcp",
        permission_mode="bypass",
        permission_rules=PermissionRules(
            allow=[
                "write_file",
                "edit_file",
                "apply_patch",
            ]
        ),
        **authority,
        **extra,
    )


def _recording_tool(name: str, calls: list[dict]):
    from openprogram.agent.types import AgentTool, AgentToolResult
    from openprogram.providers.types import TextContent

    async def execute(_call_id, args, _cancel, _on_update):
        calls.append(dict(args))
        return AgentToolResult(content=[TextContent(text="RAN")])

    return AgentTool(
        name=name,
        description=name,
        parameters={},
        label=name,
        execute=execute,
    )


def _run(tool, args):
    return asyncio.run(tool.execute("c1", args, None, None))


@pytest.fixture
def mcp_authority(tmp_path, monkeypatch):
    from openprogram import paths
    from openprogram.agent import authority

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    authority._reset_owner_cache_for_tests()
    return authority.mcp_client_authority("0123456789abcdef")


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("write_file", {"path": "../outside.txt", "content": "secret"}),
        ("edit_file", {"path": "../outside.txt", "old": "a", "new": "b"}),
        (
            "apply_patch",
            {
                "patch": "*** Begin Patch\n*** Add File: ../outside.txt\n+x\n*** End Patch",
            },
        ),
    ],
)
def test_mcp_outside_writes_are_denied_before_allow_and_bypass(
    tmp_path,
    monkeypatch,
    mcp_authority,
    tool_name,
    arguments,
):
    from openprogram.agent.permissions.approval import wrap_with_approval
    from openprogram.worktree.context import reset_worktree, set_worktree

    project = tmp_path / "project"
    project.mkdir()
    token = set_worktree(str(project))
    calls = []
    try:
        req = _mcp_request(mcp_authority)
        wrapped = wrap_with_approval(
            _recording_tool(tool_name, calls),
            req,
            lambda _e: None,
        )
        result = _run(wrapped, arguments)
    finally:
        reset_worktree(token)

    assert result.is_error is True
    assert result.details["reason_code"] == "HARD_CONSTRAINT_DENIED"
    assert calls == []


def test_mcp_write_in_additional_working_directory_reaches_authority_gate(
    tmp_path,
    monkeypatch,
    mcp_authority,
):
    from openprogram.agent.permissions.approval import wrap_with_approval
    from openprogram.worktree.context import reset_worktree, set_worktree

    project = tmp_path / "project"
    extra = tmp_path / "extra"
    project.mkdir()
    extra.mkdir()
    token = set_worktree(str(project))
    calls = []
    try:
        req = _mcp_request(
            mcp_authority,
            additional_working_dirs=[str(extra)],
        )
        wrapped = wrap_with_approval(
            _recording_tool("write_file", calls),
            req,
            lambda _e: None,
        )
        result = _run(
            wrapped,
            {
                "path": str(extra / "inside.txt"),
                "content": "ok",
            },
        )
    finally:
        reset_worktree(token)

    assert result.is_error is True
    assert result.details["reason_code"] == "AUTHORITY_CAPABILITY_DENIED"
    assert calls == []


def test_mcp_relative_write_in_bound_worktree_reaches_authority_gate(
    tmp_path,
    monkeypatch,
    mcp_authority,
):
    from openprogram.agent.permissions.approval import wrap_with_approval
    from openprogram.worktree.context import reset_worktree, set_worktree

    project = tmp_path / "project"
    process_cwd = tmp_path / "process-cwd"
    project.mkdir()
    process_cwd.mkdir()
    monkeypatch.chdir(process_cwd)
    token = set_worktree(str(project))
    calls = []
    try:
        req = _mcp_request(mcp_authority)
        wrapped = wrap_with_approval(
            _recording_tool("write_file", calls),
            req,
            lambda _e: None,
        )
        result = _run(
            wrapped,
            {
                "path": "inside.txt",
                "content": "ok",
            },
        )
    finally:
        reset_worktree(token)

    assert result.is_error is True
    assert result.details["reason_code"] == "AUTHORITY_CAPABILITY_DENIED"
    assert calls == []


@pytest.mark.parametrize("authority_kind", ["paired", "owner-shaped"])
def test_mcp_cannot_keep_worktree_before_authority_or_bypass(
    mcp_authority,
    authority_kind,
):
    from openprogram.agent import authority
    from openprogram.agent.permissions.approval import wrap_with_approval
    from openprogram.agent.questions import get_question_registry

    request_authority = mcp_authority
    if authority_kind == "owner-shaped":
        request_authority = authority.local_owner_authority()
    calls = []
    events = []
    req = _mcp_request(request_authority)
    wrapped = wrap_with_approval(
        _recording_tool("worktree_keep", calls),
        req,
        events.append,
    )

    result = _run(wrapped, {"worktree_id": "wt-1"})

    assert result.is_error is True
    assert result.details["reason_code"] == "HARD_CONSTRAINT_DENIED"
    assert calls == []
    assert events == []
    assert get_question_registry().list_pending("mcp-session") == []
