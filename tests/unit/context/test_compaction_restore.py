"""A compaction writes the working state after its summary
(context/compaction.html, "What a compaction restores").

- The approved plan, the todo board, the running jobs and the paths read
  and changed come after the summary text, in the same message, and render
  the same bytes on every request.
- A plan over its budget is cut with a note; the whole block stays in its
  budget.
- A second compaction adds to the first one's path lists; a finished job is
  no longer listed.
- A stored tool call keeps its arguments as the JSON text the model sent;
  its paths and plan are read from that text too.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from openprogram.runtime.context.compaction import restore
from openprogram.runtime.context.render import render_dag_messages
from openprogram.state.store.session.nodes import Call, Graph


def _graph(plan: str = "1. change parser\n2. add tests") -> Graph:
    g = Graph()
    g.add(Call(id="u0", role="user", output="make a plan", predecessor=None))
    g.add(Call(id="a0", role="llm", output="planned", predecessor="u0"))
    g.add(Call(id="p0", role="code", name="exit_plan_mode", input={"plan": plan},
               output="Plan approved and plan mode is off.", caller="a0"))
    g.add(Call(id="r0", role="code", name="read", input={"file_path": "src/parser.py"},
               output="...", caller="a0"))
    g.add(Call(id="r1", role="code", name="read", input={"file_path": "src/lexer.py"},
               output="...", caller="a0"))
    g.add(Call(id="w0", role="code", name="edit", input={"file_path": "src/parser.py"},
               output="ok", caller="a0"))
    g.add(Call(id="u1", role="user", output="go on", predecessor="a0"))
    g.add(Call(id="a1", role="llm", output="going", predecessor="u1"))
    return g


@pytest.fixture
def state(monkeypatch):
    todos = [{"id": "1", "subject": "change parser", "status": "completed"},
             {"id": "2", "subject": "add tests", "status": "in_progress"},
             {"id": "3", "subject": "update docs", "status": "pending"}]
    jobs = [SimpleNamespace(id="j_run", label="indexer", status=SimpleNamespace(value="running")),
            SimpleNamespace(id="j_done", label="old", status=SimpleNamespace(value="completed"))]
    monkeypatch.setattr("openprogram.programs.tools.planning.todo.shared.load", lambda _sid: todos)
    monkeypatch.setattr("openprogram.runtime.jobs.store.list_jobs", lambda _sid, **_k: jobs)
    monkeypatch.setattr("openprogram.runtime.jobs.types.is_terminal",
                        lambda status: status.value in ("completed", "cancelled", "errored"))
    return todos, jobs


def test_the_state_comes_after_the_summary_and_renders_the_same(state):
    g = _graph()
    text, files = restore.restored("c", g, "a1", ["u0", "a0"])
    assert "## Approved plan\n1. change parser" in text
    assert "[in_progress] 2 add tests" in text and "[completed] 1 change parser" in text
    assert "j_run indexer: running" in text and "j_done" not in text
    assert files == {"read": ["src/parser.py", "src/lexer.py"], "modified": ["src/parser.py"]}
    assert "## Files read\n- src/parser.py\n- src/lexer.py" in text
    g.add(Call(id="S", role="llm", name="context/summary", output="The earlier part...\nrecap",
               predecessor=None, metadata={"covers_ids": ["u0", "a0"], "render_role": "user",
                                           "restored_state": text}))
    renders = {render_dag_messages(g, ["S", "u1", "a1"])[0].content[0].text for _ in range(10)}
    assert len(renders) == 1
    only = renders.pop()
    assert only.startswith("The earlier part...\nrecap\n\n<restored-state>")


def test_a_long_plan_is_cut_with_a_note_and_the_block_keeps_its_budget(state):
    from openprogram.runtime.context.tokens import _text_tokens

    text, _ = restore.restored("c", _graph("step " * 20_000), "a1", ["u0", "a0"])
    assert "[Plan cut here:" in text
    assert _text_tokens(text) <= restore.RESTORED_TOKENS + 50


def test_a_plan_mode_entered_again_retires_the_approved_plan():
    g = _graph()
    g.add(Call(id="e1", role="code", name="enter_plan_mode", input={}, output="on", caller="a1"))
    assert restore.approved_plan(restore._chain_calls(g, "a1")) == ""


def test_a_second_compaction_adds_to_the_path_lists(state):
    g = _graph()
    _, first = restore.restored("c", g, "a1", ["u0", "a0"])
    g.add(Call(id="S1", role="llm", name="context/summary", output="recap", predecessor=None,
               metadata={"covers_ids": ["u0", "a0"], restore.RESTORED_FILES: first}))
    g.add(Call(id="r2", role="code", name="read", input={"file_path": "README.md"}, output="", caller="a1"))
    _, second = restore.restored("c", g, "a1", ["u0", "a0", "u1", "a1"])
    assert second["read"][-1] == "README.md"
    assert set(first["read"]) <= set(second["read"])
    assert second["modified"] == ["src/parser.py"]


def test_only_the_latest_paths_are_kept():
    calls = [SimpleNamespace(id=f"r{i}", caller="a0", name="read", input={"file_path": f"f{i}.py"})
             for i in range(40)]
    files = restore.file_lists(calls, ["a0"], None)
    assert len(files["read"]) == restore.MAX_PATHS and files["read"][-1] == "f39.py"


def test_stored_calls_with_json_text_arguments_give_their_paths_and_plan(state):
    import json

    g = Graph()
    g.add(Call(id="u0", role="user", output="read it", predecessor=None))
    g.add(Call(id="a0", role="llm", output="reading", predecessor="u0"))
    g.add(Call(id="p0", role="code", name="exit_plan_mode", input=json.dumps({"plan": "1. fix"}),
               output="Plan approved and plan mode is off.", caller="a0"))
    g.add(Call(id="r0", role="code", name="read", input=json.dumps({"file_path": "data_s1.txt"}),
               output="...", caller="a0"))
    g.add(Call(id="w0", role="code", name="write", input='{"path": "out.txt", "content": "x"}',
               output="ok", caller="a0"))
    g.add(Call(id="b0", role="code", name="read", input="not json", output="", caller="a0"))
    g.add(Call(id="u1", role="user", output="go on", predecessor="a0"))
    g.add(Call(id="a1", role="llm", output="going", predecessor="u1"))
    text, files = restore.restored("c", g, "a1", ["u0", "a0"])
    assert files == {"read": ["data_s1.txt"], "modified": ["out.txt"]}
    assert "## Files read\n- data_s1.txt" in text and "## Files changed\n- out.txt" in text
    assert "## Approved plan\n1. fix" in text
