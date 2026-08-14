"""Behavior tests for self-programmed task-list workflows."""
from __future__ import annotations

import json
import inspect
import re
import textwrap
import threading
from pathlib import Path

import pytest

import openprogram.programs.agentic_functions.agentic_workflow as TL


def _code(body: str, helpers: str = "") -> str:
    source = textwrap.dedent(helpers).strip()
    if source:
        source += "\n\n"
    source += "def workflow():\n" + textwrap.indent(
        textwrap.dedent(body).strip(), "    "
    )
    return f"```python\n{source}\n```"


def _project(
    *,
    name: str = "Research workflow",
    summary: str = "Research and synthesize a topic",
    tags: list[str] | None = None,
    readme: str = "# Research workflow\n\nReusable research steps.\n",
    files: dict[str, str] | None = None,
) -> str:
    return json.dumps({
        "project_metadata": {
            "name": name,
            "summary": summary,
            "tags": tags or ["research"],
        },
        "readme": readme,
        "files": files or {
            "steps/discover.py": "def discover():\n    return agent('discover papers')\n",
            "entry.py": "def workflow():\n    return discover()\n",
        },
    }, ensure_ascii=False)


@pytest.fixture
def session_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(TL, "_session_repo", lambda _sid: tmp_path)
    monkeypatch.setattr(TL, "_workflow_projects_root", lambda: tmp_path / "catalog")
    monkeypatch.setattr(TL, "_registered_agentic_functions", lambda: {})
    monkeypatch.setattr(TL, "current_session_id", lambda: "s1")
    return tmp_path


def _planner(monkeypatch: pytest.MonkeyPatch, *replies: str) -> list[str]:
    prompts: list[str] = []
    queue = list(replies)
    pending_legacy: list[str] = []

    def project_reply(reply: str, prompt: str) -> str:
        if reply.strip() == "SINGLE":
            task = prompt.split("<task>\n", 1)[1].split("\n</task>", 1)[0]
            return _project(files={
                "steps/task.py": (
                    "def run_task():\n"
                    f"    return agent({(task + chr(10) + chr(10) + TL.DELIVERY_INSTRUCTIONS)!r})\n"
                ),
                "entry.py": "def workflow():\n    return run_task()\n",
            })
        source = TL._extract_source(reply)
        if "def workflow():" not in source:
            return _project(files={
                "steps/placeholder.py": "def project_marker():\n    return None\n",
                "entry.py": source,
            })
        helper = source.replace("def workflow():", "def run_workflow_step():", 1)
        return _project(files={
            "steps/legacy.py": helper,
            "entry.py": "def workflow():\n    return run_workflow_step()\n",
        })

    def fake(_sid, prompt, **_kwargs):
        prompts.append(prompt)
        if pending_legacy:
            return project_reply(pending_legacy.pop(), prompt)
        if not queue:
            raise AssertionError("unexpected planner call")
        reply = queue.pop(0)
        if "<workflow project candidates>" in prompt and (
            reply.strip() == "SINGLE" or re.search(r"```python", reply)
        ):
            pending_legacy.append(reply)
            return json.dumps({"action": "create"})
        if "<workflow project candidates>" not in prompt and re.search(
            r"```python", reply,
        ):
            return project_reply(reply, prompt)
        return reply

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

    monkeypatch.setattr(TL, "_agent_function", lambda session_id, spawn_caller: fake)
    return calls


def _llm_executor(monkeypatch: pytest.MonkeyPatch, fn=None) -> list[dict]:
    calls: list[dict] = []

    def fake(prompt, *, model="", effort="", response_format=None,
             choices=None, web_search=False, timeout_s=None):
        if "<workflow_summary>" in prompt:
            return {"summary": "Completed the workflow.", "return_result": False}
        kwargs = {
            "model": model, "effort": effort,
            "response_format": response_format, "choices": choices,
            "web_search": web_search, "timeout_s": timeout_s,
        }
        calls.append({"prompt": prompt, **kwargs})
        return fn(prompt, kwargs) if fn else f"done: {prompt}"

    monkeypatch.setattr(TL, "_llm_function", lambda: fake)
    return calls


def _summarizer(
    monkeypatch: pytest.MonkeyPatch,
    summary: str = "Completed the requested work.",
    *,
    return_result: bool = False,
) -> list[dict]:
    calls: list[dict] = []

    def fake(prompt, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        return {"summary": summary, "return_result": return_result}

    monkeypatch.setattr(TL, "_llm_function", lambda: fake)
    return calls


def _instance(repo: Path, run_id: str) -> Path:
    return repo / "workflows" / run_id


def _state(repo: Path, run_id: str) -> dict:
    return json.loads((_instance(repo, run_id) / "state.json").read_text())


def test_small_task_is_persisted_as_a_reusable_multifile_project(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    prompts = _planner(monkeypatch, "SINGLE")
    calls = _executor(monkeypatch)

    result = TL.agentic_workflow("rename one variable")

    assert result["status"] == "completed"
    assert calls[0]["prompt"].startswith("rename one variable")
    assert "save substantive deliverables" in calls[0]["prompt"]
    assert "reuse" in prompts[0] and "revise" in prompts[0] and "create" in prompts[0]
    snapshot = _instance(session_repo, result["run_id"]) / "snapshot"
    assert (snapshot / "entry.py").exists()
    assert (snapshot / "steps" / "task.py").exists()
    assert not (_instance(session_repo, result["run_id"]) / "code.py").exists()
    assert result["project_id"]
    assert result["project_revision"] == "0001"
    assert _state(session_repo, result["run_id"])["status"] == "completed"
    assert not (session_repo / "todos.json").exists()


def test_single_agent_returns_handoff_and_keeps_full_result_internal(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    conclusion = "完整结论。" * 400
    _planner(monkeypatch, "SINGLE")
    _executor(monkeypatch, lambda _prompt, _kwargs: conclusion)
    summary_calls = _summarizer(monkeypatch, "完成资料整理，结果已写入 research.md。")

    result = TL.agentic_workflow("write conclusion")

    assert result["summary_kind"] == "workflow_handoff_v1"
    assert result["summary"] == "完成资料整理，结果已写入 research.md。"
    assert result["return_result"] is False
    assert result["result"] is None
    assert _state(session_repo, result["run_id"])["result"] == conclusion
    assert conclusion in json.dumps(
        _state(session_repo, result["run_id"]), ensure_ascii=False,
    )
    assert conclusion not in json.dumps(result, ensure_ascii=False)
    assert conclusion not in summary_calls[0]["prompt"]
    summary_prompt = summary_calls[0]["prompt"]
    assert "Usually begin with a brief overview" in summary_prompt
    assert "2-3 sentences are often enough" in summary_prompt
    assert "use a short numbered list" in summary_prompt
    assert "End with a clear assessment of whether the task was completed" in summary_prompt
    assert "Do not force citations, references, or artifact paths" in summary_prompt
    assert '"summary": "formatted Markdown"' in summary_prompt
    assert '"summary": "1-5 short bullets"' not in summary_prompt


def test_programmed_workflow_returns_handoff_and_keeps_full_result_internal(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    conclusion = "完整结论。" * 400
    _planner(monkeypatch, _code(f"return {conclusion!r}"))
    _summarizer(monkeypatch, "完成报告生成，文件保存在 report.md。")

    result = TL.agentic_workflow("write conclusion")

    assert result["summary"] == "完成报告生成，文件保存在 report.md。"
    assert result["return_result"] is False
    assert result["result"] is None
    assert _state(session_repo, result["run_id"])["result"] == conclusion
    assert conclusion not in json.dumps(result, ensure_ascii=False)


def test_intermediate_result_reused_as_argument_stays_private(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    private_finding = "SUBSTANTIVE_PRIVATE_FINDING_7d14"
    monkeypatch.setattr(
        TL, "_registered_agentic_functions", lambda: {"lookup": lambda: private_finding},
    )
    _planner(monkeypatch, _code("""
        finding = lookup()
        agent("save this report: " + finding)
        return "done"
    """))
    _executor(monkeypatch, lambda _prompt, _kwargs: "saved")
    summary_calls = _summarizer(monkeypatch, "任务已完成。")

    result = TL.agentic_workflow("research and save a report")
    state = _state(session_repo, result["run_id"])

    assert private_finding in json.dumps(state, ensure_ascii=False)
    assert private_finding not in summary_calls[0]["prompt"]
    assert private_finding not in json.dumps(result, ensure_ascii=False)
    assert all("argument_summary" not in item for item in result["items"])


def test_repair_errors_stay_private_in_public_payload(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    private_error = "SUBSTANTIVE_PRIVATE_FINDING_91c2"

    def fail() -> None:
        raise RuntimeError(private_error)

    monkeypatch.setattr(TL, "_registered_agentic_functions", lambda: {"lookup": fail})
    _planner(
        monkeypatch,
        _code('lookup()\nreturn "unreachable"'),
        _code('return "repaired"'),
    )
    _summarizer(monkeypatch, "任务已完成。")

    result = TL.agentic_workflow("repair a failing workflow")
    state = _state(session_repo, result["run_id"])

    assert private_error in json.dumps(state, ensure_ascii=False)
    assert private_error not in json.dumps(result, ensure_ascii=False)
    assert all("error" not in item for item in result["items"])
    assert all("error" not in revision for revision in result["revisions"])


def test_explicit_direct_return_includes_raw_result(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    direct_result = "直接返回给用户的完整内容"
    _planner(monkeypatch, _code(f"return {direct_result!r}"))
    _summarizer(monkeypatch, "已完成直接回答。", return_result=True)

    result = TL.agentic_workflow(
        "请直接在聊天中返回完整内容，不要写文件",
    )

    assert result["summary"] == "已完成直接回答。"
    assert result["return_result"] is True
    assert result["result"] == direct_result


@pytest.mark.parametrize("task", (
    "请勿在聊天中返回完整正文，写入文件",
    "不将完整内容在聊天中返回，只给摘要",
))
def test_agent_preview_cannot_authorize_raw_result(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path, task: str,
) -> None:
    raw_result = "PRIVATE REPORT BODY"
    _planner(monkeypatch, _code(f"return {raw_result!r}"))
    _summarizer(monkeypatch, "完成报告生成。", return_result=True)

    result = TL.agentic_workflow(task)

    assert result["return_result"] is False
    assert result["result"] is None
    assert raw_result not in json.dumps(result, ensure_ascii=False)


def test_direct_result_authorization_handles_common_wording() -> None:
    denied = (
        "不要在聊天中返回完整内容，写入文件",
        "在这里给出摘要，不要完整正文",
        "不得在聊天中返回完整正文，请写入文件",
        "不能在聊天中返回完整正文，请写入文件",
        "不要保存文件，也不要直接返回完整正文，在聊天里只给摘要",
        "不要保存文件，也不能直接把完整正文返回到聊天里，只给摘要",
        "不要写入磁盘，也禁止直接将完整内容输出在当前消息中",
        "请勿在聊天中返回完整正文，写入文件",
        "不在聊天中返回完整正文，只显示摘要",
        "不把完整正文返回到聊天里，只给摘要",
        "不将完整内容在聊天中返回，只给摘要",
        "Never return the full report here; save it to a file.",
        "You must not return the complete report in chat.",
        "Don’t return the full report here.",
    )
    allowed = (
        "不要写文件，直接返回完整内容",
        "Return the full report here",
        "Return the complete report in chat",
    )
    assert all(not TL._direct_result_requested(task) for task in denied)
    assert all(TL._direct_result_requested(task) for task in allowed)


def test_summary_function_uses_trace_without_raw_deliverable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_deliverable = "正文机密内容" * 2000
    calls = _summarizer(monkeypatch, "完成两项分析并保存到 /tmp/report.md。")
    handoff = TL._summarize_workflow({
        "task": "分析两份文件并生成报告",
        "status": "completed",
        "result": raw_deliverable,
        "items": [{
            "function": "registered",
            "status": "completed",
            "argument_summary": "生成报告并保存到 /tmp/report.md",
            "result_summary": "正文发现：不应进入 workflow summary",
        }, {
            "function": "agent",
            "status": "completed",
            "argument_summary": "验证产物",
            "result_summary": "Saved artifact: /tmp/report.md; warning: none",
        }],
    })

    assert handoff == {
        "summary": "完成两项分析并保存到 /tmp/report.md。",
        "return_result": False,
    }
    assert raw_deliverable not in calls[0]["prompt"]
    assert "正文发现" not in calls[0]["prompt"]
    assert "Saved artifact: /tmp/report.md; warning: none" in calls[0]["prompt"]
    assert calls[0]["response_format"] == {"type": "json_object"}


def test_summary_failure_never_exposes_raw_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args, **_kwargs):
        raise RuntimeError("summary provider unavailable")

    monkeypatch.setattr(TL, "_llm_function", lambda: fail)
    handoff = TL._summarize_workflow({
        "task": "generate report",
        "status": "completed",
        "result": "FULL REPORT BODY",
        "items": [{"function": "agent", "status": "completed"}],
    })

    assert handoff["return_result"] is False
    assert "FULL REPORT BODY" not in handoff["summary"]
    assert handoff["summary_error"] == "RuntimeError: summary provider unavailable"


def test_summary_failure_is_persisted_but_not_public(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    raw_result = "PRIVATE REPORT BODY"
    _planner(monkeypatch, _code(f"return {raw_result!r}"))

    def fail(*_args, **_kwargs):
        raise RuntimeError("summary provider unavailable")

    monkeypatch.setattr(TL, "_llm_function", lambda: fail)
    result = TL.agentic_workflow("生成报告并保存到文件")
    state = _state(session_repo, result["run_id"])

    assert state["handoff"]["summary_error"] == (
        "RuntimeError: summary provider unavailable"
    )
    assert "completed" not in result["summary"].lower()
    assert "summary_error" not in result
    assert raw_result not in json.dumps(result, ensure_ascii=False)


def test_non_string_summary_uses_safe_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    def malformed(*_args, **_kwargs):
        return {"summary": {"report_body": "SUBSTANTIVE FINDING"}}

    monkeypatch.setattr(TL, "_llm_function", lambda: malformed)
    handoff = TL._summarize_workflow({
        "task": "generate report",
        "status": "completed",
        "result": "FULL REPORT BODY",
        "items": [{"function": "agent", "status": "completed"}],
    })

    assert handoff == {
        "summary": (
            "Workflow finished 1 recorded call(s): agent. "
            "Summary generation was unavailable; verify generated artifacts."
        ),
        "return_result": False,
        "summary_error": "ValueError: workflow summary text was not a string",
    }
    assert "SUBSTANTIVE FINDING" not in handoff["summary"]


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

    result = TL.agentic_workflow("compose")

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

    result = TL.agentic_workflow("compose")

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
        TL.agentic_workflow("resume")

    run_id = next((session_repo / "workflows").iterdir()).name
    records = _state(session_repo, run_id)["items"]
    assert [(r["function"], r["call_index"]) for r in records] == [
        ("agent", 0), ("agent", 1)
    ]
    entry_path = _instance(session_repo, run_id) / "snapshot" / "entry.py"
    entry_path.write_text(TL._validated_reply(_code('''
        agent("one")
        agent("two")
        return "finished"
    ''')))

    result = TL.resume_workflow(run_id)

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

    result = TL.agentic_workflow("repair")

    assert result["status"] == "completed"
    assert [call["prompt"] for call in calls] == ["prepare", "finish prepared"]
    revision_prompt = prompts[2]
    assert "Traceback (most recent call last)" in revision_prompt
    assert "RuntimeError: verification failed" in revision_prompt
    assert '"function": "agent"' in revision_prompt
    assert "<base_project>" in revision_prompt
    instance = _instance(session_repo, result["run_id"])
    assert (instance / "snapshot" / "steps" / "legacy.py").exists()
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

    result = TL.agentic_workflow("invalid")

    assert result["status"] == "completed"
    assert calls[0]["prompt"] == "fixed"
    assert "SyntaxError" in prompts[2]
    assert "imports are forbidden" in prompts[3]
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

    result = TL.agentic_workflow("missing")

    assert result["status"] == "completed"
    assert "exactly one def workflow()" in prompts[2]


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

    result = TL.agentic_workflow("many")

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

    result = TL.agentic_workflow("delegate")

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


def test_agentic_workflow_registers_only_the_task_parameter() -> None:
    assert str(inspect.signature(TL.agentic_workflow)) == "(task: 'str') -> 'dict'"
    assert TL.agentic_workflow._agent_tool.parameters == {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "The task to plan and execute",
            },
        },
        "required": ["task"],
    }


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

    result = TL.agentic_workflow("registry")

    assert result["status"] == "completed"
    assert result["summary"] == "Completed registered processing."
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

    first = TL.agentic_workflow("same")
    second = TL.agentic_workflow("same")

    assert first["run_id"] != second["run_id"]
    assert [call["prompt"] for call in calls] == ["first run", "second run"]
    assert _state(session_repo, first["run_id"])["task"] == "same"
    assert _state(session_repo, second["run_id"])["task"] == "same"


def test_planner_prompt_documents_real_agentic_programming_convention(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    prompts = _planner(
        monkeypatch,
        json.dumps({"action": "create"}),
        _project(files={
            "steps/result.py": "def result():\n    return 'ok'\n",
            "entry.py": "def workflow():\n    return result()\n",
        }),
    )
    _executor(monkeypatch)
    _summarizer(monkeypatch)

    TL.agentic_workflow("prompt")

    decision_prompt, prompt = prompts
    assert "reuse" in decision_prompt
    assert "revise" in decision_prompt
    assert "create" in decision_prompt
    assert "@agentic_function" not in prompt
    assert "runtime.exec" not in prompt
    assert "def workflow():" in prompt
    assert "project_metadata" in prompt
    assert "steps/example.py" in prompt
    assert "complete project, not a patch" in prompt
    assert "save substantive deliverables" in prompt
    assert "explicitly asks for the content in chat" in prompt
    assert "Do not return a report body as the workflow handoff" in prompt
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

    result = TL.agentic_workflow("cap")

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
        TL.agentic_workflow("single")
    run_id = next((session_repo / "workflows").iterdir()).name

    result = TL.resume_workflow(run_id)

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

    result = TL.agentic_workflow("rewrite")

    assert result["status"] == "completed"
    assert "def workflow(:" in prompts[3]
    assert "SyntaxError" in prompts[3]


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

    result = TL.agentic_workflow("caught")

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
        TL.agentic_workflow("cancel")

    assert len(prompts) == 2


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

    result = TL.agentic_workflow("environment")

    assert result["status"] == "completed"
    assert result["summary"] == "Completed environment validation."
    assert [item["function"] for item in result["items"]] == ["registered", "agent"]


def test_same_run_id_concurrent_resume_executes_once(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _planner(monkeypatch, _code('raise KeyboardInterrupt("pause")'))
    _executor(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        TL.agentic_workflow("concurrent")
    run_id = next((session_repo / "workflows").iterdir()).name
    (_instance(session_repo, run_id) / "snapshot" / "entry.py").write_text(
        TL._validated_reply(_code('return agent("once")'))
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
        TL.agentic_workflow("types")
    run_id = next((session_repo / "workflows").iterdir()).name
    (_instance(session_repo, run_id) / "snapshot" / "entry.py").write_text(
        TL._validated_reply(_code(
            'return registered({1: "integer", "1": "string"})'
        ))
    )
    _summarizer(monkeypatch, "Completed typed replay.")

    result = TL.resume_workflow(run_id)

    assert result["status"] == "completed"
    assert result["summary"] == "Completed typed replay."
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
        if attempts == 2:
            return json.dumps({"action": "create"})
        return _project(files={
            "steps/plan.py": "def run_plan():\n    return 'planned'\n",
            "entry.py": "def workflow():\n    return run_plan()\n",
        })

    monkeypatch.setattr(TL, "_run_planner_turn", planner)
    _executor(monkeypatch)
    with pytest.raises(KeyboardInterrupt, match="planner killed"):
        TL.agentic_workflow("planning")
    run_id = next((session_repo / "workflows").iterdir()).name
    assert not (_instance(session_repo, run_id) / "code.py").exists()

    result = TL.resume_workflow(run_id)

    assert result["status"] == "completed"
    assert attempts == 3
    assert (_instance(session_repo, run_id) / "snapshot" / "steps" / "plan.py").exists()


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

    result = TL.agentic_workflow("research recent papers")

    assert result["status"] == "completed"
    assert result["project_id"]
    assert result["project_revision"] == "0001"
    assert [call["prompt"] for call in calls] == ["discover papers"]
    instance = _instance(session_repo, result["run_id"])
    snapshot = instance / "snapshot"
    assert (snapshot / "steps" / "discover.py").exists()
    assert (snapshot / "entry.py").exists()
    assert (snapshot / "README.md").exists()
    assert (snapshot / "workflow.json").exists()
    project = session_repo / "catalog" / result["project_id"]
    assert (project / "revisions" / "0001" / "steps" / "discover.py").exists()
    assert json.loads((project / "project.json").read_text())["active_revision"] == "0001"
    assert "workflow project candidates" in prompts[0]


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

    result = TL.agentic_workflow("create a reusable workflow")

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

    result = TL.agentic_workflow("create a maintainable workflow")

    assert result["status"] == "completed"
    assert "at least one Python helper under steps/" in prompts[2]
    assert [call["prompt"] for call in calls] == ["discover papers"]
    revision = (
        session_repo / "catalog" / result["project_id"]
        / "revisions" / result["project_revision"]
    )
    assert (revision / "steps" / "discover.py").exists()


def test_similar_task_in_another_session_reuses_project_without_authoring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TL, "_session_repo", lambda sid: tmp_path / "sessions" / sid)
    monkeypatch.setattr(TL, "_workflow_projects_root", lambda: tmp_path / "catalog")
    monkeypatch.setattr(TL, "_registered_agentic_functions", lambda: {})
    active_session = "s1"
    monkeypatch.setattr(TL, "current_session_id", lambda: active_session)
    for sid in ("s1", "s2"):
        (tmp_path / "sessions" / sid).mkdir(parents=True)
    prompts = _planner(
        monkeypatch,
        json.dumps({"action": "create"}),
        _project(),
        json.dumps({"action": "reuse", "project_id": "research-workflow"}),
    )
    calls = _executor(monkeypatch)
    _summarizer(monkeypatch, "Completed reusable research.")

    first = TL.agentic_workflow("research papers")
    active_session = "s2"
    second = TL.agentic_workflow("research another paper")

    assert second["project_id"] == first["project_id"] == "research-workflow"
    assert second["project_revision"] == first["project_revision"] == "0001"
    assert len(list((tmp_path / "catalog" / "research-workflow" / "revisions").iterdir())) == 1
    assert [call["prompt"] for call in calls] == ["discover papers", "discover papers"]
    assert "research-workflow" in prompts[2]
    assert "steps/discover.py" not in prompts[2]


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
    prompts = _planner(
        monkeypatch,
        json.dumps({"action": "create"}), first,
        json.dumps({"action": "revise", "project_id": "research-workflow"}), revised,
    )
    _executor(monkeypatch)
    _summarizer(monkeypatch, "Completed revised research.")

    initial = TL.agentic_workflow("research papers")
    updated = TL.agentic_workflow("research papers and verify them")

    assert initial["project_revision"] == "0001"
    assert updated["project_revision"] == "0002"
    project = session_repo / "catalog" / "research-workflow" / "revisions"
    assert (project / "0001" / "steps" / "shared.py").read_bytes() == (
        project / "0002" / "steps" / "shared.py"
    ).read_bytes()
    assert "discover papers" in prompts[3]
    assert "steps/shared.py" in prompts[3]
    assert "Reusable research steps" in prompts[3]


def test_failed_candidate_repairs_snapshot_before_publishing_active_revision(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    bad = _project(files={
        "steps/run.py": (
            "def run():\n    raise RuntimeError('broken candidate')\n"
        ),
        "entry.py": "def workflow():\n    return run()\n",
    })
    fixed = _project(files={
        "steps/run.py": "def run():\n    return 'fixed candidate'\n",
        "entry.py": "def workflow():\n    return run()\n",
    })
    prompts = _planner(
        monkeypatch,
        json.dumps({"action": "create"}), bad, fixed,
    )
    _executor(monkeypatch)
    _summarizer(monkeypatch, "Completed repaired workflow.")

    result = TL.agentic_workflow("repair project")

    assert result["project_revision"] == "0001"
    project = session_repo / "catalog" / result["project_id"]
    assert "fixed candidate" in (
        project / "revisions" / "0001" / "steps" / "run.py"
    ).read_text()
    assert "broken candidate" in prompts[2]
    assert "RuntimeError: broken candidate" in prompts[2]
    assert len(list((project / "revisions").iterdir())) == 1


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
        TL.agentic_workflow("cancel project")

    assert len(prompts) == 2
    assert not (session_repo / "catalog").exists()


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

    result = TL.agentic_workflow("safe project")

    assert result["status"] == "completed"
    assert "invalid workflow project path" in prompts[2]
    assert not (session_repo / "escape.py").exists()
    assert list((session_repo / "catalog").iterdir())


def test_continue_word_still_creates_a_new_run_and_uses_catalog_decision(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    prompts = _planner(
        monkeypatch,
        json.dumps({"action": "create"}), _project(),
        json.dumps({"action": "reuse", "project_id": "research-workflow"}),
    )
    calls = _executor(monkeypatch)
    _summarizer(monkeypatch, "Completed catalog-selected workflow.")

    first = TL.agentic_workflow("research papers")
    second = TL.agentic_workflow("继续研究这些 papers")

    assert first["run_id"] != second["run_id"]
    assert first["project_id"] == second["project_id"]
    assert len(calls) == 2
    assert "workflow project candidates" in prompts[2]


def test_capped_project_run_does_not_publish_candidate(
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

    result = TL.agentic_workflow("capped project")

    assert result["status"] == "capped"
    assert len(calls) == TL.MAX_ITEMS_EXECUTED
    assert not (session_repo / "catalog").exists()


def test_non_candidate_reuse_is_rejected_before_authoring(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    prompts = _planner(
        monkeypatch,
        json.dumps({"action": "reuse", "project_id": "not-a-candidate"}),
        json.dumps({"action": "create"}),
        _project(),
    )
    _executor(monkeypatch)
    _summarizer(monkeypatch, "Completed authorized project.")

    result = TL.agentic_workflow("authorized project")

    assert result["status"] == "completed"
    assert "must come from the current candidates" in prompts[1]


def test_create_name_collision_allocates_new_project_without_overwrite(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    _planner(
        monkeypatch,
        json.dumps({"action": "create"}), _project(),
        json.dumps({"action": "create"}), _project(),
    )
    _executor(monkeypatch)
    _summarizer(monkeypatch, "Completed separate project.")

    first = TL.agentic_workflow("first unrelated task")
    second = TL.agentic_workflow("second unrelated task")

    assert first["project_id"] == "research-workflow"
    assert second["project_id"] == "research-workflow-2"
    assert (session_repo / "catalog" / first["project_id"]).exists()
    assert (session_repo / "catalog" / second["project_id"]).exists()
