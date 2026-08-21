from __future__ import annotations

import json

import pytest


def _harness(root, name: str):
    repo = root / name
    pkg = repo / "demo_pkg" / "agentics"
    pkg.mkdir(parents=True)
    (repo / "demo_pkg" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__init__.py").write_text("AGENTIC_FUNCTIONS = []\n", encoding="utf-8")
    return repo


def test_registry_only_discovers_owner_recorded_harnesses(tmp_path, monkeypatch):
    from openprogram.programs import _programs, _registry
    import openprogram.paths as paths

    state = tmp_path / "state"
    base = tmp_path / "agentics"
    base.mkdir()
    trusted = _harness(base, "trusted")
    _harness(base, "untrusted")
    monkeypatch.setattr(paths, "get_state_dir", lambda: state)
    monkeypatch.setattr(_programs, "applications_dir", lambda: str(base))

    _programs.record_program_source(trusted, source="https://example.test/trusted.git")

    found = list(_registry._iter_external_harness_dirs(str(base)))
    assert found == [("trusted", str(trusted.resolve()))]
    manifest = json.loads((state / "program-sources.json").read_text())
    assert manifest["programs"][0]["source"] == "https://example.test/trusted.git"


def test_external_file_loader_rejects_unrecorded_source(tmp_path, monkeypatch):
    from openprogram.programs import _programs, _registry
    import openprogram.paths as paths

    state = tmp_path / "state"
    base = tmp_path / "agentics"
    base.mkdir()
    untrusted = _harness(base, "untrusted")
    source = untrusted / "demo_pkg" / "agentics" / "__init__.py"
    monkeypatch.setattr(paths, "get_state_dir", lambda: state)
    monkeypatch.setattr(_programs, "applications_dir", lambda: str(base))

    try:
        _registry._load_external_file(
            str(base), "untrusted", str(source.relative_to(base))
        )
    except PermissionError:
        pass
    else:
        raise AssertionError("unrecorded Python source was loaded")


def test_load_agentic_modules_skips_unrecorded_directory(tmp_path, monkeypatch):
    from openprogram.programs import _programs, _registry
    import openprogram.paths as paths

    state = tmp_path / "state"
    base = tmp_path / "agentics"
    base.mkdir()
    trusted = _harness(base, "trusted")
    _harness(base, "untrusted")
    monkeypatch.setattr(paths, "get_state_dir", lambda: state)
    monkeypatch.setattr(_programs, "applications_dir", lambda: str(base))
    monkeypatch.setattr(_registry, "AGENTIC_MODULES", [])
    monkeypatch.setattr(_programs, "import_installed_programs", lambda: [])
    loaded = []
    monkeypatch.setattr(_registry, "_import_external_harness", loaded.append)
    _programs.record_program_source(trusted, source="file:///owner/trusted")

    _registry.load_agentic_modules(str(base))

    assert loaded == [str(trusted.resolve())]


def test_remove_program_source_revokes_loading(tmp_path, monkeypatch):
    from openprogram.programs import _programs
    import openprogram.paths as paths

    state = tmp_path / "state"
    base = tmp_path / "agentics"
    base.mkdir()
    trusted = _harness(base, "trusted")
    monkeypatch.setattr(paths, "get_state_dir", lambda: state)
    monkeypatch.setattr(_programs, "applications_dir", lambda: str(base))
    _programs.record_program_source(trusted, source="file:///owner/trusted")

    _programs.remove_program_source(trusted)

    assert not _programs.is_owner_controlled_program_path(trusted)


def test_owner_can_record_a_dev_symlink(tmp_path, monkeypatch):
    from openprogram.programs import _programs
    import openprogram.paths as paths

    state = tmp_path / "state"
    base = tmp_path / "agentics"
    base.mkdir()
    target = _harness(tmp_path / "checkout", "linked")
    linked = base / "linked"
    try:
        linked.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")
    monkeypatch.setattr(paths, "get_state_dir", lambda: state)
    monkeypatch.setattr(_programs, "applications_dir", lambda: str(base))

    _programs.record_program_source(linked, source="file:///owner/linked")

    assert _programs.owner_controlled_program_sources()[0]["path"] == str(linked)
    assert _programs.is_owner_controlled_program_path(target)


def test_model_file_policy_protects_source_registry(tmp_path, monkeypatch):
    from openprogram import sandbox
    from openprogram.programs import _programs
    import openprogram.paths as paths

    state = tmp_path / "state"
    monkeypatch.setattr(paths, "get_state_dir", lambda: state)

    violation = sandbox.validate_write_path(state / "Program-Sources.JSON")

    assert violation and "source registry" in violation


def test_catalogued_clone_with_matching_origin_is_migrated_once(tmp_path, monkeypatch):
    from openprogram.programs import _programs
    import openprogram.paths as paths

    state = tmp_path / "state"
    base = tmp_path / "agentics"
    base.mkdir()
    repo = _harness(base, "Official-Harness")
    (repo / ".git").mkdir()
    (repo / ".git" / "config").write_text(
        '[remote "origin"]\n'
        "\turl = https://github.com/example/Official-Harness.git\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "get_state_dir", lambda: state)
    monkeypatch.setattr(_programs, "applications_dir", lambda: str(base))
    program = _programs.Program(
        function="official_agent", package="demo_pkg", extra="official",
        repo="https://github.com/example/Official-Harness", summary="test",
    )

    assert program.is_installed()
    assert _programs.is_owner_controlled_program_path(repo / "demo_pkg")
    row = json.loads((state / "program-sources.json").read_text())["programs"][0]
    assert row["kind"] == "git-migration"


def test_untrusted_clone_does_not_hide_installed_distribution(tmp_path, monkeypatch):
    from openprogram.programs import _programs

    base = tmp_path / "agentics"
    base.mkdir()
    _harness(base, "demo_pkg")
    monkeypatch.setattr(_programs, "applications_dir", lambda: str(base))
    monkeypatch.setattr(_programs, "_catalogued_clone_origin", lambda *_: None)
    monkeypatch.setattr(_programs, "_has_installed_distribution", lambda _: True)
    program = _programs.Program(
        function="demo_agent",
        package="demo_pkg",
        extra="demo",
        repo="https://github.com/example/Demo-Harness",
        summary="test",
        install_dir="demo_pkg",
    )

    assert program.is_installed()


def test_recorded_program_remains_available_after_runtime_relocation(
    tmp_path, monkeypatch
):
    from openprogram.programs import _programs
    import openprogram.paths as paths

    state = tmp_path / "state"
    source_base = tmp_path / "checkout" / "openprogram" / "programs" / "applications"
    source_base.mkdir(parents=True)
    repo = _harness(source_base, "Official-Harness")
    installed_base = tmp_path / "installed" / "openprogram" / "programs" / "applications"
    installed_base.mkdir(parents=True)
    monkeypatch.setattr(paths, "get_state_dir", lambda: state)
    monkeypatch.setattr(_programs, "applications_dir", lambda: str(source_base))
    _programs.record_program_source(repo, source="file:///owner/official")
    monkeypatch.setattr(_programs, "applications_dir", lambda: str(installed_base))
    _harness(installed_base, "Official-Harness")
    program = _programs.Program(
        function="official_agent",
        package="demo_pkg",
        extra="official",
        repo="https://github.com/example/Official-Harness",
        summary="test",
    )

    assert program.in_tree_pkg_dir() == str(repo / "demo_pkg")
    assert program.is_installed()
