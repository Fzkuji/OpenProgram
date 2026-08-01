"""``core.md`` is on the system prompt of EVERY turn, so its declared
budget has to be enforced, not merely printed in the file header."""
from __future__ import annotations

from openprogram.memory.core import (
    CORE_BUDGET_CHARS,
    strip_chrome,
    truncate_to_budget,
)


def test_short_body_is_untouched():
    body = "# Core\n\nA short memory that fits comfortably."
    assert truncate_to_budget(body) == body


def test_oversized_body_is_cut_to_the_budget():
    body = "\n\n".join(
        f"# Section {i}\n\n" + ("filler sentence. " * 40) for i in range(20)
    )
    assert len(body) > CORE_BUDGET_CHARS * 5  # precondition

    out = truncate_to_budget(body)
    assert len(out) <= CORE_BUDGET_CHARS, len(out)
    assert "memory_browse" in out, "must point at how to read the rest"


def test_cut_lands_on_a_section_boundary():
    """Whole headings survive or go — never half a section."""
    sections = [f"# S{i}\n\nbody {i} " + "x" * 400 for i in range(12)]
    out = truncate_to_budget("\n\n".join(sections))

    kept = [s for s in sections if s in out]
    assert kept, "at least one whole section should survive"
    # Every heading present in the output brought its whole section along.
    for i in range(12):
        if f"# S{i}\n" in out:
            assert sections[i] in out, f"section {i} was cut in half"


def test_single_oversized_section_does_not_split_a_word():
    body = "# Only\n\n" + "alpha beta gamma delta. " * 400
    out = truncate_to_budget(body)
    assert len(out) <= CORE_BUDGET_CHARS
    prose = out.split("[trimmed")[0].rstrip()
    assert prose.endswith("."), prose[-40:]


def test_unheaded_body_still_respects_the_budget():
    out = truncate_to_budget("no headings at all. " * 500)
    assert len(out) <= CORE_BUDGET_CHARS


def test_strip_chrome_drops_header_and_footer():
    rule = "═" * 60
    text = (
        f"{rule}\n"
        "OpenProgram memory (machine-wide) — 174% (3569/2048 chars), "
        "last consolidated 2026-08-02\n"
        f"{rule}\n\n"
        "# Real content\n\nkeep me\n\n"
        "[for full context start with `memory_browse`]\n"
    )
    out = strip_chrome(text)
    assert out.startswith("# Real content")
    assert out.endswith("keep me")
    assert "3569/2048" not in out, "the byte-count header is not content"


def test_system_prompt_block_stays_within_budget(tmp_path, monkeypatch):
    """End-to-end: an oversized core.md must not put 800 tokens on every
    turn's system prompt."""
    from openprogram.memory import core, store

    core_file = tmp_path / "core.md"
    core_file.write_text(
        "\n\n".join(f"# S{i}\n\n" + ("word " * 300) for i in range(10)),
        encoding="utf-8",
    )
    monkeypatch.setattr(store, "core", lambda: core_file)

    block = core.system_prompt_block()
    # Budget + the memory-tools pointer the block always appends.
    assert len(block) < CORE_BUDGET_CHARS + 700, len(block)
    assert "memory_browse" in block
