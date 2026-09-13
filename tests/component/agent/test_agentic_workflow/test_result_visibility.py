"""workflow result visibility tests."""
from __future__ import annotations
from ._support import (
    Path,
    TL,
    _code,
    _executor,
    _planner,
    _project_entry,
    _run_task,
    _snapshot_package,
    _state,
    _summarizer,
    json,
    pytest,
    session_repo,
)


def test_single_agent_returns_handoff_and_keeps_full_result_internal(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    conclusion = "完整结论。" * 400
    _planner(monkeypatch, "SINGLE")
    _executor(monkeypatch, lambda _prompt, _kwargs: conclusion)
    summary_calls = _summarizer(monkeypatch, "完成资料整理，结果已写入 research.md。")

    result = _run_task("write conclusion")

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

    result = _run_task("write conclusion")

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

    result = _run_task("research and save a report")
    state = _state(session_repo, result["run_id"])

    assert private_finding in json.dumps(state, ensure_ascii=False)
    assert private_finding not in summary_calls[0]["prompt"]
    assert private_finding not in json.dumps(result, ensure_ascii=False)
    assert all("argument_summary" not in item for item in result["items"])



def test_explicit_direct_return_includes_raw_result(
    monkeypatch: pytest.MonkeyPatch, session_repo: Path,
) -> None:
    direct_result = "直接返回给用户的完整内容"
    _planner(monkeypatch, _code(f"return {direct_result!r}"))
    _summarizer(monkeypatch, "已完成直接回答。", return_result=True)

    result = _run_task(
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
    raw_result = "PRIVATE REPORT BODY\n" + ("confidential detail " * 40)
    _planner(monkeypatch, _code(f"return {raw_result!r}"))
    _summarizer(monkeypatch, "完成报告生成。", return_result=True)

    result = _run_task(task)

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
    response_format = calls[0]["response_format"]
    assert response_format.name == "workflow_summary"
    assert response_format.fallback == "prompt"
    assert response_format.schema == {
        "type": "object",
        "properties": {"summary": {"type": "string", "minLength": 1}},
        "required": ["summary"],
        "additionalProperties": False,
    }



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
    raw_result = "PRIVATE REPORT BODY\n" + ("confidential detail " * 40)
    _planner(monkeypatch, _code(f"return {raw_result!r}"))

    def fail(*_args, **_kwargs):
        raise RuntimeError("summary provider unavailable")

    monkeypatch.setattr(TL, "_llm_function", lambda: fail)
    result = _run_task("生成报告并保存到文件")
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



def test_summary_failure_uses_short_zero_call_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args, **_kwargs):
        raise RuntimeError("structured summary unavailable")

    monkeypatch.setattr(TL, "_llm_function", lambda: fail)
    handoff = TL._summarize_workflow({
        "task": "research recent papers",
        "status": "completed",
        "result": "Research report saved to llm_knowledge_2026_research.md.",
        "items": [],
    })

    assert handoff["summary"] == (
        "Research report saved to llm_knowledge_2026_research.md."
    )
    assert handoff["return_result"] is False



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

    result = _run_task("compose")

    assert result["status"] == "completed"
    assert [call["prompt"] for call in calls] == ["produce", "consume VALUE"]
    assert [item["function"] for item in result["items"]] == ["agent", "agent"]



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
        _run_task("types")
    run_id = next((session_repo / "workflows").iterdir()).name
    (_snapshot_package(session_repo, run_id) / "workflow.py").write_text(
        _project_entry('return registered({1: "integer", "1": "string"})')
    )
    _summarizer(monkeypatch, "Completed typed replay.")

    result = TL.resume_workflow(run_id)

    assert result["status"] == "completed"
    assert result["summary"] == "Completed typed replay."
    assert calls == 1

