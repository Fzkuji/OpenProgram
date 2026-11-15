"""The user's Programs directory: layout, scan and the move from workflows/ + applications/."""
from __future__ import annotations

import json
from pathlib import Path

from openprogram.state import program_directory


def _legacy_profile(state: Path) -> dict:
    """A profile in the layout before ``programs/``: workflows/ and applications/."""
    workflows = state / "workflows"
    for name in ("weekly_report", "auto_research"):
        (workflows / name / ".git").mkdir(parents=True)
        (workflows / name / "__init__.py").write_text("PROGRAM_ENTRIES = []\n")
    (workflows / "auto_research" / "app").mkdir()
    (workflows / "CONVENTIONS.md").write_text("# conventions\n")
    (workflows / ".DS_Store").write_bytes(b"\0")
    applications = state / "applications"
    for folder in ("data/inst-1", "environments/key", "runs", "staging", "versions/abc"):
        (applications / folder).mkdir(parents=True)
    (applications / "state.sqlite").write_bytes(b"sqlite")
    (applications / "catalog.json.lock").write_text("")
    rows = [
        {"kind": "application", "path": str(applications / "versions" / "abc"), "source": "/elsewhere/calculator",
         "application": {"id": "example.calculator", "source": "/elsewhere/calculator", "enabled": False,
                         "hidden": True, "display_title": "Calc"}},
        {"kind": "application", "path": str(applications / "versions" / "def"),
         "source": str(workflows / "auto_research" / "app"),
         "application": {"id": "local.auto-research", "source": str(workflows / "auto_research" / "app")}},
    ]
    (applications / "catalog.json").write_text(json.dumps({"version": 1, "applications": rows}))
    return {"rows": rows}


def test_migration_moves_everything_once_and_rewrites_the_catalog(tmp_path):
    state = tmp_path / ".openprogram"
    _legacy_profile(state)
    lines: list[str] = []

    moves = program_directory.migrate_user_directory(state, log=lambda m, *a: lines.append(m % a))

    root = state / "programs"
    assert not (state / "workflows").exists() and not (state / "applications").exists()
    assert sorted(p.name for p in root.iterdir()) == [
        ".runtime", "CONVENTIONS.md", "auto_research", "catalog.json", "catalog.json.lock", "weekly_report"]
    assert sorted(p.name for p in (root / ".runtime").iterdir()) == [
        "data", "environments", "runs", "staging", "state.sqlite", "versions"]
    assert (root / ".runtime" / "data" / "inst-1").is_dir()
    assert len(moves) == 11
    assert sum(line.startswith("programs migration: moved") for line in lines) == 11

    rows = json.loads((root / "catalog.json").read_text())["applications"]
    assert rows[0]["path"] == str(root / ".runtime" / "versions" / "abc")
    assert rows[0]["source"] == "/elsewhere/calculator"
    assert {k: rows[0]["application"][k] for k in ("enabled", "hidden", "display_title")} == {
        "enabled": False, "hidden": True, "display_title": "Calc"}
    assert rows[1]["source"] == rows[1]["application"]["source"] == str(root / "auto_research" / "app")

    again: list[str] = []
    assert program_directory.migrate_user_directory(state, log=lambda m, *a: again.append(m % a)) == []
    assert again == []


def test_migration_keeps_an_item_whose_destination_exists(tmp_path):
    state = tmp_path / ".openprogram"
    (state / "workflows" / "report").mkdir(parents=True)
    (state / "programs" / "report").mkdir(parents=True)
    lines: list[str] = []
    assert program_directory.migrate_user_directory(state, log=lambda m, *a: lines.append(m % a)) == []
    assert (state / "workflows" / "report").is_dir()
    assert any("kept" in line for line in lines) and any("left" in line for line in lines)


def test_migration_merges_runtime_state_into_a_directory_the_runtime_already_made(tmp_path):
    state = tmp_path / ".openprogram"
    old_runs = state / "applications" / "runs" / "default" / "chats"
    old_runs.mkdir(parents=True)
    (old_runs / "old.json").write_text("{}", encoding="utf-8")
    new_runs = state / "programs" / ".runtime" / "runs"
    (new_runs / ".locks").mkdir(parents=True)
    (new_runs / ".chats-index.json").write_text("[]", encoding="utf-8")
    lines: list[str] = []

    moves = program_directory.migrate_user_directory(state, log=lambda m, *a: lines.append(m % a))

    assert (new_runs / "default" / "chats" / "old.json").read_text(encoding="utf-8") == "{}"
    assert (new_runs / ".chats-index.json").read_text(encoding="utf-8") == "[]"
    assert not (state / "applications").exists()
    assert [Path(d).name for _, d in moves] == ["default"]
    assert not any("kept" in line for line in lines)


def test_a_fresh_profile_has_nothing_to_migrate(tmp_path):
    assert program_directory.migrate_user_directory(tmp_path, log=lambda *a: None) == []
    assert not (tmp_path / "programs").exists()


def test_scan_finds_packages_and_applications_and_skips_runtime_state(tmp_path):
    root = tmp_path / "programs"
    (root / "report").mkdir(parents=True)
    (root / "report" / "__init__.py").write_text("")
    (root / "notes").mkdir()
    (root / "notes" / "application.json").write_text("{}")
    (root / "both").mkdir()
    (root / "both" / "__init__.py").write_text("")
    (root / "both" / "application.json").write_text("{}")
    for skipped in (".runtime", ".removed", "_private", "harness_clone"):
        (root / skipped).mkdir()
    (root / ".runtime" / "application.json").write_text("{}")
    (root / "catalog.json").write_text("{}")

    found = program_directory.program_dirs(root)
    assert sorted(found) == ["both", "notes", "report"]
    assert program_directory.program_kinds(found["both"]) == ["package", "application"]


def test_runtime_files_are_not_program_source(tmp_path, monkeypatch):
    from openprogram.state import paths
    from openprogram.runtime.agent import workflow_site

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path)
    workflow_site._file_cache.clear()
    try:
        package = tmp_path / "programs" / "report" / "steps.py"
        library = tmp_path / "programs" / ".runtime" / "environments" / "k" / "lib" / "site.py"
        for path in (package, library):
            path.parent.mkdir(parents=True)
            path.write_text("")
        assert workflow_site.is_workflow_file(str(package))
        assert not workflow_site.is_workflow_file(str(library))
    finally:
        workflow_site._file_cache.clear()


def test_the_catalog_and_runtime_live_in_the_programs_directory(tmp_path, monkeypatch):
    from openprogram.state import paths
    from openprogram.programs._host.ui import catalog, catalog_store

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path)
    assert catalog_store.path() == tmp_path / "programs" / "catalog.json"
    assert catalog.home() == tmp_path / "programs" / ".runtime"


def _application(directory: Path, app_id: str, *, backend: bool = False) -> Path:
    directory.mkdir(parents=True)
    (directory / "index.html").write_text("<!doctype html><title>x</title>")
    manifest = {"id": app_id, "title": "Notes", "version": "1.0.0"}
    if backend:
        (directory / "backend.py").write_text("OPERATIONS = {}\n")
        manifest["backend"] = {"kind": "python", "entry": "backend:OPERATIONS"}
    (directory / "application.json").write_text(json.dumps(manifest))
    return directory


def test_an_application_placed_in_the_directory_is_recorded_unless_it_needs_trust(tmp_path, monkeypatch):
    from openprogram.state import paths
    from openprogram.programs._host.ui import catalog

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path)
    plain = _application(tmp_path / "programs" / "notes", "local.notes")
    _application(tmp_path / "programs" / "runner", "local.runner", backend=True)

    assert catalog.adopt_user_applications() == ["local.notes"]
    row = catalog.record("local.notes")
    assert row["source"] == str(plain.resolve()) and row["application"]["enabled"] is True
    assert catalog.record("local.runner") is None
    assert catalog.adopt_user_applications() == []

    catalog.remove("local.notes")
    assert catalog.adopt_user_applications() == []
    assert catalog.record("local.notes") is None


def test_check_validates_an_application_directory(tmp_path):
    import pytest
    from openprogram.programs._host.check import CheckFailed, check_program

    report = check_program(_application(tmp_path / "notes", "local.notes"))
    assert report["ok"] and report["kinds"] == ["application"]
    assert report["application"]["id"] == "local.notes"
    assert report["application"]["needs_trust"] is False

    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "application.json").write_text('{"id": "Bad Id"}')
    with pytest.raises(CheckFailed) as failed:
        check_program(broken)
    assert "application.json" in failed.value.report["problems"][0]

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(CheckFailed):
        check_program(empty)
