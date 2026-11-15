"""Replace the threads one module starts without touching anyone else's."""
from __future__ import annotations

from collections.abc import Callable
import sys
import threading
from types import ModuleType

import pytest


def fake_threads_started_by(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType | str,
    factory: Callable[..., object],
) -> None:
    """Route ``threading.Thread(...)`` calls made in *module* to *factory*.

    ``threading.Thread`` is process-wide: a fake installed for every caller
    also swallows the threads that thread pools, asyncio executors and other
    tests' background work start meanwhile. Only calls whose immediate
    caller lives in *module* get the fake; everyone else gets a real thread.
    """
    name = module if isinstance(module, str) else module.__name__
    original_thread = threading.Thread

    def thread(*args, **kwargs):
        if sys._getframe(1).f_globals.get("__name__") == name:
            return factory(*args, **kwargs)
        return original_thread(*args, **kwargs)

    monkeypatch.setattr(threading, "Thread", thread)
