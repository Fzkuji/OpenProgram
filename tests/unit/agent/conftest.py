"""Shared fixtures for the agent unit tests."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _fresh_send_window():
    """Each test starts with an unspent hourly send allowance: the window
    is process memory, and every test sends from the same session ids."""
    from openprogram.programs.tools.agents.send_message.send_message import depth

    with depth._send_lock:
        depth._sends.clear()
    yield
    with depth._send_lock:
        depth._sends.clear()
