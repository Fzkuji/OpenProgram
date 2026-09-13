"""workflow authoring tests."""
from __future__ import annotations
from ._support import (
    Path,
    SimpleNamespace,
    TL,
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
    json,
    mp,
    pytest,
    session_repo,
)


def test_checkout_head_reports_an_invalid_git_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "broken"
    (project / ".git").mkdir(parents=True)
    monkeypatch.setattr(TL, "_git", lambda *_args: "abc123")
    monkeypatch.setattr(
        TL.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout=b"", stderr=b"",
        ),
    )

    with pytest.raises(TL.InvalidWorkflow, match="Git archive is invalid"):
        TL._checkout_head(project)



def test_create_workflow_publishes_one_project_without_executing(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    prompts = _planner(monkeypatch, _project())
    monkeypatch.setattr(
        TL, "_execute_snapshot",
        lambda *_args, **_kwargs: pytest.fail("create must not execute"),
    )
    monkeypatch.setattr(
        TL, "_agent_function",
        lambda *_args: pytest.fail("create must not execute"),
    )

    result = TL.create_workflow("research recent papers")

    assert set(result) == {"workflow_id", "revision"}
    assert result["workflow_id"] == "research_workflow"
    assert len(result["revision"]) == 40
    project = session_repo / "catalog" / "research_workflow"
    assert _git_output(project, "rev-parse", "HEAD") == result["revision"]
    assert [
        path.name for path in (session_repo / "catalog").iterdir()
        if not path.name.startswith(".")
    ] == ["research_workflow"]
    assert not (session_repo / "workflows").exists()
    assert "<reusable_workflows>" in prompts[0]



def test_create_workflow_author_failure_publishes_nothing(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    monkeypatch.setattr(TL, "_run_planner_turn", lambda *_a, **_k: "{}")

    with pytest.raises(TL.InvalidWorkflow, match="failed validation after"):
        TL.create_workflow("research papers")

    assert not (session_repo / "catalog").exists()



def test_create_workflow_cancel_publishes_nothing(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    from openprogram.agentic_programming.function import CancelledError

    def cancel(*_args, **_kwargs) -> str:
        raise CancelledError("stop author")

    monkeypatch.setattr(TL, "_run_planner_turn", cancel)

    with pytest.raises(CancelledError, match="stop author"):
        TL.create_workflow("research papers")

    assert not (session_repo / "catalog").exists()



def test_revise_workflow_publishes_new_revision_and_keeps_old_one(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _candidate, old_revision = _install_workflow_project(
        session_repo, _project(),
    )
    revised = _project(
        summary="Research and verify a topic",
        files={
            "steps/discover.py": (
                "def discover(task):\n"
                "    return agent('discover and verify papers')\n"
            ),
            "entry.py": "def workflow(task):\n    return discover(task)\n",
        },
    )
    prompts = _planner(monkeypatch, revised)
    monkeypatch.setattr(
        TL, "_execute_snapshot",
        lambda *_args, **_kwargs: pytest.fail("revise must not execute"),
    )

    result = TL.revise_workflow("research_workflow", "also verify the papers")

    assert result["workflow_id"] == "research_workflow"
    assert result["revision"] != old_revision
    project = session_repo / "catalog" / "research_workflow"
    assert _git_output(project, "rev-parse", "HEAD") == result["revision"]
    assert _git_output(project, "rev-list", "--count", "HEAD") == "2"
    assert "discover papers" in _git_output(
        project, "show", f"{old_revision}:steps/discover.py",
    )
    assert "<base_project>" in prompts[0]



def test_revise_workflow_rejects_unknown_project(session_repo: Path) -> None:
    with pytest.raises(TL.InvalidWorkflow):
        TL.revise_workflow("missing_workflow", "change it")



def test_create_and_revise_workflow_are_registered_public_tools() -> None:
    from openprogram.programs._runtime import exposed_names, get

    assert get("create_workflow") is not None
    assert get("revise_workflow") is not None
    assert {"create_workflow", "revise_workflow"} <= exposed_names()



def test_invalid_plans_keep_requesting_rewrites_with_concrete_errors(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    syntax_error = json.loads(_package_project())
    syntax_error["files"]["steps/discover.py"] = "def discover(:\n"
    forbidden_import = json.loads(_package_project())
    forbidden_import["files"]["steps/discover.py"] = (
        "import os\n\ndef discover(task):\n    return task\n"
    )
    prompts = _planner(
        monkeypatch,
        json.dumps({"action": "create"}),
        json.dumps(syntax_error),
        json.dumps(forbidden_import),
        _project(files={
            "steps/run.py": "def run(task):\n    return agent('fixed')\n",
            "entry.py": "def workflow(task):\n    return run(task)\n",
        }),
    )
    calls = _executor(monkeypatch)

    result = _run_task("invalid")

    assert result["status"] == "completed"
    assert calls[0]["prompt"] == "fixed"
    assert "SyntaxError" in prompts[2]
    assert "may not use import statements" in prompts[3]
    assert _state(session_repo, result["run_id"])["status"] == "completed"



def test_project_author_prompt_matches_top_level_validator_rules() -> None:
    prompt = TL._author_prompt("research papers", {})

    assert "Plain import statements such as `import json` are forbidden" in prompt
    assert "module top level may contain only" in prompt
    assert "module-level constants or other assignments are forbidden" in prompt
    assert "Standard-library imports such as `pathlib`" in prompt
    assert "delegate filesystem, browser, and other external work" in prompt



def test_project_author_stops_after_bounded_invalid_replies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies: list[str] = []

    def invalid_reply(*_args, **_kwargs) -> str:
        replies.append("invalid")
        return "{}"

    monkeypatch.setattr(TL, "_run_planner_turn", invalid_reply)
    monkeypatch.setattr(TL, "_workflow_import_catalog", lambda: "")

    with pytest.raises(
        TL.InvalidWorkflow,
        match=rf"after {TL.PROJECT_AUTHOR_ATTEMPTS} attempts",
    ):
        TL._request_project_candidate(
            "research papers",
            {},
            session_id="session",
            agent_id="main",
            spawn_caller=None,
        )

    assert len(replies) == TL.PROJECT_AUTHOR_ATTEMPTS



def test_project_execution_failure_never_repairs_or_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = tmp_path / "run"
    (instance / "snapshot").mkdir(parents=True)
    state = {
        "run_id": "run",
        "task": "research papers",
        "status": "running",
        "executions": 0,
        "items": [],
        "revisions": [],
        "result": "",
        "last_error": "",
        "project_id": "",
        "project_action": "create",
        "project_metadata": {"entrypoint": "research_workflow"},
        "workflow_dependencies": {},
    }
    TL._save_state(instance / "state.json", state)
    monkeypatch.setattr(
        TL,
        "_execute_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("broken")),
    )
    monkeypatch.setattr(
        TL, "_request_project_candidate",
        lambda *_args, **_kwargs: pytest.fail("run failure must not re-author"),
    )
    monkeypatch.setattr(
        TL, "_publish_snapshot",
        lambda *_args, **_kwargs: pytest.fail("run failure must not publish"),
    )
    monkeypatch.setattr(
        TL,
        "_summarize_workflow",
        lambda _state: {"summary": "failed", "return_result": False},
    )

    result = TL._run_project_instance_locked(
        instance,
        state,
        run_id="run",
        session_id="session",
        agent_id="main",
        spawn_caller=None,
        functions={},
    )

    assert result["status"] == "failed"
    assert result["revisions"] == []
    persisted = json.loads((instance / "state.json").read_text(encoding="utf-8"))
    assert "RuntimeError: broken" in persisted["last_error"]



def test_planner_prompt_documents_real_agentic_programming_convention(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    prompts = _planner(
        monkeypatch,
        json.dumps({"action": "create"}),
        _project(files={
            "steps/result.py": "def result():\n    return 'ok'\n",
            "entry.py": "def workflow(task):\n    return result()\n",
        }),
    )
    _executor(monkeypatch)
    _summarizer(monkeypatch)

    _run_task("prompt")

    prompt = prompts[-1]
    assert "@agentic_function" in prompt
    assert "runtime.exec" not in prompt
    assert "short_stable_python_name(task: str)" in prompt
    assert "llm, agent, goal" in prompt
    assert "project_metadata" in prompt
    assert "steps/example.py" in prompt
    assert "complete project, not a patch" in prompt
    assert "save substantive deliverables" in prompt
    assert "explicitly asks for the content in chat" in prompt
    assert "Do not return a report body as the workflow handoff" in prompt
    assert "ordinary relative imports" in prompt
    assert "Do not use dynamic" in prompt
    assert "<reusable_workflows>" in prompt



def test_public_entry_creates_and_executes_reusable_multifile_project(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    prompts = _planner(
        monkeypatch,
        json.dumps({"action": "create"}),
        _project(),
    )
    calls = _executor(monkeypatch)
    _summarizer(monkeypatch, "Completed paper discovery.")

    result = _run_task("research recent papers")

    assert result["status"] == "completed"
    assert result["project_id"]
    assert len(result["project_revision"]) == 40
    assert [call["prompt"] for call in calls] == ["discover papers"]
    instance = _instance(session_repo, result["run_id"])
    snapshot = _snapshot_package(session_repo, result["run_id"])
    assert (snapshot / "steps" / "discover.py").exists()
    assert (snapshot / "workflow.py").exists()
    assert (snapshot / "README.md").exists()
    assert (snapshot / "pyproject.toml").exists()
    assert not (snapshot / "workflow.json").exists()
    project = session_repo / "catalog" / result["project_id"]
    assert (project / "steps" / "discover.py").exists()
    assert (project / "pyproject.toml").exists()
    assert _git_output(project, "rev-parse", "HEAD") == result["project_revision"]
    assert "<reusable_workflows>" in prompts[0]



def test_public_entry_publishes_project_as_git_repository(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _planner(
        monkeypatch,
        json.dumps({"action": "create"}),
        _project(),
    )
    _executor(monkeypatch)
    _summarizer(monkeypatch)

    result = _run_task("research recent papers")

    project = TL._workflow_projects_root() / result["project_id"]
    head = _git_output(project, "rev-parse", "HEAD")
    assert (project / ".git").is_dir()
    assert result["project_revision"] == head
    assert not (project / "project.json").exists()
    assert not (project / "revisions").exists()



def test_new_project_rejects_legacy_zero_argument_entry(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    legacy = json.loads(_project())
    legacy["files"]["workflow.py"] = legacy["files"]["workflow.py"].replace(
        "def research_workflow(task):", "def research_workflow():",
    )
    prompts = _planner(
        monkeypatch,
        json.dumps({"action": "create"}),
        json.dumps(legacy),
        _project(),
    )
    _executor(monkeypatch)
    _summarizer(monkeypatch)

    result = _run_task("new task")

    assert result["status"] == "completed"
    assert "must accept exactly one positional task argument" in prompts[2]



@pytest.mark.parametrize("obsolete_reply", (
    "SINGLE",
    "```python\ndef workflow():\n    return 'obsolete'\n```",
))
def test_new_run_rejects_obsolete_planner_protocol(
    monkeypatch: pytest.MonkeyPatch,
    session_repo: Path,
    obsolete_reply: str,
) -> None:
    prompts: list[str] = []
    replies = iter((
        obsolete_reply,
        json.dumps({"action": "create"}),
        _project(),
    ))

    def planner(_sid, prompt, **_kwargs):
        prompts.append(prompt)
        return next(replies)

    monkeypatch.setattr(TL, "_run_planner_turn", planner)
    _executor(monkeypatch)
    _summarizer(monkeypatch)

    result = _run_task("create a reusable workflow")

    assert result["status"] == "completed"
    assert "planner reply was not valid JSON" in prompts[1]
    assert (session_repo / "catalog" / result["project_id"]).exists()
    assert not (_instance(session_repo, result["run_id"]) / "code.py").exists()



def test_entry_only_project_is_rejected_before_execution_and_publish(
    monkeypatch: pytest.MonkeyPatch,
    session_repo: Path,
) -> None:
    prompts = _planner(
        monkeypatch,
        json.dumps({"action": "create"}),
        _project(files={"entry.py": "def workflow():\n    return 'single file'\n"}),
        _project(),
    )
    calls = _executor(monkeypatch)
    _summarizer(monkeypatch)

    result = _run_task("create a maintainable workflow")

    assert result["status"] == "completed"
    assert "at least one helper module" in prompts[2]
    assert [call["prompt"] for call in calls] == ["discover papers"]
    project = session_repo / "catalog" / result["project_id"]
    assert (project / "steps" / "discover.py").exists()



def test_revise_reads_full_active_project_and_preserves_unchanged_file(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    first = _project(files={
        "steps/discover.py": "def discover():\n    return agent('discover papers')\n",
        "steps/shared.py": "def shared():\n    return 'stable'\n",
        "entry.py": "def workflow():\n    return discover() + shared()\n",
    })
    revised = _project(
        summary="Research and verify a topic",
        files={
            "steps/discover.py": "def discover():\n    return agent('discover and verify papers')\n",
            "steps/shared.py": "def shared():\n    return 'stable'\n",
            "entry.py": "def workflow():\n    return discover() + shared()\n",
        },
    )
    prompts = _planner(monkeypatch, first, revised)

    initial = TL.create_workflow("research papers")
    updated = TL.revise_workflow("research_workflow", "also verify the papers")

    assert initial["revision"] != updated["revision"]
    project = session_repo / "catalog" / "research_workflow"
    assert _git_output(project, "rev-list", "--count", "HEAD") == "2"
    assert _git_output(
        project, "show", f"{initial['revision']}:steps/shared.py",
    ) == (project / "steps" / "shared.py").read_text(encoding="utf-8").strip()
    assert "discover papers" in prompts[1]
    assert "steps/shared.py" in prompts[1]
    assert "Reusable research steps" in prompts[1]



def test_concurrent_process_publish_creates_two_git_commits(
    monkeypatch: pytest.MonkeyPatch,
    session_repo: Path,
) -> None:
    if "fork" not in mp.get_all_start_methods():
        pytest.skip("cross-process catalog test requires fork")
    _planner(monkeypatch, json.dumps({"action": "create"}), _project())
    _executor(monkeypatch)
    _summarizer(monkeypatch)
    initial = _run_task("create concurrent project")
    project_id = initial["project_id"]

    candidates = {
        "a": TL._validate_project_candidate(json.loads(_project(
            summary="Concurrent revision A",
            readme="# Concurrent A\n",
            files={
                "steps/run.py": "def run():\n    return 'a'\n",
                "entry.py": "def workflow():\n    return run()\n",
            },
        ))),
        "b": TL._validate_project_candidate(json.loads(_project(
            summary="Concurrent revision B",
            readme="# Concurrent B\n",
            files={
                "steps/run.py": "def run():\n    return 'b'\n",
                "entry.py": "def workflow():\n    return run()\n",
            },
        ))),
    }
    instances = {}
    for label, candidate in candidates.items():
        instance = session_repo / "concurrent" / label
        instance.mkdir(parents=True)
        TL._replace_snapshot(instance, candidate)
        instances[label] = instance

    context = mp.get_context("fork")
    results = context.Queue()

    def publish(label: str) -> None:
        try:
            _project_id, revision = TL._publish_snapshot(
                instances[label],
                project_id=project_id,
                action="revise",
                metadata=candidates[label]["project_metadata"],
            )
            results.put((label, revision, ""))
        except Exception as exc:
            results.put((label, "", f"{type(exc).__name__}: {exc}"))

    processes = [context.Process(target=publish, args=(label,)) for label in candidates]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    published = [results.get(timeout=2) for _ in processes]
    assert all(not error for _label, _revision, error in published)
    assert len({revision for _label, revision, _error in published}) == 2
    project = session_repo / "catalog" / project_id
    for label, revision, _error in published:
        assert f"return '{label}'" in _git_output(
            project, "show", f"{revision}:steps/run.py",
        )
    assert _git_output(project, "rev-list", "--count", "HEAD") == "3"
    index = TL._read_project_index(project)
    active = next(label for label, revision, _error in published
                  if revision == index["active_revision"])
    assert index["project_metadata"] == candidates[active]["project_metadata"]
    assert (project / "README.md").read_text(encoding="utf-8") == candidates[active]["readme"]
    assert len(_git_output(project, "worktree", "list", "--porcelain").split("worktree ")) == 2



def test_invalid_project_path_replans_without_mutating_catalog(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    invalid = _project(files={
        "../escape.py": "def helper():\n    return 1\n",
        "entry.py": "def workflow():\n    return helper()\n",
    })
    valid = _project(files={
        "steps/helper.py": "def helper():\n    return 1\n",
        "entry.py": "def workflow():\n    return helper()\n",
    })
    prompts = _planner(
        monkeypatch,
        json.dumps({"action": "create"}), invalid, valid,
    )
    _executor(monkeypatch)
    _summarizer(monkeypatch, "Completed safe project.")

    result = _run_task("safe project")

    assert result["status"] == "completed"
    assert "invalid workflow project path" in prompts[2]
    assert not (session_repo / "escape.py").exists()
    assert list((session_repo / "catalog").iterdir())



def test_create_name_collision_allocates_new_project_without_overwrite(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _planner(
        monkeypatch,
        json.dumps({"action": "create"}), _project(),
        json.dumps({"action": "create"}), _project(),
        _project(name="research_workflow_two"),
    )
    _executor(monkeypatch)
    _summarizer(monkeypatch, "Completed separate project.")

    first = _run_task("first unrelated task")
    second = _run_task("second unrelated task")

    assert first["project_id"] == "research_workflow"
    assert second["project_id"] == "research_workflow_two"
    assert (session_repo / "catalog" / first["project_id"]).exists()
    assert (session_repo / "catalog" / second["project_id"]).exists()



def test_run_published_workflow_executes_old_revision_after_new_publish(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    initial = _project(files={
        "steps/discover.py": (
            "def discover(task):\n"
            "    return agent('discover initial')\n"
        ),
        "entry.py": "def workflow(task):\n    return discover(task)\n",
    })
    _candidate, old_revision = _install_workflow_project(session_repo, initial)
    calls = _executor(monkeypatch)
    _summarizer(monkeypatch, "Completed initial revision.")

    first = TL._run_published_workflow(
        "research papers",
        "research_workflow",
        old_revision,
        session_id=TL.current_session_id(),
        spawn_caller=None,
    )
    assert first["status"] == "completed"
    assert [call["prompt"] for call in calls] == ["discover initial"]

    revised = _project(files={
        "steps/discover.py": (
            "def discover(task):\n"
            "    return agent('discover revised')\n"
        ),
        "entry.py": "def workflow(task):\n    return discover(task)\n",
    })
    revision_instance = session_repo / "research-workflow-revised"
    revision_instance.mkdir()
    TL._replace_snapshot(revision_instance, TL._validate_project_candidate(json.loads(revised)))
    TL._publish_snapshot(
        revision_instance,
        project_id="research_workflow",
        action="revise",
        metadata=TL._validate_project_candidate(json.loads(revised))["project_metadata"],
        workflow_dependencies=TL._replace_snapshot(
            revision_instance,
            TL._validate_project_candidate(json.loads(revised)),
        ),
    )
    calls.clear()
    _summarizer(monkeypatch, "Completed old revision again.")

    second = TL._run_published_workflow(
        "research papers again",
        "research_workflow",
        old_revision,
        session_id=TL.current_session_id(),
        spawn_caller=None,
    )

    assert second["status"] == "completed"
    assert second["project_revision"] == old_revision
    assert [call["prompt"] for call in calls] == ["discover initial"]



def test_revise_workflow_rejects_unchanged_candidate(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _install_workflow_project(session_repo, _project())
    _planner(monkeypatch, _project())
    project = session_repo / "catalog" / "research_workflow"

    with pytest.raises(TL.InvalidWorkflow, match="revision unchanged"):
        TL.revise_workflow("research_workflow", "keep everything the same")

    assert _git_output(project, "rev-list", "--count", "HEAD") == "1"

