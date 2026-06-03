"""``setup.update_config`` — atomic read-modify-write of config.json.

The race it guards: concurrent writers doing ``_read_config()`` … ``_write_config()``
separately clobber each other (last write wins). ``update_config`` serialises
the whole critical section, so concurrent mutators all land.
"""
from __future__ import annotations

import threading
import time

from openprogram import setup


def test_update_config_concurrent_mutators_all_land(tmp_path, monkeypatch):
    cfgp = tmp_path / "config.json"
    monkeypatch.setattr(setup, "get_config_path", lambda: cfgp)
    setup._write_config({"a": {}})

    def make(key):
        def mutate(cfg):
            sub = dict(cfg.get("a", {}))   # read
            time.sleep(0.02)               # widen the window a bare r/m/w would lose
            sub[key] = key
            cfg["a"] = sub                 # write
        return mutate

    threads = [threading.Thread(target=setup.update_config, args=(make(k),))
               for k in ("x", "y", "z")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Without serialisation each thread reads {} / a stale dict and the last
    # write wins, leaving a single key. The lock means all three land.
    assert setup._read_config()["a"] == {"x": "x", "y": "y", "z": "z"}


def test_update_config_returns_and_mutates_in_place(tmp_path, monkeypatch):
    cfgp = tmp_path / "config.json"
    monkeypatch.setattr(setup, "get_config_path", lambda: cfgp)
    setup._write_config({})
    out = setup.update_config(lambda cfg: cfg.setdefault("ui", {}).update({"port": 18109}))
    assert out["ui"]["port"] == 18109
    assert setup._read_config()["ui"]["port"] == 18109


def test_update_config_keeps_0600(tmp_path, monkeypatch):
    cfgp = tmp_path / "config.json"
    monkeypatch.setattr(setup, "get_config_path", lambda: cfgp)
    setup.update_config(lambda cfg: cfg.update({"k": "v"}))
    assert (cfgp.stat().st_mode & 0o777) == 0o600
