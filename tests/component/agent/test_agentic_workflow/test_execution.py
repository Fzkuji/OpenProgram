"""workflow execution tests."""
from __future__ import annotations
from ._support import (
    Path,
    TL,
    _code,
    _executor,
    _git_output,
    _install_workflow_project,
    _instance,
    _package_project,
    _planner,
    _project,
    _run_task,
    _snapshot_package,
    _state,
    _summarizer,
    inspect,
    json,
    pytest,
    session_repo,
)


def test_agentic_workflow_is_not_registered() -> None:
    from openprogram.agentic_programming import function as agentic_runtime
    from openprogram.programs._runtime import exposed_names, get

    assert get("agentic_workflow") is None
    assert "agentic_workflow" not in exposed_names()
    assert "agentic_workflow" not in agentic_runtime._registry
    assert not hasattr(TL, "agentic_workflow")



def test_small_task_is_persisted_as_a_reusable_multifile_project(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    prompts = _planner(monkeypatch, "SINGLE")
    calls = _executor(monkeypatch)

    result = _run_task("rename one variable")

    assert result["status"] == "completed"
    assert calls[0]["prompt"].startswith("rename one variable")
    assert "save substantive deliverables" in calls[0]["prompt"]
    assert "@agentic_function" in prompts[0]
    snapshot = _snapshot_package(session_repo, result["run_id"])
    assert (snapshot / "workflow.py").exists()
    assert (snapshot / "steps" / "task.py").exists()
    assert not (_instance(session_repo, result["run_id"]) / "code.py").exists()
    assert result["project_id"]
    assert len(result["project_revision"]) == 40
    assert _state(session_repo, result["run_id"])["status"] == "completed"
    assert not (session_repo / "todos.json").exists()



def test_execution_errors_stay_private_in_public_payload(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    private_error = "SUBSTANTIVE_PRIVATE_FINDING_91c2"

    def fail() -> None:
        raise RuntimeError(private_error)

    monkeypatch.setattr(TL, "_registered_agentic_functions", lambda: {"lookup": fail})
    _planner(
        monkeypatch,
        _code('lookup()\nreturn "unreachable"'),
    )
    _summarizer(monkeypatch, "任务失败。")

    result = _run_task("run a failing workflow")
    state = _state(session_repo, result["run_id"])

    assert result["status"] == "failed"
    assert result["revisions"] == []
    assert private_error in json.dumps(state, ensure_ascii=False)
    assert private_error not in json.dumps(result, ensure_ascii=False)
    assert all("error" not in item for item in result["items"])



def test_missing_workflow_is_rejected_with_exact_validation_reason(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    missing = json.loads(_package_project())
    missing["files"]["workflow.py"] = (
        "from openprogram.agentic_programming import agentic_function\n\n"
        "def helper(task):\n    return task\n"
    )
    prompts = _planner(
        monkeypatch,
        json.dumps({"action": "create"}),
        json.dumps(missing),
        _project(files={
            "steps/run.py": "def run(task):\n    return 'ok'\n",
            "entry.py": "def workflow(task):\n    return run(task)\n",
        }),
    )
    _executor(monkeypatch)

    result = _run_task("missing")

    assert result["status"] == "completed"
    assert "must define one @agentic_function literature_review()" in prompts[2]



def test_execution_cap_counts_only_real_calls(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _planner(monkeypatch, _code('''
        for i in range(41):
            agent(f"work-{i}")
    '''))
    calls = _executor(monkeypatch)

    result = _run_task("many")

    assert result["status"] == "capped"
    assert len(calls) == TL.MAX_ITEMS_EXECUTED == 40
    assert len(result["items"]) == 40



def test_agent_uses_existing_spawn_signature(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _planner(monkeypatch, _code('''
        return agent(
            "special", description="delegate", agent_id="research",
            start_from="inherit", archive_when_done=True
        )
    '''))
    calls = _executor(monkeypatch)

    result = _run_task("delegate")

    assert result["status"] == "completed"
    assert calls[0]["description"] == "delegate"
    assert calls[0]["agent_id"] == "research"
    assert calls[0]["start_from"] == "inherit"
    assert calls[0]["archive_when_done"] is True



def test_real_agent_implementation_is_callable_with_public_signature() -> None:
    agent = TL._agent_function("test-session", None)

    assert callable(agent)
    assert str(inspect.signature(agent)) == (
        '(prompt: \'str\', description: \'str\' = \'\', agent_id: \'str\' = \'\', '
        'start_from: \'str\' = \'clean\', run_in_background: \'bool\' = False, '
        'to: \'str\' = \'\', archive_when_done: \'bool\' = False) -> \'str\''
    )
    assert agent("probe").startswith("[agent error]")



def test_new_runs_for_same_task_are_independent(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _planner(
        monkeypatch,
        _project(files={
            "steps/run.py": "def run(task):\n    return agent(task)\n",
            "entry.py": "def workflow(task):\n    return run(task)\n",
        }),
    )
    calls = _executor(monkeypatch)
    created = TL.create_workflow("same")

    first = TL._run_published_workflow(
        "same", created["workflow_id"], created["revision"],
        session_id=TL.current_session_id(), spawn_caller=None,
    )
    second = TL._run_published_workflow(
        "same", created["workflow_id"], created["revision"],
        session_id=TL.current_session_id(), spawn_caller=None,
    )

    assert first["run_id"] != second["run_id"]
    assert [call["prompt"] for call in calls] == ["same", "same"]
    assert _state(session_repo, first["run_id"])["task"] == "same"
    assert _state(session_repo, second["run_id"])["task"] == "same"



def test_capped_status_cannot_be_caught_by_generated_code(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _planner(monkeypatch, _code('''
        try:
            for i in range(41):
                agent(f"work-{i}")
        except RuntimeError:
            return "caught"
    '''))
    calls = _executor(monkeypatch)

    result = _run_task("cap")

    assert result["status"] == "capped"
    assert len(calls) == 40



def test_generated_environment_excludes_runtime_and_agentic_function(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    monkeypatch.setattr(
        TL, "_registered_agentic_functions", lambda: {"registered": lambda: "ok"}
    )
    _planner(monkeypatch, _code('''
        missing = []
        try:
            runtime
        except NameError:
            missing.append("runtime")
        try:
            agentic_function
        except NameError:
            missing.append("agentic_function")
        return agent(",".join(missing) + ":" + registered())
    '''))
    _executor(monkeypatch)
    _summarizer(monkeypatch, "Completed environment validation.")

    result = _run_task("environment")

    assert result["status"] == "completed"
    assert result["summary"] == "Completed environment validation."
    assert [item["function"] for item in result["items"]] == ["registered", "agent"]



def test_public_entry_executes_standard_agentic_function_package(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _planner(
        monkeypatch,
        json.dumps({"action": "create"}),
        _package_project(),
    )
    calls = _executor(monkeypatch)
    _summarizer(monkeypatch)

    result = _run_task("recent papers")

    assert result["status"] == "completed"
    assert result["project_id"] == "literature_review"
    assert [call["prompt"] for call in calls] == ["discover recent papers"]
    project = session_repo / "catalog" / "literature_review"
    assert (project / "__init__.py").exists()
    assert (project / "workflow.py").exists()
    assert (project / "steps" / "discover.py").exists()
    assert (project / "tests" / "test_workflow.py").exists()
    assert not (project / "workflow.json").exists()
    metadata = TL.tomllib.loads(
        (project / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert metadata["project"]["entry-points"]["openprogram.workflows"] == {
        "literature_review": (
            "workflows.literature_review:literature_review"
        ),
    }
    snapshot = _instance(session_repo, result["run_id"]) / "snapshot"
    assert (snapshot / "workflows" / "literature_review" / "workflow.py").exists()



def test_package_execution_uses_agent_loop_primitive_not_sub_agent_adapter(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _planner(
        monkeypatch,
        json.dumps({"action": "create"}),
        _package_project(),
    )
    calls = []

    def agent_loop(prompt: str, **_kwargs) -> str:
        calls.append(prompt)
        return "done"

    monkeypatch.setattr(TL, "_agent_loop_function", lambda: agent_loop)
    monkeypatch.setattr(
        TL,
        "_agent_function",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("package execution used the legacy sub-agent adapter")
        ),
    )
    _summarizer(monkeypatch)

    result = _run_task("recent papers")

    assert result["status"] == "completed"
    assert calls == ["discover recent papers"]



def test_public_entry_snapshots_transitive_workflow_dependencies(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _leaf, leaf_revision = _install_workflow_project(
        session_repo,
        _project(
            name="paper_fetch",
            summary="Fetch paper metadata",
            files={
                "steps/fetch.py": (
                    "def fetch(task):\n"
                    "    return agent('fetch ' + task)\n"
                ),
                "entry.py": "def workflow(task):\n    return fetch(task)\n",
            },
        ),
    )
    _middle, middle_revision = _install_workflow_project(
        session_repo,
        _project(
            name="paper_search",
            summary="Search papers",
            files={
                "steps/search.py": (
                    "from workflows.paper_fetch import paper_fetch\n\n"
                    "def search(task):\n"
                    "    return paper_fetch(task)\n"
                ),
                "entry.py": "def workflow(task):\n    return search(task)\n",
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

    assert [call["prompt"] for call in calls] == ["fetch recent papers"]
    assert result["workflow_dependencies"] == {
        "paper_fetch": leaf_revision,
        "paper_search": middle_revision,
    }
    packages = {
        path.name for path in (
            _instance(session_repo, result["run_id"]) / "snapshot" / "workflows"
        ).iterdir()
    }
    assert packages == {"literature_review", "paper_search", "paper_fetch"}



def test_public_entry_passes_task_to_parameterized_project(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    parameterized = _project(files={
        "steps/discover.py": (
            "def discover(task):\n"
            "    return agent(f'discover {task}')\n"
        ),
        "entry.py": "def workflow(task):\n    return discover(task)\n",
    })
    prompts = _planner(
        monkeypatch,
        json.dumps({"action": "create"}),
        parameterized,
        _project(),
    )
    calls = _executor(monkeypatch)
    _summarizer(monkeypatch)

    result = _run_task("recent papers")

    assert result["status"] == "completed"
    assert len(prompts) == 2
    assert [call["prompt"] for call in calls] == ["discover recent papers"]



def test_capped_project_run_does_not_add_a_revision(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _planner(
        monkeypatch,
        json.dumps({"action": "create"}),
        _project(files={
            "steps/calls.py": (
                "def run_calls():\n"
                "    for i in range(41):\n"
                "        agent(str(i))\n"
            ),
            "entry.py": (
                "def workflow():\n"
                "    return run_calls()\n"
            ),
        }),
    )
    calls = _executor(monkeypatch)

    result = _run_task("capped project")

    assert result["status"] == "capped"
    assert len(calls) == TL.MAX_ITEMS_EXECUTED
    project = session_repo / "catalog" / result["project_id"]
    assert _git_output(project, "rev-list", "--count", "HEAD") == "1"

