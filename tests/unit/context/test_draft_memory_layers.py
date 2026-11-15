"""A new chat's draft context carries its project's MEMORY.md.

Before the first message there is no session to name the project, so the
draft is built for the project the chat has selected: personal memory
first, the project's after it, as the chat will inject them.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from openprogram.runtime.context import session_stats as stats


@pytest.fixture
def env(tmp_path, monkeypatch):
    import openprogram.runtime.conversation.model_tools as model_tools
    import openprogram.runtime.context.tokens as tokens
    import openprogram.state.paths as paths
    import openprogram.programs as programs
    from openprogram.state import config as user_config
    from openprogram.state.memory import store
    from openprogram.state.memory.project_layer import project_memory_root
    from openprogram.state.store.project import project_store

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(user_config, "read_config", lambda: {})
    monkeypatch.setattr(programs, "agent_tools", lambda **_kw: [])
    monkeypatch.setattr(model_tools, "load_agent_profile",
                        lambda _id: {"id": "main", "name": "Main"})
    monkeypatch.setattr(tokens, "real_context_window", lambda _model: 200_000)
    projects = {
        "p1": SimpleNamespace(id="p1", path=str(tmp_path), is_default=False),
        "default": SimpleNamespace(id="default", path=str(tmp_path), is_default=True),
    }
    monkeypatch.setattr(project_store, "get_project", projects.get)

    personal = store.ensure()
    (personal / "MEMORY.md").write_text("Personal map: tea.\n", encoding="utf-8")
    project = project_memory_root("p1")
    project.mkdir(parents=True, exist_ok=True)
    (project / "MEMORY.md").write_text("Project map: the build.\n", encoding="utf-8")
    return SimpleNamespace(personal=personal, project=project)


def test_a_draft_in_a_project_shows_personal_then_project_memory(env):
    bd = stats.compute_draft_breakdown("main", "p", "m", project_id="p1")

    rows = bd["memory_detail"]
    assert [r["path"] for r in rows] == ["personal/MEMORY.md", "project/MEMORY.md"]
    assert "tea" in rows[0]["text"] and "build" in rows[1]["text"]


def test_a_draft_in_the_default_project_shows_one_memory(env):
    for project_id in ("default", ""):
        bd = stats.compute_draft_breakdown("main", "p", "m", project_id=project_id)
        assert [r["path"] for r in bd["memory_detail"]] == ["MEMORY.md"]
        assert "tea" in bd["memory_detail"][0]["text"]


def test_the_selected_project_does_not_outlive_the_draft(env):
    from openprogram.state.memory.project_layer import project_root

    stats.compute_draft_breakdown("main", "p", "m", project_id="p1")
    assert project_root() is None
