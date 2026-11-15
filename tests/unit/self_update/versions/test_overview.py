"""What the Versions page and the two notices read from the version store."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from openprogram.operations.self_update.versions import layout as paths
from openprogram.operations.self_update.versions import overview, pointer as pointers
from openprogram.operations.self_update.versions import store as sealed

A, B, C = "20261101T100000Z-aaaaaaaaaaaa", "20261102T100000Z-bbbbbbbbbbbb", "20261103T100000Z-cccccccccccc"
BASE = "py3.12-0123456789abcdef"


def _version(layout, identifier, parent="", summary="A change"):
    directory = layout.version(identifier)
    directory.mkdir(parents=True)
    (directory / "version.json").write_text(json.dumps({
        "id": identifier, "commit": identifier[-12:] * 3, "base": BASE, "shell_compat": 1, "built_by": "t",
        "built_at": "2026-11-08T10:42:00Z", "components": [], "parent": parent, "frontend_sha": "", "summary": summary,
    }))


@pytest.fixture
def layout(tmp_path, monkeypatch):
    built = paths.Layout(tmp_path / "runtime")
    built.root.mkdir()
    _version(built, A)
    _version(built, B, parent=A)
    _version(built, C, parent=B)
    pointers.create(built, pointers.Pointer(generation=1, version=C, base=BASE, last_good=B, last_good_base=BASE,
                                            previous_good=A, previous_good_base=BASE, writer="controller"))
    monkeypatch.setattr(sealed, "committed_versions", lambda _layout: {A, B, C})
    return built


NO_STORE = SimpleNamespace(root=Path("/nonexistent"))


def test_read_names_current_fallback_and_kept_versions(layout):
    page = overview.read(layout)
    assert page["managed"] is True
    assert (page["current"]["id"], page["current"]["number"]) == (C, 3)
    assert page["fallback"] == {"id": B, "number": 2}
    assert [item["id"] for item in page["versions"]] == [C, B, A]
    assert page["versions"][0]["current"] is True
    assert page["versions"][0]["summary"] == "A change"
    assert [item["can_undo_change"] for item in page["versions"]] == [True, True, False]


def test_a_failed_candidate_is_not_offered(layout, monkeypatch):
    _version(layout, "20261104T100000Z-dddddddddddd", parent=C)
    assert "20261104T100000Z-dddddddddddd" not in [item["id"] for item in overview.read(layout)["versions"]]


def test_numbers_never_change_or_come_back(layout):
    assert overview.numbers(layout) == {A: 1, B: 2, C: 3}
    (layout.version(A) / "version.json").unlink()
    layout.version(A).rmdir()
    assert overview.numbers(layout)[C] == 3
    newest = "20261105T100000Z-eeeeeeeeeeee"
    _version(layout, newest, parent=C)
    assert overview.numbers(layout)[newest] == 4


def _guard_line(from_id, to_id, reason, at=None):
    moment = datetime.now(timezone.utc) if at is None else at
    return f"{moment.strftime('%Y-%m-%dT%H:%M:%SZ')} launcher: moved from {from_id} to {to_id}: {reason}\n"


def test_a_number_the_build_stamped_is_the_number_shown(layout):
    identifier = "20261104T100000Z-dddddddddddd"
    _version(layout, identifier, parent=C)
    manifest = layout.version(identifier) / "version.json"
    manifest.write_text(json.dumps({**json.loads(manifest.read_text()), "number": 9}))
    assert overview.numbers(layout)[identifier] == 9
    following = "20261105T100000Z-eeeeeeeeeeee"
    _version(layout, following, parent=identifier)
    assert overview.numbers(layout)[following] == 10


def test_a_guard_return_is_a_notice_once_and_explains_the_running_row(layout):
    layout.recovery_log.write_text(_guard_line(C, B, "repeated abnormal ends"))
    pointer = pointers.read(layout)
    pointers.compare_and_set(layout, pointer.generation, writer="launcher", version=B, base=BASE)
    notices = overview.pending_notices(layout, NO_STORE)
    assert [(notice["kind"], notice["number"]) for notice in notices] == [("returned", 3)]
    assert overview.read(layout)["current"]["returned_from"]["number"] == 3
    overview.acknowledge(layout, [notices[0]["id"]])
    assert overview.pending_notices(layout, NO_STORE) == []


def test_a_return_during_probation_belongs_to_its_update(layout):
    layout.recovery_log.write_text(_guard_line(C, B, "repeated abnormal ends on probation"))
    assert overview.pending_notices(layout, NO_STORE) == []


def test_saved_edits_are_listed_and_noticed_once(layout):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    saved = layout.root / "unapplied" / stamp
    saved.mkdir(parents=True)
    (saved / "manifest.json").write_text(json.dumps({"version": C, "changed": ["a.py"], "added": ["b.py"], "removed": ["c.py"]}))
    assert overview.read(layout)["unapplied"][0]["files"] == 3
    notices = overview.pending_notices(layout, NO_STORE)
    assert (notices[0]["kind"], notices[0]["files"]) == ("direct_edits", 3)
    overview.acknowledge(layout, [notices[0]["id"]])
    assert overview.pending_notices(layout, NO_STORE) == []
    assert overview.find_unapplied(layout, stamp)["path"] == str(saved)
    assert overview.find_unapplied(layout, "missing") is None


def test_an_old_return_is_not_news(layout):
    old = datetime.fromtimestamp(1_000_000, timezone.utc)
    layout.recovery_log.write_text(_guard_line(C, B, "repeated abnormal ends", at=old))
    assert overview.pending_notices(layout, NO_STORE) == []


def test_undo_is_offered_only_for_commits_still_in_use(layout, monkeypatch):
    monkeypatch.setattr(overview, "_on_local_line", lambda _layout: {A[-12:] * 3, B[-12:] * 3})
    offered = {item["id"]: item["can_undo_change"] for item in overview.read(layout)["versions"]}
    assert offered == {A: False, B: True, C: False}  # A has no parent; C's commit left the line.


def test_undo_stays_offered_when_the_repository_cannot_say(layout, monkeypatch):
    monkeypatch.setattr(overview, "_on_local_line", lambda _layout: None)
    assert {item["id"]: item["can_undo_change"] for item in overview.read(layout)["versions"]}[C] is True
