from __future__ import annotations

import asyncio

import pytest


@pytest.fixture
def managed(tmp_path):
    from openprogram.memory.scriptorium.management.tools import management_tools
    from openprogram.memory.scriptorium.management.workspace import MemoryWorkspace

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


def test_managed_file_tools_reject_escape_and_source_mutation(managed, tmp_path):
    workspace, _, handlers = managed
    outside = tmp_path / "outside.txt"
    escaped = _call(handlers["Write"], file_path=str(outside), content="x")
    assert escaped["is_error"]
    assert not outside.exists()

    source = workspace.stage_dir / "sources" / "evidence.txt"
    source.parent.mkdir(exist_ok=True)
    source.write_text("evidence")
    denied = _call(handlers["Write"], file_path=str(source), content="changed")
    assert denied["is_error"]
    assert source.read_text() == "evidence"

    case_variant = workspace.stage_dir / "Sources" / "forged.txt"
    denied = _call(
        handlers["Write"], file_path=str(case_variant), content="changed",
    )
    assert denied["is_error"]
    assert not case_variant.exists()


def test_managed_shell_requires_the_os_sandbox(managed, monkeypatch):
    from openprogram import sandbox

    _, _, handlers = managed
    monkeypatch.setattr(sandbox, "unavailable_reason", lambda: "missing sandbox")
    result = _call(handlers["shell"], command="true")
    assert result["is_error"]
    assert "missing sandbox" in result["content"][0]["text"]
