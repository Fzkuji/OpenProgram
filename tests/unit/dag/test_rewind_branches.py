"""Rewind points when two branches of one conversation, or a scheduled run, edit files at once."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from openprogram.runtime.agent.turn_request import TurnRequest
from openprogram.runtime.conversation.rewind import list_rewind_points, plan_rewind, rewind_to
from openprogram.state.store.session.session_store import SessionStore
from openprogram.state.store.snapshot.checkpoint import CheckpointStore, PromptCheckpoints
from openprogram.state.store.snapshot.checkpoint.helpers import (
    _prompt_lineage,
    _track_for_prompt,
    capture_prompt_checkpoint,
    join_prompt_checkpoint,
)


@pytest.fixture
def store(tmp_path: Path, monkeypatch, request):
    value = SessionStore(tmp_path / "sessions")
    request.addfinalizer(value.close)
    monkeypatch.setattr(
        "openprogram.state.store.session.session_store.shared._default_store", value,
        raising=False,
    )
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: value)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path / "state")
    return value


def _append(store, sid, msg_id, role, pred, display=None):
    store.append_message(sid, {
        "id": msg_id, "role": role, "content": msg_id, "predecessor": pred,
        **({"caller": "ROOT"} if role == "user" and display != "root" else {}),
        **({"display": display} if display else {}),
    }, advance_head=False)


class Conversation:
    """Branches of one conversation whose turns edit files through the checkpoint calls."""

    def __init__(self, store, sid="s"):
        self.store, self.sid = store, sid
        store.create_session(sid, "main", title="t")
        _append(store, sid, "ROOT", "user", None, display="root")
        self.journal = CheckpointStore(store._session_dir(sid))
        self.prompts = PromptCheckpoints(store._session_dir(sid))

    def prompt(self, user: str, after: str = "ROOT") -> str:
        """A prompt that starts a turn; the turn stays open until ``reply``."""
        _append(self.store, self.sid, user, "user", after)
        assert capture_prompt_checkpoint(self.store, self.sid, user, f"{user}_reply")
        return user

    def edit(self, user: str, path: Path, text: str, turn: str | None = None) -> None:
        turn = turn or f"{user}_reply"
        _track_for_prompt(
            self.journal, str(path), None, turn,
            _prompt_lineage(SimpleNamespace(store=self.store, session_id=self.sid)),
        )
        self.journal.backup_before_edit(turn, str(path))
        path.write_text(text, encoding="utf-8", newline="")
        self.journal.commit_after_edit(turn, str(path), operation="edit")

    def reply(self, user: str) -> str:
        _append(self.store, self.sid, f"{user}_reply", "assistant", user)
        return f"{user}_reply"

    def select(self, head: str) -> None:
        self.store.set_head(self.sid, head)


def _apply(sid, msg, mode="code_and_conversation"):
    plan = plan_rewind(sid, msg, mode=mode)
    assert plan["status"] == "ready", plan
    return rewind_to(
        sid, msg, mode=mode,
        idempotency_key=plan["idempotency_key"], expected_plan_hash=plan["plan_hash"],
    )


@pytest.fixture
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()

    def file(name: str, text: str = "original\n") -> Path:
        # newline="" keeps "\n" one byte on Windows too; the checkpoints
        # record byte sizes, which the tests compare with len(text).
        path = folder / name
        path.write_text(text, encoding="utf-8", newline="")
        return path
    return file


def test_turns_on_two_branches_each_get_a_rewind_point_holding_their_own_edits(store, work):
    mine, theirs = work("a.py"), work("b.py")
    talk = Conversation(store)
    talk.prompt("a1")
    talk.prompt("b1")
    talk.edit("a1", mine, "by a\n")
    talk.edit("b1", theirs, "by b\n")

    assert list(talk.prompts.get("a1")["files"]) == [str(mine)]
    assert list(talk.prompts.get("b1")["files"]) == [str(theirs)]
    for user in ("a1", "b1"):
        talk.select(talk.reply(user))
        point = list_rewind_points("s")[0]
        assert (point["msg_id"], point["checkpoint"], point["code_available"]) == (user, "prompt", True)


def test_a_rewind_after_interleaved_edits_restores_its_branch_and_leaves_the_other(store, work):
    mine, theirs, shared = work("a.py"), work("b.py"), work("shared.py")
    talk = Conversation(store)
    talk.prompt("a1")
    talk.prompt("b1")
    talk.edit("b1", shared, "b wrote first\n")
    talk.edit("a1", mine, "by a\n")
    talk.edit("b1", theirs, "by b\n")
    talk.edit("a1", shared, "b wrote first\na added\n")
    talk.reply("b1")
    talk.select(talk.reply("a1"))

    result = _apply("s", "a1")

    assert result["status"] == "committed"
    assert sorted(result["restored_paths"]) == sorted([str(mine), str(shared)])
    assert mine.read_text() == "original\n"
    assert theirs.read_text() == "by b\n"
    assert shared.read_text() == "b wrote first\n"

    talk.select("b1_reply")
    assert _apply("s", "b1")["status"] == "committed"
    assert theirs.read_text() == "original\n" and shared.read_text() == "original\n"
    assert mine.read_text() == "original\n"


@pytest.mark.parametrize("a_writes_again", [False, True])
def test_a_file_the_other_branch_edited_on_top_is_left_alone_and_named(store, work, a_writes_again):
    mine, shared = work("a.py"), work("shared.py")
    talk = Conversation(store)
    talk.prompt("a1")
    talk.prompt("b1")
    talk.edit("a1", mine, "by a\n")
    talk.edit("a1", shared, "a\n")
    talk.edit("b1", shared, "a\nb\n")
    if a_writes_again:
        talk.edit("a1", shared, "a\nb\na again\n")
    talk.reply("b1")
    talk.select(talk.reply("a1"))
    before = shared.read_text()

    plan = plan_rewind("s", "a1")
    result = rewind_to("s", "a1")

    assert plan["status"] == "blocked" and plan["error_code"] == "file_conflict"
    assert [(row["path"], row["reason"]) for row in plan["skipped"]] == [(str(shared), "changed_since_edit")]
    assert result["status"] == "blocked"
    assert shared.read_text() == before and mine.read_text() == "by a\n"
    # The conversation can still go back; the files stay.
    assert _apply("s", "a1", "conversation")["status"] == "committed"
    assert shared.read_text() == before


def test_a_later_prompt_goes_back_only_as_far_as_the_other_branchs_edit(store, work):
    shared = work("shared.py")
    talk = Conversation(store)
    talk.prompt("a0")
    talk.edit("a0", shared, "a0\n")
    talk.reply("a0")
    talk.prompt("a1", after="a0_reply")
    talk.reply("a1")
    talk.prompt("b1", after="a0_reply")
    talk.edit("b1", shared, "a0\nb once\n")
    talk.prompt("a2", after="a1_reply")
    talk.edit("b1", shared, "a0\nb once\nb twice\n")
    talk.edit("a2", shared, "a0\nb once\nb twice\na2\n")
    talk.reply("b1")
    talk.select(talk.reply("a2"))

    # Read when a2 arrived, the file did not yet hold b's second edit.
    assert plan_rewind("s", "a2")["files"][0]["deletions"] == 1
    assert _apply("s", "a1")["status"] == "committed"
    assert shared.read_text() == "a0\nb once\nb twice\n"


def test_a_scheduled_runs_edit_survives_a_rewind_on_the_selected_branch(store, work):
    report = work("report.md")
    talk = Conversation(store)
    talk.prompt("run1")  # the run's own branch
    talk.prompt("a1")
    talk.edit("run1", report, "from the run\n")
    talk.reply("run1")
    talk.reply("a1")
    talk.prompt("a2", after="a1_reply")
    # The next prompt reads every file the conversation has edited, on any branch.
    assert talk.prompts.get("a2")["files"][str(report)]["size"] == len("from the run\n")
    talk.edit("a2", report, "from the run\nfrom a\n")
    talk.select(talk.reply("a2"))

    assert str(report) not in talk.prompts.get("a1")["files"]
    assert _apply("s", "a1")["status"] == "committed"
    assert report.read_text() == "from the run\n"


def test_a_turn_without_its_own_point_records_in_the_one_it_belongs_to(store, work):
    by_agent, by_followup = work("agent.py"), work("followup.py")
    talk = Conversation(store)
    talk.prompt("a1")
    talk.prompt("b1")
    # A sub-agent of a1's turn, and a runtime-started turn after a1's reply.
    assert join_prompt_checkpoint(store, "s", "sub_reply", spawned=True, spawned_by="a1_reply") == "a1"
    talk.edit("a1", by_agent, "agent\n", turn="sub_reply")
    talk.reply("a1")
    _append(store, "s", "note", "user", "a1_reply", display="runtime")
    assert join_prompt_checkpoint(
        store, "s", "note_reply",
        ancestors=lambda: [row["id"] for row in reversed(store.get_branch("s", "note"))],
    ) == "a1"
    talk.edit("a1", by_followup, "followup\n", turn="note_reply")

    assert sorted(talk.prompts.get("a1")["files"]) == sorted([str(by_agent), str(by_followup)])
    assert talk.prompts.get("b1")["files"] == {}


@pytest.mark.parametrize("fields,captured", [
    ({"advance_head": False, "side_branch": True, "branch_id": "b", "source": "web"}, True),
    ({"advance_head": False, "background": True, "source": "scheduler", "speaker_kind": "runtime"}, True),
    ({"advance_head": False, "source": "agent_spawn", "spawn_caller": "a1_reply"}, False),
    # A job in the same conversation names that conversation as its caller's.
    ({"advance_head": False, "source": "agent_spawn", "spawn_caller": "a1_reply",
      "spawned_from_session": "s"}, False),
])
def test_a_turn_beside_the_selected_branch_gets_a_rewind_point(store, fields, captured):
    from openprogram.runtime.conversation.turn_runtime import prep

    talk = Conversation(store)
    talk.prompt("a1")
    talk.prompt("b1")  # the newest point is another branch's
    writer = SimpleNamespace(persist_user=lambda _id, _msg, _caller: None)
    req = TurnRequest(session_id="s", user_text="hello", agent_id="main", history_override=[], **fields)

    prep.prepare_turn(
        db=store, req=req, writer=writer, user_msg_id="new", on_event=lambda _e: None,
        assistant_msg_id="new_reply",
    )

    if captured:
        assert talk.prompts.get("new")["turn_ids"] == ["new_reply"]
    else:
        assert talk.prompts.get("new") is None
        assert talk.prompts.get("a1")["turn_ids"] == ["a1_reply", "new_reply"]


def test_a_file_tool_records_in_the_checkpoint_of_the_turn_it_runs_in(store, work):
    import contextvars

    from openprogram.state.store import _current_turn_id, _store
    from openprogram.state.store.snapshot.checkpoint.helpers import (
        checkpoint_after_edit,
        checkpoint_before_edit,
    )

    mine, theirs = work("a.py"), work("b.py")
    talk = Conversation(store)
    talk.prompt("a1")
    talk.prompt("b1")
    shim = SimpleNamespace(session_id="s", store=store)

    def tool_edit(turn: str, path: Path) -> None:
        _store.set(shim)
        _current_turn_id.set(turn)
        assert checkpoint_before_edit(str(path)) is True
        path.write_text(f"by {turn}\n", encoding="utf-8", newline="")
        assert checkpoint_after_edit(str(path), "edit") is True

    # The older prompt's turn edits after the newer prompt arrived.
    contextvars.copy_context().run(tool_edit, "a1_reply", mine)
    contextvars.copy_context().run(tool_edit, "b1_reply", theirs)

    assert list(talk.prompts.get("a1")["files"]) == [str(mine)]
    assert list(talk.prompts.get("b1")["files"]) == [str(theirs)]


def _owning(monkeypatch, *turns):
    """The rewind's turns own these background turns."""
    monkeypatch.setattr(
        "openprogram.runtime.conversation.history_ownership.owned_change_set_closure",
        lambda _sid, _turns: {
            "status": "ready", "owned_turn_ids": list(turns), "blockers": [], "linked": [],
        },
    )


@pytest.mark.parametrize("edited_before", [False, True])
def test_a_late_turn_of_an_earlier_prompt_goes_back_with_the_later_prompts_edit(
    store, work, monkeypatch, edited_before,
):
    shared = work("shared.py")
    talk = Conversation(store)
    start = "original\n"
    after = "ROOT"
    if edited_before:
        # The earlier prompt's point then already lists the file.
        talk.prompt("a0")
        talk.edit("a0", shared, start := "a0\n")
        after = talk.reply("a0")
    talk.prompt("a1", after=after)
    assert join_prompt_checkpoint(store, "s", "job_reply", spawned=True, spawned_by="a1_reply") == "a1"
    talk.reply("a1")
    talk.prompt("a2", after="a1_reply")
    talk.edit("a2", shared, start + "by a2\n")
    # a1's background turn first edits the file after a2's turn did.
    talk.edit("a1", shared, start + "by a2\nby job\n", turn="job_reply")
    talk.select(talk.reply("a2"))
    _owning(monkeypatch, "job_reply")

    if edited_before:
        # A later prompt on the same line does not replace what a1's point read.
        assert talk.prompts.get("a1")["files"][str(shared)]["size"] == len(start)
    assert _apply("s", "a1")["status"] == "committed"
    assert shared.read_text() == start


def test_a_late_turn_goes_back_to_what_the_other_branch_left(store, work, monkeypatch):
    shared = work("shared.py")
    talk = Conversation(store)
    talk.prompt("a1")
    assert join_prompt_checkpoint(store, "s", "job_reply", spawned=True, spawned_by="a1_reply") == "a1"
    talk.prompt("b1")
    talk.reply("a1")
    talk.prompt("a2", after="a1_reply")
    talk.edit("b1", shared, "b\n")
    talk.edit("a2", shared, "b\nby a2\n")
    talk.edit("a1", shared, "b\nby a2\nby job\n", turn="job_reply")
    talk.reply("b1")
    talk.select(talk.reply("a2"))
    _owning(monkeypatch, "job_reply")

    assert _apply("s", "a1")["status"] == "committed"
    assert shared.read_text() == "b\n"


def test_an_edit_a_files_only_rewind_took_back_does_not_block_the_other_branch(store, work):
    shared = work("shared.py")
    talk = Conversation(store)
    talk.prompt("a1")
    talk.prompt("b1")
    talk.edit("b1", shared, "b\n")
    talk.edit("a1", shared, "b\na\n")
    talk.reply("b1")
    talk.select(talk.reply("a1"))
    assert _apply("s", "a1", "code")["status"] == "committed"
    assert shared.read_text() == "b\n"

    talk.select("b1_reply")
    assert plan_rewind("s", "b1")["skipped"] == []
    assert _apply("s", "b1")["status"] == "committed"
    assert shared.read_text() == "original\n"

