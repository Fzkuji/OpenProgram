"""Folders follow meaning: what breaks the filing rule is found by code,
and a regroup moves files with links, the map and each file's origin
following, and no paragraph changed."""
from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import closing
from pathlib import Path

import pytest

# What another tool writes: no IDs, no defined footnote.
EXTERNAL = "# Globex\n\nApplied on the careers site.\n\nNo reply yet.\n"


@pytest.fixture(autouse=True)
def _conversation(chats):
    chats.say("s1", "m1", "remember this")


def _note(title: str, block: str, extra: str = "") -> str:
    evidence = "e-" + hashlib.sha256(block.encode()).hexdigest()[:10]
    return (
        f"# {title}\n\n"
        f"{title} is on record.{extra}[^{evidence}] ^{block}\n\n"
        f"[^{evidence}]: Time: `2026-01-01`; Sources: [said](/s/s1#m1)\n"
    )


def _career(tmp_path: Path) -> Path:
    """A flat run: ten company notes, one of them beside its own folder,
    one written outside, one linking to another."""
    root = tmp_path / "memory"
    career = root / "topics/career"
    (career / "acme-applications").mkdir(parents=True)
    for index in range(8):
        (career / f"firm{index}-application.md").write_text(
            _note(f"Firm {index}", f"f{index:07d}"), encoding="utf-8")
    (career / "acme-applications.md").write_text(
        _note("Acme", "acme0001"), encoding="utf-8")
    (career / "acme-applications/assessment.md").write_text(
        _note("Acme assessment", "acme0002"), encoding="utf-8")
    (career / "resume.md").write_text(
        _note(
            "Resume", "resume01",
            " See [Acme](acme-applications.md#^acme0001).",
        ),
        encoding="utf-8",
    )
    (career / "globex-applications.md").write_text(EXTERNAL, encoding="utf-8")
    return root


def _install(root: Path) -> None:
    """One commit, so the map and the ownership record exist."""
    from openprogram.state.memory.management import MemoryWorkspace

    with closing(MemoryWorkspace(root)) as space:
        baseline = space.baseline()
        (space.stage_dir / "topics/core.md").write_text(
            _note("Core", "core0001"), encoding="utf-8")
        space.commit_edits(*baseline)


def _origins(root: Path) -> dict[str, str]:
    from openprogram.state.memory.workspace_layout import runtime_dir

    files = json.loads(
        (runtime_dir(root) / "installed.json").read_text(encoding="utf-8")
    )["files"]
    return {name: entry["origin"] for name, entry in files.items()}


PLAN = {
    **{
        f"topics/career/firm{index}-application.md":
        f"topics/career/companies/firm{index}/overview.md"
        for index in range(8)
    },
    "topics/career/acme-applications.md": "topics/career/companies/acme/overview.md",
    "topics/career/acme-applications/assessment.md":
        "topics/career/companies/acme/assessment.md",
    "topics/career/globex-applications.md":
        "topics/career/companies/globex/overview.md",
}


def test_a_flat_run_and_a_note_beside_its_folder_are_reported(tmp_path):
    from openprogram.state.memory.management import MemoryWorkspace, filing

    root = _career(tmp_path)
    found = filing.problems(root / "topics")
    assert any(line.startswith("topics/career/ holds 11 notes directly") for line in found)
    assert any(
        "topics/career/acme-applications.md sits beside the folder" in line
        and "move it to topics/career/acme-applications/overview.md" in line
        for line in found
    )
    # A memory agent reads them at the end of the workspace structure.
    with closing(MemoryWorkspace(root)) as space:
        structure = space.structure()
    assert "Filing problems (fix each with the Move tool):" in structure
    assert "topics/career/ holds 11 notes directly" in structure


def test_a_well_filed_tree_reports_nothing(tmp_path):
    from openprogram.state.memory.management import MemoryWorkspace, filing

    root = tmp_path / "memory"
    for index in range(12):
        folder = root / f"topics/career/companies/firm{index}"
        folder.mkdir(parents=True)
        (folder / "overview.md").write_text(
            _note(f"Firm {index}", f"f{index:07d}"), encoding="utf-8")
    # An empty folder beside a note is not that note's folder.
    (root / "topics/career/resume").mkdir()
    (root / "topics/career/resume.md").write_text(
        _note("Resume", "resume01"), encoding="utf-8")
    assert filing.problems(root / "topics") == []
    with closing(MemoryWorkspace(root)) as space:
        assert "Filing problems" not in space.structure()


def test_code_alone_makes_a_note_beside_its_folder_the_overview(tmp_path):
    from openprogram.state.memory.management import filing

    root = _career(tmp_path)
    _install(root)
    report = filing.regroup(root)
    assert report["moved"] == [{
        "from": "topics/career/acme-applications.md",
        "to": "topics/career/acme-applications/overview.md",
    }]
    assert not (root / "topics/career/acme-applications.md").exists()
    assert (root / "topics/career/acme-applications/overview.md").is_file()
    # The link that pointed at the note follows it.
    resume = (root / "topics/career/resume.md").read_text(encoding="utf-8")
    assert "(acme-applications/overview.md#^acme0001)" in resume
    # Which thing each remaining note is about is not code's to decide.
    assert any("holds 10 notes directly" in line for line in report["problems_after"])


def test_a_plan_regroups_with_links_map_origin_and_ids_following(tmp_path):
    from openprogram.state.memory.management import filing
    from openprogram.state.memory.markdown import read_topic_tree
    from openprogram.state.memory.retrieval import inspect

    root = _career(tmp_path)
    _install(root)
    (root / "MEMORY.md").write_text(
        "# Map\n\n"
        "- [Acme](topics/career/acme-applications.md): application\n"
        "- `topics/career/acme-applications/` — assessment\n"
        "- `firm3-application.md` — third firm\n"
        "- `topics/career/globex-applications.md`\n",
        encoding="utf-8",
    )
    bodies = sorted(
        hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (root / "topics").rglob("*.md") if path.name != "resume.md"
    )
    ids = {unit.memory_id for unit in read_topic_tree(root / "topics").units
           if not unit.derived_id}
    assert _origins(root)["career/globex-applications.md"] == "outside"

    report = filing.regroup(root, PLAN)

    assert report["applied"] and report["skipped"] == []
    assert report["problems_after"] == []
    topics = root / "topics"
    assert sorted(
        path.relative_to(topics).as_posix() for path in topics.rglob("*.md")
    ) == sorted([
        "core.md", "career/resume.md",
        "career/companies/acme/overview.md", "career/companies/acme/assessment.md",
        "career/companies/globex/overview.md",
        *[f"career/companies/firm{index}/overview.md" for index in range(8)],
    ])
    assert not (topics / "career/acme-applications").exists()
    # No note changed a byte, the outside one included, and it stays outside.
    assert bodies == sorted(
        hashlib.sha256(path.read_bytes()).hexdigest()
        for path in topics.rglob("*.md") if path.name != "resume.md"
    )
    assert (topics / "career/companies/globex/overview.md").read_text(
        encoding="utf-8") == EXTERNAL
    origins = _origins(root)
    assert origins["career/companies/globex/overview.md"] == "outside"
    assert origins["career/companies/acme/overview.md"] == "runtime"
    assert "career/globex-applications.md" not in origins
    # Every block ID survives and the one link follows the move.
    assert ids == {unit.memory_id for unit in read_topic_tree(topics).units
                   if not unit.derived_id}
    assert "(companies/acme/overview.md#^acme0001)" in (
        topics / "career/resume.md").read_text(encoding="utf-8")
    # The map names the new places: full paths, a folder, a short name.
    guide = (root / "MEMORY.md").read_text(encoding="utf-8")
    assert "(topics/career/companies/acme/overview.md)" in guide
    assert "`topics/career/companies/acme/`" in guide
    assert "`companies/firm3/overview.md`" in guide
    assert "`topics/career/companies/globex/overview.md`" in guide
    assert "acme-applications" not in guide
    # Search reads the files where they are now.
    hits = inspect.search(root, "Acme assessment", top_k=3)["results"]
    assert hits and hits[0]["path"] == "topics/career/companies/acme/assessment.md"
    relations = json.loads((root / "relations.json").read_text(encoding="utf-8"))
    assert relations["backlinks"] == {"acme0001": ["resume01"]}


def test_a_plan_drawn_up_earlier_skips_what_no_longer_applies(tmp_path):
    from openprogram.state.memory.management import filing

    root = _career(tmp_path)
    _install(root)
    plan = {
        "topics/career/firm0-application.md": "topics/career/companies/firm0/overview.md",
        "topics/career/gone.md": "topics/career/companies/gone/overview.md",
        "topics/career/firm1-application.md": "topics/career/resume.md",
    }
    report = filing.regroup(root, plan, dry_run=True)
    assert not report["applied"]
    assert (root / "topics/career/firm0-application.md").is_file()
    report = filing.regroup(root, plan)
    assert {(row["from"], row["reason"]) for row in report["skipped"]} == {
        ("topics/career/gone.md", "source is not there"),
        ("topics/career/firm1-application.md", "target already exists"),
    }
    assert (root / "topics/career/companies/firm0/overview.md").is_file()
    assert (root / "topics/career/firm1-application.md").is_file()


def test_the_first_seen_day_of_an_uncited_paragraph_moves_with_its_file(tmp_path):
    from openprogram.state.memory.management import filing
    from openprogram.state.memory.markdown import read_topic_tree
    from openprogram.state.memory.runtime import first_seen
    from openprogram.state.memory.workspace_layout import runtime_dir

    root = _career(tmp_path)
    _install(root)

    def days() -> list[str]:
        known = first_seen.load(root)
        return [
            known[unit.memory_id]
            for unit in read_topic_tree(root / "topics").units
            if unit.derived_id
        ]

    path = runtime_dir(root) / "first-seen.json"
    recorded = json.loads(path.read_text(encoding="utf-8"))
    recorded["days"] = {key: "2025-03-04" for key in recorded["days"]}
    path.write_text(json.dumps(recorded), encoding="utf-8")
    assert days() == ["2025-03-04", "2025-03-04"]
    filing.regroup(root, {
        "topics/career/globex-applications.md":
        "topics/career/companies/globex/overview.md",
    })
    assert days() == ["2025-03-04", "2025-03-04"]


def test_a_move_that_would_overwrite_or_leave_topics_is_refused(tmp_path):
    from openprogram.state.memory.management import filing

    root = _career(tmp_path)
    topics = root / "topics"
    before = sorted(path.as_posix() for path in topics.rglob("*.md"))
    for moves, message in (
        ({"topics/career/firm0-application.md": "topics/career/resume.md"}, "already exists"),
        ({"topics/career/firm0-application.md": "notes/firm0.md"}, "inside topics/"),
        ({"topics/career/firm0-application.md": "topics/../firm0.md"}, "inside topics/"),
        ({"topics/career/nothing.md": "topics/career/x/overview.md"}, "does not exist"),
        ({"topics/career/firm0-application.md": "topics/career/x.txt"}, "only Topic .md"),
        ({"topics/career/firm0-application.md": "topics/career/resume/overview.md"},
         "is a note beside the folder"),
        ({"topics/career/firm0-application.md": "topics/career/x/overview.md",
          "topics/career/firm1-application.md": "topics/career/x/overview.md"},
         "share the to path"),
    ):
        with pytest.raises(ValueError, match=message):
            filing.stage_moves(topics, moves)
    assert before == sorted(path.as_posix() for path in topics.rglob("*.md"))
    # Two notes may trade places in one call.
    first = (topics / "career/firm0-application.md").read_bytes()
    filing.stage_moves(topics, {
        "topics/career/firm0-application.md": "topics/career/firm1-application.md",
        "topics/career/firm1-application.md": "topics/career/firm0-application.md",
    })
    assert (topics / "career/firm1-application.md").read_bytes() == first


def test_the_move_tool_regroups_inside_an_agent_turn(tmp_path):
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.management.agent import _commit_turn
    from openprogram.state.memory.management.tools import TOOLS, management_tools

    root = _career(tmp_path)
    _install(root)
    assert "Move" in {entry["function"]["name"] for entry in TOOLS}
    with closing(MemoryWorkspace(root, allowed_new_source_refs=set())) as space:
        baseline = space.baseline()
        space.turn_baseline = baseline
        audit: list[dict] = []
        handlers = {tool.name: tool.handler for tool in management_tools(space, audit)}
        result = asyncio.run(handlers["Move"]({"moves": [
            {"from": source, "to": target} for source, target in PLAN.items()
        ]}))
        text = result["content"][0]["text"]
        assert not result["is_error"], text
        assert "topics/career/globex-applications.md -> " in text
        assert "Filing problems still open" not in text
        refused = asyncio.run(handlers["Move"]({"moves": [
            {"from": "topics/career/resume.md", "to": "topics/core.md"},
        ]}))
        assert refused["is_error"]
        assert _commit_turn(space, baseline, audit) is None
    assert (root / "topics/career/companies/globex/overview.md").read_text(
        encoding="utf-8") == EXTERNAL
    assert _origins(root)["career/companies/globex/overview.md"] == "outside"


def test_a_writer_is_told_when_it_files_a_note_into_a_flat_run(tmp_path, chats):
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.management.tools import management_tools

    root = _career(tmp_path)
    _install(root)
    ref = chats.say("s2", "m1", "applied to Initech")
    with closing(MemoryWorkspace(root, allowed_new_source_refs={ref})) as space:
        space.turn_baseline = space.baseline()
        handlers = {tool.name: tool.handler for tool in management_tools(space, [])}
        content = (
            "# Initech\n\nApplied to Initech.[^e1]\n\n"
            "[^e1]: Time: `2026-02-02`; Sources: [said](/s/s2#m1)\n"
        )
        flat = asyncio.run(handlers["Write"]({
            "file_path": "topics/career/initech-application.md", "content": content,
        }))["content"][0]["text"]
        assert "topics/career/ now holds 12 notes directly" in flat
        filed = asyncio.run(handlers["Write"]({
            "file_path": "topics/career/companies/initech/overview.md",
            "content": content,
        }))["content"][0]["text"]
        assert "holds" not in filed


def test_an_outside_file_changed_while_moved_is_still_refused(tmp_path):
    from openprogram.state.memory.management import MemoryWorkspace
    from openprogram.state.memory.management.transaction import TransactionError

    root = _career(tmp_path)
    _install(root)
    with closing(MemoryWorkspace(root, allowed_new_source_refs=set())) as space:
        baseline = space.baseline()
        old = space.stage_dir / "topics/career/globex-applications.md"
        new = space.stage_dir / "topics/career/companies/globex/overview.md"
        new.parent.mkdir(parents=True)
        new.write_text(old.read_text(encoding="utf-8") + "\nEdited.\n", encoding="utf-8")
        old.unlink()
        with pytest.raises(TransactionError) as caught:
            space.commit_edits(*baseline)
    assert caught.value.code == "OUTSIDE_FILE_READ_ONLY"
    assert (root / "topics/career/globex-applications.md").read_text(
        encoding="utf-8") == EXTERNAL


def test_the_prompts_carry_the_filing_rule():
    from openprogram.state.memory.prompts import (
        ORGANIZE_MEMORY, REGROUP_MEMORY, SYSTEM_PROMPT, WRITE_MEMORY,
    )
    from openprogram.state.memory.workspace_layout import FLAT_RUN_LIMIT

    assert f"No directory holds more than {FLAT_RUN_LIMIT} notes directly" in SYSTEM_PROMPT
    assert "A note never sits beside a folder of the same name" in SYSTEM_PROMPT
    assert "under a folder named for the kind" in SYSTEM_PROMPT
    assert "{" not in SYSTEM_PROMPT
    assert "Fix every filing problem the workspace structure lists" in ORGANIZE_MEMORY
    assert "never in a directory the workspace structure lists as a filing problem" in WRITE_MEMORY
    task = REGROUP_MEMORY.format(
        directory="topics/career", count=28, topic_paths="topics/career/a.md",
    )
    assert f"no directory holds more than {FLAT_RUN_LIMIT}" in task
    assert "only Move" in task and "topics/career/a.md" in task


def test_the_nightly_pass_regroups_before_it_reorganizes(tmp_path, monkeypatch):
    from openprogram.state.memory import writing
    from openprogram.state.memory.management import api, filing

    root = _career(tmp_path)
    _install(root)
    calls: list[tuple[str, str]] = []

    def regroup_topics(memory_dir, *, agent, directory, **_kwargs):
        calls.append(("regroup", directory))
        # The agent's part: which note is about which thing.
        plan = {
            source: target for source, target in PLAN.items()
            if (Path(memory_dir) / source).is_file()
        }
        plan["topics/career/acme-applications/overview.md"] = (
            "topics/career/companies/acme/overview.md"
        )
        filing.apply_moves(memory_dir, plan)
        return [{"tool": "commit", "status": "ok", "topic_paths": sorted(plan.values())}]

    monkeypatch.setattr(api, "regroup_topics", regroup_topics)
    audit = writing._regroup_workspace(root, agent=object())
    # Code moved the note beside its folder first; the agent got the run.
    assert calls == [("regroup", "topics/career")]
    assert audit and filing.problems(root / "topics") == []
    assert (root / "topics/career/companies/acme/assessment.md").is_file()
