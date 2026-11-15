"""User Programs load only the checked commit, from an extracted copy."""
from __future__ import annotations

import importlib
import logging
import shutil
import subprocess
import sys
import threading
import time
import types
import uuid

import pytest

from openprogram.programs._host import packages
from openprogram.programs._host import registry as _registry

_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
_DIRTY = "Uncommitted changes. The last checked version is still loaded."


def _git(directory, *args):
    return subprocess.run(["git", "-C", str(directory), *args], check=True, capture_output=True,
                          text=True, env=_ENV).stdout.strip()


def _source(name, version):
    return (f'"""Probe."""\nVERSION = "{version}"\n\n\n'
            f"def {name}_run(text: str) -> str:\n"
            '    """Say the version."""\n'
            "    return VERSION\n\n\n"
            f"PROGRAM_ENTRIES = [{name}_run]\n")


@pytest.fixture
def program(tmp_path):
    """A package with a first commit, under a root of its own."""
    name = f"wf_checked_{uuid.uuid4().hex[:8]}"
    root = tmp_path / "programs"
    package = root / name
    package.mkdir(parents=True)
    (root / packages._MIGRATED_MARKER).write_text("", encoding="utf-8")
    (package / "__init__.py").write_text(_source(name, "A"), encoding="utf-8")
    _git(package, "init", "-q")
    _git(package, "add", "-A")
    _git(package, "commit", "-qm", "A")
    try:
        yield types.SimpleNamespace(name=name, root=root, path=package, cache=tmp_path / "programs-cache" / name)
    finally:
        _forget(name)
        packages._finder.packages.pop(name, None)


def _forget(name):
    """What a worker restart loses."""
    packages._unregister(name)
    packages._evict(name)
    packages._status.pop(name, None)
    packages._fingerprints.pop(name, None)
    packages._failed.pop(name, None)


def _commit(program, source, message):
    (program.path / "__init__.py").write_text(source, encoding="utf-8")
    _git(program.path, "commit", "-qam", message)
    return _git(program.path, "rev-parse", "HEAD")


def _check(program):
    _git(program.path, "update-ref", packages.CHECKED_REF, "HEAD")
    return _git(program.path, "rev-parse", "HEAD")


def _load(program):
    return packages.load_user_packages(program.root)[program.name]


def _version(program):
    return sys.modules[program.name].VERSION


def test_the_package_imports_from_the_extracted_commit(program):
    first = _check(program)
    status = _load(program)
    assert status.state == "loaded", status.error
    assert status.revision == first and status.path == str(program.path)
    assert sys.modules[program.name].__file__ == str(program.cache / first / "__init__.py")
    assert _registry.program_tools(program.name) == [f"{program.name}_run"]
    assert (program.cache / "good").read_text(encoding="utf-8") == first


def test_a_plain_commit_is_not_loaded(program):
    first = _check(program)
    _load(program)
    second = _commit(program, _source(program.name, "B"), "B")

    status = _load(program)
    assert status.state == "loaded" and status.revision == first and _version(program) == "A"
    assert second[:7] in status.note and "openprogram programs check" in status.note

    _check(program)
    status = _load(program)
    assert status.revision == second and status.note == "" and _version(program) == "B"


def test_a_commit_without_a_ref_stays_pending(program):
    status = _load(program)
    assert status.state == "pending" and status.entries == []
    assert "openprogram programs check" in status.note


def test_a_broken_checked_version_leaves_the_prior_version_serving(program):
    first = _check(program)
    _load(program)
    broken = _commit(program, "raise RuntimeError('scripted failure')\n", "broken")
    _check(program)

    status = _load(program)
    assert status.state == "error" and "scripted failure" in status.error
    assert status.revision == first and status.entries == [f"{program.name}_run"]
    assert status.note == f"Kept version {first[:7]}; version {broken[:7]} failed."
    assert _version(program) == "A"
    assert _registry.program_tools(program.name) == [f"{program.name}_run"]

    fixed = _commit(program, _source(program.name, "C"), "C")
    _check(program)
    status = _load(program)
    assert (status.state, status.error, status.revision) == ("loaded", "", fixed)
    assert _version(program) == "C"


def test_a_failed_version_is_not_retried_on_every_edit(program, monkeypatch):
    _check(program)
    _load(program)
    _commit(program, "raise RuntimeError('scripted failure')\n", "broken")
    _check(program)
    _load(program)

    def refuse(*args):
        raise AssertionError("the working version must not be disposed again")

    monkeypatch.setattr(packages, "_import_version", refuse)
    (program.path / "notes.md").write_text("draft", encoding="utf-8")
    assert _load(program).state == "error"


def test_the_fallback_survives_a_restart(program):
    first = _check(program)
    _load(program)
    broken = _commit(program, "raise RuntimeError('scripted failure')\n", "broken")
    _check(program)
    _forget(program.name)

    status = _load(program)
    assert status.state == "error" and status.revision == first
    assert status.note == f"Kept version {first[:7]}; version {broken[:7]} failed."
    assert _version(program) == "A"


def test_a_first_version_that_fails_has_nothing_to_keep(program):
    _commit(program, "raise RuntimeError('scripted failure')\n", "broken")
    _check(program)
    status = _load(program)
    assert status.state == "error" and "scripted failure" in status.error
    assert status.revision == "" and status.entries == [] and status.note == ""


def test_a_dirty_working_copy_serves_the_checked_version_after_a_restart(program):
    first = _check(program)
    _load(program)
    (program.path / "__init__.py").write_text(_source(program.name, "dirty"), encoding="utf-8")

    status = _load(program)
    assert (status.state, status.revision, status.note) == ("loaded", first, _DIRTY)

    _forget(program.name)
    status = _load(program)
    assert (status.state, status.revision, status.note) == ("loaded", first, _DIRTY)
    assert _version(program) == "A"


def test_losing_the_init_file_is_an_uncommitted_edit(program):
    first = _check(program)
    _load(program)
    (program.path / "__init__.py").unlink()
    status = _load(program)
    assert (status.state, status.revision, status.note) == ("loaded", first, _DIRTY)


def test_a_lazy_import_cannot_see_later_edits(program):
    (program.path / "lazy.py").write_text('VALUE = "checked"\n', encoding="utf-8")
    _git(program.path, "add", "-A")
    _git(program.path, "commit", "-qm", "lazy")
    _check(program)
    _load(program)

    (program.path / "lazy.py").write_text('VALUE = "edited"\n', encoding="utf-8")
    _load(program)
    assert importlib.import_module(f"{program.name}.lazy").VALUE == "checked"


def test_a_passing_check_makes_the_commit_callable(program):
    from openprogram.programs._host.check import check_program

    report = check_program(program.path, message="Checked")
    assert _git(program.path, "rev-parse", packages.CHECKED_REF) == report["revision"]
    assert _load(program).revision == report["revision"]


def test_check_says_when_git_is_missing(program, monkeypatch):
    from openprogram.programs._host.check import CheckFailed, check_program

    monkeypatch.setattr(packages, "git_available", lambda: False)
    with pytest.raises(CheckFailed) as failed:
        check_program(program.path)
    assert failed.value.report["problems"] == ["Git is not available"]


def test_without_git_the_status_says_so(program, monkeypatch):
    monkeypatch.setattr(packages, "git_available", lambda: False)
    status = _load(program)
    assert status.state == "error" and status.error == "Git is not available"
    assert "openprogram programs check" not in status.note


def test_without_git_a_loaded_version_keeps_serving(program, monkeypatch):
    first = _check(program)
    _load(program)
    (program.path / "notes.md").write_text("draft", encoding="utf-8")
    monkeypatch.setattr(packages, "git_available", lambda: False)
    status = _load(program)
    assert (status.state, status.revision, status.note) == ("loaded", first, "Git is not available")


def test_migration_marks_clean_packages_once(program):
    (program.root / packages._MIGRATED_MARKER).unlink()
    head = _git(program.path, "rev-parse", "HEAD")
    dirty_name = f"{program.name}_dirty"
    dirty = program.root / dirty_name
    dirty.mkdir()
    (dirty / "__init__.py").write_text(_source(dirty_name, "A"), encoding="utf-8")
    _git(dirty, "init", "-q")
    _git(dirty, "add", "-A")
    _git(dirty, "commit", "-qm", "A")
    (dirty / "__init__.py").write_text(_source(dirty_name, "edited"), encoding="utf-8")
    try:
        status = _load(program)
        assert status.state == "loaded" and status.revision == head
        assert packages._checked_commit(dirty) == ""
        assert packages.load_user_packages(program.root)[dirty_name].state == "pending"
    finally:
        shutil.rmtree(dirty)
        _forget(dirty_name)

    assert (program.root / packages._MIGRATED_MARKER).exists()
    _commit(program, _source(program.name, "B"), "B")
    assert _load(program).revision == head


def test_replacing_waits_for_running_calls(program, monkeypatch):
    _check(program)
    _load(program)
    second = _commit(program, _source(program.name, "B"), "B")
    _check(program)
    monkeypatch.setattr(packages, "CALL_WAIT_SECONDS", 10.0)
    entered = threading.Event()

    def call():
        with packages._running_call(program.name):
            entered.set()
            time.sleep(0.2)

    thread = threading.Thread(target=call)
    thread.start()
    assert entered.wait(5)
    status = _load(program)
    thread.join()
    assert status.revision == second and _version(program) == "B"


def test_a_call_past_the_wait_defers_the_replace_to_the_next_pass(program, monkeypatch):
    first = _check(program)
    _load(program)
    second = _commit(program, _source(program.name, "B"), "B")
    _check(program)
    monkeypatch.setattr(packages, "CALL_WAIT_SECONDS", 0.01)
    before = packages.root_fingerprint()

    with packages._running_call(program.name):
        status = _load(program)
        assert status.revision == first and second[:7] in status.note and _version(program) == "A"
        assert packages.root_fingerprint() != before

    status = _load(program)
    assert status.revision == second and status.note == "" and _version(program) == "B"


def test_a_registered_entry_counts_as_a_running_call():
    def fn():
        """Doc."""
        return packages._calls.get("owner")

    assert packages._bound_entry(fn, fn, "owner")() == 1
    assert "owner" not in packages._calls


def test_old_copies_are_pruned(program):
    _check(program)
    _load(program)
    latest = ""
    for version in "BCDEF":
        latest = _commit(program, _source(program.name, version), version)
        _check(program)
        _load(program)
    versions = [path.name for path in program.cache.iterdir() if path.is_dir() and not path.name.startswith(".")]
    assert latest in versions and len(versions) <= 3 + 1


def test_a_removed_package_loses_its_copies(program):
    _check(program)
    _load(program)
    assert program.cache.is_dir()
    shutil.rmtree(program.path)
    assert program.name not in packages.load_user_packages(program.root)
    assert not program.cache.exists()


def test_a_cached_file_maps_back_to_the_working_copy(monkeypatch, tmp_path):
    root = tmp_path / "programs"
    monkeypatch.setattr(packages, "programs_root", lambda: root)
    cached = (tmp_path / "programs-cache").resolve() / "name" / ("a" * 40) / "sub" / "x.py"
    assert packages.working_copy_path(cached) == root.resolve() / "name" / "sub" / "x.py"
    outside = tmp_path / "elsewhere.py"
    assert packages.working_copy_path(outside) == outside


def test_the_catalog_shows_a_loaded_package_at_its_working_copy(program):
    from openprogram.interfaces.server.routes.catalog import programs as catalog

    root = packages.programs_root()
    root.mkdir(parents=True, exist_ok=True)
    home = root / program.name
    shutil.copytree(program.path, home)
    _git(home, "update-ref", packages.CHECKED_REF, "HEAD")
    try:
        assert packages.load_user_packages(root)[program.name].state == "loaded"
        rows = catalog._registered_agent_methods()[f"applications/{program.name}"]
        assert rows[0]["name"] == f"{program.name}_run"
        assert rows[0]["source"] == home.resolve()
    finally:
        shutil.rmtree(home)
        packages.load_user_packages(root)


def test_the_load_error_api_carries_registry_failures(monkeypatch):
    from openprogram.interfaces.server.routes.catalog import programs as catalog

    monkeypatch.setattr(_registry, "_load_errors", [{"name": "external:broken", "error": "ValueError: bad"}])
    assert catalog._package_states()["broken"] == {
        "name": "broken", "state": "error", "error": "ValueError: bad", "note": ""}


def test_registry_failures_are_listed():
    _registry._load_errors.clear()
    _registry._debug_registry_error("external:broken", ValueError("bad"))
    try:
        assert _registry.load_errors() == [{"name": "external:broken", "error": "ValueError: bad"}]
    finally:
        _registry._load_errors.clear()


def test_a_failed_watcher_pass_logs_a_warning(monkeypatch, caplog):
    from openprogram.programs._host import watcher

    naps = iter([None])
    passed = threading.Event()

    def sleep(_seconds):
        if next(naps, passed) is passed:
            # The pass is over; the daemon thread parks instead of looping on.
            passed.set()
            threading.Event().wait()

    fingerprints = iter([("first",), ("second",)])
    monkeypatch.setattr(watcher, "time", types.SimpleNamespace(sleep=sleep))
    monkeypatch.setattr(watcher, "_catalog_fingerprint", lambda _base: next(fingerprints))
    monkeypatch.setattr(_registry, "rescan", lambda: (_ for _ in ()).throw(ValueError("scripted")))
    monkeypatch.delenv("OPENPROGRAM_NO_PROGRAMS_WATCH", raising=False)
    with caplog.at_level(logging.WARNING, logger="openprogram.programs._host.watcher"):
        watcher.start_in_worker(poll_interval=0)
        assert passed.wait(5)
    assert any("programs watcher pass failed: scripted" in record.getMessage() for record in caplog.records)
