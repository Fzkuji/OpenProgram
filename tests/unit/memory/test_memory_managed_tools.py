from __future__ import annotations

import asyncio

import pytest


@pytest.fixture
def managed(tmp_path):
    from openprogram.state.memory.management.tools import management_tools
    from openprogram.state.memory.management.workspace import MemoryWorkspace

    workspace = MemoryWorkspace(tmp_path / "memory")
    audit = []
    try:
        yield workspace, audit, {
            definition.name: definition.handler
            for definition in management_tools(workspace, audit)
        }
    finally:
        workspace.close()


def _call(handler, **arguments):
    return asyncio.run(handler(arguments))


def test_nested_agent_receives_managed_file_replacements(managed):
    workspace, _, handlers = managed
    assert {"Read", "Write", "Edit", "Grep", "Glob", "shell"} <= set(handlers)

    path = workspace.stage_dir / "topics" / "facts.md"
    written = _call(handlers["Write"], file_path=str(path), content="alpha\n")
    assert not written["is_error"]
    edited = _call(
        handlers["Edit"], file_path=str(path), old_string="alpha",
        new_string="beta",
    )
    assert not edited["is_error"]
    read = _call(handlers["Read"], file_path=str(path))
    assert not read["is_error"] and "beta" in read["content"][0]["text"]


def test_managed_file_tools_reject_escape(managed, tmp_path):
    _workspace, _, handlers = managed
    outside = tmp_path / "outside.txt"
    escaped = _call(handlers["Write"], file_path=str(outside), content="x")
    assert escaped["is_error"]
    assert not outside.exists()


def test_the_owners_batch_runs_without_a_sandbox_where_there_is_none(managed, monkeypatch):
    from openprogram.host import sandbox

    _, _, handlers = managed
    monkeypatch.setattr(sandbox, "unavailable_reason", lambda: "missing sandbox")
    result = _call(handlers["shell"], command="true")
    assert not result["is_error"]


def test_someone_elses_words_get_no_shell_without_a_sandbox(tmp_path, monkeypatch):
    from openprogram.host import sandbox
    from openprogram.state.memory.management.tools import management_tools
    from openprogram.state.memory.management.workspace import MemoryWorkspace, foreign_source

    monkeypatch.setattr(sandbox, "unavailable_reason", lambda: "missing sandbox")
    with foreign_source(True):
        workspace = MemoryWorkspace(tmp_path / "memory")
    try:
        handlers = {tool.name: tool.handler for tool in management_tools(workspace, [])}
        marker = tmp_path / "ran"
        result = _call(handlers["shell"], command=f"touch {marker}")
    finally:
        workspace.close()
    assert result["is_error"] and "missing sandbox" in result["content"][0]["text"]
    assert not marker.exists()
