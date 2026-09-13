"""Public budget views do not use connections after their read scope ends."""
from contextlib import contextmanager

from ._support import (
    Job,
    ResourceGovernor,
    ResourceLimits,
    UsageLedger,
    build_job_resource_view,
    resolve_resource_limits,
)


def test_shared_budget_queries_finish_before_ledger_can_close(tmp_path, monkeypatch):
    ledger = UsageLedger(tmp_path / 'usage.db')
    try:
        resolved = resolve_resource_limits(ResourceLimits(max_total_tokens=100), scheduler_capacity=4)
        governor = ResourceGovernor(
            ledger, limit_resolver=lambda *_: resolved, session_limit_resolver=lambda *_: resolved,
        )
        job = Job(id='child', parent_session_id='session', prompt='p', agent_id='main')
        assert governor.admit_job(job, persist=lambda _: None).accepted
        original_read = ledger.read
        @contextmanager
        def close_after_read():
            with original_read() as conn:
                yield conn
            ledger.close()
        monkeypatch.setattr(ledger, 'read', close_after_read)
        view = build_job_resource_view(job, ledger=ledger, resolved=resolved)
        assert view.budget['shared_remaining']['tokens'] == 100
    finally:
        ledger.close()
