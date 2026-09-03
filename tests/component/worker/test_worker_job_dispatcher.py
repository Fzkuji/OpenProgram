from __future__ import annotations

from pathlib import Path


def test_worker_keeps_durable_job_dispatcher_alive() -> None:
    source = Path("openprogram/worker/runner.py").read_text(encoding="utf-8")

    lock_acquired = source.index("initialize_provider_runtime()")
    dispatcher_started = source.index("get_job_runner()")
    web_started = source.index("start_web(port=port, open_browser=False)")
    dispatcher_stopped = source.index("shutdown_job_runner()")
    lock_released = source.rindex("lock.release()")

    assert lock_acquired < dispatcher_started < web_started
    assert dispatcher_stopped < lock_released


def test_self_update_recovery_precedes_scheduler_but_not_web() -> None:
    source = Path("openprogram/worker/runner.py").read_text(encoding="utf-8")
    assert source.index("get_job_runner()") < source.index("recover_pending_updates()")
    assert source.index("start_web(port=port, open_browser=False)") < source.index("recover_pending_updates()")
    assert source.index("recover_pending_updates()") < source.index("scheduler_stop, scheduler_thread = start_in_worker()")
