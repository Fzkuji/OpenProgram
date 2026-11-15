"""Files written outside OpenProgram, and paragraphs that cite nothing.

Whatever a Topic file holds is memory and is found by search. A file
another tool wrote is read where it lies and never rewritten, moved or
linked into by a writer or the organizer; a person's own note may cite
nothing, a writer's paragraph must. Whether a paragraph cites is checked
where a write commits, by who wrote it, not by the parser.
"""
from __future__ import annotations

import re
from contextlib import closing
from pathlib import Path

import pytest

NOTE = (
    "# Note\n"
    "\n"
    "A fact worth keeping.[^e-1f4c7a2b90] ^abc12345\n"
    "\n"
    "[^e-1f4c7a2b90]: Time: `2026-01-01`; Sources: [D1:1](/s/s1#d1-1)\n"
)
# What another tool writes: no IDs, a footnote that is never defined.
EXTERNAL = (
    "# Reading list\n"
    "\n"
    "Finish the Raft paper before the offsite.[^1]\n"
    "\n"
    "Ask Mina about the cormorant survey.\n"
)


@pytest.fixture(autouse=True)
def _conversation(chats):
    chats.say("s1", "d1-1", "remember this")


def _root(tmp_path: Path, **extra: str) -> Path:
    root = tmp_path / "memory"
    (root / "topics").mkdir(parents=True)
    (root / "topics/note.md").write_text(NOTE, encoding="utf-8")
    for name, text in extra.items():
        path = root / "topics" / name.replace("__", "/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _cited(text: str, link: str, label: str = "e1") -> str:
    return f"{text}[^{label}]\n\n[^{label}]: Time: `2026-02-02`; Sources: [said]({link})\n"


def _writer(root: Path, refs: set[str]):
    from openprogram.state.memory.management import MemoryWorkspace

    return closing(MemoryWorkspace(root, allowed_new_source_refs=refs))


# -- S5: sources are checked at commit, by author --------------------------


def test_a_writer_paragraph_without_a_footnote_is_refused(tmp_path, chats):
    from openprogram.state.memory.management.transaction import TransactionError

    root = _root(tmp_path)
    ref = chats.say("s2", "m1", "the owner moved to Kyoto")
    with _writer(root, {ref}) as space:
        baseline = space.baseline()
        (space.stage_dir / "topics/home.md").write_text(
            "# Home\n\nThe owner moved to Kyoto.\n", encoding="utf-8",
        )
        with pytest.raises(TransactionError) as caught:
            space.commit_edits(*baseline)
    assert caught.value.code == "MISSING_SOURCE"
    assert not (root / "topics/home.md").exists()


def test_a_writer_may_not_cite_a_conversation_outside_its_batch(tmp_path, chats):
    from openprogram.state.memory.management.transaction import TransactionError

    root = _root(tmp_path)
    mine = chats.say("mine", "m1", "my fact")
    theirs = chats.link("theirs", "m1", "their fact")
    with _writer(root, {mine}) as space:
        baseline = space.baseline()
        (space.stage_dir / "topics/x.md").write_text(
            "# X\n\n" + _cited("Borrowed.", theirs), encoding="utf-8",
        )
        with pytest.raises(TransactionError) as caught:
            space.commit_edits(*baseline)
    assert caught.value.code == "SOURCE_NOT_ALLOWED"


def test_a_person_may_save_a_paragraph_that_cites_nothing(tmp_path):
    from openprogram.state.memory.management.transaction import staged_edit
    from openprogram.state.memory.markdown import read_topic_tree

    root = _root(tmp_path)

    def write(stage: Path) -> None:
        (stage / "topics/tea.md").write_text(
            "# Tea\n\nI prefer green tea in the morning.\n", encoding="utf-8",
        )

    ok, message = staged_edit(root, write, record_history=False)
    assert ok, message
    saved = (root / "topics/tea.md").read_text(encoding="utf-8")
    # Normalization gives a person's paragraph its ID like any other.
    assert re.search(r"green tea in the morning\. \^[0-9a-f]{8}\n", saved)
    unit = next(u for u in read_topic_tree(root / "topics").units if u.topic_path == "tea.md")
    assert unit.origin == "person" and not unit.derived_id


def test_strict_reading_checks_structure_not_sources(tmp_path):
    from openprogram.state.memory.markdown import TopicFormatError, read_checked_tree

    topics = tmp_path / "topics"
    topics.mkdir()
    (topics / "a.md").write_text("# A\n\nA note. ^aaaa1111\n", encoding="utf-8")
    (topics / "b.md").write_text("# B\n\nTwice. ^aaaa1111\n", encoding="utf-8")
    (topics / "c.md").write_text("# C\n\nNo ID.\n", encoding="utf-8")
    # A file written outside that copies one of our IDs blames no write.
    units = read_checked_tree(topics, checked={"a.md"}, outside={"b.md", "c.md"})
    assert any(u.memory_id == "aaaa1111" for u in units)
    with pytest.raises(TopicFormatError, match="duplicate memory_id"):
        read_checked_tree(topics, checked={"a.md"})
    with pytest.raises(TopicFormatError, match="duplicate memory_id"):
        read_checked_tree(topics, checked={"b.md"})
    with pytest.raises(TopicFormatError, match="block ID required"):
        read_checked_tree(topics, checked={"c.md"})


# -- A: outside files are memory, read-only ----------------------------------


def test_a_broken_outside_file_never_fails_a_write_and_keeps_its_bytes(tmp_path, chats):
    from openprogram.state.memory.management import ownership

    root = _root(tmp_path, **{"ext.md": EXTERNAL})
    ref = chats.say("s2", "m1", "the owner moved to Kyoto")
    with _writer(root, {ref}) as space:
        baseline = space.baseline()
        path = space.stage_dir / "topics/note.md"
        path.write_text(
            path.read_text(encoding="utf-8") + "\n" + _cited(
                "The owner moved to Kyoto.", "/s/s2#m1", "e7",
            ),
            encoding="utf-8",
        )
        space.commit_edits(*baseline)
    assert (root / "topics/ext.md").read_text(encoding="utf-8") == EXTERNAL
    assert "moved to Kyoto" in (root / "topics/note.md").read_text(encoding="utf-8")
    recorded = ownership.load(root)
    assert recorded["note.md"]["origin"] == ownership.RUNTIME
    assert recorded["ext.md"]["origin"] == ownership.OUTSIDE
    # No repair ran: the run left no trace of one.
    from openprogram.state.memory.workspace_layout import runtime_dir

    history = runtime_dir(root) / "agent-history.jsonl"
    assert not history.exists() or "repair-format" not in history.read_text()


def test_outside_files_keep_their_bytes_through_a_writer_and_the_organizer(tmp_path, chats):
    from openprogram.state.memory.management import ownership

    files = {f"ext{i}.md": EXTERNAL.replace("Raft", f"Raft-{i}") for i in range(17)}
    root = _root(tmp_path, **files)
    ref = chats.say("s2", "m1", "the owner moved to Kyoto")
    with _writer(root, {ref}) as space:
        baseline = space.baseline()
        (space.stage_dir / "topics/home.md").write_text(
            "# Home\n\n" + _cited("The owner moved to Kyoto.", "/s/s2#m1"),
            encoding="utf-8",
        )
        space.commit_edits(*baseline)
    # The organizer moves a paragraph between files of its own.
    with _writer(root, set()) as space:
        baseline = space.baseline()
        home = (space.stage_dir / "topics/home.md").read_text(encoding="utf-8")
        (space.stage_dir / "topics/places.md").write_text(
            home.replace("# Home", "# Places"), encoding="utf-8",
        )
        (space.stage_dir / "topics/home.md").unlink()
        space.commit_edits(*baseline)
    for name, text in files.items():
        assert (root / "topics" / name).read_text(encoding="utf-8") == text
    assert ownership.outside_paths(root) == set(files)


def test_a_writer_that_touches_an_outside_file_installs_nothing(tmp_path, chats):
    from openprogram.state.memory.management.transaction import TransactionError

    root = _root(tmp_path, **{"ext.md": EXTERNAL})
    ref = chats.say("s2", "m1", "the owner moved to Kyoto")
    for change in ("rewrite", "delete"):
        with _writer(root, {ref}) as space:
            baseline = space.baseline()
            (space.stage_dir / "topics/home.md").write_text(
                "# Home\n\n" + _cited("The owner moved to Kyoto.", "/s/s2#m1"),
                encoding="utf-8",
            )
            ext = space.stage_dir / "topics/ext.md"
            if change == "rewrite":
                ext.write_text(EXTERNAL + "\nA writer's addition.\n", encoding="utf-8")
            else:
                ext.unlink()
            with pytest.raises(TransactionError) as caught:
                space.commit_edits(*baseline)
        assert caught.value.code == "OUTSIDE_FILE_READ_ONLY", change
        assert (root / "topics/ext.md").read_text(encoding="utf-8") == EXTERNAL
        assert not (root / "topics/home.md").exists()


def test_a_writer_may_not_link_into_an_outside_file(tmp_path, chats):
    from openprogram.state.memory.management.transaction import TransactionError

    root = _root(tmp_path, **{"ext.md": "# Ext\n\nA note with an ID. ^ext00001\n"})
    ref = chats.say("s2", "m1", "see the note")
    with _writer(root, {ref}) as space:
        baseline = space.baseline()
        (space.stage_dir / "topics/home.md").write_text(
            "# Home\n\n" + _cited("See [the note](ext.md#^ext00001).", "/s/s2#m1"),
            encoding="utf-8",
        )
        with pytest.raises(TransactionError) as caught:
            space.commit_edits(*baseline)
    assert caught.value.code == "OUTSIDE_FILE_READ_ONLY"


def test_our_file_reordered_elsewhere_stays_ours_and_one_missing_an_id_does_not(tmp_path, chats):
    from openprogram.state.memory.management import ownership

    root = _root(tmp_path)
    ref = chats.say("s2", "m1", "two facts")
    with _writer(root, {ref}) as space:
        baseline = space.baseline()
        (space.stage_dir / "topics/two.md").write_text(
            "# Two\n\n" + _cited("First fact.", "/s/s2#m1", "e1")
            + "\n" + _cited("Second fact.", "/s/s2#m1", "e2"),
            encoding="utf-8",
        )
        space.commit_edits(*baseline)
    path = root / "topics/two.md"
    text = path.read_text(encoding="utf-8")
    first, second = re.findall(r"^.*fact\.\[\^e-[0-9a-f]+\] \^[0-9a-f]+$", text, re.M)
    path.write_text(text.replace(first, "@@").replace(second, first).replace("@@", second), encoding="utf-8")
    assert "two.md" not in ownership.outside_paths(root)
    path.write_text(path.read_text(encoding="utf-8").replace(first, "Rewritten elsewhere."), encoding="utf-8")
    assert "two.md" in ownership.outside_paths(root)


def test_a_person_saving_an_outside_file_makes_it_memorys_own(tmp_path):
    from openprogram.state.memory.management import ownership
    from openprogram.state.memory.management.transaction import staged_edit
    from openprogram.state.memory.markdown import read_topic_tree

    root = _root(tmp_path, **{"ext.md": "# Ext\n\nAsk Mina about the survey.\n"})
    assert ownership.outside_paths(root) == {"ext.md"}
    origin = lambda: {  # noqa: E731
        u.origin for u in read_topic_tree(
            root / "topics", outside=ownership.outside_paths(root),
        ).units if u.topic_path == "ext.md"
    }
    assert origin() == {"outside"}

    def write(stage: Path) -> None:
        (stage / "topics/ext.md").write_text(
            "# Ext\n\nAsk Mina about the cormorant survey.\n", encoding="utf-8",
        )

    ok, message = staged_edit(root, write, record_history=False)
    assert ok, message
    assert ownership.outside_paths(root) == set()
    assert origin() == {"person"}
    assert re.search(r"survey\. \^[0-9a-f]{8}$", (root / "topics/ext.md").read_text(), re.M)


def test_a_title_only_topic_saved_by_a_person_is_kept(tmp_path):
    from openprogram.state.memory.management.transaction import staged_edit

    root = _root(tmp_path)

    def write(stage: Path) -> None:
        (stage / "topics/note.md").write_text("# Note\n\n## Later\n", encoding="utf-8")

    ok, message = staged_edit(root, write, record_history=False, allow_removed=True)
    assert ok, message
    assert (root / "topics/note.md").read_text(encoding="utf-8") == "# Note\n\n## Later\n"


def test_every_paragraph_of_every_file_is_found_by_search(tmp_path):
    from openprogram.state.memory.retrieval.bm25 import MemoryBM25Index

    words = ["albatross", "brindle", "cormorant", "dunlin", "egret", "fulmar"]
    files = {
        f"ext{i}.md": "# Notes\n\n" + "\n\n".join(
            f"Paragraph about {word}{i}." for word in words
        ) + "\n"
        for i in range(3)
    }
    files["broken.md"] = EXTERNAL
    root = _root(tmp_path, **files)
    index = MemoryBM25Index(root)
    total = 3 * len(words) + 2 + 1  # three files, the broken one, NOTE
    assert len(index.events) == total
    for i in range(3):
        for word in words:
            hits = index.search(f"{word}{i}", top_k=3)
            assert hits and f"{word}{i}" in hits[0]["content"]


def test_search_says_where_an_uncited_paragraph_came_from(tmp_path, chats):
    from openprogram.state.memory.management.transaction import staged_edit
    from openprogram.state.memory.retrieval.bm25 import (
        MemoryBM25Index,
        render_search_results,
    )

    root = _root(tmp_path, **{"ext.md": EXTERNAL})

    def write(stage: Path) -> None:
        (stage / "topics/tea.md").write_text(
            "# Tea\n\nI prefer green tea.\n", encoding="utf-8",
        )

    assert staged_edit(root, write, record_history=False)[0]
    index = MemoryBM25Index(root)
    by_word = {
        word: index.search(word, top_k=1)[0]
        for word in ("cormorant", "green", "keeping")
    }
    assert by_word["cormorant"]["origin"] == "outside"
    assert by_word["green"]["origin"] == "person"
    assert by_word["keeping"]["origin"] == "cited"
    assert "source: outside file" in render_search_results([by_word["cormorant"]])
    assert "source: added by person" in render_search_results([by_word["green"]])
    cited = render_search_results([by_word["keeping"]])
    assert "\n   source:" not in cited and "\n   refs: " in cited


def test_the_editor_learns_each_paragraphs_origin(tmp_path):
    from openprogram.interfaces.server.routes.settings.memory import topic_origins

    root = _root(tmp_path, **{"ext.md": EXTERNAL})
    outside = topic_origins(root, root / "topics/ext.md")
    assert outside["origin"] == "outside"
    assert {row["origin"] for row in outside["paragraphs"]} == {"outside"}
    ours = topic_origins(root, root / "topics/note.md")
    assert ours["origin"] == "runtime"
    assert ours["paragraphs"] == [{
        "memory_id": "abc12345", "line": 3, "derived": False, "origin": "cited",
    }]


# -- B: no repair, a file that is not text is named once ---------------------


def test_no_repair_or_held_files_remain_in_the_code():
    root = Path(__file__).resolve().parents[3] / "openprogram"
    assert not (root / "state/memory/management/unrepaired.py").exists()
    pattern = re.compile(r"repair_committed_topics|\bheld_out\b|hold_unparsed|release_held|unrepaired")
    allowed = (
        "OUTCOMES = (",  # verification's own outcome word
        '"outcome": "repaired" if',
        "_RETIRED_RECORDS = (",
    )
    found = []
    for path in root.rglob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line) and not any(mark in line for mark in allowed):
                found.append(f"{path.relative_to(root)}:{number}: {line.strip()}")
    assert found == []


def test_the_worker_start_drops_the_old_held_record_and_nothing_else(tmp_path):
    from openprogram.state.memory.session_watcher import startup_pass
    from openprogram.state.memory.workspace_layout import ensure_runtime_dir

    root = _root(tmp_path)
    runtime = ensure_runtime_dir(root)
    (runtime / "unrepaired-topics.json").write_text("{}", encoding="utf-8")
    (runtime / "installed.json").write_text("{}", encoding="utf-8")
    startup_pass([root])
    assert not (runtime / "unrepaired-topics.json").exists()
    assert (runtime / "installed.json").read_text(encoding="utf-8") == "{}"


def test_a_topic_file_that_is_not_text_is_named_once(tmp_path):
    from openprogram.state.memory.runtime.writer_status import unreadable_topics

    root = _root(tmp_path)
    (root / "topics/scan.md").write_bytes(b"\xff\xfe\x00binary")
    assert unreadable_topics(root) == ["topics/scan.md"]


def test_writing_paused_names_this_memorys_stop(tmp_path, monkeypatch):
    from openprogram.state.memory import session_watcher
    from openprogram.state.memory.runtime.writer_status import writing_paused

    root = _root(tmp_path)
    state = {"workspaces": {str(root): {"reason_code": "GIT_COMMIT_FAILED", "reason": "no git"}}, "credentials": {}}
    monkeypatch.setattr(session_watcher, "load_state", lambda: state)
    assert writing_paused(root) == {
        "scope": "workspace", "reason_code": "GIT_COMMIT_FAILED", "reason": "no git",
    }
    state["workspaces"] = {}
    assert writing_paused(root) is None


# -- G: a derived ID that went stale leads to its file -----------------------


def test_a_stale_derived_id_returns_the_files_current_paragraphs(tmp_path):
    from openprogram.state.memory.markdown import read_topic_tree
    from openprogram.state.memory.markdown.parser import (
        derived_path_key,
        file_for_derived_id,
        path_key,
    )
    from openprogram.state.memory.retrieval.inspect import CHANGED_NOTE, read_file

    root = _root(tmp_path, **{"ext.md": EXTERNAL})
    unit = next(
        u for u in read_topic_tree(root / "topics").units
        if "cormorant" in u.content
    )
    assert derived_path_key(unit.memory_id) == path_key("ext.md")
    found = read_file(root, "topics/ext.md", block_id=unit.memory_id)["content"]
    assert found.startswith("Ask Mina about the cormorant survey.")
    assert "source: outside file" in found

    (root / "topics/ext.md").write_text(
        EXTERNAL.replace("cormorant", "shag"), encoding="utf-8",
    )
    stale = read_file(root, "topics/ext.md", block_id=unit.memory_id)["content"]
    assert stale.startswith(CHANGED_NOTE) and "shag survey" in stale
    assert file_for_derived_id(root / "topics", unit.memory_id) == root / "topics/ext.md"


# -- D: the map lists files written outside, redrawn at installs only --------


def test_the_outside_list_fits_its_quarter_and_counts_the_rest(tmp_path):
    from openprogram.state.memory.runtime import memory_guide

    files = {f"ext{i:03}.md": f"# Notes {i}\n\nPlain note {i}.\n" for i in range(300)}
    root = _root(tmp_path, **files)
    tail = memory_guide.outside_tail(root)
    assert len(tail.encode("utf-8")) <= memory_guide.GUIDE_MAX_BYTES // 4
    listed = len(re.findall(r"^- `topics/", tail, re.M))
    last = tail.splitlines()[-1]
    assert last == f"{300 - listed} more files are not listed"
    assert "— Notes 0" in tail


def test_the_outside_list_takes_a_title_from_front_matter_or_heading(tmp_path):
    from openprogram.state.memory.runtime import memory_guide

    root = _root(tmp_path, **{
        "a.md": "---\nname: Reading list\n---\n# Heading\n\nNote.\n",
        "b.md": "# Second heading\n\nNote.\n",
        "c.md": "Just a note.\n",
    })
    tail = memory_guide.outside_tail(root)
    assert "- `topics/a.md` — Reading list" in tail
    assert "- `topics/b.md` — Second heading" in tail
    assert "- `topics/c.md`\n" in tail + "\n"


def test_the_prompt_map_changes_only_when_memory_installs(tmp_path, chats):
    from openprogram.state.memory.runtime import memory_guide

    root = _root(tmp_path, **{"ext.md": EXTERNAL})
    ref = chats.say("s2", "m1", "anything")

    def install(text: str) -> None:
        with _writer(root, {ref}) as space:
            baseline = space.baseline()
            (space.stage_dir / "topics/home.md").write_text(
                "# Home\n\n" + _cited(text, "/s/s2#m1"), encoding="utf-8",
            )
            space.commit_edits(*baseline)

    install("The owner moved to Kyoto.")
    first = memory_guide.prompt_text(root)
    assert "## Other files" in first and "topics/ext.md" in first
    for i in range(5):
        (root / "topics" / f"new{i}.md").write_text(f"# New {i}\n\nA note.\n", encoding="utf-8")
    assert memory_guide.prompt_text(root) == first
    install("The owner moved to Osaka.")
    second = memory_guide.prompt_text(root)
    assert second != first and "topics/new4.md" in second
    assert memory_guide.prompt_text(root) == second


# -- F: a file the chat agent writes with its file tools ----------------------


def test_a_file_written_with_the_file_tools_is_recalled_and_left_alone(tmp_path, chats):
    from openprogram.state.memory.management import ownership
    from openprogram.state.memory.retrieval.bm25 import MemoryBM25Index
    from openprogram.state.memory.runtime.memory_guide import _LAYOUT

    root = _root(tmp_path)
    written = "# Trip\n\nThe owner flies to Lisbon in May.\n"
    (root / "topics/trip.md").write_text(written, encoding="utf-8")
    assert MemoryBM25Index(root).search("Lisbon", top_k=1)
    ref = chats.say("s2", "m1", "anything")
    with _writer(root, {ref}) as space:
        baseline = space.baseline()
        (space.stage_dir / "topics/home.md").write_text(
            "# Home\n\n" + _cited("The owner moved to Kyoto.", "/s/s2#m1"),
            encoding="utf-8",
        )
        space.commit_edits(*baseline)
    assert (root / "topics/trip.md").read_text(encoding="utf-8") == written
    assert ownership.load(root)["trip.md"]["origin"] == ownership.OUTSIDE
    assert "use `memory_update`" in _LAYOUT
