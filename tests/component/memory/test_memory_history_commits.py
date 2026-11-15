"""Every change to memory is its own commit, named for what made it.

Edits made outside OpenProgram are committed on their own before a write
installs, a write and the reorganizing it triggers are separate commits, and
a rejected write leaves history untouched.
"""
from __future__ import annotations

import subprocess
from contextlib import closing
from pathlib import Path

import pytest


def _record(chats, message_id: str, content: str, ordinal: int):
    """A chat turn memory may cite, said in the ``history`` session."""
    from openprogram.state.memory.runtime.state import ChatTurn

    chats.say("history", message_id, content)
    return ChatTurn(
        session_id="history", message_id=message_id, ordinal=ordinal,
        role="user", content=content, timestamp="2026-08-10T12:00:00+08:00",
    )


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True,
    ).stdout


def _subjects(root: Path) -> list[str]:
    return _git(root, "log", "--format=%s").splitlines()


def _write(root: Path, record, path: str, fact: str) -> None:
    """One writer turn: stage a cited paragraph and install it."""
    from openprogram.state.memory.management import MemoryWorkspace

    from openprogram.state.memory.chat_refs import link

    with closing(MemoryWorkspace(root, allowed_new_source_refs={record.ref})) as space:
        baseline = space.baseline()
        target = space.stage_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            f"# Notes\n\n{fact}[^e1]\n\n"
            f"[^e1]: Time: `2026-08-10`; Sources: [note]({link(record.ref)})\n"
        )
        space.commit_edits(*baseline)


@pytest.fixture
def memory(tmp_path: Path, chats):
    from openprogram.state.memory.management.transaction import git_commit_state
    from openprogram.state.memory.store import _ensure_git_history

    root = tmp_path / "memory"
    (root / "topics").mkdir(parents=True)
    first, second = _record(chats, "m1", "tea", 1), _record(chats, "m2", "walks", 2)
    _ensure_git_history(root)
    _write(root, first, "topics/tea.md", "Owner likes tea.")
    git_commit_state(root, "memory: write tea")
    return root, second


def test_an_outside_edit_gets_its_own_commit_before_the_next_write(memory):
    from openprogram.state.memory.management.transaction import git_commit_state

    root, second = memory
    tea = root / "topics/tea.md"
    tea.write_text(tea.read_text().replace("likes tea", "likes green tea"))

    _write(root, second, "topics/walks.md", "Owner walks daily.")
    git_commit_state(root, "memory: write walks")

    assert _subjects(root)[:3] == [
        "memory: write walks", "memory: edits made outside OpenProgram", "memory: write tea",
    ]
    outside = _git(root, "show", "--name-only", "--format=", "HEAD~1").split()
    assert outside == ["topics/tea.md"]
    assert "topics/tea.md" not in _git(root, "show", "--name-only", "--format=", "HEAD").split()


def test_a_rejected_write_leaves_history_alone(memory):
    from openprogram.state.memory.management import MemoryWorkspace

    root, second = memory
    tea = root / "topics/tea.md"
    tea.write_text(tea.read_text().replace("likes tea", "likes green tea"))
    head = _git(root, "rev-parse", "HEAD")

    with closing(MemoryWorkspace(root, allowed_new_source_refs={second.ref})) as space:
        baseline = space.baseline()
        (space.stage_dir / "topics/bad.md").write_text("# Bad\n\n- uncited bullet\n")
        with pytest.raises(Exception):
            space.commit_edits(*baseline)

    assert _git(root, "rev-parse", "HEAD") == head


def test_a_write_and_the_reorganizing_it_triggers_are_separate_commits(tmp_path: Path, chats):
    from openprogram.state.memory.runtime.online import OnlineMemoryRuntime
    from openprogram.state.memory.store import _ensure_git_history

    root = tmp_path / "memory"
    record = _record(chats, "m1", "tea", 1)

    def writer(space, batch):
        _write(root, batch[0], "topics/tea.md", "Owner likes tea.")
        return ["topics/tea.md"]

    def organizer(space):
        tea = root / "topics/tea.md"
        tea.write_text(tea.read_text().replace("# Notes", "# Drinks"))

    runtime = OnlineMemoryRuntime(root, token_counter=len)
    runtime.local_batch_threshold = 1
    runtime.local_token_threshold = 1
    (root / "topics").mkdir(parents=True)
    _ensure_git_history(root)
    assert runtime.process(
        [record], writer, local_manager=organizer, force=True,
        history_message='memory: write "Tea chat" (s1)',
    )
    assert _subjects(root)[:2] == [
        "memory: reorganize after writing", 'memory: write "Tea chat" (s1) (1 turns)',
    ]


def test_a_writer_failure_is_announced_once_per_reason(tmp_path: Path, monkeypatch):
    import openprogram.runtime.events as events
    from openprogram.state.memory.runtime import writer_status

    frames = []
    monkeypatch.setattr(events, "emit_ws_frame", frames.append)
    root = tmp_path / "memory"
    for code in ("MODEL_RATE_LIMIT", "MODEL_RATE_LIMIT", "MODEL_AUTHENTICATION"):
        writer_status.record_failure(root, code, retryable=False)
    writer_status.record_success(root)
    writer_status.record_failure(root, "MODEL_AUTHENTICATION", retryable=False)
    writer_status.record_success(root)
    # Retried on its own, so nobody is interrupted for it.
    writer_status.record_failure(root, "MODEL_TRANSPORT", retryable=True)

    assert [frame["data"]["reason_code"] for frame in frames] == [
        "MODEL_RATE_LIMIT", "MODEL_AUTHENTICATION", "MODEL_AUTHENTICATION",
    ]
    assert {frame["type"] for frame in frames} == {"memory_writer_failed"}
