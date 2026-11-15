"""Harness clones that older installers left in the source tree move to ``~/.openprogram/programs``."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from openprogram.state import program_directory


def _clone(container: Path, name: str, *, agentics: bool) -> Path:
    clone = container / name
    (clone / ".git").mkdir(parents=True)
    package = clone / name
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "helpers.py").write_text("def ready():\n    return True\n")
    if agentics:
        (package / "agentics").mkdir()
        (package / "agentics" / "__init__.py").write_text("PROGRAM_ENTRIES = []\n")
    (clone / "pyproject.toml").write_text(f"[project]\nname = '{name}'\n")
    return clone


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A temporary HOME whose default profile records two in-tree clones.

    The running package is relocated too: the migration also looks in the
    package's own ``programs/`` directory, which must never be this
    checkout's while a test moves things.
    """
    import openprogram

    monkeypatch.setattr(openprogram, "__file__", str(tmp_path / "installed" / "openprogram" / "__init__.py"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OPENPROGRAM_PROFILE", raising=False)
    state = tmp_path / "home" / ".openprogram"
    # Threads left by earlier tests may already have created the state
    # directory of the new HOME; the fixture only needs it to exist.
    state.mkdir(parents=True, exist_ok=True)
    catalog = tmp_path / "checkout" / "openprogram" / "programs"
    applications = catalog / "applications"
    extra = _clone(applications, "cool_harness", agentics=False)
    paper = _clone(applications, "paper_harness", agentics=True)
    (catalog / "__init__.py").write_text("")
    stale = "/nowhere/openprogram/programs/applications/cool_harness"
    rows = [
        {"path": stale, "source": "https://github.com/owner/Cool-Harness.git", "kind": "git-migration"},
        {"scope": "programs", "path": "applications/cool_harness", "entity_kind": "package",
         "source": "https://github.com/owner/Cool-Harness.git", "kind": "git-migration", "recorded_at": 1},
        {"scope": "programs", "path": "applications/paper_harness", "entity_kind": "package",
         "source": "https://github.com/owner/Paper-Harness.git", "kind": "git-migration", "recorded_at": 2},
        # The same clone recorded twice by two refreshes.
        {"scope": "programs", "path": "applications/cool_harness", "entity_kind": "package",
         "source": "https://github.com/owner/Cool-Harness.git", "kind": "git-migration", "recorded_at": 3},
        {"scope": "programs", "path": "applications/wiki_agent_harness", "kind": "git-migration"},
    ]
    (state / "program-sources.json").write_text(json.dumps(
        {"version": 2, "catalog_root": str(catalog), "programs": rows}))
    (state / "programs").mkdir()
    (state / "programs" / "catalog.json").write_text(json.dumps({"version": 1, "applications": [
        {"kind": "application", "path": str(extra / "app"), "source": str(extra / "app"),
         "application": {"id": "local.cool", "source": str(extra / "app")}},
    ]}))
    return {"state": state, "catalog": catalog, "extra": extra, "paper": paper, "stale": stale}


def test_recorded_clones_move_into_the_programs_directory(home):
    state = home["state"]
    lines: list[str] = []

    moves = program_directory.migrate_user_directory(log=lambda m, *a: lines.append(m % a))

    new_extra = state / "programs" / "cool_harness"
    new_paper = state / "programs" / "paper_harness"
    assert sorted(moves) == sorted([(str(home["extra"]), str(new_extra)), (str(home["paper"]), str(new_paper))])
    assert not home["extra"].exists() and not home["paper"].exists()
    assert (new_extra / ".git").is_dir()
    assert (new_extra / "cool_harness" / "helpers.py").is_file()

    document = json.loads((state / "program-sources.json").read_text())
    assert "catalog_root" not in document
    assert [row["path"] for row in document["programs"]] == [home["stale"], str(new_extra), str(new_paper)]
    assert all("scope" not in row and "entity_kind" not in row for row in document["programs"])
    assert document["programs"][1]["source"] == "https://github.com/owner/Cool-Harness.git"

    catalog = json.loads((state / "programs" / "catalog.json").read_text())
    row = catalog["applications"][0]
    assert row["path"] == row["source"] == row["application"]["source"] == str(new_extra / "app")
    assert any("applications/wiki_agent_harness" in line for line in lines)

    # Nothing is left to do the second time.
    assert program_directory.migrate_user_directory(log=lambda *a: None) == []
    assert json.loads((state / "program-sources.json").read_text()) == document


def test_moved_clones_are_listed_from_the_programs_directory(home, monkeypatch):
    import sys

    from openprogram.interfaces.cli.commands.programs import program_rows
    from openprogram.programs._host import registry, source_loader

    program_directory.migrate_user_directory(log=lambda *a: None)

    state = home["state"]
    clone = state / "programs" / "paper_harness"
    harnesses = {row["owner"]: row for row in program_rows() if row["kind"] == "harness"}
    assert harnesses["external:paper_harness"]["directory"] == str(clone)

    def paper_modules():
        return [name for name in sys.modules if name == "paper_harness" or name.startswith("paper_harness.")]

    # A fresh process: no package captured yet, nothing imported.
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(source_loader._program_source_finder, "sources", {})
    monkeypatch.setattr(registry, "_contexts", dict(registry._contexts))
    for name in paper_modules():
        monkeypatch.delitem(sys.modules, name)
    try:
        # The moved clone's package imports through the directory the
        # harness loader puts on sys.path.
        registry._import_external_harness(str(clone))
        from paper_harness.helpers import ready

        assert ready() is True
        assert sys.modules["paper_harness"].__file__ == str(clone / "paper_harness" / "__init__.py")
    finally:
        # Drop the fake package's modules; monkeypatch restores the real ones.
        for name in paper_modules():
            del sys.modules[name]


def test_a_clone_in_the_running_package_moves_too(home, tmp_path):
    state = home["state"]
    package_programs = tmp_path / "installed" / "openprogram" / "programs"
    clone = _clone(package_programs / "packages", "third_party", agentics=True)
    document = json.loads((state / "program-sources.json").read_text())
    document["programs"].append({"scope": "programs", "path": "packages/third_party", "kind": "git"})
    (state / "program-sources.json").write_text(json.dumps(document))

    moves = program_directory.migrate_user_directory(log=lambda *a: None)

    assert (str(clone), str(state / "programs" / "third_party")) in moves


def test_a_profile_without_records_takes_nothing(home, monkeypatch):
    monkeypatch.setenv("OPENPROGRAM_PROFILE", "dev")
    dev = Path(home["state"]).with_name(".openprogram-dev")
    dev.mkdir()
    (dev / "program-sources.json").write_text(json.dumps(
        {"version": 2, "catalog_root": str(home["catalog"]), "programs": []}))

    assert program_directory.migrate_user_directory(dev, log=lambda *a: None) == []
    assert home["extra"].is_dir() and home["paper"].is_dir()
    assert "catalog_root" not in json.loads((dev / "program-sources.json").read_text())


def test_an_occupied_destination_or_a_development_link_stays_put(home, tmp_path):
    state = home["state"]
    (state / "programs" / "cool_harness").mkdir()
    checkout = tmp_path / "dev-checkout"
    home["paper"].rename(checkout)
    home["paper"].symlink_to(checkout, target_is_directory=True)
    lines: list[str] = []

    assert program_directory.migrate_user_directory(log=lambda m, *a: lines.append(m % a)) == []
    assert home["extra"].is_dir()
    assert home["paper"].is_symlink()
    assert any("already exists" in line for line in lines)
