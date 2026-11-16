"""The server benchmark measures what its baseline says it measures."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.transcript import server_benchmark
from tests.support.transcript.live_server import SESSION_ID, live_chat


def test_view_memory_is_what_the_view_holds_not_what_its_open_replaced(tmp_path: Path) -> None:
    with live_chat(tmp_path) as chat:
        server_benchmark._populate(chat.sessions, SESSION_ID, 3_000)
        server_benchmark._fresh_store(chat)
        # The first open also builds the session-wide copies a second one reuses.
        first = server_benchmark._view_memory(SESSION_ID)["memory_per_view_kib"]
        second = server_benchmark._view_memory(SESSION_ID)["memory_per_view_kib"]
    assert second > 0
    assert first == pytest.approx(second, rel=0.25)
