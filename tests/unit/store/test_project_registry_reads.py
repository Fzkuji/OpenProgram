"""Looking a project up reads the published registry without taking its lock."""

from __future__ import annotations

import json
import os
import time

import pytest

from openprogram.state.store.project import project_store as store
from openprogram.state.store.session.session_lock import registry_file_lock


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "projects_dir", lambda: tmp_path)
    monkeypatch.setattr(store, "_registry_path", lambda: tmp_path / "projects.json")
    monkeypatch.setattr(store, "_snapshot", None, raising=False)
    store._write_registry({
        "p1": {"id": "p1", "name": "one", "path": str(tmp_path / "one"), "session_ids": ["s1"]},
        "p2": {"id": "p2", "name": "two", "path": str(tmp_path / "two"), "session_ids": ["s2"]},
    })
    return tmp_path / "projects.json"


def _publish(path, registry):
    """Replace the registry the way another process does: a whole new file."""
    temporary = path.with_name(path.name + ".other")
    temporary.write_text(json.dumps(registry), encoding="utf-8")
    os.replace(temporary, path)


def test_lookups_return_while_another_holder_has_the_registry_lock(registry, tmp_path):
    with registry_file_lock(tmp_path, "projects-registry"):
        started = time.monotonic()
        by_session = store.project_for_session("s2")
        by_id = store.get_project("p1")
        everything = store.list_projects()
        waited = time.monotonic() - started
    assert waited < 1.0, "a lookup waited for the registry lock"
    assert by_session.id == "p2"
    assert by_id.name == "one"
    assert [project.id for project in everything] == ["p1", "p2"]


def test_lookup_sees_a_registry_published_by_another_process(registry):
    assert store.project_for_session("s3") is None
    _publish(registry, {"p3": {"id": "p3", "name": "three", "path": "", "session_ids": ["s3"]}})
    assert store.project_for_session("s3").id == "p3"
    assert store.get_project("p1") is None


def test_lookup_rereads_a_file_rewritten_in_place_with_the_same_size_and_time(registry):
    assert store.project_for_session("s1").id == "p1"
    before = os.stat(registry)
    text = registry.read_text(encoding="utf-8")
    registry.write_text(text.replace('"s1"', '"s9"'), encoding="utf-8")
    os.utime(registry, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = os.stat(registry)
    assert (after.st_ino, after.st_size, after.st_mtime_ns) == (
        before.st_ino, before.st_size, before.st_mtime_ns)
    assert store.project_for_session("s1") is None
    assert store.project_for_session("s9").id == "p1"


def test_settled_registry_is_parsed_once(registry, monkeypatch):
    old = time.time_ns() - 60 * 1_000_000_000
    os.utime(registry, ns=(old, old))
    loads = []
    real = store._load_registry_file
    monkeypatch.setattr(store, "_load_registry_file", lambda path: loads.append(path) or real(path))
    for _ in range(20):
        assert store.project_for_session("s2").id == "p2"
    assert len(loads) == 1


def test_lookup_result_is_not_shared_with_the_kept_registry(registry):
    store.get_project("p1").session_ids.append("mine")
    assert store.get_project("p1").session_ids == ["s1"]


def test_change_made_here_is_seen_by_the_next_lookup(registry):
    assert store.project_for_session("s1").id == "p1"
    store.bind_session("s7", "p2")
    assert store.project_for_session("s7").id == "p2"
    store.unbind_session("s7")
    assert store.project_for_session("s7") is None


def test_unreadable_registry_is_set_aside_and_reads_as_empty(registry):
    registry.write_text("[1, 2]", encoding="utf-8")
    assert store.list_projects() == []
    assert not registry.exists()
    kept = [path for path in registry.parent.iterdir() if ".unreadable-" in path.name]
    assert len(kept) == 1 and kept[0].read_text(encoding="utf-8") == "[1, 2]"


def test_registry_replaced_before_the_lock_is_not_set_aside(registry, monkeypatch):
    good = registry.read_text(encoding="utf-8")
    registry.write_text("not json", encoding="utf-8")
    real = store._registry_transaction
    from contextlib import contextmanager

    @contextmanager
    def writer_got_there_first():
        _publish(registry, json.loads(good))
        with real():
            yield

    monkeypatch.setattr(store, "_registry_transaction", writer_got_there_first)
    assert sorted(store._read_registry()) == ["p1", "p2"]
    assert registry.exists()
    assert not [path for path in registry.parent.iterdir() if ".unreadable-" in path.name]
