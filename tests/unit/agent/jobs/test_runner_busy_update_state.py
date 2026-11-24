"""A self-update state another process holds for a moment is not an error."""
from __future__ import annotations

import logging

import pytest

from openprogram.operations.self_update.delivery import delivery, restart
from openprogram.operations.self_update.types import ConcurrentUpdateError
from openprogram.runtime.jobs.runner import recovery


def _runner():
    return recovery.RecoveryOperations()


@pytest.mark.parametrize("failure, level", [
    (ConcurrentUpdateError("self-update state is busy"), logging.INFO),
    (RuntimeError("broken"), logging.ERROR),
])
def test_only_a_real_failure_is_logged_as_an_error(monkeypatch, caplog, failure, level):
    def busy(_runner):
        raise failure

    monkeypatch.setattr(restart, "reconcile", busy)
    with caplog.at_level(logging.INFO):
        _runner()._reconcile_self_updates()
    assert [record.levelno for record in caplog.records] == [level]
    assert (caplog.records[0].exc_info is not None) == (level == logging.ERROR)


@pytest.mark.parametrize("failure, level", [
    (ConcurrentUpdateError("self-update state is busy"), logging.INFO),
    (RuntimeError("broken"), logging.WARNING),
])
def test_a_busy_state_while_delivering_results_is_not_a_warning(monkeypatch, caplog, failure, level):
    def busy():
        raise failure

    monkeypatch.setattr(delivery, "deliver_pending", busy)
    with caplog.at_level(logging.INFO):
        _runner()._deliver_self_update_results()
    assert [record.levelno for record in caplog.records] == [level]
