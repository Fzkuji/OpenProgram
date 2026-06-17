"""Phase 3 metering — subprocess UsageContext propagation contract.

spawn doesn't copy contextvars, so process_runner must (a) snapshot the
parent context and (b) pass it positionally to _child_entry, which restores
it via apply_snapshot. mp.Process passes args POSITIONALLY, so a reorder
would silently feed the snapshot into the wrong slot — these tests pin the
parameter name/position and the restore behaviour."""
from __future__ import annotations

import inspect

import pytest

from openprogram.metering.context import (
    UsageContext,
    apply_snapshot,
    current_usage_context,
    snapshot,
    usage_scope,
)
from openprogram.metering import context as _ctx_mod


@pytest.fixture(autouse=True)
def reset_usage_context():
    """The usage contextvar is process-global; an earlier test that called
    apply_snapshot() without cleanup would leak its value here. Pin a clean
    default for each test and restore after."""
    token = _ctx_mod._current.set(UsageContext())
    try:
        yield
    finally:
        _ctx_mod._current.reset(token)


def test_child_entry_accepts_usage_snapshot_param():
    from openprogram.agent.process_runner import _child_entry
    params = list(inspect.signature(_child_entry).parameters)
    # The snapshot must be the LAST positional param (appended, so older
    # call sites that omit it still default to None).
    assert "usage_ctx_snapshot" in params
    assert params[-1] == "usage_ctx_snapshot"
    # and it must be optional (defaults to None) so non-metering callers work
    sig = inspect.signature(_child_entry)
    assert sig.parameters["usage_ctx_snapshot"].default is None


def test_snapshot_carries_call_kind_and_session_across_boundary():
    """Simulate the parent→child handoff: snapshot in one 'process', reset
    the contextvar to default (fresh interpreter), then apply_snapshot."""
    with usage_scope(call_kind="exec", session_id="sess-9", agent_id="ag-2"):
        snap = snapshot()

    # back at default (fresh spawn interpreter has the default context)
    assert current_usage_context().call_kind == "unknown"

    apply_snapshot(snap)
    c = current_usage_context()
    assert c.call_kind == "exec"
    assert c.session_id == "sess-9"
    assert c.agent_id == "ag-2"


def test_apply_snapshot_none_is_noop():
    # process_runner passes None when metering is unavailable — must not raise
    # and must leave the (default) context untouched.
    before = current_usage_context()
    apply_snapshot(None)
    assert current_usage_context() == before
