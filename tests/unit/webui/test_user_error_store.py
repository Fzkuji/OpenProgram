from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import os
import stat
import time

from openprogram.webui.user_errors import UserErrorRecord, UserErrorStore


def _record(index: int, occurred_at_epoch: float) -> UserErrorRecord:
    occurred_at = datetime.fromtimestamp(
        occurred_at_epoch,
        tz=timezone.utc,
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return UserErrorRecord(
        principal_id="owner/install/test-owner",
        error_id=f"err_{index:04d}",
        request_id=f"request-{index}",
        scope="session",
        code="handler_error",
        message="Action failed",
        action="chat",
        session_id="session-1",
        operation_id=None,
        retryable=False,
        severity="error",
        correlation_id=f"corr_{index:04d}",
        occurred_at=occurred_at,
        occurred_at_epoch=occurred_at_epoch,
    )


def test_user_error_store_round_trips_safe_record(tmp_path) -> None:
    path = tmp_path / "user_errors.db"
    store = UserErrorStore(path)
    now = time.time()
    record = _record(1, now)

    store.record(record, now=now)

    reopened = UserErrorStore(path)
    assert reopened.get(record.principal_id, record.error_id, now=now) == record
    assert reopened.list_open(record.principal_id).records == (record,)


def test_user_error_store_enforces_capacity_and_seven_day_retention(
    tmp_path,
    monkeypatch,
) -> None:
    from openprogram.webui import user_errors

    monkeypatch.setattr(user_errors, "MAX_RECORDS_PER_PRINCIPAL", 3)
    path = tmp_path / "user_errors.db"
    store = UserErrorStore(path)
    now = 2_000_000.0

    expired = _record(1, now - user_errors.RETENTION_SECONDS - 0.001)
    boundary = _record(2, now - user_errors.RETENTION_SECONDS)
    newest = [_record(i, now + i) for i in range(3, 6)]
    for record in (expired, boundary, *newest):
        store.record(record, now=now + 5)

    reopened = UserErrorStore(path)
    rows = reopened.list_open(
        "owner/install/test-owner",
        limit=10,
        now=now + 5,
    ).records

    assert [row.error_id for row in rows] == ["err_0005", "err_0004", "err_0003"]
    assert reopened.get(expired.principal_id, expired.error_id) is None
    assert reopened.get(boundary.principal_id, boundary.error_id) is None


def test_user_error_store_keeps_exact_retention_boundary(tmp_path) -> None:
    from openprogram.webui.user_errors import RETENTION_SECONDS

    now = 3_000_000.0
    record = _record(1, now - RETENTION_SECONDS)
    store = UserErrorStore(tmp_path / "user_errors.db")

    store.record(record, now=now)

    assert store.get(record.principal_id, record.error_id, now=now) == record


def test_activity_for_one_principal_prunes_expired_other_principal(tmp_path) -> None:
    from openprogram.webui.user_errors import RETENTION_SECONDS

    now = 4_000_000.0
    store = UserErrorStore(tmp_path / "user_errors.db")
    expired = replace(
        _record(1, now - RETENTION_SECONDS - 0.001),
        principal_id="owner/install/expired-owner",
    )
    active = replace(
        _record(2, now),
        principal_id="owner/install/active-owner",
    )

    store.record(expired, now=now - RETENTION_SECONDS - 0.001)
    store.record(active, now=now)

    assert store.get(expired.principal_id, expired.error_id, now=now) is None
    assert store.get(active.principal_id, active.error_id, now=now) == active


def test_user_error_store_isolates_authenticated_principals(tmp_path) -> None:
    path = tmp_path / "user_errors.db"
    store = UserErrorStore(path)
    now = time.time()
    owner_record = _record(1, now)
    other_record = replace(
        owner_record,
        principal_id="owner/install/other-owner",
        error_id="err_other",
        correlation_id="corr_other",
    )

    store.record(owner_record, now=now)
    store.record(other_record, now=now)

    assert store.get(owner_record.principal_id, other_record.error_id) is None
    assert store.get(other_record.principal_id, owner_record.error_id) is None
    assert store.list_open(owner_record.principal_id).records == (owner_record,)
    assert store.list_open(other_record.principal_id).records == (other_record,)


def test_user_error_store_default_path_tracks_active_profile(
    tmp_path,
    monkeypatch,
) -> None:
    from openprogram import paths

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENPROGRAM_PROFILE", "error-test")
    monkeypatch.setattr(paths, "_migration_checked", True)
    monkeypatch.setattr(paths, "_root_mode_checked", set())
    now = time.time()
    store = UserErrorStore()

    store.record(_record(1, now), now=now)

    expected = tmp_path / ".openprogram-error-test" / "user_errors.db"
    assert store.path == expected
    assert expected.exists()
    if os.name != "nt":
        assert stat.S_IMODE(expected.stat().st_mode) == 0o600
