"""The worker log stays bounded: a large one is copied into numbered
generations and truncated in place, which the open O_APPEND writers follow."""
from __future__ import annotations

import os

from openprogram.operations.worker.lifecycle import rotate_worker_log


def test_a_small_log_is_left_alone(tmp_path):
    log = tmp_path / "worker.log"
    log.write_text("short\n")
    assert rotate_worker_log(log, limit_bytes=1024) is False
    assert log.read_text() == "short\n"
    assert not (tmp_path / "worker.log.1").exists()


def test_a_missing_log_is_nothing_to_rotate(tmp_path):
    assert rotate_worker_log(tmp_path / "worker.log") is False


def test_a_large_log_shifts_into_generations_and_is_truncated_for_its_open_writer(tmp_path):
    log = tmp_path / "worker.log"
    (tmp_path / "worker.log.1").write_text("gen1\n")
    (tmp_path / "worker.log.2").write_text("gen2\n")
    (tmp_path / "worker.log.3").write_text("gen3\n")
    log.write_text("x" * 2048)
    # launchd keeps the file open in append mode across the rotation.
    writer = os.open(log, os.O_WRONLY | os.O_APPEND)
    try:
        assert rotate_worker_log(log, limit_bytes=1024, keep=3) is True
        os.write(writer, b"after\n")
    finally:
        os.close(writer)

    assert log.read_text() == "after\n", "the writer's next line lands at the top of the emptied file"
    assert (tmp_path / "worker.log.1").read_text() == "x" * 2048
    assert (tmp_path / "worker.log.2").read_text() == "gen1\n"
    assert (tmp_path / "worker.log.3").read_text() == "gen2\n", "the oldest generation past keep is gone"
    assert not (tmp_path / "worker.log.4").exists()
    assert rotate_worker_log(log, limit_bytes=1024, keep=3) is False, "a fresh log rotates no further"
