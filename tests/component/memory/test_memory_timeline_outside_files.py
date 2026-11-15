"""Timeline files a paragraph written outside OpenProgram under the day it
entered memory, read from the memory folder's git history.

Runs git, so it is a component test; the rest of the outside-file rules
are unit tests in tests/unit/memory/test_memory_outside_files.py.
"""
from __future__ import annotations

import os
import subprocess
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


@pytest.fixture(autouse=True)
def _conversation(chats):
    chats.say("s1", "d1-1", "remember this")


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "memory"
    (root / "topics").mkdir(parents=True)
    (root / "topics/note.md").write_text(NOTE, encoding="utf-8")
    return root


def _cited(text: str, link: str, label: str = "e1") -> str:
    return f"{text}[^{label}]\n\n[^{label}]: Time: `2026-02-02`; Sources: [said]({link})\n"


def _writer(root: Path, refs: set[str]):
    from openprogram.state.memory.management import MemoryWorkspace

    return closing(MemoryWorkspace(root, allowed_new_source_refs=refs))


def _git(root: Path, *args: str, date: str | None = None) -> None:
    env = dict(os.environ)
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=root, check=True, capture_output=True, env=env,
    )


def test_timeline_files_outside_paragraphs_under_the_day_they_entered_memory(tmp_path, chats):
    root = _root(tmp_path)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "start", date="2025-01-01T12:00:00")
    (root / "topics/a.md").write_text("# A\n\nThe pelican note.\n", encoding="utf-8")
    (root / "topics/b.md").write_text("# B\n\nThe heron note.\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "notes", date="2025-03-04T12:00:00")

    ref = chats.say("s2", "m1", "anything")

    def write_once(text: str) -> None:
        with _writer(root, {ref}) as space:
            baseline = space.baseline()
            (space.stage_dir / "topics/home.md").write_text(
                "# Home\n\n" + _cited(text, "/s/s2#m1"), encoding="utf-8",
            )
            space.commit_edits(*baseline)

    write_once("The owner moved to Kyoto.")
    day = root / "timeline/2025/03/04.md"
    text = day.read_text(encoding="utf-8")
    assert "The pelican note. (recorded 2025-03-04)" in text
    assert "The heron note. (recorded 2025-03-04)" in text
    cited_day = (root / "timeline/2026/02/02.md").read_text(encoding="utf-8")

    os.utime(root / "topics/a.md", (0, 0))
    write_once("The owner moved to Osaka.")
    assert day.read_text(encoding="utf-8") == text
    # A cited paragraph keeps its event date and its rendering.
    assert cited_day.replace("Kyoto", "Osaka").split("[Topic]")[0] == (
        root / "timeline/2026/02/02.md"
    ).read_text(encoding="utf-8").split("[Topic]")[0]
    from openprogram.state.memory.runtime import first_seen

    assert set(first_seen.load(root).values()) == {"2025-03-04"}
