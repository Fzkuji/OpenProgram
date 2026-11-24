"""A cancelled call on an OSWorld machine that hangs lets the machine go after a bounded wait.

The request runs on a thread of its own; the machine is a fake that never answers.
"""
from __future__ import annotations

import threading
import time

import pytest

from openprogram.programs.tools.computer_use.computer_use import errors
from openprogram.programs.tools.computer_use.computer_use.contract import Call
from openprogram.programs.tools.computer_use.computer_use.providers import osworld

from tests.support.screen_targets import machine


class Cancelled(Exception):
    """What the conversation raises once it is stopped."""


def test_a_cancelled_call_stops_waiting_for_a_machine_that_hangs(monkeypatch):
    monkeypatch.setattr(osworld, "CANCEL_SECONDS", 0.3)
    hang, stopped = threading.Event(), threading.Event()

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, content, headers):
            stopped.set()
            hang.wait(5)
            raise ConnectionResetError("closed")

    provider = osworld.OSWorldScreen(machine("lab", "Lab VM", screen={"kind": "osworld", "url": "http://10.0.0.9:5000"}))
    monkeypatch.setattr(osworld.OSWorldScreen, "_client", lambda self: _Client())

    def check():
        if stopped.is_set():
            raise Cancelled()

    started = time.monotonic()
    try:
        with pytest.raises(Cancelled):
            provider.act({}, [("click", {"x": 1, "y": 1})], Call("conv", "agent", check_cancelled=check))
        assert time.monotonic() - started < 3
    finally:
        hang.set()


def test_a_call_that_is_not_cancelled_gets_the_machines_answer(monkeypatch):
    call = Call("conv", "agent", check_cancelled=lambda: None)
    assert osworld._bounded(lambda: "answer", call) == "answer"
    with pytest.raises(errors.BadArgument):
        osworld._bounded(lambda: (_ for _ in ()).throw(errors.BadArgument("x")), call)
