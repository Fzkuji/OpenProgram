"""The server benchmark measures what its baseline says it measures."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.transcript import server_benchmark
from tests.support.transcript.live_server import SESSION_ID, live_chat


@pytest.mark.slow
def test_view_memory_is_what_the_view_holds_not_what_its_open_replaced(tmp_path: Path) -> None:
    with live_chat(tmp_path) as chat:
        server_benchmark._populate(chat.sessions, SESSION_ID, 3_000)
        server_benchmark._fresh_store(chat)
        # The first open also builds the session-wide copies a second one reuses.
        first = server_benchmark._view_memory(SESSION_ID)["memory_per_view_kib"]
        # The measure counts every allocation of the process, so a thread an
        # earlier test of this worker left running moves one reading by a
        # third now and then; what a view holds is the size readings agree on.
        later = [server_benchmark._view_memory(SESSION_ID)["memory_per_view_kib"] for _ in range(3)]
    assert min(later) > 0
    assert any(first == pytest.approx(reading, rel=0.25) for reading in later), (first, later)
