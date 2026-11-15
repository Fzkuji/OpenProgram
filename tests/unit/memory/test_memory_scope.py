"""Who may put what into memory, and which chats a new paragraph may cite.

Memory cites chat messages instead of keeping its own copy of them, so the
checks that matter are on the citation: a new paragraph rests on a message
the session store holds and memory may cite, a paired speaker only appends,
and committed citations are left alone by later edits.
"""
from __future__ import annotations

import json
import sys
from contextlib import closing

import pytest


@pytest.fixture
def authorities(tmp_path, monkeypatch):
    import openprogram.state.paths as paths
    from openprogram.runtime.permissions import authority

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    authority._reset_owner_cache_for_tests()
    return (
        authority.local_owner_authority(),
        authority.paired_channel_authority("telegram", "main", "u456", "B"),
    )


def _create(topic: str, content: str, source: str) -> list[dict]:
    return [{
        "op": "create_record",
        "content": content,
        "time": "2026-08-10",
        "sources": [{"source": source, "label": "fact"}],
        "destination": {
            "topic_path": f"topics/{topic}", "headings": [], "position": "end",
        },
    }]


def test_a_new_paragraph_cites_a_message_the_chat_holds(tmp_path, chats):
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.management.transaction import TransactionError

    root = tmp_path / "memory"
    said = chats.link("s1", "m1", "I live in Kyoto")
    with closing(MemoryWorkspace(root)) as space:
        space.update(
            memory_changes=_create("home.md", "The owner lives in Kyoto.", said),
            git_commit="off",
        )
        with pytest.raises(TransactionError, match="missing source reference"):
            space.update(
                memory_changes=_create("made-up.md", "Invented.", "/s/s1#m9"),
                git_commit="off",
            )
    assert "[fact](/s/s1#m1)" in (root / "topics/home.md").read_text()
    assert not (root / "topics/made-up.md").exists()


def test_a_tool_result_or_runtime_turn_is_not_citable(tmp_path, chats):
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.management.transaction import TransactionError

    root = tmp_path / "memory"
    tool = chats.link("s1", "t1", "ls output", role="tool")
    notice = chats.link("s1", "r1", "job finished", source="job_followup")
    with closing(MemoryWorkspace(root)) as space:
        for source in (tool, notice):
            with pytest.raises(TransactionError, match="not a message memory may cite"):
                space.update(
                    memory_changes=_create("x.md", "A fact.", source),
                    git_commit="off",
                )


def test_a_chat_edit_without_sources_cites_the_message_that_asked(tmp_path, chats):
    from openprogram.state.memory.management import MemoryWorkspace

    root = tmp_path / "memory"
    asked = chats.say("s1", "m1", "remember that I prefer tea")
    with closing(MemoryWorkspace(root)) as space:
        space.update(
            memory_changes=[{
                "op": "create_record", "content": "The owner prefers tea.",
                "time": "undated",
                "destination": {
                    "topic_path": "topics/drinks.md", "headings": [],
                    "position": "end",
                },
            }],
            current_ref=asked, git_commit="off",
        )
    assert "[chat](/s/s1#m1)" in (root / "topics/drinks.md").read_text()


def test_a_restricted_edit_cites_only_its_own_conversation(tmp_path, chats):
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.management.transaction import TransactionError

    root = tmp_path / "memory"
    mine = chats.say("mine", "m1", "my fact")
    theirs = chats.link("theirs", "m1", "their fact")
    with closing(MemoryWorkspace(root)) as space:
        with pytest.raises(TransactionError, match="not evidence of this transaction"):
            space.update(
                memory_changes=_create("x.md", "Borrowed.", theirs),
                allowed_refs={mine}, git_commit="off",
            )


def test_paired_append_boundary_cannot_rewrite_existing_topics(tmp_path, chats):
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.management.transaction import TransactionError
    from openprogram.state.memory.markdown import parse_topic_tree

    root = tmp_path / "memory"
    said = chats.link("s1", "m1", "original evidence")
    with closing(MemoryWorkspace(root)) as space:
        space.update(
            memory_changes=_create("note.md", "Original fact.", said),
            git_commit="off", append_only=True,
        )
        original = (root / "topics/note.md").read_text(encoding="utf-8")
        block = parse_topic_tree(root / "topics")[0].memory_id
        with pytest.raises(TransactionError) as caught:
            space.update(
                memory_changes=[{
                    "op": "update_record", "memory_id": block,
                    "content": "Rewritten fact.", "time": "2026-08-10",
                    "sources": [{"source": said, "label": "fact"}],
                }],
                git_commit="off", append_only=True,
            )
    assert caught.value.code == "APPEND_ONLY_REQUIRED"
    assert (root / "topics/note.md").read_text(encoding="utf-8") == original


def test_unchanged_paragraphs_keep_their_existing_references(tmp_path, chats):
    """An edit elsewhere must not re-litigate a block's committed citations,
    even when the chat it cites has since been deleted."""
    from openprogram.state.memory.management import MemoryWorkspace

    root = tmp_path / "memory"
    first = chats.link("gone", "m1", "the first evidence")
    second = chats.link("s2", "m1", "the second evidence")
    with closing(MemoryWorkspace(root)) as space:
        space.update(
            memory_changes=_create("first.md", "First fact.", first),
            git_commit="off",
        )
        kept = (root / "topics/first.md").read_text(encoding="utf-8")
        del chats.sessions["gone"]
        space.update(
            memory_changes=_create("second.md", "Second fact.", second),
            git_commit="off",
        )
    assert (root / "topics/first.md").read_text(encoding="utf-8") == kept
    assert (root / "topics/second.md").exists()


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX modes are not meaningful on Windows"
)
def test_memory_files_are_owner_only(tmp_path, monkeypatch, chats):
    import openprogram.state.paths as paths
    from openprogram.state.memory import store
    from openprogram.state.memory.management import MemoryWorkspace

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    root = store.ensure()
    said = chats.link("s1", "m1", "private speech")
    with closing(MemoryWorkspace(root)) as space:
        space.update(memory_changes=_create("me.md", "A private fact.", said))

    # Git's own files take git's modes; the private root keeps them unreachable.
    assert root.stat().st_mode & 0o777 == 0o700
    for path in root.rglob("*"):
        if path.is_symlink() or ".git" in path.relative_to(root).parts:
            continue
        mode = path.stat().st_mode & 0o777
        assert mode & 0o077 == 0, f"{path} is group/world accessible ({mode:o})"
    assert (tmp_path / "state").stat().st_mode & 0o777 == 0o700


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX modes are not meaningful on Windows"
)
def test_an_existing_world_readable_workspace_is_migrated(tmp_path, monkeypatch):
    import openprogram.state.paths as paths
    from openprogram.state.memory import store

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    root = store.ensure()
    (root / "topics/me.md").write_text("# Me\n", encoding="utf-8")

    # Put the workspace back the way an older install left it.
    for path in root.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    root.chmod(0o755)
    store._permissions_migrated.discard(root)

    store.ensure()

    for path in root.rglob("*"):
        assert path.stat().st_mode & 0o077 == 0, path


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX modes are not meaningful on Windows"
)
def test_the_workspace_lock_is_owner_only(tmp_path):
    from openprogram.state.memory.management.transaction import workspace_write_lock
    from openprogram.state.memory.workspace_layout import runtime_dir

    root = tmp_path / "memory"
    root.mkdir()
    with workspace_write_lock(root, timeout_s=1.0):
        pass
    lock = runtime_dir(root) / "write.lock"
    assert lock.stat().st_mode & 0o077 == 0


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX modes are not meaningful on Windows"
)
def test_permission_migration_does_not_follow_symlinks(tmp_path):
    from openprogram.state.memory import store

    root = tmp_path / "memory"
    (root / "topics").mkdir(parents=True)
    outsider = tmp_path / "outside.md"
    outsider.write_text("not ours\n", encoding="utf-8")
    outsider.chmod(0o644)
    (root / "topics" / "link.md").symlink_to(outsider)

    store.restrict_workspace_permissions(root)

    # The link's target belongs to somebody else and keeps its mode.
    assert outsider.stat().st_mode & 0o777 == 0o644


def test_a_message_without_authority_is_refused(tmp_path):
    from openprogram.state.authority import stamp_schema
    from openprogram.state.memory import writing

    stamped = stamp_schema({
        "role": "user", "id": "m1", "content": "unattributed speech",
    })
    with pytest.raises(ValueError, match="requires authority"):
        writing._records("s1", [stamped])


def test_history_written_before_authority_is_still_accepted(tmp_path):
    """A node with no schema stamp predates attribution and stays readable."""
    from openprogram.state.memory import writing

    rows = writing._records("s1", [
        {"role": "user", "id": "m1", "content": "old conversation"},
    ])
    assert [row.ref for row in rows] == ["openprogram/s1/m1"]


def test_cli_turns_carry_owner_authority(authorities):
    """CLI messages reach the writer attributed to the owner."""
    from openprogram.runtime.permissions import authority
    from openprogram.state import authority as state_authority
    from openprogram.state.memory import writing

    owner, _paired = authorities
    user_msg = state_authority.stamp_schema({
        "role": "user", "id": "m1", "content": "hello", **owner,
    })
    reply_msg = state_authority.stamp_schema({
        "role": "assistant", "id": "m1_reply", "content": "hi",
        **authority.runtime_authority(owner, "cli"),
    })

    rows = writing._records("s1", [user_msg, reply_msg])
    assert [row.message_id for row in rows] == ["m1", "m1_reply"]


def test_status_does_not_disclose_the_workspace_path(tmp_path, monkeypatch):
    from openprogram.programs.tools.knowledge.memory import memory as memory_tools
    from openprogram.state.memory import store
    from openprogram.state.memory.retrieval import inspect

    root = tmp_path / "a-revealing-directory-name" / "memory"
    (root / "topics").mkdir(parents=True)
    monkeypatch.setattr(store, "ensure", lambda: root)

    overview = memory_tools.memory_get()
    assert "a-revealing-directory-name" not in overview

    # The owner's own surface still gets the real location.
    owner_view = inspect.status(root, include_path=True)
    assert owner_view["workspace_path"] == str(root)
    assert "workspace_path" not in json.dumps(inspect.status(root))
