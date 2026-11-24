"""Switching a machine off lets the calls running on it finish before its providers are closed."""
from __future__ import annotations

import threading
import time

import pytest

from openprogram.programs.tools.computer_use.computer_use import registry
from openprogram.programs.tools.computer_use.computer_use.contract import Call

from tests.support.screen_targets import FakeProvider, install_machines, local, machine


class Closing(FakeProvider):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.closed_at: list[float] = []

    def close(self, call: Call) -> None:
        self.closed_at.append(time.monotonic())


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(registry, "_operated", {})
    monkeypatch.setattr(registry, "_running", {})
    install_machines(monkeypatch, [local(), machine("lab", screen="fake"), machine("devbox", screen="fake")])


def test_closing_waits_for_the_running_call():
    lab = Closing("screen", "lab")
    started, finish = threading.Event(), threading.Event()
    finished_at: list[float] = []

    def call() -> None:
        with registry.operating(lab, Call(conversation="c1", agent="c1")):
            started.set()
            finish.wait(5)
            finished_at.append(time.monotonic())

    worker = threading.Thread(target=call)
    worker.start()
    assert started.wait(5)
    closer = threading.Thread(target=registry.release, kwargs={"machine": "lab"})
    closer.start()
    time.sleep(0.2)
    assert lab.closed_at == []
    finish.set()
    worker.join(5)
    closer.join(5)
    assert len(lab.closed_at) == 1 and lab.closed_at[0] >= finished_at[0]


def test_a_call_on_another_machine_does_not_hold_it_up():
    lab, devbox = Closing("screen", "lab"), Closing("window", "devbox")
    with registry.operating(lab, Call(conversation="c1", agent="c1")):
        pass
    started, finish = threading.Event(), threading.Event()

    def call() -> None:
        with registry.operating(devbox, Call(conversation="c1", agent="c1")):
            started.set()
            finish.wait(5)

    worker = threading.Thread(target=call)
    worker.start()
    try:
        assert started.wait(5)
        began = time.monotonic()
        assert registry.release(machine="lab", wait=5) == 1
        assert time.monotonic() - began < 1
    finally:
        finish.set()
        worker.join(5)


def test_a_call_that_does_not_finish_in_time_does_not_keep_the_machine_open():
    lab = Closing("screen", "lab")
    started, finish = threading.Event(), threading.Event()

    def call() -> None:
        with registry.operating(lab, Call(conversation="c1", agent="c1")):
            started.set()
            finish.wait(5)

    worker = threading.Thread(target=call)
    worker.start()
    try:
        assert started.wait(5)
        assert registry.release(machine="lab", wait=0.2) == 1
        assert len(lab.closed_at) == 1
    finally:
        finish.set()
        worker.join(5)
