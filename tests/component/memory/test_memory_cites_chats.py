"""Memory cites the chat a fact came from instead of keeping a copy of it.

A footnote links ``/s/<session>#<message>``, the chat page's own address;
following it reads the conversation around that message. A workspace from
before this keeps working: its ``sources/`` copy is turned into chat links
once and removed, with Git keeping the old copy.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest


def test_a_reference_and_its_chat_link_name_the_same_message():
    from openprogram.state.memory import chat_refs

    ref = chat_refs.ref("s-1", "m:2")
    assert ref == "openprogram/s-1/m:2"
    assert chat_refs.link(ref) == "/s/s-1#m:2"
    assert chat_refs.from_target("/s/s-1#m:2") == ref
    assert chat_refs.from_target(ref) == ref
    for target in ("../sources/x.md#source-1", "/s/s-1", "https://x/s/a#b", ""):
        assert chat_refs.from_target(target) is None


def test_an_excerpt_shows_the_cited_turn_among_its_neighbours(chats):
    from openprogram.state.memory import chat_refs

    for index, (role, text) in enumerate([
        ("user", "first"), ("assistant", "second"), ("tool", "ls output"),
        ("user", "the cited one"), ("assistant", "after"),
    ]):
        chats.say("s1", f"m{index}", text, role=role)
    lookup = chat_refs.ChatLookup()
    excerpt = lookup.excerpt("openprogram/s1/m3", before=2, after=1)
    assert [(turn["text"], turn["cited"]) for turn in excerpt["turns"]] == [
        ("second", False), ("the cited one", True), ("after", False),
    ]
    rendered = chat_refs.render_excerpt(excerpt)
    assert "**→** **user**" in rendered
    assert "ls output" not in rendered

    assert lookup.excerpt("openprogram/gone/m1")["deleted"] is True
    assert "deleted" in chat_refs.render_excerpt({"deleted": True})


def _anchor(ref: str) -> str:
    return "source-" + hashlib.sha256(ref.encode()).hexdigest()[:16]


@pytest.fixture
def old_workspace(tmp_path: Path) -> Path:
    """A workspace as the source-archive layout left it, under Git."""
    root = tmp_path / "memory"
    chat_ref = "openprogram/wechat-direct/af66_reply"
    imported = "claude-code/5141bccad4f0913b/m7"
    archive = root / "sources/openprogram/_v2/wechat-direct.md"
    archive.parent.mkdir(parents=True)
    archive.write_text(
        "<!-- openprogram-source-archive:v2 -->\n\n"
        f'<a id="{_anchor(chat_ref)}"></a>\n'
        f"<!-- source-id:{chat_ref} -->\n"
        "<!-- record-lines:1 -->\n[2026-10-16] agent: hello\n",
        encoding="utf-8",
    )
    other = root / "sources/claude-code/_v2/5141bccad4f0913b.md"
    other.parent.mkdir(parents=True)
    other.write_text(
        f'<a id="{_anchor(imported)}"></a>\n<!-- source-id:{imported} -->\n',
        encoding="utf-8",
    )
    topic = root / "topics/people/owner.md"
    topic.parent.mkdir(parents=True)
    topic.write_text(
        "# Owner\n\nGreets in Chinese.[^e-1] ^abc12345\n\n"
        "[^e-1]: Time: `2026-10`; Sources: "
        f"[你好](../../sources/openprogram/_v2/wechat-direct.md#{_anchor(chat_ref)}), "
        f"[imported](../../sources/claude-code/_v2/5141bccad4f0913b.md#{_anchor(imported)})\n",
        encoding="utf-8",
    )
    (root / "MEMORY.md").write_text("# Memory\n", encoding="utf-8")
    from openprogram.state.memory.store import _ensure_git_history

    _ensure_git_history(root)
    return root


def test_the_old_source_copy_becomes_chat_links_once(old_workspace: Path):
    from openprogram.state.memory import store
    from openprogram.state.memory.markdown import parse_topic_tree

    root = old_workspace
    assert store.cite_chats_directly(root) is True

    text = (root / "topics/people/owner.md").read_text(encoding="utf-8")
    assert "[你好](/s/wechat-direct#af66_reply)" in text
    # Never a chat here: it reads as an original that is gone.
    assert "[imported](/s/claude-code-5141bccad4f0913b#m7)" in text
    assert not (root / "sources").exists()
    assert parse_topic_tree(root / "topics")[0].source_refs == (
        "openprogram/wechat-direct/af66_reply",
        "openprogram/claude-code-5141bccad4f0913b/m7",
    )
    log = subprocess.run(
        ["git", "-C", str(root), "log", "--format=%s"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    assert log[0] == "memory: cite chats directly"
    # Git keeps the copy that was removed.
    assert "hello" in subprocess.run(
        ["git", "-C", str(root), "show",
         "HEAD~1:sources/openprogram/_v2/wechat-direct.md"],
        capture_output=True, text=True, check=True,
    ).stdout

    assert store.cite_chats_directly(root) is False


def test_a_project_workspace_is_converted_when_it_is_resolved(
    old_workspace: Path, monkeypatch,
):
    """Project layers are read without ``store.ensure``, so resolving the
    root is where an old project workspace gets its chat links."""
    from openprogram.state.memory import project_layer
    from openprogram.state.store.project import layout

    monkeypatch.setattr(layout, "memory_dir", lambda _project_id: old_workspace)
    assert project_layer.project_memory_root("p1") == old_workspace.resolve()
    assert not (old_workspace / "sources").exists()
    assert "/s/wechat-direct#af66_reply" in (
        old_workspace / "topics/people/owner.md"
    ).read_text(encoding="utf-8")
