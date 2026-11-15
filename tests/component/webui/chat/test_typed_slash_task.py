"""A /task typed in the web chat starts another agent on a branch of its own."""
from __future__ import annotations

import pytest

from tests.component.providers.scripted_provider import ScriptedText
from tests.support.transcript.live_server import live_chat


@pytest.mark.parametrize(
    ("line", "background", "earlier_turn", "forks_from_task"),
    [
        ("/task helper: look this up", False, True, True),
        ("/task --clean helper: look this up", False, True, False),
        ("/task --async helper: look this up", True, True, True),
        ("/task helper: look this up", False, False, True),
        ("/task --async helper: look this up", True, False, True),
    ],
    ids=["default", "clean", "async", "first-line", "first-line-async"],
)
def test_typed_task_runs_on_a_branch_and_leaves_the_conversation_in_place(
    tmp_path, line, background, earlier_turn, forks_from_task,
):
    with live_chat(tmp_path) as chat:
        if earlier_turn:
            chat.model.add(ScriptedText("main reply"))
            chat.send("hello")
            chat.wait_finished()

        chat.model.by_prompt["look this up"] = (ScriptedText("spawned reply"),)
        chat.send(line)
        if background:
            chat.wait_job_finished()
        chat.wait_finished()

        loaded = chat.whole_read()[-1]["data"]
        nodes = chat.sessions._open(chat.session_id)[1].nodes_by_id

    rows = [row for turn in loaded["turns"] for row in turn["rows"]]
    typed_at = next(at for at, row in enumerate(rows) if row["content"] == line)
    typed = rows[typed_at]
    assert loaded["head_id"] == typed["id"]
    assert [row["content"] for row in rows if row["role"] == "user"] == (
        ["hello", line] if earlier_turn else [line]
    )

    card = rows[typed_at + 1]
    assert card["rowKind"] == "sub_agent_step"
    assert card["content"] == "spawned reply"
    assert len(rows) == typed_at + 2

    job_head = card["attach"]["head_id"]
    assert job_head.startswith("j_")
    spawned = nodes[job_head.removesuffix("_reply")]
    assert spawned.caller == (typed["id"] if forks_from_task else "ROOT")


@pytest.mark.parametrize("line", ["/task helper: look this up", "/task --clean helper: look this up"],
                         ids=["inherit", "clean"])
def test_a_task_branch_is_not_a_version_of_any_message(tmp_path, line):
    with live_chat(tmp_path) as chat:
        chat.model.add(ScriptedText("main reply"))
        chat.send("hello")
        chat.wait_finished()
        chat.model.by_prompt["look this up"] = (ScriptedText("spawned reply"),)
        chat.send(line)
        chat.wait_finished()
        chat.model.add(ScriptedText("after"))
        chat.send("next")
        chat.wait_finished()

        loaded = chat.whole_read()[-1]["data"]

    rows = [row for turn in loaded["turns"] for row in turn["rows"]]
    assert [row["content"] for row in rows if row["role"] == "user"] == ["hello", line, "next"]
    assert {row["id"]: row["siblingTotal"] for row in rows if row["siblingTotal"] != 1} == {}
