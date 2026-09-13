"""workflow resume tests."""
from __future__ import annotations
from ._support import (
    Path,
    TL,
    _code,
    _dir_bytes,
    _executor,
    _git_output,
    _install_workflow_project,
    _instance,
    _llm_executor,
    _planner,
    _project,
    _project_entry,
    _run_task,
    _snapshot_package,
    _state,
    _summarizer,
    _workflow_run_states,
    json,
    pytest,
    session_repo,
    threading,
)


def test_resume_historical_run_keeps_artifact_unchanged(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _candidate, revision = _install_workflow_project(session_repo, _project())
    _executor(monkeypatch)
    _summarizer(monkeypatch, "Completed historical research.")
    first = TL._run_published_workflow(
        "research papers",
        "research_workflow",
        revision,
        session_id=TL.current_session_id(),
        spawn_caller=None,
    )
    instance = _instance(session_repo, first["run_id"])
    artifact_before = _dir_bytes(instance / "snapshot")
    catalog_before = _dir_bytes(session_repo / "catalog")
    project_ref = (instance / "project_ref.json").read_bytes()

    result = TL.resume_workflow(first["run_id"])

    assert result["status"] == "completed"
    assert result["run_id"] == first["run_id"]
    assert result["project_revision"] == revision
    assert _dir_bytes(instance / "snapshot") == artifact_before
    assert _dir_bytes(session_repo / "catalog") == catalog_before
    assert (instance / "project_ref.json").read_bytes() == project_ref
    assert not (instance / "code.py").exists()



def test_resume_legacy_code_run_keeps_artifact_unchanged(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    instance = session_repo / "workflows" / "legacy"
    instance.mkdir(parents=True)
    source = "def workflow():\n    return 'legacy ok'\n"
    (instance / "code.py").write_text(source)
    TL._save_state(instance / "state.json", {
        "run_id": "legacy", "task": "legacy", "status": "interrupted",
        "executions": 0, "items": [], "revisions": [], "result": "",
        "last_error": "",
    })
    _summarizer(monkeypatch, "Completed legacy resume.")
    artifact_before = (instance / "code.py").read_bytes()

    result = TL.resume_workflow("legacy")

    assert result["status"] == "completed"
    assert result["result"] is None
    assert (instance / "code.py").read_bytes() == artifact_before
    assert _state(session_repo, "legacy")["result"] == "legacy ok"



@pytest.mark.parametrize("artifact_kind", ["package", "legacy", "single"])
@pytest.mark.parametrize(
    ("exit_kind", "expected_status"),
    [("cancelled", "cancelled"), ("interrupted", "interrupted")],
)
def test_resume_persists_control_exit_and_keeps_cancelled_terminal(
    monkeypatch: pytest.MonkeyPatch,
    session_repo: Path,
    artifact_kind: str,
    exit_kind: str,
    expected_status: str,
) -> None:
    from openprogram.agentic_programming.function import CancelledError

    run_id = f"{artifact_kind}-{exit_kind}"
    instance = session_repo / "workflows" / run_id
    instance.mkdir(parents=True)
    state = {
        "run_id": run_id,
        "task": "resume control exit",
        "status": "interrupted",
        "executions": 0,
        "items": [],
        "revisions": [],
        "result": "",
        "last_error": "",
    }
    attempts = 0
    should_raise = True
    exception_type = CancelledError if exit_kind == "cancelled" else KeyboardInterrupt

    def execute(*_args, **_kwargs) -> str:
        nonlocal attempts
        attempts += 1
        if should_raise:
            raise exception_type("stop resume")
        return "finished"

    if artifact_kind == "package":
        (instance / "snapshot").mkdir()
        state["project_metadata"] = {}
        monkeypatch.setattr(TL, "_execute_snapshot", execute)
    elif artifact_kind == "legacy":
        (instance / "code.py").write_text(
            "def workflow():\n    return 'legacy'\n", encoding="utf-8"
        )
        monkeypatch.setattr(TL, "_execute_source", execute)
    else:
        (instance / "code.py").write_text("SINGLE\n", encoding="utf-8")
        monkeypatch.setattr(TL, "_agent_function", lambda *_args: execute)
    TL._save_state(instance / "state.json", state)
    _summarizer(monkeypatch, "Completed resumed workflow.")

    with pytest.raises(exception_type, match="stop resume"):
        TL.resume_workflow(run_id)

    exit_state = _state(session_repo, run_id)
    assert exit_state["status"] == expected_status
    assert exception_type.__name__ in exit_state["last_error"]
    assert "stop resume" in exit_state["last_error"]
    assert attempts == 1
    exit_bytes = _dir_bytes(instance)
    should_raise = False

    if expected_status == "cancelled":
        monkeypatch.setattr(
            TL,
            "_registered_agentic_functions",
            lambda: pytest.fail("cancelled resume must not prepare execution"),
        )

    result = TL.resume_workflow(run_id)

    if expected_status == "cancelled":
        assert result["status"] == "cancelled"
        assert attempts == 1
        assert _dir_bytes(instance) == exit_bytes
    else:
        assert result["status"] == "completed"
        assert attempts == 2
        assert _state(session_repo, run_id)["last_error"] == ""



def test_single_resume_persists_cancel_before_checkpoint_setup(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    from openprogram.agentic_programming.function import CancelledError

    run_id = "single-setup-cancelled"
    instance = session_repo / "workflows" / run_id
    instance.mkdir(parents=True)
    (instance / "code.py").write_text("SINGLE\n", encoding="utf-8")
    TL._save_state(instance / "state.json", {
        "run_id": run_id,
        "task": "resume before checkpoint setup",
        "status": "interrupted",
        "executions": 0,
        "items": [],
        "revisions": [],
        "result": "",
        "last_error": "",
    })

    def cancel_before_checkpoint(*_args):
        raise CancelledError("stop setup")

    monkeypatch.setattr(TL, "_agent_function", cancel_before_checkpoint)

    with pytest.raises(CancelledError, match="stop setup"):
        TL.resume_workflow(run_id)

    state = _state(session_repo, run_id)
    assert state["status"] == "cancelled"
    assert "CancelledError: stop setup" in state["last_error"]
    assert state["items"] == []



def test_resume_without_snapshot_does_not_reselect_project(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    instance = session_repo / "workflows" / "bare"
    instance.mkdir(parents=True)
    TL._save_state(instance / "state.json", {
        "run_id": "bare", "task": "research papers", "status": "interrupted",
        "executions": 0, "items": [], "revisions": [], "result": "",
        "last_error": "",
    })
    monkeypatch.setattr(
        TL, "_search_projects",
        lambda *_args, **_kwargs: pytest.fail("resume must not re-search"),
    )

    with pytest.raises(TL.InvalidWorkflow, match="no snapshot or legacy code"):
        TL.resume_workflow("bare")

    assert not (session_repo / "catalog").exists()



def test_llm_is_injected_and_checkpointed(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _planner(monkeypatch, _code('''
        first = llm("summarize", model="test-model", effort="low")
        return llm("use " + first)
    '''))
    _executor(monkeypatch)
    calls = _llm_executor(
        monkeypatch,
        lambda prompt, _kwargs: "VALUE" if prompt == "summarize" else "USED",
    )

    result = _run_task("compose")

    assert result["status"] == "completed"
    assert [call["prompt"] for call in calls] == ["summarize", "use VALUE"]
    assert calls[0]["model"] == "test-model"
    assert calls[0]["effort"] == "low"
    assert [item["function"] for item in result["items"]] == ["llm", "llm"]



def test_same_function_name_uses_call_order_keys_and_replays_each_call(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _planner(monkeypatch, _code('''
        agent("one")
        agent("two")
        raise KeyboardInterrupt("killed")
    '''))
    calls = _executor(monkeypatch)

    with pytest.raises(KeyboardInterrupt, match="killed"):
        _run_task("resume")

    run_id = next((session_repo / "workflows").iterdir()).name
    records = _state(session_repo, run_id)["items"]
    assert [(r["function"], r["call_index"]) for r in records] == [
        ("agent", 0), ("agent", 1)
    ]
    entry_path = _snapshot_package(session_repo, run_id) / "workflow.py"
    entry_path.write_text(_project_entry('''
        agent("one")
        agent("two")
        return "finished"
    '''))

    result = TL.resume_workflow(run_id)

    assert result["status"] == "completed"
    assert [call["prompt"] for call in calls] == ["one", "two"]
    assert len({record["key"] for record in result["items"]}) == 2



def test_execution_failure_keeps_error_and_checkpoints_without_repair(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    helper = '''
        def prepare():
            return agent("prepare", description="prepare")
    '''
    initial = _code('''
        value = prepare()
        raise RuntimeError("verification failed")
    ''', helpers=helper)
    prompts = _planner(monkeypatch, initial)
    calls = _executor(monkeypatch, lambda _prompt, _kwargs: "prepared")

    result = _run_task("failing run")

    assert result["status"] == "failed"
    assert [call["prompt"] for call in calls] == ["prepare"]
    assert len(prompts) == 1
    state = _state(session_repo, result["run_id"])
    assert "RuntimeError: verification failed" in state["last_error"]
    assert [item["status"] for item in state["items"]] == ["completed"]
    assert result["revisions"] == []
    project = session_repo / "catalog" / result["project_id"]
    assert _git_output(project, "rev-list", "--count", "HEAD") == "1"



def test_registered_agentic_function_is_injected_and_checkpointed(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    calls: list[str] = []

    def registered(value):
        calls.append(value)
        return value.upper()

    monkeypatch.setattr(TL, "_registered_agentic_functions", lambda: {"registered": registered})
    _planner(monkeypatch, _code('return registered("value")'))
    _executor(monkeypatch)
    _summarizer(monkeypatch, "Completed registered processing.")

    result = _run_task("registry")

    assert result["status"] == "completed"
    assert result["summary"] == "Completed registered processing."
    assert calls == ["value"]
    assert result["items"][0]["function"] == "registered"



def test_single_run_resumes_after_interruption(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _planner(monkeypatch, "SINGLE")
    attempts = 0

    def execution(_prompt, _kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise KeyboardInterrupt("killed")
        return "done"

    _executor(monkeypatch, execution)
    with pytest.raises(KeyboardInterrupt, match="killed"):
        _run_task("single")
    run_id = next((session_repo / "workflows").iterdir()).name

    result = TL.resume_workflow(run_id)

    assert result["status"] == "completed"
    assert attempts == 2



def test_caught_callable_error_writes_failed_after_checkpoint(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    def failing():
        raise ValueError("bad call")

    monkeypatch.setattr(TL, "_registered_agentic_functions", lambda: {"failing": failing})
    _planner(monkeypatch, _code('''
        try:
            failing()
        except ValueError:
            return "handled"
    '''))
    _executor(monkeypatch)

    result = _run_task("caught")

    assert result["status"] == "completed"
    record = _state(session_repo, result["run_id"])["items"][0]
    assert record["status"] == "failed"
    assert "ValueError: bad call" in record["error"]
    assert "error" not in result["items"][0]
    assert record["finished_at"] is not None



def test_cancel_signal_propagates_without_planner_rewrite(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    from openprogram.agentic_programming.function import CancelledError

    def cancel():
        raise CancelledError("stop")

    monkeypatch.setattr(TL, "_registered_agentic_functions", lambda: {"cancel": cancel})
    prompts = _planner(monkeypatch, _code("cancel()"))
    _executor(monkeypatch)

    with pytest.raises(CancelledError, match="stop"):
        _run_task("cancel")

    assert len(prompts) == 1
    run_id = next((session_repo / "workflows").iterdir()).name
    assert _state(session_repo, run_id)["status"] == "cancelled"



def test_same_run_id_concurrent_resume_executes_once(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _planner(monkeypatch, _code('raise KeyboardInterrupt("pause")'))
    _executor(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        _run_task("concurrent")
    run_id = next((session_repo / "workflows").iterdir()).name
    (_snapshot_package(session_repo, run_id) / "workflow.py").write_text(
        _project_entry('return agent("once")')
    )
    calls = _executor(monkeypatch)
    results = []

    def resume():
        results.append(TL.resume_workflow(run_id))

    threads = [threading.Thread(target=resume) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len(results) == 2
    assert len(calls) == 1



def test_resume_accepts_legacy_zero_argument_project_snapshot(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _planner(monkeypatch, _code('raise KeyboardInterrupt("pause")'))
    _executor(monkeypatch)
    with pytest.raises(KeyboardInterrupt, match="pause"):
        _run_task("legacy task")
    run_id = next((session_repo / "workflows").iterdir()).name
    instance = _instance(session_repo, run_id)
    TL.shutil.rmtree(instance / "snapshot")
    legacy = TL._validate_legacy_project_candidate({
        "project_metadata": {
            "name": "Legacy workflow",
            "summary": "Legacy resume fixture",
            "tags": ["legacy"],
        },
        "readme": "# Legacy workflow\n",
        "files": {
            "steps/placeholder.py": "def placeholder():\n    return None\n",
            "entry.py": TL._validated_reply(
                _code('return agent("legacy resumed")')
            ),
        },
    }, allow_legacy_entry=True)
    TL._write_candidate_directory(instance / "snapshot", legacy)
    state = _state(session_repo, run_id)
    state["project_metadata"] = legacy["project_metadata"]
    TL._save_state(instance / "state.json", state)
    calls = _executor(monkeypatch)
    _summarizer(monkeypatch)

    result = TL.resume_workflow(run_id)

    assert result["status"] == "completed"
    assert [call["prompt"] for call in calls] == ["legacy resumed"]



def test_resume_does_not_publish_even_if_publish_required(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _candidate, revision = _install_workflow_project(session_repo, _project())
    _executor(monkeypatch)
    _summarizer(monkeypatch, "Completed resume without publish.")
    first = TL._run_published_workflow(
        "research papers",
        "research_workflow",
        revision,
        session_id=TL.current_session_id(),
        spawn_caller=None,
    )
    instance = _instance(session_repo, first["run_id"])
    state = _state(session_repo, first["run_id"])
    state["publish_required"] = True
    TL._save_state(instance / "state.json", state)
    catalog_before = _dir_bytes(session_repo / "catalog")

    result = TL.resume_workflow(first["run_id"])

    assert result["status"] == "completed"
    assert result["project_revision"] == revision
    assert _dir_bytes(session_repo / "catalog") == catalog_before



def test_cancelled_project_run_does_not_publish_candidate(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    from openprogram.agentic_programming.function import CancelledError

    monkeypatch.setattr(
        TL, "_registered_agentic_functions", lambda: {
            "cancel": lambda: (_ for _ in ()).throw(CancelledError("stop"))
        },
    )
    prompts = _planner(
        monkeypatch,
        json.dumps({"action": "create"}),
        _project(files={
            "steps/cancel.py": "def run_cancel():\n    return cancel()\n",
            "entry.py": "def workflow():\n    return run_cancel()\n",
        }),
    )
    _executor(monkeypatch)

    with pytest.raises(CancelledError, match="stop"):
        _run_task("cancel project")

    assert len(prompts) == 2
    project = session_repo / "catalog" / "research_workflow"
    assert _git_output(project, "rev-list", "--count", "HEAD") == "1"



def test_resume_failed_legacy_code_run_keeps_artifact_unchanged(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    instance = session_repo / "workflows" / "legacy-fail"
    instance.mkdir(parents=True)
    source = "def workflow():\n    raise RuntimeError('boom')\n"
    (instance / "code.py").write_text(source, encoding="utf-8")
    TL._save_state(instance / "state.json", {
        "run_id": "legacy-fail", "task": "legacy", "status": "failed",
        "executions": 0, "items": [], "revisions": [], "result": "",
        "last_error": "RuntimeError: boom",
    })
    planner_calls: list[str] = []

    def forbidden(_sid, prompt, **_kwargs):
        planner_calls.append(prompt)
        return ""

    monkeypatch.setattr(TL, "_run_planner_turn", forbidden)
    _summarizer(monkeypatch, "Completed legacy resume.")
    artifact_before = (instance / "code.py").read_bytes()

    result = TL.resume_workflow("legacy-fail")

    assert result["status"] == "failed"
    assert planner_calls == []
    assert (instance / "code.py").read_bytes() == artifact_before
    assert not list(instance.glob("code.*.py"))



def test_auto_workflow_create_cancel_persists_cancelled_run(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    from openprogram.agentic_programming.function import CancelledError

    _planner(monkeypatch, json.dumps({"action": "create"}))

    def boom(_task: str) -> dict:
        raise CancelledError("stop create")

    monkeypatch.setattr(TL, "create_workflow", boom)

    with pytest.raises(CancelledError, match="stop create"):
        TL.auto_workflow("research papers")

    states = _workflow_run_states(session_repo)
    assert len(states) == 1
    assert states[0]["status"] == "cancelled"
    assert not any(state["status"] == "running" for state in states)

