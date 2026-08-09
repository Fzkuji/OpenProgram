"""``core.md`` is on the system prompt of EVERY turn, so it has a budget.

The budget used to be a gate: a transaction whose staged ``core.md``
measured over the limit was refused whole, including the topic edits it
carried, and the repair guidance told the writer to leave the file alone.
So an oversized block froze and then failed every later write — the whole
conversation lost, not only the fact that would not fit.

It is a rendering limit now. ``topics/core.md`` is the master and takes
whatever it is given; the root file is rebuilt from it in file order until
the next paragraph does not fit. What is left out stays in the master,
still indexed and still searchable, so trimming costs visibility alone.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from openprogram.memory.scriptorium.management import MemoryConfig, MemoryWorkspace
from openprogram.memory.scriptorium.runtime.derived_views import (
    promote_legacy_core,
    render_core_block,
)

REF = "openprogram/thread-1/msg-1"
ANCHOR = "source-" + hashlib.sha256(REF.encode()).hexdigest()[:16]
SOURCE = (
    f'# thread-1\n\n<a id="{ANCHOR}"></a>\n'
    f"<!-- source-id:{REF} -->\n"
    "[2026-01-01] user: remember this\n"
)


def _paragraph(index: int, text: str, *, up: str = "../") -> str:
    """One topic paragraph. ``up`` is how far the file sits below the root."""
    return (
        f"\n{text}[^e-1f4c7a2b{index:02d}] ^blockid{index:03d}\n\n"
        f"[^e-1f4c7a2b{index:02d}]: Time: `2026-01-01`; Sources: [{REF}]"
        f"({up}sources/openprogram/thread-1.md#{ANCHOR})\n"
    )


def _workspace(tmp_path: Path, *, limit: int) -> MemoryWorkspace:
    root = tmp_path / "memory"
    (root / "sources" / "openprogram").mkdir(parents=True)
    (root / "sources" / "openprogram" / "thread-1.md").write_text(
        SOURCE, encoding="utf-8"
    )
    (root / "topics").mkdir(parents=True)
    return MemoryWorkspace(root, config=MemoryConfig(core_max_tokens=limit))


def _master(space: MemoryWorkspace, paragraphs: int) -> None:
    (space.stage_dir / "topics" / "core.md").write_text(
        "# Core\n" + "".join(
            _paragraph(index, f"Stable fact number {index}.")
            for index in range(paragraphs)
        ),
        encoding="utf-8",
    )


def test_a_block_within_budget_is_rendered_whole(tmp_path: Path):
    space = _workspace(tmp_path, limit=2_000)
    _master(space, 3)

    block = render_core_block(space.stage_dir, budget_tokens=2_000)

    rendered = (space.stage_dir / "core.md").read_text(encoding="utf-8")
    assert block.dropped == ()
    assert "Stable fact number 2." in rendered
    assert 0 < block.tokens <= 2_000


def test_what_does_not_fit_stays_in_the_master(tmp_path: Path):
    space = _workspace(tmp_path, limit=200)
    _master(space, 8)

    block = render_core_block(space.stage_dir, budget_tokens=200)

    rendered = (space.stage_dir / "core.md").read_text(encoding="utf-8")
    master = (space.stage_dir / "topics" / "core.md").read_text(encoding="utf-8")
    assert block.dropped, "a 200-token budget cannot hold eight paragraphs"
    assert block.tokens <= 200
    for block_id in block.dropped:
        assert f"^{block_id}" in master, "the master keeps every paragraph"
        assert f"^{block_id}" not in rendered
    assert "Stable fact number 0." in rendered, "file order decides what fits"


def test_a_footnote_follows_the_paragraph_it_belongs_to(tmp_path: Path):
    space = _workspace(tmp_path, limit=200)
    _master(space, 8)

    render_core_block(space.stage_dir, budget_tokens=200)

    rendered = (space.stage_dir / "core.md").read_text(encoding="utf-8")
    for index in range(8):
        citation = f"[^e-1f4c7a2b{index:02d}]"
        assert (citation in rendered) == (f"{citation}:" in rendered), (
            "a rendered paragraph keeps its definition, and a dropped one "
            "does not leave its definition behind"
        )
    assert f"](sources/openprogram/thread-1.md#{ANCHOR})" in rendered, (
        "the master cites ../sources from topics/; the render sits at the root"
    )


def test_an_oversized_block_no_longer_refuses_a_transaction(tmp_path: Path):
    """The failure this replaces: a big ``core.md`` and an edit that does
    not touch it, refused together."""
    space = _workspace(tmp_path, limit=120)
    _master(space, 12)
    (space.stage_dir / "topics" / "note.md").write_text(
        "# Note\n" + _paragraph(99, "Something else entirely."),
        encoding="utf-8",
    )

    assert space._synchronize().endswith("blocks")
    assert space.last_core_block.dropped
    assert (space.memory_dir / "topics" / "note.md").is_file()


def test_a_hand_written_root_block_moves_into_topics(tmp_path: Path):
    space = _workspace(tmp_path, limit=2_000)
    (space.stage_dir / "core.md").write_text(
        "# Core\n" + _paragraph(0, "Written before the block was derived.", up=""),
        encoding="utf-8",
    )

    assert promote_legacy_core(space.stage_dir) is True

    master = (space.stage_dir / "topics" / "core.md").read_text(encoding="utf-8")
    assert "Written before the block was derived." in master
    assert f"](../sources/openprogram/thread-1.md#{ANCHOR})" in master, (
        "moving into topics/ moves the links with it"
    )
    assert promote_legacy_core(space.stage_dir) is False, "it moves once"


def test_a_root_block_the_topic_format_cannot_parse_is_left_alone(tmp_path: Path):
    """Replacing it with an empty render would destroy the only copy."""
    space = _workspace(tmp_path, limit=2_000)
    (space.stage_dir / "core.md").write_text(
        "# Core\n\nA bare sentence with no footnote and no block ID.\n",
        encoding="utf-8",
    )

    assert promote_legacy_core(space.stage_dir) is False
    assert not (space.stage_dir / "topics" / "core.md").exists()

    render_core_block(space.stage_dir, budget_tokens=2_000)
    assert "A bare sentence" in (
        space.stage_dir / "core.md"
    ).read_text(encoding="utf-8")
