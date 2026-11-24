"""The skills watcher wakes on file events and keeps watching after one reading of the folders fails."""
from openprogram.extensions.skills import watcher


def test_a_failed_reading_is_retried_soon(monkeypatch):
    readings = iter([({"a": 1.0}, {}, {}, []), RuntimeError("folder vanished"), ({"a": 2.0}, {}, {}, [])])

    def reading():
        value = next(readings)
        if isinstance(value, Exception):
            raise value
        return value

    waits = []
    ticks = iter([False, False, True])
    emitted = []
    monkeypatch.setattr(watcher, "_walk", reading)
    monkeypatch.setattr(watcher, "_same", lambda seen: False)
    monkeypatch.setattr(watcher, "_watch_roots", lambda watched: watched)
    monkeypatch.setattr(watcher, "_wait", lambda due: waits.append(due) or next(ticks))
    monkeypatch.setattr(watcher, "_emit", lambda: emitted.append(True))
    watcher._run()
    assert emitted == [True]
    assert waits[0] is None, "nothing to look at until something changes"
    assert waits[1] is not None, "the failed reading is taken again without waiting for a change"


def test_every_root_is_watched_and_a_plugin_root_wakes_the_watcher(tmp_path, monkeypatch):
    from openprogram.host._platform import changes
    roots = [("user", tmp_path / "user")]
    registered = []
    monkeypatch.setattr(watcher, "_source_dirs", lambda: roots)
    monkeypatch.setattr(changes, "watch", lambda path, topic, **kw: registered.append((path, topic, kw)))
    monkeypatch.setattr(changes, "unwatch", lambda path, topic: registered.append(("-", path, topic)))
    watched = watcher._watch_roots(set())
    assert registered == [(str(tmp_path / "user"), watcher.TOPIC, {"recursive": True})]
    roots.append(("plugin:x", tmp_path / "plugin"))
    watcher._watch_roots(watched)
    assert registered[-1] == (str(tmp_path / "plugin"), watcher.TOPIC, {"recursive": True})
    with changes.listen(watcher.TOPIC) as listener:
        from openprogram.extensions.skills import loader
        monkeypatch.setattr(loader, "_PLUGIN_SKILL_DIRS", {})
        loader.register_plugin_skills("x", tmp_path / "plugin")
        assert listener.wait(0) == {watcher.TOPIC}


def test_folders_are_walked_again_only_after_a_change(tmp_path, monkeypatch):
    skill = tmp_path / "one" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text("---\nname: one\n---\n", encoding="utf-8")
    later = tmp_path / "later"
    monkeypatch.setattr(watcher, "_source_dirs", lambda: [("user", tmp_path), ("project", later)])
    seen = watcher._scan()
    assert list(seen[0]) == [str(skill)]
    assert watcher._same(seen)
    (tmp_path / "two").mkdir()
    assert not watcher._same(seen)
    seen = watcher._scan()
    later.mkdir()
    assert not watcher._same(seen)
