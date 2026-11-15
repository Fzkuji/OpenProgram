from __future__ import annotations

import json

import pytest


def _harness(root, name: str):
    repo = root / name
    pkg = repo / "demo_pkg" / "agentics"
    pkg.mkdir(parents=True)
    (repo / "demo_pkg" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__init__.py").write_text("PROGRAM_ENTRIES = []\n", encoding="utf-8")
    return repo


def test_registry_only_discovers_owner_recorded_harnesses(tmp_path, monkeypatch):
    from openprogram.programs._host import sources as program_sources
    from openprogram.programs._host import registry as _registry
    import openprogram.state.paths as paths

    state = tmp_path / "state"
    base = state / "programs"
    base.mkdir(parents=True)
    trusted = _harness(base, "trusted")
    _harness(base, "untrusted")
    monkeypatch.setattr(paths, "get_state_dir", lambda: state)

    program_sources.record_program_source(trusted, source="https://example.test/trusted.git")

    found = list(_registry._iter_external_harness_dirs())
    assert found == [("trusted", str(trusted.resolve()))]
    manifest = json.loads((state / "program-sources.json").read_text())
    assert manifest["programs"][0]["source"] == "https://example.test/trusted.git"


def test_external_file_loader_rejects_unrecorded_source(tmp_path, monkeypatch):
    from openprogram.programs._host import registry as _registry
    import openprogram.state.paths as paths

    state = tmp_path / "state"
    base = state / "programs"
    base.mkdir(parents=True)
    untrusted = _harness(base, "untrusted")
    source = untrusted / "demo_pkg" / "agentics" / "__init__.py"
    monkeypatch.setattr(paths, "get_state_dir", lambda: state)

    try:
        _registry._load_external_file(
            str(base), "untrusted", str(source.relative_to(base))
        )
    except PermissionError:
        pass
    else:
        raise AssertionError("unrecorded Python source was loaded")


def test_load_program_modules_skips_unrecorded_directory(tmp_path, monkeypatch):
    from openprogram.programs._host import sources as program_sources
    from openprogram.programs._host import registry as _registry
    import openprogram.state.paths as paths

    state = tmp_path / "state"
    base = state / "programs"
    base.mkdir(parents=True)
    trusted = _harness(base, "trusted")
    _harness(base, "untrusted")
    monkeypatch.setattr(paths, "get_state_dir", lambda: state)
    monkeypatch.setattr(_registry, "TOOL_PROGRAMS", {})
    monkeypatch.setattr(_registry, "builtin_application_names", lambda: [])
    loaded = []
    monkeypatch.setattr(_registry, "_import_external_harness", loaded.append)
    program_sources.record_program_source(trusted, source="file:///owner/trusted")

    _registry.load_program_modules()

    assert loaded == [str(trusted.resolve())]


def test_remove_program_source_revokes_loading(tmp_path, monkeypatch):
    from openprogram.programs._host import sources as program_sources
    import openprogram.state.paths as paths

    state = tmp_path / "state"
    base = state / "programs"
    base.mkdir(parents=True)
    trusted = _harness(base, "trusted")
    monkeypatch.setattr(paths, "get_state_dir", lambda: state)
    program_sources.record_program_source(trusted, source="file:///owner/trusted")

    program_sources.remove_program_source(trusted)

    assert not program_sources.is_owner_controlled_program_path(trusted)


def test_owner_can_record_a_dev_symlink(tmp_path, monkeypatch):
    from openprogram.programs._host import sources as program_sources
    import openprogram.state.paths as paths

    state = tmp_path / "state"
    base = state / "programs"
    base.mkdir(parents=True)
    target = _harness(tmp_path / "checkout", "linked")
    linked = base / "linked"
    try:
        linked.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")
    monkeypatch.setattr(paths, "get_state_dir", lambda: state)

    program_sources.record_program_source(linked, source="file:///owner/linked")

    assert program_sources.owner_controlled_program_sources()[0]["path"] == str(linked)
    assert program_sources.is_owner_controlled_program_path(target)


def test_model_file_policy_leaves_the_source_registry_writable(tmp_path, monkeypatch):
    from openprogram.host import sandbox
    import openprogram.state.paths as paths

    state = tmp_path / "state"
    monkeypatch.setattr(paths, "get_state_dir", lambda: state)

    assert sandbox.validate_write_path(state / "Program-Sources.JSON") is None


def test_recorded_program_outside_the_programs_directory_stays_available(
    tmp_path, monkeypatch
):
    from openprogram.programs._host import sources as program_sources
    import openprogram.state.paths as paths

    state = tmp_path / "state"
    (state / "programs").mkdir(parents=True)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    repo = _harness(checkout, "Some-Harness")
    monkeypatch.setattr(paths, "get_state_dir", lambda: state)
    program_sources.record_program_source(repo, source="file:///owner/some", base=str(checkout))

    assert program_sources.is_owner_controlled_program_path(repo / "demo_pkg")
    assert [row["path"] for row in program_sources.owner_controlled_program_sources()] == [str(repo)]


def test_a_source_outside_the_programs_directory_needs_an_explicit_base(tmp_path, monkeypatch):
    from openprogram.programs._host import sources as program_sources
    import openprogram.state.paths as paths

    state = tmp_path / "state"
    monkeypatch.setattr(paths, "get_state_dir", lambda: state)
    elsewhere = _harness(tmp_path / "openprogram" / "programs" / "applications", "some_harness")

    with pytest.raises(ValueError, match="directly under"):
        program_sources.record_program_source(elsewhere, source="fixture")
