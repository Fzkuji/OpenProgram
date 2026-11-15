"""Schedules, the Agent registry, projects and checkpoints save through a held file.

Each store below writes a temporary file and renames it over the real one.
On Windows the rename is refused while a reader, the indexer or an antivirus
scan holds the destination, and a store that renamed directly lost the write.
Every one now publishes with ``filesystem.replace_file``, which waits.

These tests name the platform through the seam's own ``_sys`` and stand a
refusing ``os.replace`` in for the system call, so they run here; they were
not run on Windows. ``tests/contracts/repository/test_portable_file_handling``
counts the renames that stay direct.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from openprogram.host._platform import filesystem


def _sharing_violation() -> PermissionError:
    error = PermissionError(13, "The process cannot access the file because it is being used by another process")
    error.winerror = 32
    return error


@pytest.fixture
def attempts(monkeypatch) -> list[str]:
    """Windows, where each destination is held for its first two renames."""
    real = os.replace
    calls: list[str] = []

    def replace(source, target, *args, **kwargs):
        calls.append(os.fspath(target))
        if calls.count(os.fspath(target)) <= 2:
            raise _sharing_violation()
        return real(source, target, *args, **kwargs)

    monkeypatch.setattr(filesystem, "_sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(os, "replace", replace)
    return calls


@pytest.fixture
def state(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "state"
    root.mkdir()
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: root)
    return root


def _task_runs(state: Path) -> Path:
    from openprogram.runtime.scheduler import task_runs

    task_runs._write(str(state / "task-runs.json"), {"t1": {"last": 1}})
    return state / "task-runs.json"


def _run_ending(state: Path) -> Path:
    from openprogram.runtime.scheduler import run_finish

    run_finish.keep({"execution_id": "e1", "status": "done"})
    return Path(run_finish._kept_path("e1"))


def _schedule(state: Path) -> Path:
    from openprogram.programs.tools.jobs.cron import cron

    cron._save(str(state / "schedule.json"), [{"id": "job", "prompt": "中文"}])
    return state / "schedule.json"


def _cron_worker_state(state: Path) -> Path:
    from openprogram.programs.tools.jobs.cron import worker

    worker._write_json(str(state / "worker-state.json"), {"job": {"last_run": 1}})
    return state / "worker-state.json"


def _agent_registry(state: Path) -> Path:
    from openprogram.runtime.agent_registry import manager

    manager._write_json(state / "agents" / "main" / "agent.json", {"id": "main"})
    return state / "agents" / "main" / "agent.json"


def _session_aliases(state: Path) -> Path:
    from openprogram.runtime.agent_registry import session_aliases

    session_aliases._write([{"session_id": "s1"}])
    return session_aliases._path()


def _channel_bindings(state: Path) -> Path:
    from openprogram.interfaces.channels import bindings

    bindings._write([{"agent_id": "main"}])
    return bindings._path()


def _topics(state: Path) -> Path:
    from openprogram.runtime.conversation import topics

    topics._write_book(state, topics.TopicBook([{"id": "t1", "title": "one"}], {"m1": "t1"}))
    return state / topics.TOPICS_FILE


def _project_registry(state: Path) -> Path:
    from openprogram.state.store.project import project_store

    project_store._registry_path().parent.mkdir(parents=True, exist_ok=True)
    project_store._write_registry({"p1": {"id": "p1", "name": "项目"}})
    return project_store._registry_path()


def _project_identity(state: Path) -> Path:
    from openprogram.state.store.project import identity

    folder = state / "work"
    folder.mkdir()
    token = identity.capture_directory_identity(folder)["directory_identity"]
    assert identity.read_marker(folder) == token
    return next(path for path in folder.rglob("*") if path.is_file())


def _checkpoint_manifest(state: Path) -> Path:
    from openprogram.state.store.snapshot.checkpoint import manifest

    manifest.save(state / "turns" / "t1" / "manifest.json", {"version": 1, "entries": []})
    return state / "turns" / "t1" / "manifest.json"


def _mutation_sequence(state: Path) -> Path:
    from openprogram.state.store.snapshot.checkpoint.store import CheckpointStore

    assert CheckpointStore._next_mutation_sequence() == 1
    return state / "mutation-locks" / "workspace-sequence"


def _program_meta(state: Path) -> Path:
    from openprogram.programs._host import meta_storage

    meta_storage.save_meta("programs-meta.json", {"favorites": ["a"]})
    return state / "programs-meta.json"


def _update_overview(state: Path) -> Path:
    from openprogram.operations.self_update.versions import overview

    overview._write_json(state / "overview.json", {"current": "v1"})
    return state / "overview.json"


def _systemd_unit(state: Path) -> Path:
    from openprogram.operations.worker.services import systemd

    systemd._write_unit(state / "openprogram.service", b"[Unit]\n")
    return state / "openprogram.service"


def _trash_record(state: Path) -> Path:
    from openprogram.host.sandbox import recoverable_delete

    recoverable_delete._atomic_write(state / "entry.json", "{}", 0o600)
    return state / "entry.json"


SITES = [
    _task_runs, _run_ending, _schedule, _cron_worker_state, _agent_registry, _session_aliases,
    _channel_bindings, _topics, _project_registry, _project_identity, _checkpoint_manifest,
    _mutation_sequence, _program_meta, _update_overview, _systemd_unit, _trash_record,
]


@pytest.mark.parametrize("write", SITES, ids=lambda site: site.__name__.lstrip("_"))
def test_a_save_survives_a_reader_holding_the_file(write, state, attempts):
    target = write(state)

    assert target.is_file() and target.stat().st_size > 0
    assert attempts.count(str(target)) == 3
    leftovers = [path.name for path in target.parent.iterdir() if ".tmp" in path.name or path.name.startswith(".tmp")]
    assert leftovers == []


def test_a_checkpoint_blob_is_stored_through_a_scan_of_the_blob_folder(state, attempts):
    from openprogram.state.store.snapshot.checkpoint.prompt_checkpoints import PromptCheckpoints, _copy_blob

    source_store = PromptCheckpoints(state / "one")
    target_store = PromptCheckpoints(state / "two")
    source_store.blobs.mkdir(parents=True)
    digest = "ab" * 32
    source_store.blob_path(digest).write_bytes(b"saved file")

    _copy_blob(source_store, target_store, digest)

    assert target_store.blob_path(digest).read_bytes() == b"saved file"
    assert attempts.count(str(target_store.blob_path(digest))) == 3


def test_an_unreadable_project_registry_is_set_aside_through_a_held_file(state, attempts):
    from openprogram.state.store.project import project_store

    registry = project_store._registry_path()
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text("[]", encoding="utf-8")

    assert project_store._read_registry() == {}

    kept = [path for path in registry.parent.iterdir() if ".unreadable-" in path.name]
    assert len(kept) == 1 and kept[0].read_text(encoding="utf-8") == "[]"


def test_the_worker_log_generations_shift_through_a_held_file(tmp_path, attempts):
    from openprogram.operations.worker import lifecycle

    log = tmp_path / "worker.log"
    log.write_bytes(b"x" * 64)
    (tmp_path / "worker.log.1").write_bytes(b"older")

    assert lifecycle.rotate_worker_log(log, limit_bytes=16, keep=3) is True

    assert (tmp_path / "worker.log.2").read_bytes() == b"older"
    assert (tmp_path / "worker.log.1").read_bytes() == b"x" * 64 and log.stat().st_size == 0


def test_posix_saves_with_one_rename(state, monkeypatch):
    real = os.replace
    calls: list[str] = []
    monkeypatch.setattr(os, "replace", lambda source, target: (calls.append(os.fspath(target)), real(source, target))[1])

    target = _task_runs(state)

    assert calls == [str(target)]
    assert json.loads(target.read_bytes()) == {"t1": {"last": 1}}
