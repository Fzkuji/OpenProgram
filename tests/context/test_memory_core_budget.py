"""``core.md`` is on the system prompt of EVERY turn, so its declared
budget has to be enforced, not merely printed in the file header.

The previous memory layer trimmed an oversized core when reading it. The
current one refuses the write instead: truncating on read means the file
on disk and the block the model sees disagree, and the model is the one
maintaining the file.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.memory.management import MemoryConfig, MemoryWorkspace


def _workspace(tmp_path: Path, *, limit: int) -> MemoryWorkspace:
    return MemoryWorkspace(tmp_path, config=MemoryConfig(core_max_tokens=limit))


def test_a_core_within_budget_is_accepted(tmp_path: Path):
    space = _workspace(tmp_path, limit=2_000)
    (space.stage_dir / "core.md").write_text(
        "# Core\n\nA short memory that fits comfortably.\n", encoding="utf-8"
    )

    space._synchronize()


def test_an_oversized_core_is_refused(tmp_path: Path):
    space = _workspace(tmp_path, limit=50)
    (space.stage_dir / "core.md").write_text(
        "# Core\n\n" + "filler sentence. " * 200, encoding="utf-8"
    )

    with pytest.raises(ValueError, match="Core Memory exceeds"):
        space._synchronize()


def test_the_budget_is_configurable(tmp_path: Path):
    body = "# Core\n\n" + "filler sentence. " * 200
    generous = _workspace(tmp_path / "a", limit=10_000)
    (generous.stage_dir / "core.md").write_text(body, encoding="utf-8")
    generous._synchronize()

    strict = _workspace(tmp_path / "b", limit=10)
    (strict.stage_dir / "core.md").write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match="Core Memory exceeds"):
        strict._synchronize()
