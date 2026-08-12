"""Behavior tests for self-programmed task-list workflows."""
from __future__ import annotations

import json
import inspect
import textwrap
import threading
from pathlib import Path

import pytest

import openprogram.functions.agentics.task_list as TL


def _code(body: str, helpers: str = "") -> str:
    source = textwrap.dedent(helpers).strip()
    if source:
        source += "\n\n"
    source += "def workflow():\n" + textwrap.indent(
        textwrap.dedent(body).strip(), "    "
    )
    return f"```python\n{source}\n```"


@pytest.fixture
def session_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(TL, "_session_repo", lambda _sid: tmp_path)
    return tmp_path


def _planner(monkeypatch: pytest.MonkeyPatch, *replies: str) -> list[str]:
    prompts: list[str] = []
    queue = list(replies)

    def fake(_sid, prompt, **_kwargs):
        prompts.append(prompt)
        if not queue:
            raise AssertionError("unexpected planner call")
        return queue.pop(0)

    monkeypatch.setattr(TL, "_run_planner_turn", fake)
    return prompts


def _executor(monkeypatch: pytest.MonkeyPatch, fn=None) -> list[dict]:
    calls: list[dict] = []

    def fake(prompt, description="", agent_id="", start_from="clean",
             run_in_background=False, to="", archive_when_done=False):
        kwargs = {
            "description": description, "agent_id": agent_id,
            "start_from": start_from, "run_in_background": run_in_background,
            "to": to, "archive_when_done": archive_when_done,
        }
        calls.append({"prompt": prompt, **kwargs})
        return fn(prompt, kwargs) if fn else f"done: {prompt}"

    monkeypatch.setattr(TL, "_agent_function", lambda: fake)
    return calls


def _llm_executor(monkeypatch: pytest.MonkeyPatch, fn=None) -> list[dict]:
    calls: list[dict] = []

    def fake(prompt, *, model="", effort="", response_format=None,
             choices=None, web_search=False, timeout_s=None):
        kwargs = {
            "model": model, "effort": effort,
            "response_format": response_format, "choices": choices,
            "web_search": web_search, "timeout_s": timeout_s,
        }
        calls.append({"prompt": prompt, **kwargs})
        return fn(prompt, kwargs) if fn else f"done: {prompt}"

    monkeypatch.setattr(TL, "_llm_function", lambda: fake)
    return calls


def _instance(repo: Path, run_id: str) -> Path:
    return repo / "workflows" / run_id


def _state(repo: Path, run_id: str) -> dict:
    return json.loads((_instance(repo, run_id) / "state.json").read_text())


def test_small_task_uses_single_agent_and_persists_independent_run(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    prompts = _planner(monkeypatch, "SINGLE")
    calls = _executor(monkeypatch)

    result = TL.run_task_list("rename one variable", session_id="s1")

    assert result["status"] == "completed"
    assert calls[0]["prompt"] == "rename one variable"
    assert "SINGLE" in prompts[0]
    assert (_instance(session_repo, result["run_id"]) / "code.py").exists()
    assert _state(session_repo, result["run_id"])["status"] == "completed"
    assert not (session_repo / "todos.json").exists()


def test_plain_helper_uses_agent_and_its_result_in_workflow(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    source = _code(
        'return consume(produce())',
        helpers='''
        def produce():
            return agent("produce", description="produce")

        def consume(value):
            return agent("consume " + value, description="consume")
        ''',
    )
    _planner(monkeypatch, source)
    calls = _executor(
        monkeypatch,
        lambda prompt, _kwargs: "VALUE" if prompt == "produce" else "USED",
    )

    result = TL.run_task_list("compose", session_id="s1")

    assert result["status"] == "completed"
    assert [call["prompt"] for call in calls] == ["produce", "consume VALUE"]
    assert [item["function"] for item in result["items"]] == ["agent", "agent"]


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

    result = TL.run_task_list("compose", session_id="s1")

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
        TL.run_task_list("resume", session_id="s1")

    run_id = next((session_repo / "workflows").iterdir()).name
    records = _state(session_repo, run_id)["items"]
    assert [(r["function"], r["call_index"]) for r in records] == [
        ("agent", 0), ("agent", 1)
    ]
    code_path = _instance(session_repo, run_id) / "code.py"
    code_path.write_text(TL._validated_reply(_code('''
        agent("one")
        agent("two")
        return "finished"
    ''')))

    result = TL.resume_workflow(run_id, session_id="s1")

    assert result["status"] == "completed"
    assert [call["prompt"] for call in calls] == ["one", "two"]
    assert len({record["key"] for record in result["items"]}) == 2


def test_exception_rewrites_with_traceback_and_state_then_replays_completed_call(
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
    fixed = _code('''
        value = prepare()
        return agent("finish " + value)
    ''', helpers=helper)
    prompts = _planner(monkeypatch, initial, fixed)
    calls = _executor(
        monkeypatch,
        lambda prompt, _kwargs: "prepared" if prompt == "prepare" else "finished",
    )

    result = TL.run_task_list("repair", session_id="s1")

    assert result["status"] == "completed"
    assert [call["prompt"] for call in calls] == ["prepare", "finish prepared"]
    revision_prompt = prompts[1]
    assert "Traceback (most recent call last)" in revision_prompt
    assert "RuntimeError: verification failed" in revision_prompt
    assert '"function": "agent"' in revision_prompt
    assert "<current_code>" in revision_prompt
    instance = _instance(session_repo, result["run_id"])
    assert (instance / "code.1.py").exists()
    assert len(result["revisions"]) == 1


def test_invalid_plans_keep_requesting_rewrites_with_concrete_errors(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    prompts = _planner(
        monkeypatch,
        "```python\ndef workflow(:\n```",
        _code("import os"),
        _code('return agent("fixed")'),
    )
    calls = _executor(monkeypatch)

    result = TL.run_task_list("invalid", session_id="s1")

    assert result["status"] == "completed"
    assert calls[0]["prompt"] == "fixed"
    assert "SyntaxError" in prompts[1]
    assert "imports are forbidden" in prompts[2]
    assert _state(session_repo, result["run_id"])["status"] == "completed"


def test_missing_workflow_is_rejected_with_exact_validation_reason(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    prompts = _planner(
        monkeypatch,
        "```python\ndef helper():\n    return 1\n```",
        _code('return "ok"'),
    )
    _executor(monkeypatch)

    result = TL.run_task_list("missing", session_id="s1")

    assert result["status"] == "completed"
    assert "exactly one def workflow()" in prompts[1]


def test_execution_cap_counts_only_real_calls(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    initial = _code('''
        for i in range(40):
            agent(f"work-{i}")
        raise RuntimeError("revise after completed calls")
    ''')
    revised = _code('''
        for i in range(40):
            agent(f"work-{i}")
        agent("forty-first")
    ''')
    _planner(monkeypatch, initial, revised)
    calls = _executor(monkeypatch)

    result = TL.run_task_list("many", session_id="s1")

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

    result = TL.run_task_list("delegate", session_id="s1")

    assert result["status"] == "completed"
    assert calls[0]["description"] == "delegate"
    assert calls[0]["agent_id"] == "research"
    assert calls[0]["start_from"] == "inherit"
    assert calls[0]["archive_when_done"] is True


def test_real_agent_implementation_is_callable_with_public_signature() -> None:
    agent = TL._agent_function()

    assert callable(agent)
    assert str(inspect.signature(agent)) == (
        '(prompt: \'str\', description: \'str\' = \'\', agent_id: \'str\' = \'\', '
        'start_from: \'str\' = \'clean\', run_in_background: \'bool\' = False, '
        'to: \'str\' = \'\', archive_when_done: \'bool\' = False) -> \'str\''
    )
    assert agent("probe").startswith("[agent error] no active parent turn")


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

    result = TL.run_task_list("registry", session_id="s1")

    assert result["status"] == "completed"
    assert result["summary"] == "VALUE"
    assert calls == ["value"]
    assert result["items"][0]["function"] == "registered"


def test_new_runs_for_same_task_are_independent(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _planner(
        monkeypatch,
        _code('return agent("first run")'),
        _code('return agent("second run")'),
    )
    calls = _executor(monkeypatch)

    first = TL.run_task_list("same", session_id="s1")
    second = TL.run_task_list("same", session_id="s1")

    assert first["run_id"] != second["run_id"]
    assert [call["prompt"] for call in calls] == ["first run", "second run"]
    assert _state(session_repo, first["run_id"])["task"] == "same"
    assert _state(session_repo, second["run_id"])["task"] == "same"


def test_planner_prompt_documents_real_agentic_programming_convention(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    prompts = _planner(monkeypatch, "SINGLE")
    _executor(monkeypatch)

    TL.run_task_list("prompt", session_id="s1")

    prompt = prompts[0]
    assert "@agentic_function" not in prompt
    assert "runtime.exec" not in prompt
    assert "def workflow():" in prompt
    assert 'def find_issues():' in prompt
    assert 'description="find issues"' in prompt
    assert "llm(prompt" in prompt
    assert "one model request" in prompt
    assert "step(" not in prompt
    assert "import" in prompt and "forbidden" in prompt


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

    result = TL.run_task_list("cap", session_id="s1")

    assert result["status"] == "capped"
    assert len(calls) == 40


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
        TL.run_task_list("single", session_id="s1")
    run_id = next((session_repo / "workflows").iterdir()).name

    result = TL.resume_workflow(run_id, session_id="s1")

    assert result["status"] == "completed"
    assert attempts == 2


def test_invalid_revision_reports_the_invalid_candidate_as_current_code(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    initial = _code('raise RuntimeError("first")')
    invalid = "```python\ndef workflow(:\n```"
    fixed = _code('return "fixed"')
    prompts = _planner(monkeypatch, initial, invalid, fixed)
    _executor(monkeypatch)

    result = TL.run_task_list("rewrite", session_id="s1")

    assert result["status"] == "completed"
    assert "def workflow(:" in prompts[2]
    assert "SyntaxError" in prompts[2]


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

    result = TL.run_task_list("caught", session_id="s1")

    assert result["status"] == "completed"
    record = result["items"][0]
    assert record["status"] == "failed"
    assert "ValueError: bad call" in record["error"]
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
        TL.run_task_list("cancel", session_id="s1")

    assert len(prompts) == 1


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

    result = TL.run_task_list("environment", session_id="s1")

    assert result["status"] == "completed"
    assert result["summary"] == "done: runtime,agentic_function:ok"
    assert [item["function"] for item in result["items"]] == ["registered", "agent"]


def test_same_run_id_concurrent_resume_executes_once(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _planner(monkeypatch, _code('raise KeyboardInterrupt("pause")'))
    _executor(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        TL.run_task_list("concurrent", session_id="s1")
    run_id = next((session_repo / "workflows").iterdir()).name
    (_instance(session_repo, run_id) / "code.py").write_text(
        TL._validated_reply(_code('return agent("once")'))
    )
    calls = _executor(monkeypatch)
    results = []

    def resume():
        results.append(TL.resume_workflow(run_id, session_id="s1"))

    threads = [threading.Thread(target=resume) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len(results) == 2
    assert len(calls) == 1


def test_checkpoint_preserves_path_result_and_mixed_key_arguments(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    calls = 0

    def registered(_value):
        nonlocal calls
        calls += 1
        return Path("/tmp/result")

    monkeypatch.setattr(TL, "_registered_agentic_functions", lambda: {"registered": registered})
    _planner(monkeypatch, _code('''
        registered({1: "integer", "1": "string"})
        raise KeyboardInterrupt("pause")
    '''))
    _executor(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        TL.run_task_list("types", session_id="s1")
    run_id = next((session_repo / "workflows").iterdir()).name
    (_instance(session_repo, run_id) / "code.py").write_text(
        TL._validated_reply(_code(
            'return registered({1: "integer", "1": "string"})'
        ))
    )

    result = TL.resume_workflow(run_id, session_id="s1")

    assert result["status"] == "completed"
    assert result["summary"] == "/tmp/result"
    assert calls == 1


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


def test_resume_continues_planning_when_interrupted_before_code_is_written(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    attempts = 0

    def planner(_sid, _prompt, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise KeyboardInterrupt("planner killed")
        return _code('return "planned"')

    monkeypatch.setattr(TL, "_run_planner_turn", planner)
    _executor(monkeypatch)
    with pytest.raises(KeyboardInterrupt, match="planner killed"):
        TL.run_task_list("planning", session_id="s1")
    run_id = next((session_repo / "workflows").iterdir()).name
    assert not (_instance(session_repo, run_id) / "code.py").exists()

    result = TL.resume_workflow(run_id, session_id="s1")

    assert result["status"] == "completed"
    assert attempts == 2
