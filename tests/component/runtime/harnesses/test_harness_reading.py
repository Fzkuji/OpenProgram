"""A harness reading is answered from what was last read; an old one is read again behind it."""
import threading
import time

from openprogram.runtime.harnesses import models


def test_old_reading_is_answered_and_read_again(monkeypatch):
    calls = []
    again = threading.Event()

    def reader():
        calls.append(1)
        if len(calls) > 1:
            again.set()
        return {"id": "codex", "n": len(calls)}

    monkeypatch.setitem(models._READERS, "codex", reader)
    models.forget()
    assert models.read_harness("codex")["n"] == 1
    assert models.read_harness("codex")["n"] == 1 and len(calls) == 1

    with models._cache_lock:
        stamp, entry = models._cache["codex"]
        models._cache["codex"] = (stamp - models.CACHE_SECONDS - 1, entry)
    began = time.monotonic()
    assert models.read_harness("codex")["n"] == 1
    assert time.monotonic() - began < 0.5
    assert again.wait(2)
    deadline = time.monotonic() + 2
    while models.read_harness("codex")["n"] != 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert models.read_harness("codex")["n"] == 2
    models.forget()
