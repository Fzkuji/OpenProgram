"""Agents defined as Markdown files (.claude/agents, .agents/agents) are
read-only Agents other agents may start. No model requests."""
from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.runtime.agent_registry import delegation, manager, markdown_imports


def _write(folder: Path, name: str, text: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


REVIEWER = """---
name: code-reviewer
description: Reviews a diff for bugs.
  Reports file and line.
tools: Read, Grep, Glob, Bash(git diff:*), Unknown
model: sonnet
---
You review code. Change nothing.
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    _write(root / ".claude" / "agents", "code-reviewer.md", REVIEWER)
    _write(root / ".agents" / "agents", "writer.md",
           "---\nname: Writer\ndescription: Writes docs.\nisolation: worktree\n"
           "model: anthropic/claude-x\n---\nWrite docs.\n")
    _write(root / ".claude" / "agents", "broken.md", "no frontmatter here\n")
    _write(root / ".claude" / "agents", "nameless.md", "---\ndescription: x\n---\nbody\n")
    monkeypatch.setattr("openprogram.state.worktree.context.current_worktree_path", lambda: str(root / "src"))
    monkeypatch.setattr(manager, "list_all", lambda: [manager.AgentSpec(id="main", name="Assistant")])
    return root


def test_markdown_files_become_read_only_agents(project):
    specs = {spec.id: spec for spec in markdown_imports.list_imports()}
    assert set(specs) == {"md:code-reviewer", "md:writer"}
    reviewer = specs["md:code-reviewer"]
    assert reviewer.name == "code-reviewer"
    assert reviewer.description == "Reviews a diff for bugs. Reports file and line."
    assert reviewer.system_prompt == "You review code. Change nothing."
    assert reviewer.tools == {"mode": "selected", "allowed": ["read", "grep", "glob", "bash"]}
    # "sonnet" names no provider: the conversation's model applies.
    assert (reviewer.model.provider, reviewer.model.id) == ("", "")
    writer = specs["md:writer"]
    assert writer.isolation == "worktree" and writer.tools == {"mode": "automatic"}
    assert (writer.model.provider, writer.model.id) == ("anthropic", "claude-x")


def test_they_are_in_the_roster_and_found_by_name(project):
    assert ("md:code-reviewer", "code-reviewer", "Reviews a diff for bugs. Reports file and line.") \
        in delegation.roster()
    assert delegation.find("Writer").id == "md:writer"
    assert manager.get("md:writer").name == "Writer"
    assert manager.get("md:nobody") is None


def test_a_saved_agent_wins_its_name(project, monkeypatch):
    monkeypatch.setattr(manager, "list_all", lambda: [manager.AgentSpec(id="writer", name="writer")])
    assert delegation.find("writer").id == "writer"
    assert [spec.id for spec in delegation.delegatable()] == ["writer", "md:code-reviewer"]


def test_they_are_never_written(project):
    with pytest.raises(manager.AgentNotFound):
        manager.update("md:writer", {"name": "x"})
    with pytest.raises(manager.AgentNotFound):
        manager.set_default("md:writer")
    manager.delete("md:writer")  # nothing saved, nothing removed
    assert (project / ".agents" / "agents" / "writer.md").exists()


def test_the_project_file_wins_over_home(tmp_path):
    project, home = tmp_path / "p", tmp_path / "h"
    _write(project / ".claude" / "agents", "scout.md", "---\nname: Scout\ndescription: project\n---\n")
    _write(home / ".claude" / "agents", "scout.md", "---\nname: Scout\ndescription: home\n---\n")
    (spec,) = markdown_imports.list_imports([project, home])
    assert spec.description == "project"
