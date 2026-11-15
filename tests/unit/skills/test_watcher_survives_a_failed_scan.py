"""The skills watcher keeps watching after one reading of the folders fails."""
from openprogram.extensions.skills import watcher


def test_a_failed_reading_is_retried_at_the_next_tick(monkeypatch):
    readings = iter([{"a": 1.0}, RuntimeError("folder vanished"), {"a": 2.0}])

    def reading():
        value = next(readings)
        if isinstance(value, Exception):
            raise value
        return value

    ticks = iter([False, False, True])
    emitted = []
    monkeypatch.setattr(watcher, "_mtime_map", reading)
    monkeypatch.setattr(watcher._stop, "wait", lambda _seconds: next(ticks))
    monkeypatch.setattr(watcher, "_emit", lambda: emitted.append(True))
    watcher._run_polling()
    assert emitted == [True]
