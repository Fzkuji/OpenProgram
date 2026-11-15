"""MEMORY.md: a generated first map, then the memory agents' own file."""
from contextlib import closing

VISA = (
    "# Visa\n"
    "\n"
    "Visitor visa subclass 600 covers the conference trip.[^e-1f4c7a2b90] ^abc12345\n"
    "\n"
    "[^e-1f4c7a2b90]: Time: `2026-01-01`; Sources: [D1:1](/s/s1#d1-1)\n"
)
CORE = (
    "# Core\n"
    "\n"
    "Owner prefers Chinese.[^e-2f4c7a2b91] ^def45678\n"
    "\n"
    "[^e-2f4c7a2b91]: Time: `2026-01-02`; Sources: [D1:1](/s/s1#d1-1)\n"
)


def _root(tmp_path):
    root = tmp_path / "memory"
    (root / "topics/projects/trip").mkdir(parents=True)
    (root / "topics/projects/trip/visa.md").write_text(VISA, encoding="utf-8")
    (root / "topics/core.md").write_text(CORE, encoding="utf-8")
    return root


def _commit(root, edit=None):
    from openprogram.state.memory.management import MemoryWorkspace

    with closing(MemoryWorkspace(root)) as workspace:
        baseline = workspace.baseline()
        if edit:
            edit(workspace.stage_dir)
        else:
            (workspace.stage_dir / "topics/core.md").write_text(
                CORE.replace("Chinese.", "Chinese by default."), encoding="utf-8")
        workspace.commit_edits(*baseline)


def test_first_commit_writes_a_generated_map(tmp_path):
    root = _root(tmp_path)
    _commit(root)
    text = (root / "MEMORY.md").read_text(encoding="utf-8")
    assert "## Layout" in text and "`timeline/`" in text
    # The map names files and states no facts: a file goes by its title.
    assert "`topics/projects/trip/visa.md` — Visa" in text
    assert "Visitor visa subclass 600" not in text
    assert "`topics/core.md` —" not in text


def test_the_agents_edit_survives_later_commits(tmp_path):
    root = _root(tmp_path)
    _commit(root)
    _commit(root, lambda stage: (stage / "MEMORY.md").write_text("# Map\n\nTrip planning lives in topics/projects/trip/.\n", encoding="utf-8"))
    _commit(root)
    assert (root / "MEMORY.md").read_text(encoding="utf-8").startswith("# Map\n\nTrip planning")


def test_the_map_comes_before_core_in_the_prompt(tmp_path):
    from openprogram.state.memory.local_backend import _core_block

    root = _root(tmp_path)
    _commit(root)
    block = _core_block(root)
    assert block.index("# Memory map") < block.index("Owner prefers Chinese by default.")


def test_the_prompt_cuts_a_long_map(tmp_path):
    from openprogram.state.memory.runtime import memory_guide

    (tmp_path / "MEMORY.md").write_text("x" * (memory_guide.GUIDE_MAX_BYTES + 500), encoding="utf-8")
    text = memory_guide.prompt_text(tmp_path)
    assert text.endswith("read it for the rest)")
    assert text.count("x") == memory_guide.GUIDE_MAX_BYTES


def test_git_history_records_the_map(tmp_path):
    import subprocess

    from openprogram.state.memory.runtime.state import RuntimeStateStore

    root = _root(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    _commit(root)
    RuntimeStateStore(root).git_commit("memory: test")
    tracked = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True).stdout
    assert "MEMORY.md" in tracked.splitlines()


def test_deleting_a_topic_s_last_record_takes_its_line_out_of_the_map(tmp_path):
    """A delete through ``memory_update`` runs no agent; the map, which
    every session reads, must not keep naming (or quoting) what was removed."""
    from openprogram.state.memory.management import MemoryWorkspace

    root = _root(tmp_path)
    _commit(root)
    agents_map = (
        "# Map\n\n## Subjects\n\n"
        "- `topics/projects/` — work\n"
        "  - `topics/projects/trip/visa.md` — Visitor visa subclass 600 covers the conference trip.\n"
        "- `topics/projects/trip/` — the trip\n"
    )
    _commit(root, lambda stage: (stage / "MEMORY.md").write_text(agents_map, encoding="utf-8"))
    with closing(MemoryWorkspace(root)) as workspace:
        workspace.update(memory_changes=[{"op": "delete_record", "memory_id": "abc12345"}],
                         git_commit="off")
    assert not (root / "topics/projects/trip/visa.md").exists()
    text = (root / "MEMORY.md").read_text(encoding="utf-8")
    assert "Visitor visa" not in text
    assert text.startswith("# Map\n\n## Subjects\n\n- `topics/projects/` — work\n")
    assert "Owner prefers Chinese" in text  # the core section stays
