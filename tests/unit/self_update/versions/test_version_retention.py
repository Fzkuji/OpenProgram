"""What retention keeps and removes."""
import json
import os
import time

import pytest

from openprogram.operations.self_update.versions import pointer, retention, store
from tests.support.sealed_versions import BASE, make_layout, make_version, point_at

COMMIT = "a" * 40


@pytest.fixture
def layout(tmp_path):
    root = make_layout(tmp_path / "state" / "runtime")
    return root


@pytest.fixture
def state(layout):
    return layout.root.parent


def _committed(monkeypatch, *names):
    monkeypatch.setattr(store, "committed_versions", lambda _layout: set(names))


def _names(count):
    return [f"202611{day:02d}T000000Z-aaaaaaaaaaaa" for day in range(1, count + 1)]


def _write_update(state, name, phase, **request):
    directory = state / "self-updates" / name
    directory.mkdir(parents=True)
    (directory / "request.json").write_text(json.dumps(request))
    (directory / "state.json").write_text(json.dumps({"phase": phase}))
    return directory


def test_the_good_set_and_five_recent_are_kept(layout, state, monkeypatch):
    names = _names(10)
    for name in names:
        make_version(layout, name)
    point_at(layout, names[-1], last_good=names[-2], last_good_base=BASE, previous_good=names[0], previous_good_base=BASE)
    _committed(monkeypatch, *names)
    plan = retention.plan(layout, state)
    assert not plan.held
    assert set(plan.versions) == set(names[1:5])
    retention.run(layout, state)
    remaining = {item["id"] for item in store.list_versions(layout)}
    assert remaining == set(names) - set(names[1:5])
    assert not any(layout.trash.iterdir())


def test_nothing_is_touched_while_the_slot_is_held(layout, state, monkeypatch):
    names = _names(8)
    for name in names:
        make_version(layout, name)
    point_at(layout, names[-1])
    _committed(monkeypatch, *names)
    _write_update(state, "u1", "trial", candidate_version=names[0])
    plan = retention.run(layout, state)
    assert plan.held
    assert len(store.list_versions(layout)) == 8


def test_recent_boot_records_keep_a_version(layout, state, monkeypatch):
    names = _names(8)
    for name in names:
        make_version(layout, name)
    point_at(layout, names[-1])
    _committed(monkeypatch, *names)
    layout.boot.mkdir()
    layout.attempts(names[0]).write_text("1\n")
    assert names[0] not in retention.plan(layout, state).versions


def test_a_failed_candidate_goes_after_seven_days(layout, state, monkeypatch):
    make_version(layout, "failed")
    make_version(layout, "good")
    point_at(layout, "good")
    _committed(monkeypatch, "good")
    assert retention.plan(layout, state).versions == []
    later = time.time() + retention.FAILED_CANDIDATE_SECONDS + 1
    assert retention.plan(layout, state, now=later).versions == ["failed"]


def test_unreferenced_bases_and_frontends_go(layout, state, monkeypatch):
    names = _names(7)
    for name in names[:-1]:
        make_version(layout, name)
    make_version(layout, names[-1], frontend="new")
    point_at(layout, names[-1])
    _committed(monkeypatch, *names)
    (layout.bases / "py3.11-aaaaaaaaaaaaaaaa").mkdir()
    old_frontend = layout.frontends / "abandoned.zip"
    old_frontend.write_bytes(b"")
    plan = retention.run(layout, state)
    assert plan.bases == ["py3.11-aaaaaaaaaaaaaaaa"]
    assert not (layout.bases / "py3.11-aaaaaaaaaaaaaaaa").exists()
    assert (layout.bases / BASE).is_dir()
    assert not old_frontend.exists()
    assert len(list(layout.frontends.iterdir())) == 2


def test_finished_work_goes_and_aborted_work_after_fourteen_days(layout, state, monkeypatch):
    make_version(layout, "good")
    point_at(layout, "good")
    _committed(monkeypatch, "good")
    for task in ("done", "failed", "open"):
        (layout.work / task).mkdir(parents=True)
    _write_update(state, "u1", "succeeded", task="done")
    _write_update(state, "u2", "aborted", task="failed")
    assert retention.plan(layout, state).work == ["done"]
    later = time.time() + retention.ABORTED_WORK_SECONDS + 1
    assert retention.plan(layout, state, now=later).work == ["done", "failed"]
    retention.run(layout, state)
    assert not (layout.work / "done").exists()
    assert (layout.work / "open").is_dir()


def test_unknown_history_deletes_nothing(layout, state, monkeypatch):
    make_version(layout, "good")
    point_at(layout, "good")
    monkeypatch.setattr(store, "committed_versions", lambda _layout: None)
    assert retention.plan(layout, state).held


def test_a_version_a_waiting_update_needs_is_kept(layout, state, monkeypatch):
    names = _names(7)
    for name in names:
        make_version(layout, name)
    point_at(layout, names[-1])
    _committed(monkeypatch, *names)
    (state / "self-updates").mkdir(parents=True)
    (state / "self-updates" / "queue.json").write_text(json.dumps({"schema": 1, "entries": [
        {"request": {"update_id": "su_q", "candidate_version": names[0], "controller_version": names[-1]}},
    ]}))
    assert names[0] not in retention.plan(layout, state).versions


def test_the_version_an_open_app_loaded_is_kept(layout, state, monkeypatch):
    names = _names(7)
    for name in names:
        make_version(layout, name)
    point_at(layout, names[-1])
    _committed(monkeypatch, *names)
    (layout.root / "shell.json").write_text(json.dumps({"pid": os.getpid(), "versionId": names[0]}))
    assert names[0] not in retention.plan(layout, state).versions
    (layout.root / "shell.json").write_text(json.dumps({"pid": 2 ** 22 + 1, "versionId": names[0]}))
    assert names[0] in retention.plan(layout, state).versions
