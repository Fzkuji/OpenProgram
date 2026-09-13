"""workflow selection tests."""
from __future__ import annotations
from ._support import (
    Path,
    TL,
    _dir_bytes,
    _executor,
    _git_output,
    _install_legacy_project,
    _install_workflow_project,
    _planner,
    _project,
    _summarizer,
    _workflow_run_states,
    inspect,
    json,
    pytest,
    session_repo,
)


def test_search_workflows_excludes_auto_workflow_and_legacy_projects(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _install_workflow_project(session_repo, _project())
    _install_workflow_project(
        session_repo,
        _project(name="auto_workflow", summary="Research orchestration entry"),
    )
    _install_legacy_project(session_repo, "legacy_research")

    result = TL.search_workflows("research papers")

    assert [row["workflow_id"] for row in result["workflows"]] == [
        "research_workflow",
    ]



def test_auto_workflow_is_callable_but_not_an_agent_tool() -> None:
    from openprogram.programs._runtime import exposed_names, get

    assert get("auto_workflow") is not None
    assert "auto_workflow" not in exposed_names()
    assert callable(TL.auto_workflow)
    assert list(inspect.signature(TL.auto_workflow._fn).parameters) == ["task"]
    assert set(TL.auto_workflow.spec["parameters"]["properties"]) == {"task"}



def test_auto_workflow_selection_prefers_reuse_for_semantic_matches() -> None:
    prompt = TL.prompts._auto_decision_prompt(
        "填写本周周报",
        [{
            "workflow_id": "weekly_report",
            "summary": "Inspect a Project and prepare an evidence-based weekly report",
            "tags": ["weekly report", "status update"],
            "retrieval_score": 0,
        }],
    )

    assert "Prefer reuse" in prompt
    assert "wording, language, output path" in prompt
    assert "materially different execution structure" in prompt
    assert "evidence-based weekly report" in prompt



def test_auto_workflow_rejects_unjustified_create_when_candidates_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        TL,
        "_run_planner_turn",
        lambda *_args, **_kwargs: json.dumps({"action": "create"}),
    )

    with pytest.raises(
        TL.InvalidWorkflow,
        match="must name missing_capability",
    ):
        TL._request_auto_decision(
            "填写本周周报",
            [{
                "workflow_id": "weekly_report",
                "summary": "Prepare an evidence-based weekly report",
                "tags": ["weekly report"],
            }],
            session_id="session",
            agent_id="main",
            spawn_caller=None,
        )



def test_auto_workflow_accepts_prompt_schema_when_catalog_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        TL,
        "_run_planner_turn",
        lambda *_args, **_kwargs: json.dumps({
            "action": "create",
            "missing_capability": "No workflow projects exist",
        }),
    )

    assert TL._request_auto_decision(
        "create the first workflow",
        [],
        session_id="session",
        agent_id="main",
        spawn_caller=None,
    ) == {"action": "create"}



def test_search_workflows_candidates_exclude_auto_workflow(
    session_repo: Path,
) -> None:
    _install_workflow_project(session_repo, _project())
    _install_workflow_project(
        session_repo,
        _project(name="auto_workflow", summary="Research orchestration entry"),
    )

    result = TL.search_workflows("research papers auto_workflow")

    ids = [row["workflow_id"] for row in result["workflows"]]
    assert "auto_workflow" not in ids
    assert "research_workflow" in ids



def test_auto_workflow_reuses_matching_project_without_catalog_change(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _candidate, revision = _install_workflow_project(session_repo, _project())
    prompts = _planner(
        monkeypatch,
        json.dumps({"action": "reuse", "workflow_id": "research_workflow"}),
    )
    _executor(monkeypatch)
    _summarizer(monkeypatch, "Completed reused research.")
    catalog_before = _dir_bytes(session_repo / "catalog")
    project = session_repo / "catalog" / "research_workflow"

    result = TL.auto_workflow("research recent papers")

    assert result["action"] == "reuse"
    assert result["workflow_id"] == "research_workflow"
    assert result["workflow_revision"] == revision
    assert result["run_id"]
    assert result["result"]["status"] == "completed"
    assert result["result"]["project_revision"] == revision
    assert _dir_bytes(session_repo / "catalog") == catalog_before
    assert _git_output(project, "rev-parse", "HEAD") == revision
    assert _git_output(project, "rev-list", "--count", "HEAD") == "1"
    assert '- revise:' not in prompts[0]



def test_auto_workflow_reuses_zero_score_cross_language_candidate(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _candidate, revision = _install_workflow_project(
        session_repo,
        _project(
            name="weekly_report",
            summary="Inspect a Project and prepare an evidence-based weekly report",
            tags=["weekly report", "status update"],
        ),
    )
    prompts = _planner(
        monkeypatch,
        json.dumps({"action": "reuse", "workflow_id": "weekly_report"}),
    )
    _executor(monkeypatch)
    _summarizer(monkeypatch, "Completed reused weekly report.")
    catalog_before = _dir_bytes(session_repo / "catalog")

    result = TL.auto_workflow("填写本周周报")

    assert result["action"] == "reuse"
    assert result["workflow_revision"] == revision
    assert _dir_bytes(session_repo / "catalog") == catalog_before
    assert '"retrieval_score": 0' in prompts[0]
    assert "evidence-based weekly report" in prompts[0]



def test_auto_workflow_creates_and_executes_when_catalog_is_empty(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    prompts = _planner(
        monkeypatch,
        json.dumps({"action": "create"}),
        _project(),
    )
    _executor(monkeypatch)
    _summarizer(monkeypatch, "Completed new research.")

    result = TL.auto_workflow("research recent papers")

    assert result["action"] == "create"
    assert result["workflow_id"] == "research_workflow"
    assert len(result["workflow_revision"]) == 40
    assert result["run_id"]
    assert result["result"]["status"] == "completed"
    project = session_repo / "catalog" / "research_workflow"
    assert _git_output(project, "rev-parse", "HEAD") == result["workflow_revision"]
    assert _git_output(project, "rev-list", "--count", "HEAD") == "1"
    assert '- revise:' not in prompts[0]



def test_auto_workflow_stops_when_create_fails_and_publishes_nothing(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    def fake(_sid, prompt, **_kwargs):
        if "<workflow project candidates>" in prompt:
            return json.dumps({"action": "create"})
        return "{}"

    monkeypatch.setattr(TL, "_run_planner_turn", fake)

    with pytest.raises(TL.InvalidWorkflow, match="failed validation after"):
        TL.auto_workflow("research papers")

    assert not (session_repo / "catalog").exists()
    runs = session_repo / "workflows"
    if runs.exists():
        for state_path in runs.glob("*/state.json"):
            assert json.loads(state_path.read_text(encoding="utf-8"))["status"] != "completed"



def test_auto_workflow_forwards_call_id_as_spawn_caller(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(task, workflow_id, revision, *, session_id, spawn_caller, **kwargs):
        captured.update(
            task=task, session_id=session_id, spawn_caller=spawn_caller,
            run_id=kwargs.get("run_id"), project_action=kwargs.get("project_action"),
        )
        return {"status": "completed", "run_id": kwargs.get("run_id") or "r1"}

    monkeypatch.setattr(TL, "_run_published_workflow", fake_run)
    monkeypatch.setattr(
        TL, "search_workflows",
        lambda _task: {"workflows": [{
            "workflow_id": "research_workflow", "revision": "abc",
        }]},
    )
    monkeypatch.setattr(
        TL, "_request_auto_decision",
        lambda *_args, **_kwargs: {
            "action": "reuse", "workflow_id": "research_workflow",
        },
    )
    monkeypatch.setattr(TL, "current_session_id", lambda: "sess1")
    from openprogram.agentic_programming.function import _call_id
    token = _call_id.set("wf_node_abc")
    try:
        TL.auto_workflow._fn("research llm memory")
    finally:
        _call_id.reset(token)

    assert captured["spawn_caller"] == "wf_node_abc"
    assert captured["session_id"] == "sess1"



def test_auto_decision_stops_after_attempt_limit(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    calls: list[str] = []

    def bad(_sid, _prompt, **_kwargs):
        calls.append("attempt")
        return "not json"

    monkeypatch.setattr(TL, "_run_planner_turn", bad)

    with pytest.raises(TL.InvalidWorkflow, match="selection failed after"):
        TL._request_auto_decision(
            "research papers",
            [{"workflow_id": "research_workflow", "revision": "abc"}],
            session_id=TL.current_session_id(),
            agent_id="main",
            spawn_caller=None,
        )

    assert len(calls) == TL.AUTO_DECISION_ATTEMPTS



def test_auto_workflow_selection_failure_persists_failed_run(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _install_workflow_project(session_repo, _project())

    def bad(_sid, _prompt, **_kwargs):
        return "not json"

    monkeypatch.setattr(TL, "_run_planner_turn", bad)

    with pytest.raises(TL.InvalidWorkflow, match="selection failed after"):
        TL.auto_workflow("research papers")

    runs_dir = session_repo / "workflows"
    assert runs_dir.exists()
    states = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in runs_dir.glob("*/state.json")
    ]
    assert len(states) == 1
    assert states[0]["status"] == "failed"
    assert states[0]["task"] == "research papers"
    assert "selection failed after" in states[0]["last_error"]



def test_auto_workflow_success_reuse_leaves_no_running_orphan(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _install_workflow_project(session_repo, _project())
    _planner(
        monkeypatch,
        json.dumps({"action": "reuse", "workflow_id": "research_workflow"}),
    )
    _executor(monkeypatch)
    _summarizer(monkeypatch, "Completed reused research.")

    result = TL.auto_workflow("research recent papers")

    states = _workflow_run_states(session_repo)
    assert len(states) == 1
    assert not any(state["status"] == "running" for state in states)
    assert states[0]["status"] == "completed"
    assert result["run_id"] == states[0]["run_id"]
    assert result["action"] == "reuse"



def test_auto_workflow_success_create_leaves_no_running_orphan(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _planner(
        monkeypatch,
        json.dumps({"action": "create"}),
        _project(),
    )
    _executor(monkeypatch)
    _summarizer(monkeypatch, "Completed new research.")

    result = TL.auto_workflow("research recent papers")

    states = _workflow_run_states(session_repo)
    assert len(states) == 1
    assert not any(state["status"] == "running" for state in states)
    assert states[0]["status"] == "completed"
    assert result["run_id"] == states[0]["run_id"]
    assert result["action"] == "create"



def test_auto_workflow_create_failure_persists_failed_run(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _planner(monkeypatch, json.dumps({"action": "create"}))

    def boom(_task: str) -> dict:
        raise RuntimeError("create exploded")

    monkeypatch.setattr(TL, "create_workflow", boom)

    with pytest.raises(RuntimeError, match="create exploded"):
        TL.auto_workflow("research papers")

    states = _workflow_run_states(session_repo)
    assert len(states) == 1
    assert states[0]["status"] == "failed"
    assert not any(state["status"] == "running" for state in states)



def test_selection_and_authoring_use_public_agent(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    import openprogram.agentic_programming as agentic_programming

    agent_calls: list[dict] = []

    def fake_agent(prompt, **kwargs):
        agent_calls.append({"prompt": prompt, **kwargs})
        if "<workflow project candidates>" in prompt:
            return json.dumps({"action": "create"})
        return _project()

    monkeypatch.setattr(agentic_programming, "agent", fake_agent)

    def forbidden(*_args, **_kwargs):
        pytest.fail("run_agent_turn must not be called from workflow authoring")

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run.run_agent_turn",
        forbidden,
    )
    _executor(monkeypatch)
    _summarizer(monkeypatch)

    TL.create_workflow("author via public agent")

    assert agent_calls
    assert all(call.get("tools") == list(TL.PLANNER_TOOLS) for call in agent_calls)

