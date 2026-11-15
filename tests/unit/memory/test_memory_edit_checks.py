"""A memory writer hears about a broken rule on the edit that broke it.

The Write and Edit tools run the commit's checks on a copy of the stage after
each change to a Topic file, so the writer can fix it before the turn ends and
the whole turn is discarded. Mechanical slips are repaired rather than
reported, and every format error names the file it is in.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


def _call(handler, **arguments) -> str:
    result = asyncio.run(handler(arguments))
    assert not result["is_error"], result
    return result["content"][0]["text"]


def _paragraph(fact: str, label: str, ref: str) -> str:
    return f"# Owner\n\n{fact}[^e1]\n\n[^e1]: Time: `2026-08-10`; Sources: [{label}]({ref})\n"


@pytest.fixture
def writer(tmp_path: Path, chats):
    from openprogram.state.memory.chat_refs import link
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.management.tools import management_tools

    root = tmp_path / "memory"
    (root / "topics").mkdir(parents=True)
    earlier = chats.say("checks", "m0", "I like coffee")
    in_batch = chats.say("checks", "m1", "I like tea")
    workspace = MemoryWorkspace(root, allowed_new_source_refs={in_batch})
    workspace.turn_baseline = workspace.baseline()
    handlers = {tool.name: tool.handler for tool in management_tools(workspace, [])}
    try:
        yield workspace, handlers, link(in_batch), link(earlier)
    finally:
        workspace.close()


def test_a_valid_edit_comes_back_clean_and_commits(writer):
    workspace, handlers, ref, _ = writer
    reply = _call(handlers["Write"], file_path="topics/owner.md",
                  content=_paragraph("Owner likes tea.", "likes tea", ref))
    assert "memory checks" not in reply
    workspace.commit_edits(*workspace.turn_baseline)
    assert "Owner likes tea." in (workspace.memory_dir / "topics/owner.md").read_text()


def test_a_broken_edit_is_reported_with_the_fix_and_can_be_repaired(writer):
    workspace, handlers, ref, earlier = writer
    reply = _call(handlers["Write"], file_path="topics/owner.md",
                  content=_paragraph("Owner likes coffee.", "likes coffee", earlier))
    assert "fail the memory checks" in reply
    assert "not evidence of this transaction" in reply
    assert "keep its existing" in reply
    # The edit itself is kept, so the writer fixes it rather than redoing it.
    assert (workspace.stage_dir / "topics/owner.md").is_file()

    fixed = _call(handlers["Edit"], file_path="topics/owner.md",
                  old_string=f"[likes coffee]({earlier})", new_string=f"[likes tea]({ref})")
    assert "memory checks" not in fixed
    workspace.commit_edits(*workspace.turn_baseline)


def test_uncited_prose_is_reported_by_file(writer):
    _workspace, handlers, ref, _ = writer
    content = _paragraph("Owner likes tea.", "likes tea", ref) + "\n- a bullet with no evidence\n"
    reply = _call(handlers["Write"], file_path="topics/owner.md", content=content)
    # Checked by who wrote it: a writer's paragraph must cite.
    assert "in topics/owner.md cites no chat message" in reply
    assert "Bullet lists" in reply


def test_an_edit_to_a_file_written_outside_is_reported(writer):
    workspace, handlers, ref, _ = writer
    (workspace.memory_dir / "topics/ext.md").write_text("# Ext\n\nA note.\n", encoding="utf-8")
    workspace._refresh_stage()
    workspace.turn_baseline = workspace.baseline()
    reply = _call(handlers["Write"], file_path="topics/ext.md",
                  content=_paragraph("Owner likes tea.", "likes tea", ref))
    assert "written outside OpenProgram and is read-only" in reply
    assert "Leave it exactly as it was" in reply


def test_non_topic_files_are_not_checked(writer):
    _workspace, handlers, _ref, _ = writer
    assert _call(handlers["Write"], file_path="MEMORY.md", content="# Map\n") == (
        "Wrote 6 bytes to MEMORY.md"
    )


def test_leftover_and_repeated_definitions_are_repaired():
    from openprogram.state.memory.management.topic_normalization import drop_leftover_definitions

    text = (
        "Fact.[^e-aaaaaaaaaa][^e1] ^1234abcd\n\n"
        "[^e-aaaaaaaaaa]: Time: `2026`; Sources: [a](x)\n"
        "[^e-aaaaaaaaaa]: Time: `2026`; Sources: [a](x)\n"
        "[^e-bbbbbbbbbb]: Time: `2026`; Sources: [b](y)\n"
        "[^e1]: Time: `2026`; Sources: [c](z)\n"
        "[^e2]: Time: `2026`; Sources: [d](w)\n"
    )
    repaired = drop_leftover_definitions(text)
    assert repaired.count("[^e-aaaaaaaaaa]:") == 1
    # A Runtime definition nothing cites is left over from a moved paragraph.
    assert "[^e-bbbbbbbbbb]:" not in repaired
    # The writer's own unused footnote may be evidence it forgot to cite.
    assert "[^e2]:" in repaired and "[^e1]:" in repaired


def test_a_format_error_names_its_file(tmp_path: Path):
    from openprogram.state.memory.markdown import TopicFormatError, parse_topic_tree

    topics = tmp_path / "topics"
    (topics / "people").mkdir(parents=True)
    (topics / "people/dave.md").write_text("Dave cooks.[^e-cccccccccc] ^1234abcd\n")
    with pytest.raises(TopicFormatError, match=r"undefined footnote.*\(in topics/people/dave\.md\)"):
        parse_topic_tree(topics)
