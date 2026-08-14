from __future__ import annotations

from collections.abc import Iterator
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import inspect
from pathlib import Path
import threading
import time

import pytest


UNIT_TESTS = Path(__file__).resolve().parents[1] / "unit"


def _called_directly_from_unit_test() -> bool:
    caller = inspect.currentframe()
    if caller is None or caller.f_back is None or caller.f_back.f_back is None:
        return False
    path = Path(caller.f_back.f_back.f_code.co_filename).resolve()
    return path.is_relative_to(UNIT_TESTS)


@contextmanager
def reject_unit_background_threads(
    monkeypatch: pytest.MonkeyPatch,
    *,
    direct_calls_only: bool = False,
) -> Iterator[None]:
    """Reject test-owned threads while allowing production internals to run."""
    original_thread = threading.Thread
    original_start = threading.Thread.start
    original_pool_init = ThreadPoolExecutor.__init__
    original_time_sleep = time.sleep
    original_asyncio_sleep = asyncio.sleep
    started_threads: list[threading.Thread] = []
    constructed_pools: list[ThreadPoolExecutor] = []

    def guarded_thread_start(thread: threading.Thread) -> None:
        if not direct_calls_only or _called_directly_from_unit_test():
            pytest.fail("unit test started a real background thread")
        original_start(thread)
        started_threads.append(thread)

    def guarded_thread_pool(pool: ThreadPoolExecutor, *args, **kwargs) -> None:
        if not direct_calls_only or _called_directly_from_unit_test():
            pytest.fail("unit test constructed a real thread pool")
        original_pool_init(pool, *args, **kwargs)
        constructed_pools.append(pool)

    def guarded_time_sleep(delay: float) -> None:
        if not direct_calls_only or _called_directly_from_unit_test():
            pytest.fail("unit test used a fixed time.sleep")
        original_time_sleep(delay)

    def guarded_asyncio_sleep(delay: float, result=None):
        if delay != 0 and (
            not direct_calls_only or _called_directly_from_unit_test()
        ):
            pytest.fail("unit test used a nonzero asyncio.sleep")
        return original_asyncio_sleep(delay, result)

    monkeypatch.setattr(threading.Thread, "start", guarded_thread_start)
    monkeypatch.setattr(ThreadPoolExecutor, "__init__", guarded_thread_pool)
    monkeypatch.setattr(time, "sleep", guarded_time_sleep)
    monkeypatch.setattr(asyncio, "sleep", guarded_asyncio_sleep)
    try:
        yield
    finally:
        replaced_thread_class = threading.Thread is not original_thread
        if replaced_thread_class:
            threading.Thread = original_thread

        leaked_pools = [
            pool for pool in constructed_pools if not getattr(pool, "_shutdown", False)
        ]
        for pool in leaked_pools:
            pool.shutdown(wait=False, cancel_futures=True)

        for thread in started_threads:
            if isinstance(thread, threading.Timer) and thread.is_alive():
                thread.cancel()
            if thread.is_alive():
                thread.join(timeout=0.1)
        leaked_threads = [thread for thread in started_threads if thread.is_alive()]

        if replaced_thread_class:
            pytest.fail("unit test replaced process-global threading.Thread")
        if leaked_pools:
            pytest.fail("unit test left a production thread pool open")
        if leaked_threads:
            names = ", ".join(thread.name for thread in leaked_threads)
            pytest.fail(f"unit test left production threads running: {names}")
