from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import multiprocessing
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


def _write_from_process(
    path: str,
    index: int,
    now: float,
    start,
    results,
) -> None:
    results.put(("ready", index, ""))
    if not start.wait(20):
        results.put(("error", index, "start timeout"))
        return
    try:
        UserErrorStore(path).record(_record(index, now + index), now=now + 20)
    except Exception as exc:  # pragma: no cover - parent reports child detail
        results.put(("error", index, repr(exc)))
    else:
        results.put(("ok", index, ""))


def test_user_error_store_serializes_concurrent_writers(tmp_path) -> None:
    path = tmp_path / "user_errors.db"
    now = time.time()

    def write(index: int) -> None:
        UserErrorStore(path).record(_record(index, now + index), now=now + 20)

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(write, range(1, 21)))

    records = UserErrorStore(path).list_open(
        "owner/install/test-owner",
        limit=100,
        now=now + 20,
    ).records
    assert {record.error_id for record in records} == {
        f"err_{index:04d}" for index in range(1, 21)
    }


def test_user_error_store_initializes_safely_across_processes(tmp_path) -> None:
    path = tmp_path / "user_errors.db"
    now = time.time()
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_write_from_process,
            args=(str(path), index, now, start, results),
        )
        for index in range(1, 13)
    ]
    for process in processes:
        process.start()
    ready = [results.get(timeout=30) for _ in processes]
    assert all(status == "ready" for status, _, _ in ready), ready

    start.set()
    rows = [results.get(timeout=30) for _ in processes]
    for process in processes:
        process.join(timeout=30)

    assert all(process.exitcode == 0 for process in processes)
    assert all(status == "ok" for status, _, _ in rows), rows
    records = UserErrorStore(path).list_open(
        "owner/install/test-owner",
        limit=100,
        now=now + 20,
    ).records
    assert len(records) == len(processes)
