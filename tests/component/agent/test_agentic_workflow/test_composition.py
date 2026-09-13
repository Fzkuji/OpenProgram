"""workflow composition tests."""
from __future__ import annotations
from ._support import (
    Path,
    TL,
    _executor,
    _install_workflow_project,
    _instance,
    _llm_executor,
    _package_project,
    _planner,
    _project,
    _run_task,
    _state,
    _summarizer,
    json,
    pytest,
    session_repo,
)


def test_workflow_import_catalog_ignores_non_project_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workflows"
    project = root / "literature_review"
    (project / ".git").mkdir(parents=True)
    (root / "__pycache__").mkdir()
    visited: list[Path] = []

    def checkout(path: Path) -> tuple[dict, str]:
        visited.append(path)
        return ({
            "project_metadata": {
                "entrypoint": "literature_review",
                "summary": "Review literature",
            },
        }, "abc123")

    monkeypatch.setattr(TL, "_workflow_projects_root", lambda: root)
    monkeypatch.setattr(TL, "_checkout_head", checkout)

    assert TL._workflow_import_catalog() == (
        "- from workflows.literature_review import literature_review"
        "  # Review literature @ abc123"
    )
    assert visited == [project]



def test_package_rejects_relative_import_outside_own_package() -> None:
    candidate = json.loads(_package_project())
    candidate["files"]["steps/discover.py"] = (
        "from ...other_workflow import run\n\n"
        "def discover(task):\n"
        "    return run(task)\n"
    )

    with pytest.raises(TL.InvalidWorkflow, match="import is not allowed"):
        TL._validate_project_candidate(candidate)



def test_package_accepts_registered_vanilla_function_import() -> None:
    candidate = json.loads(_package_project())
    candidate["files"]["steps/discover.py"] = (
        "from openprogram.programs.tools.web.web_search "
        "import execute as web_search\n\n"
        "def discover(task):\n"
        "    return web_search(query=task, provider='arxiv')\n"
    )

    validated = TL._validate_project_candidate(candidate)

    assert "steps/discover.py" in validated["files"]



def test_public_entry_executes_static_workflow_dependency(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _dependency, dependency_revision = _install_workflow_project(
        session_repo,
        _project(
            name="paper_search",
            summary="Search papers",
            files={
                "steps/search.py": (
                    "def search(task):\n"
                    "    return agent('search ' + task)\n"
                ),
                "entry.py": (
                    "def workflow(task):\n"
                    "    return search(task)\n"
                ),
            },
        ),
    )
    parent = json.loads(_package_project())
    parent["files"]["steps/discover.py"] = (
        "from workflows.paper_search import paper_search\n\n"
        "def discover(task):\n"
        "    return paper_search(task)\n"
    )
    _planner(
        monkeypatch,
        json.dumps({"action": "create"}),
        json.dumps(parent),
    )
    calls = _executor(monkeypatch)
    _summarizer(monkeypatch)

    result = _run_task("recent papers")

    assert result["status"] == "completed"
    assert [call["prompt"] for call in calls] == ["search recent papers"]
    state = _state(session_repo, result["run_id"])
    assert state["workflow_dependencies"] == {
        "paper_search": dependency_revision,
    }
    snapshot = _instance(session_repo, result["run_id"]) / "snapshot"
    assert (snapshot / "workflows" / "literature_review" / "workflow.py").exists()
    assert (snapshot / "workflows" / "paper_search" / "workflow.py").exists()
    assert [item["function"] for item in state["items"]] == ["agent"]



def test_author_prompt_lists_reusable_workflow_import(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _candidate, revision = _install_workflow_project(
        session_repo,
        _project(name="paper_search", summary="Search papers"),
    )

    prompt = TL._author_prompt("review papers", {})

    assert "from workflows.paper_search import paper_search" in prompt
    assert "Search papers" in prompt
    assert revision in prompt



def test_static_workflow_dependency_must_exist(session_repo: Path) -> None:
    candidate = json.loads(_package_project())
    candidate["files"]["steps/discover.py"] = (
        "from workflows.missing_workflow import missing_workflow\n\n"
        "def discover(task):\n"
        "    return missing_workflow(task)\n"
    )

    with pytest.raises(
        TL.InvalidWorkflow,
        match="workflow dependency missing_workflow is unavailable",
    ):
        TL._resolve_workflow_dependencies(
            TL._validate_project_candidate(candidate)
        )



def test_static_workflow_dependency_cycle_is_rejected(session_repo: Path) -> None:
    candidate = json.loads(_package_project())
    candidate["files"]["steps/discover.py"] = (
        "from workflows.literature_review import literature_review\n\n"
        "def discover(task):\n"
        "    return literature_review(task)\n"
    )

    with pytest.raises(
        TL.InvalidWorkflow,
        match=(
            "workflow dependency cycle: "
            "literature_review -> literature_review"
        ),
    ):
        TL._resolve_workflow_dependencies(
            TL._validate_project_candidate(candidate)
        )



def test_workflow_dependency_snapshot_keeps_resolved_commit(
    session_repo: Path,
) -> None:
    initial = _project(
        name="paper_search",
        summary="Search papers",
        files={
            "steps/search.py": "def search(task):\n    return 'initial'\n",
            "entry.py": "def workflow(task):\n    return search(task)\n",
        },
    )
    _candidate, initial_revision = _install_workflow_project(
        session_repo, initial,
    )
    parent = json.loads(_package_project())
    parent["files"]["steps/discover.py"] = (
        "from workflows.paper_search import paper_search\n\n"
        "def discover(task):\n"
        "    return paper_search(task)\n"
    )
    parent_candidate = TL._validate_project_candidate(parent)
    instance = session_repo / "fixed-dependency-run"
    instance.mkdir()

    dependencies = TL._replace_snapshot(instance, parent_candidate)

    revised = TL._validate_project_candidate(json.loads(_project(
        name="paper_search",
        summary="Search papers",
        files={
            "steps/search.py": "def search(task):\n    return 'revised'\n",
            "entry.py": "def workflow(task):\n    return search(task)\n",
        },
    )))
    revision_instance = session_repo / "paper-search-revision"
    revision_instance.mkdir()
    TL._replace_snapshot(revision_instance, revised)
    _project_id, revised_revision = TL._publish_snapshot(
        revision_instance,
        project_id="paper_search",
        action="revise",
        metadata=revised["project_metadata"],
    )

    assert dependencies == {"paper_search": initial_revision}
    assert revised_revision != initial_revision
    snapshot_source = (
        instance / "snapshot" / "workflows" / "paper_search"
        / "steps" / "search.py"
    ).read_text(encoding="utf-8")
    assert "initial" in snapshot_source
    assert "revised" not in snapshot_source

    rebuilt_dependencies = TL._replace_snapshot(
        instance,
        parent_candidate,
        pinned_dependencies=dependencies,
    )

    assert rebuilt_dependencies == {"paper_search": initial_revision}
    rebuilt_source = (
        instance / "snapshot" / "workflows" / "paper_search"
        / "steps" / "search.py"
    ).read_text(encoding="utf-8")
    assert "initial" in rebuilt_source
    assert "revised" not in rebuilt_source



def test_package_import_does_not_replace_process_tool_registrations(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    from openprogram.agentic_programming import function as function_runtime
    from openprogram.programs import _runtime as tool_runtime

    name = "literature_review"
    before_agentic = function_runtime._registry.get(name)  # noqa: SLF001
    before_tool = tool_runtime._registry.get(name)  # noqa: SLF001
    before_unexposed = name in tool_runtime._unexposed  # noqa: SLF001
    _planner(
        monkeypatch,
        json.dumps({"action": "create"}),
        _package_project(),
    )
    _executor(monkeypatch)
    _summarizer(monkeypatch)

    _run_task("recent papers")

    assert function_runtime._registry.get(name) is before_agentic  # noqa: SLF001
    assert tool_runtime._registry.get(name) is before_tool  # noqa: SLF001
    assert (name in tool_runtime._unexposed) is before_unexposed  # noqa: SLF001



def test_parameterized_project_can_compose_llm_agent_and_goal(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    project = _project(files={
        "steps/run.py": (
            "def run(task):\n"
            "    plan = llm('plan ' + task)\n"
            "    result = agent('execute ' + plan)\n"
            "    return goal('verify ' + result + ' until complete')\n"
        ),
        "entry.py": "def workflow(task):\n    return run(task)\n",
    })
    _planner(monkeypatch, json.dumps({"action": "create"}), project)
    llm_calls = _llm_executor(monkeypatch, lambda _prompt, _kwargs: "the plan")
    agent_calls = _executor(monkeypatch, lambda _prompt, _kwargs: "the result")
    goal_calls: list[str] = []

    def fake_goal(prompt: str) -> str:
        goal_calls.append(prompt)
        return "verified"

    monkeypatch.setattr(TL, "_goal_function", lambda: fake_goal)
    monkeypatch.setattr(
        TL,
        "_summarize_workflow",
        lambda _state: {"summary": "Completed.", "return_result": False},
    )

    result = _run_task("recent papers")

    assert result["status"] == "completed"
    assert [call["prompt"] for call in llm_calls] == ["plan recent papers"]
    assert [call["prompt"] for call in agent_calls] == ["execute the plan"]
    assert goal_calls == ["verify the result until complete"]
    assert [
        item["function"] for item in _state(session_repo, result["run_id"])["items"]
    ] == ["llm", "agent", "goal"]



def test_run_published_workflow_uses_pinned_dependency_not_active_head(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _leaf, leaf_revision = _install_workflow_project(
        session_repo,
        _project(
            name="paper_search",
            summary="Search papers",
            files={
                "steps/search.py": "def search(task):\n    return 'initial'\n",
                "entry.py": "def workflow(task):\n    return search(task)\n",
            },
        ),
    )
    parent_payload = json.loads(_package_project())
    parent_payload["files"]["steps/discover.py"] = (
        "from workflows.paper_search import paper_search\n\n"
        "def discover(task):\n"
        "    return paper_search(task)\n"
    )
    parent_candidate = TL._validate_project_candidate(parent_payload)
    author_instance = session_repo / "parent-author"
    author_instance.mkdir()
    parent_dependencies = TL._replace_snapshot(author_instance, parent_candidate)
    _project_id, parent_revision = TL._publish_snapshot(
        author_instance,
        project_id="literature_review",
        action="create",
        metadata=parent_candidate["project_metadata"],
        workflow_dependencies=parent_dependencies,
    )
    assert parent_dependencies == {"paper_search": leaf_revision}

    revised_leaf = TL._validate_project_candidate(json.loads(_project(
        name="paper_search",
        summary="Search papers",
        files={
            "steps/search.py": "def search(task):\n    return 'revised'\n",
            "entry.py": "def workflow(task):\n    return search(task)\n",
        },
    )))
    leaf_instance = session_repo / "leaf-revised"
    leaf_instance.mkdir()
    TL._replace_snapshot(leaf_instance, revised_leaf)
    _leaf_id, revised_leaf_revision = TL._publish_snapshot(
        leaf_instance,
        project_id="paper_search",
        action="revise",
        metadata=revised_leaf["project_metadata"],
        workflow_dependencies=TL._replace_snapshot(leaf_instance, revised_leaf),
    )
    assert revised_leaf_revision != leaf_revision

    _executor(monkeypatch, lambda prompt, _kwargs: prompt.split()[-1])
    _summarizer(monkeypatch, "Completed pinned dependency run.")

    result = TL._run_published_workflow(
        "find papers",
        _project_id,
        parent_revision,
        session_id=TL.current_session_id(),
        spawn_caller=None,
    )

    assert result["status"] == "completed"
    assert _state(session_repo, result["run_id"])["result"] == "initial"
    assert result["workflow_dependencies"] == {"paper_search": leaf_revision}

