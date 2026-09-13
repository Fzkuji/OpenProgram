"""workflow catalog tests."""
from __future__ import annotations
from ._support import (
    Path,
    TL,
    _dir_bytes,
    _executor,
    _git_output,
    _install_workflow_project,
    _planner,
    _project,
    _run_task,
    _summarizer,
    json,
    programs,
    pytest,
    session_repo,
)


def test_workflow_projects_live_under_openprogram_programs() -> None:
    programs_dir = Path(programs.__file__).resolve().parent

    assert TL._workflow_projects_root() == programs_dir / "workflow"



def test_workflow_projects_use_owner_source_catalog(
    tmp_path: Path, monkeypatch
) -> None:
    from openprogram.programs import _programs

    application = (
        tmp_path / "checkout" / "openprogram" / "programs"
        / "applications" / "gui_harness"
    )
    application.mkdir(parents=True)
    monkeypatch.setattr(
        _programs,
        "owner_controlled_program_sources",
        lambda base=None: [{"path": str(application)}],
    )

    assert TL._workflow_projects_root() == application.parent.parent / "workflow"



def test_search_workflows_returns_ranked_readonly_candidates(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _candidate, revision = _install_workflow_project(session_repo, _project())
    _install_workflow_project(
        session_repo,
        _project(
            name="deploy_tool",
            summary="Deploy binaries",
            tags=["deploy"],
        ),
    )
    monkeypatch.setattr(
        TL, "_run_planner_turn",
        lambda *_args, **_kwargs: pytest.fail("search must not call a model"),
    )
    monkeypatch.setattr(
        TL, "_llm_function",
        lambda: pytest.fail("search must not call a model"),
    )
    catalog_before = _dir_bytes(session_repo / "catalog")

    result = TL.search_workflows("research recent papers")

    assert _dir_bytes(session_repo / "catalog") == catalog_before
    assert not (session_repo / "workflows").exists()
    rows = result["workflows"]
    assert [row["workflow_id"] for row in rows] == [
        "research_workflow", "deploy_tool",
    ]
    top = rows[0]
    assert top["revision"] == revision
    assert top["name"] == "research_workflow"
    assert top["summary"] == "Research and synthesize a topic"
    assert top["tags"] == ["research"]
    assert top["retrieval_score"] >= 1
    assert "research" in top["matched_terms"]
    assert top["input_schema"] == {
        "type": "object",
        "properties": {"task": {"type": "string"}},
        "required": ["task"],
    }
    assert top["output_schema"] == {"type": "object"}
    assert top["permissions"] == []



def test_search_workflows_is_registered_as_a_public_tool() -> None:
    from openprogram.programs._runtime import exposed_names, get

    assert get("search_workflows") is not None
    assert "search_workflows" in exposed_names()



def test_load_state_recovers_revision_metadata_from_code_history(
    session_repo: Path,
) -> None:
    instance = session_repo / "workflows" / "run"
    instance.mkdir(parents=True)
    (instance / "code.1.py").write_text("old")
    (instance / "state.json").write_text(json.dumps({
        "task": "t", "status": "running", "items": [], "revisions": []
    }))

    state = TL._load_state(instance / "state.json")

    assert state["revisions"] == [{
        "version": 1,
        "recovered": True,
        "error": "revision recovered from code history",
    }]



def test_repository_metadata_rejects_non_table_entry_points(tmp_path: Path) -> None:
    project = tmp_path / "literature_review"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        "[project]\n"
        'name = "literature_review"\n'
        'version = "0.1.0"\n'
        'description = "Research papers"\n'
        'keywords = []\n'
        'entry-points = "invalid"\n\n'
        "[tool.openprogram]\n"
        'display-name = "Literature review"\n',
        encoding="utf-8",
    )

    with pytest.raises(TL.InvalidWorkflow, match="entry points must be a table"):
        TL._read_repository_metadata(project)



def test_failed_run_does_not_change_existing_catalog_head(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _candidate, head = _install_workflow_project(
        session_repo,
        _project(name="paper_search", summary="Search papers"),
    )
    bad = _project(files={
        "steps/run.py": (
            "def run():\n    raise RuntimeError('broken candidate')\n"
        ),
        "entry.py": "def workflow():\n    return run()\n",
    })
    _planner(monkeypatch, json.dumps({"action": "create"}), bad)
    _executor(monkeypatch)
    _summarizer(monkeypatch, "运行失败。")

    result = _run_task("repair project")

    assert result["status"] == "failed"
    existing = session_repo / "catalog" / "paper_search"
    assert _git_output(existing, "rev-parse", "HEAD") == head
    created = session_repo / "catalog" / result["project_id"]
    assert _git_output(created, "rev-list", "--count", "HEAD") == "1"

