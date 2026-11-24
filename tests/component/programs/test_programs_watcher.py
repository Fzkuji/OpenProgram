"""The Programs watcher takes a fingerprint when a file event says so, not every few seconds."""
from __future__ import annotations

import time

from openprogram.host._platform import changes
from openprogram.programs._host import watcher


def test_a_file_written_in_a_package_wakes_the_watcher(tmp_path, monkeypatch):
    programs = tmp_path / "programs"
    (programs / "pkg").mkdir(parents=True)
    monkeypatch.setattr(watcher, "_watch_targets", lambda base: {(base, True, None)})
    changes.start_watching()
    pass_ = watcher._Watcher(str(programs))
    try:
        with changes.listen(watcher.TOPIC) as listener:
            monkeypatch.setattr(watcher, "_catalog_fingerprint", lambda base: ("same",))
            pass_.begin()
            time.sleep(1.5)  # the watcher has started on the folder
            (programs / "pkg" / "tool.py").write_text("x = 1\n", encoding="utf-8")
            assert listener.wait(5.0) == {watcher.TOPIC}
    finally:
        changes.stop_watching()
        changes.unwatch(programs, watcher.TOPIC)


def test_an_event_that_changes_no_fingerprint_does_not_rescan(monkeypatch):
    from openprogram.programs._host import registry

    rescans = []
    monkeypatch.setattr(registry, "rescan", lambda: rescans.append(1) or {"added": []})
    monkeypatch.setattr(watcher, "_emit_changed", lambda added: None)
    monkeypatch.setattr(watcher, "_watch_targets", lambda base: set())
    fingerprints = iter([("a",), ("a",), ("b",)])
    monkeypatch.setattr(watcher, "_catalog_fingerprint", lambda base: next(fingerprints))
    pass_ = watcher._Watcher("programs")
    pass_.begin()
    pass_.step()
    assert rescans == []
    pass_.step()
    assert rescans == [1]
    assert pass_.retry_at is None


def test_the_watched_directories_follow_the_sources(monkeypatch):
    calls = []
    monkeypatch.setattr(changes, "watch", lambda path, topic, **kw: calls.append(("+", path, kw["recursive"])))
    monkeypatch.setattr(changes, "unwatch", lambda path, topic: calls.append(("-", path)))
    targets = [{("/p", True, None), ("/app", True, None)}]
    monkeypatch.setattr(watcher, "_watch_targets", lambda base: targets[0])
    pass_ = watcher._Watcher("/p")
    pass_.follow_sources()
    assert sorted(calls) == [("+", "/app", True), ("+", "/p", True)]
    calls.clear()
    targets[0] = {("/p", True, None), ("/other", True, None)}
    pass_.follow_sources()
    assert calls == [("-", "/app"), ("+", "/other", True)]
