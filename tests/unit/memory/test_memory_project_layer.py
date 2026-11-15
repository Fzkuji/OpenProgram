"""A project's own Memory is read beside the personal one, behind switches.

Both switches are on by default; the fixture starts with them off so each
test turns on what it exercises.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest



def _note(fact: str, block: str) -> str:
    return (
        "# Note\n"
        "\n"
        f"{fact}[^e-1f4c7a2b90] ^{block}\n"
        "\n"
        "[^e-1f4c7a2b90]: Time: `2026-01-01`; "
        "Sources: [D1:1](/s/s1#d1-1)\n"
    )


def _seed(root: Path, fact: str, block: str) -> None:
    (root / "topics").mkdir(parents=True, exist_ok=True)
    (root / "topics" / "note.md").write_text(_note(fact, block), encoding="utf-8")


OWNER = {
    "speaker_kind": "owner",
    "speaker_id": "owner/local",
    "speaker_display": "Owner",
    "principal_id": "owner/install/0123456789abcdef",
    "authority_tier": "owner",
    "interaction": "interactive",
}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """Personal memory, a registered project, and switchable layer settings."""
    import openprogram.state.paths as paths
    from openprogram.state import config as user_config
    from openprogram.runtime.execution import run_control as execution_run_control
    from openprogram.state.memory import store
    from openprogram.state.store.project import project_store

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    settings: dict = {"memory": {"layers": {"project": False, "project_writes": False}}}
    monkeypatch.setattr(user_config, "read_config", lambda: settings)
    folder = tmp_path / "repo"
    folder.mkdir()
    projects = {
        "s-project": SimpleNamespace(id="p1", path=str(folder), is_default=False),
        "s-adhoc": SimpleNamespace(id="default", path=str(tmp_path), is_default=True),
    }
    monkeypatch.setattr(project_store, "project_for_session", projects.get)
    monkeypatch.setattr(project_store, "list_projects", lambda: list(projects.values()))
    token = execution_run_control.set_current_session_id("s-project")
    personal = store.ensure()
    _seed(personal, "Personal: the user prefers tea.", "abc11111")
    try:
        yield SimpleNamespace(
            settings=settings,
            personal=personal,
            project=(tmp_path / "state" / "projects" / "p1" / "memory").resolve(),
        )
    finally:
        execution_run_control.reset_current_session_id(token)


def _turn_on(env, *, writes=False):
    env.settings["memory"]["layers"] = {"project": True, "project_writes": writes}


def _seed_project(env):
    from openprogram.state.memory import project_layer, store

    with project_layer.use(env.project, write=True):
        store.ensure()
    _seed(env.project, "Project: the API uses gRPC for transport.", "def22222")


def test_switched_off_reads_personal_and_reading_creates_nothing(setup):
    from openprogram.state.memory import project_layer

    assert project_layer.read_layers().single
    _turn_on(setup)
    # Switched on, but the project has no memory yet: reads stay personal
    # and do not create the project's workspace.
    assert project_layer.read_layers().single
    assert not setup.project.exists()


def test_an_opened_but_empty_project_workspace_adds_no_layer(setup):
    from openprogram.state.memory import project_layer, store

    _turn_on(setup)
    # Opening the project's Memory page initialises its workspace.
    with project_layer.use(setup.project, write=True):
        store.ensure()
    assert (setup.project / "topics").is_dir()
    assert project_layer.read_layers().single
    assert project_layer.project_roots() == []
    _seed_project(setup)
    assert not project_layer.read_layers().single
    assert project_layer.project_roots() == [setup.project]


def test_both_layers_are_on_by_default(setup):
    from openprogram.state.memory.management.config import load_memory_config

    setup.settings["memory"]["layers"] = {}
    config = load_memory_config()
    assert config.project_layer and config.project_writes


def test_personal_memory_is_the_system_projects(setup, tmp_path):
    assert setup.personal == (
        tmp_path / "state" / "projects" / "default" / "memory").resolve()


def test_reads_span_both_layers_with_qualified_paths(setup):
    from openprogram.state.memory import project_layer
    from openprogram.programs.tools.knowledge.memory import memory as tools

    _seed_project(setup)
    _turn_on(setup)
    overview = tools.memory_get()
    assert "project:topics/note.md" in overview
    assert "personal:topics/note.md" in overview
    assert "gRPC" in tools.memory_get(path="project:topics/note.md")
    assert "tea" in tools.memory_get(path="topics/note.md")  # default: personal
    exact = tools.memory_search(query="gRPC", exact=True)
    assert exact.startswith("project:topics/note.md:")

    found = project_layer.read_layers("s-project").search("API transport gRPC tea")
    assert [hit["layer"] for hit in found["results"]] == ["project", "personal"]
    # A conversation outside a project reads personal memory only.
    alone = project_layer.read_layers("s-adhoc").search("API transport gRPC tea")
    assert [hit["path"] for hit in alone["results"]] == ["topics/note.md"]


def test_recall_reads_the_layers_of_its_own_conversation(setup, monkeypatch):
    from openprogram.state.memory import project_layer
    from openprogram.state.memory.local_backend import LocalMemoryBackend

    asked = []

    class Layers:
        def search(self, query, **_):
            return {"results": [{"content": "Project: gRPC.", "trust_state": "trusted"}]}

    monkeypatch.setattr(project_layer, "read_layers", lambda sid=None: asked.append(sid) or Layers())
    assert "gRPC" in LocalMemoryBackend().search("transport", session_id="s-project")
    assert asked == ["s-project"]


def test_layers_join_general_to_specific(setup):
    """Like ``AGENTS.md``: the personal layer first, the project after."""
    from openprogram.state.memory.local_backend import LocalMemoryBackend

    _seed_project(setup)
    (setup.personal / "core.md").write_text("# Core\n\nPersonal core.\n", encoding="utf-8")
    (setup.project / "core.md").write_text("# Core\n\nProject core.\n", encoding="utf-8")
    assert "Project core." not in LocalMemoryBackend().system_prompt()
    _turn_on(setup)
    prompt = LocalMemoryBackend().system_prompt()
    assert prompt.index("Personal core.") < prompt.index("Project core.")


def test_update_names_its_layer_and_status_names_the_default(setup, monkeypatch, chats):
    from openprogram.programs.tools.knowledge.memory import memory as tools

    monkeypatch.setattr(tools, "authority_from_message", lambda *_: OWNER)
    chats.say("s1", "d1-1", "the API moved to HTTP/3")
    _seed_project(setup)
    _turn_on(setup)
    assert "layers: project, personal; writes go to personal" in tools.memory_get()
    accepted = json.loads(tools.memory_update(
        layer="project",
        memory_changes=[{
            "op": "update_record", "memory_id": "def22222",
            "content": "Project: the API moved to HTTP/3.", "time": "2026-01-01",
            "sources": [{"source": "/s/s1#d1-1", "label": "HTTP/3"}],
        }],
    ))
    assert accepted["ok"] is True and accepted["layer"] == "project", accepted
    assert "HTTP/3" in (setup.project / "topics/note.md").read_text(encoding="utf-8")
    assert "tea" in (setup.personal / "topics/note.md").read_text(encoding="utf-8")

    _turn_on(setup, writes=True)
    assert "writes go to project" in tools.memory_get()


def test_update_refuses_a_project_layer_that_is_not_there(setup, monkeypatch):
    from openprogram.programs.tools.knowledge.memory import memory as tools

    monkeypatch.setattr(tools, "authority_from_message", lambda *_: OWNER)
    refused = json.loads(tools.memory_update(
        layer="project",
        memory_changes=[{"op": "delete_record", "memory_id": "abc11111"}],
    ))
    assert refused["error"]["code"] == "INVALID_ARGUMENT"


def test_background_writes_route_to_the_project_and_never_twice(setup):
    from openprogram.state.memory import project_layer, store, writing

    personal_id = store.workspace_id()
    _turn_on(setup)
    with project_layer.write_target("s-project") as target:
        assert target is None and store.root() == setup.personal
    _turn_on(setup, writes=True)
    with project_layer.write_target("s-project") as target:
        assert target == setup.project
        assert store.root() == setup.project
        project_id = store.workspace_id()
        assert project_id != personal_id
        # A turn already written to personal memory is not written again.
        rows = [{"id": "n1", writing.WRITTEN_NODE_MARKER: personal_id},
                {"id": "n2"}]
        assert writing._marked_ids(rows, project_id) == {"n1"}
    assert store.root() == setup.personal
    # Switched back off, turns written to the project are not copied over.
    _turn_on(setup)
    with project_layer.write_target("s-project"):
        rows = [{"id": "n1", writing.WRITTEN_NODE_MARKER: project_id}]
        assert writing._marked_ids(rows, personal_id) == {"n1"}


def test_a_moved_conversation_leaves_its_written_turns_in_the_old_project(setup, monkeypatch, tmp_path):
    """The project it was moved out of keeps the turns it wrote; the new
    project's memory takes only the turns after the move."""
    from openprogram.state.memory import project_layer, store, writing

    old_root = (tmp_path / "state" / "projects" / "p0" / "memory").resolve()
    with project_layer.use(old_root, write=True):
        store.ensure()
        old_id = store.workspace_id()
    from openprogram.runtime.context import session_db
    monkeypatch.setattr(session_db, "default_db", lambda: SimpleNamespace(
        get_session=lambda sid: {"id": sid, "project_history": ["p0", "default"]}))
    _turn_on(setup, writes=True)
    with project_layer.write_target("s-project") as target:
        assert target == setup.project
        rows = [{"id": "n1", writing.WRITTEN_NODE_MARKER: old_id}, {"id": "n2"}]
        assert writing._marked_ids(rows, store.workspace_id()) == {"n1"}
    # Moved home, the personal memory does not take them either.
    with project_layer.write_target("s-adhoc"):
        assert writing._marked_ids(rows, store.workspace_id()) == {"n1"}


def test_nightly_reorganize_covers_project_memory(setup, monkeypatch):
    from openprogram.state.memory import scheduler, store

    _seed_project(setup)
    _turn_on(setup)
    seen = []

    class Backend:
        def reorganize(self, **_):
            seen.append(store.root())
            return {"status": "ok"}

    monkeypatch.setattr(scheduler, "get_backend", lambda: Backend())
    monkeypatch.setattr(
        "openprogram.runtime.agent_registry.manager.list_all", lambda: [],
    )
    scheduler.reorganize_spaces()
    assert seen == [setup.personal, setup.project]


def test_a_chat_edit_in_a_project_with_empty_memory_lands_in_the_project(
    setup, monkeypatch, chats,
):
    """Background writes already go to the project while its memory is
    still empty; an edit from the chat must land in the same place."""
    from openprogram.programs.tools.knowledge.memory import memory as tools

    monkeypatch.setattr(tools, "authority_from_message", lambda *_: OWNER)
    said = chats.link("s1", "d1-1", "the API uses gRPC")
    _turn_on(setup, writes=True)
    assert not setup.project.exists()
    accepted = json.loads(tools.memory_update(memory_changes=[{
        "op": "create_record", "content": "The API uses gRPC.",
        "time": "2026-01-01",
        "sources": [{"source": said, "label": "gRPC"}],
        "destination": {
            "topic_path": "topics/api.md", "headings": [], "position": "end",
        },
    }]))
    assert accepted["ok"] is True and accepted["layer"] == "project", accepted
    assert "gRPC" in (setup.project / "topics/api.md").read_text(encoding="utf-8")
    assert not (setup.personal / "topics/api.md").exists()
    # Personal memory is still read beside it.
    assert "tea" in tools.memory_get(path="personal:topics/note.md")


def test_an_empty_project_adds_no_layer_while_writes_stay_personal(setup):
    from openprogram.state.memory import project_layer

    _turn_on(setup, writes=False)
    assert project_layer.read_layers("s-project").single
    assert not setup.project.exists()


def test_an_empty_project_reads_as_before_while_writes_go_to_it(
    setup, monkeypatch, chats,
):
    """Until the project holds notes, reads and edits of what exists are
    the personal memory's, unqualified, exactly as with project writes
    off; only a new record goes where the background writer puts it."""
    from openprogram.programs.tools.knowledge.memory import memory as tools

    monkeypatch.setattr(tools, "authority_from_message", lambda *_: OWNER)
    said = chats.link("s1", "d1-1", "green tea")
    _turn_on(setup, writes=True)

    assert "personal:" not in tools.memory_get()
    assert "tea" in tools.memory_get(path="topics/note.md")
    assert "personal:" not in tools.memory_search(query="tea")
    updated = json.loads(tools.memory_update(memory_changes=[{
        "op": "update_record", "memory_id": "abc11111",
        "content": "Personal: the user prefers green tea.",
        "time": "2026-01-01",
        "sources": [{"source": said, "label": "tea"}],
    }]))
    assert updated["ok"] is True and "layer" not in updated, updated
    assert "green tea" in (setup.personal / "topics/note.md").read_text()
    # Nothing landed in the project, so its workspace is not created.
    assert not setup.project.exists()
