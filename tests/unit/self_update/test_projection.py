import json
import os
import time

import pytest

from openprogram.self_update import SelfUpdateStore, UpdatePhase
from openprogram.self_update.projection import ProjectionAccessError, list_status, read_status, running_status
from openprogram.self_update.recovery import SYSTEM_CHECKS
from openprogram.self_update.types import CorruptUpdateStateError, UpdateNotFoundError
from tests.unit.self_update.test_store import _request


def _snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None,
            p.stat().st_mode, p.stat().st_mtime_ns, p.stat().st_ctime_ns)
            for p in (root, *root.rglob('*'))}


def _gate(request):
    return dict(schema=1, candidate_sha=request.candidate_sha, attempt=1,
                worker_pid=os.getpid(), verified_at=time.time(),
                checks={name: True for name in SYSTEM_CHECKS})


def test_empty_history_and_named_status_do_not_create_files(tmp_path):
    store = SelfUpdateStore(tmp_path / "absent")
    assert list_status(store, session_id="session-1") == {"items": [], "next_cursor": None}
    assert running_status(store) == []
    with pytest.raises(UpdateNotFoundError):
        read_status(store, session_id="session-1", update_id="su_missing")
    assert not store.root.exists()


def test_read_only_journal_and_active_pointer_are_not_repaired(tmp_path):
    store = SelfUpdateStore(tmp_path / "updates")
    store.create(_request())
    store.transition("su_test", UpdatePhase.STAGING)
    journal = store.root / "su_test" / "events.jsonl"
    journal.write_bytes(journal.read_bytes().splitlines(keepends=True)[0])
    (store.root / "active.json").unlink()
    before = _snapshot(store.root)
    assert read_status(store, session_id="session-1")["phase"] == "staging"
    assert list_status(store, session_id="session-1")["items"][0]["phase"] == "staging"
    assert _snapshot(store.root) == before


def test_terminal_history_survives_active_cleanup_and_is_session_scoped(tmp_path):
    store = SelfUpdateStore(tmp_path / "updates")
    store.create(_request())
    store.transition("su_test", UpdatePhase.ABORTED)
    assert not (store.root / "active.json").exists()
    before = _snapshot(store.root)
    assert list_status(store, session_id="session-1")["items"][0]["phase"] == "aborted"
    assert list_status(store, session_id="foreign")["items"] == []
    with pytest.raises(ProjectionAccessError):
        read_status(store, session_id="foreign", update_id="su_test")
    assert running_status(store) == []
    assert _snapshot(store.root) == before


def test_cursor_keeps_upper_bound_and_rejects_cross_session_replay(tmp_path):
    store = SelfUpdateStore(tmp_path / "updates")
    for n in range(3):
        req = _request(f"su_{n}")
        store.create(req)
        store.transition(req.update_id, UpdatePhase.ABORTED)
    first = list_status(store, session_id="session-1", limit=1)
    store.create(_request("su_later"))
    second = list_status(store, session_id="session-1", limit=1, cursor=first["next_cursor"])
    third = list_status(store, session_id="session-1", limit=1, cursor=second["next_cursor"])
    assert [p["items"][0]["update_id"] for p in (first, second, third)] == ["su_2", "su_1", "su_0"]
    assert third["next_cursor"] is None
    with pytest.raises(ValueError, match="cursor"):
        list_status(store, session_id="foreign", cursor=first["next_cursor"])
    with pytest.raises(ValueError, match="cursor"):
        list_status(store, session_id="session-1", cursor="not base64")


@pytest.mark.parametrize("mutation", ["attempt", "sha", "timestamp", "checks", "pid", "extra"])
def test_unmatched_gate_is_unknown_not_a_fallback(tmp_path, mutation):
    store = SelfUpdateStore(tmp_path / "updates")
    request = _request()
    store.create(request)
    for phase in (UpdatePhase.STAGING, UpdatePhase.READY, UpdatePhase.ACTIVATING):
        store.transition(request.update_id, phase)
    good = _gate(request)
    bad = dict(good)
    changes = {"attempt": {"attempt": 2}, "sha": {"candidate_sha": "4" * 40},
               "timestamp": {"verified_at": 0}, "checks": {"checks": {k: 1 for k in SYSTEM_CHECKS}},
               "pid": {"worker_pid": True}, "extra": {"token": "private"}}
    bad.update(changes[mutation])
    store.transition(request.update_id, UpdatePhase.VERIFYING,
                     detail={"system_gate": bad, "previous_system_gate": good, "current_revision": "secret"})
    status = read_status(store, session_id=request.session_id)
    assert status["last_verified_runtime"] is None
    assert "secret" not in json.dumps(status)
    assert "private" not in json.dumps(status)
    assert "current_revision" not in status and "active_app" not in status


def test_phase_selects_committed_gate_and_intermediate_phase_is_unknown(tmp_path):
    store = SelfUpdateStore(tmp_path / "updates")
    req = _request()
    store.create(req)
    for phase in (UpdatePhase.STAGING, UpdatePhase.READY):
        store.transition(req.update_id, phase)
    gate = _gate(req)
    store.transition(req.update_id, UpdatePhase.ACTIVATING, detail={"previous_system_gate": gate})
    assert read_status(store, session_id=req.session_id)["last_verified_runtime"] is None
    store.transition(req.update_id, UpdatePhase.VERIFYING, detail={"system_gate": gate})
    committed = dict(gate, verified_at=time.time())
    store.transition(req.update_id, UpdatePhase.SUCCEEDED, detail={"system_gate": gate, "committed_system_gate": committed})
    status = read_status(store, session_id=req.session_id, update_id=req.update_id)
    assert status["last_verified_runtime"]["source"] == "committed_system_gate"
    assert status["last_verified_runtime"]["verified_at"] == committed["verified_at"]
    assert not status["rollback_available"]


@pytest.mark.parametrize("target", ["root", "request.json", "state.json", "events.jsonl", ".lock"])
def test_symlinks_are_rejected_without_modification(tmp_path, target):
    store = SelfUpdateStore(tmp_path / "updates")
    store.create(_request())
    path = store.root if target == "root" else (store.root / target if target == ".lock" else store.root / "su_test" / target)
    real = tmp_path / "real"
    path.rename(real)
    path.symlink_to(real, target_is_directory=target == "root")
    with pytest.raises((CorruptUpdateStateError, OSError)):
        read_status(store, session_id="session-1", update_id="su_test")
    assert path.is_symlink()


def test_corruption_and_future_schema_are_not_empty_history(tmp_path):
    store = SelfUpdateStore(tmp_path / "updates")
    store.create(_request())
    path = store.root / "su_test" / "state.json"
    for raw in ("broken", '{"schema":999}'):
        path.write_text(raw)
        before = _snapshot(store.root)
        with pytest.raises(CorruptUpdateStateError):
            list_status(store, session_id="session-1")
        assert _snapshot(store.root) == before
