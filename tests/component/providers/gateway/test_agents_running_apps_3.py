"""Dim, Kun, Nowledge Mem and OpenHanako against the fakes of
``test_agents_running_apps_1``: Dim's pipe bridge given up on after its time,
and a reconnect on the same model (``lifecycle.rewire`` after a moved port or
a new key) changing only the address and key, a pick made in the app since
staying."""
from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest

from openprogram.models.gateway import address, stash
from openprogram.models.gateway.agents import dim, engine, hanako, kun, lifecycle

from .test_agents_running_apps_1 import (  # noqa: F401 - fixtures
    FakeKun, _chat, _w, dimd, hana, key, kun_dir, mem, state,
)

MOVED = "http://127.0.0.1:18200/gateway/openai/v1"


def _move(monkeypatch) -> None:
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18200")


# Dim's pipe bridge

def _bridge(code: str) -> dim.Bridge:
    return dim.Bridge(subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL))


def test_dim_bridge_that_never_answers_is_given_up(monkeypatch) -> None:
    """A pipe has no timeout of its own: the bridge is closed after WAIT
    (magpie dimConnect's time.AfterFunc), so a hung daemon can't hold Dim's lock."""
    bridge = _bridge("import time; time.sleep(600)")
    monkeypatch.setattr(dim, "open_daemon", lambda folder, start: bridge)
    monkeypatch.setattr(dim, "WAIT", 0.5)
    began = time.monotonic()
    with pytest.raises(dim.DimError, match="didn't answer"):
        dim.connect_daemon(dim.home(), False)
    assert time.monotonic() - began < 10
    assert bridge.proc.poll() is not None and bridge.expired
    assert not dim._mu.locked()


def test_dim_bridge_timer_goes_with_the_connection(monkeypatch) -> None:
    answer = ("import sys, json\n"
              "m = json.loads(sys.stdin.readline())\n"
              "print(json.dumps({'jsonrpc': '2.0', 'id': m['id'], 'result': {}}), flush=True)\n"
              "sys.stdin.read()\n")
    bridge = _bridge(answer)
    monkeypatch.setattr(dim, "open_daemon", lambda folder, start: bridge)
    c = dim.connect_daemon(dim.home(), False)
    assert c.timer is not None and c.timer.is_alive()
    c.close()
    c.timer.join(2)
    assert not c.timer.is_alive() and not bridge.expired


# a reconnect on the same model keeps the app's own pick

def test_dim_rewire_keeps_a_model_picked_in_dim(dimd, monkeypatch) -> None:
    dimd.listed = [{"id": "deepseek/pro"}]
    engine.connect("dim", {"model": "deepseek/pro"})
    assert dimd.sel["provider_id"] == "magpie"
    dimd.sel = {"provider_id": "deepseek", "model_id": "deepseek-chat", "version": 9}  # picked in Dim
    _move(monkeypatch)
    assert lifecycle.rewire() == ["Dim"]
    assert dimd.providers["magpie"]["base"] == MOVED
    assert dimd.sel == {"provider_id": "deepseek", "model_id": "deepseek-chat", "version": 9}
    engine.connect("dim", {"model": "deepseek/pro"})  # the same pick again: still Dim's
    assert dimd.sel["provider_id"] == "deepseek"


def test_kun_rewire_keeps_kuns_default(kun_dir, monkeypatch) -> None:
    f = FakeKun(kun_dir)
    f.add("deepseek", "https://api.deepseek.com", ["deepseek-chat"], True)
    engine.connect("kun", {"model": "deepseek/pro"})
    d = f.read()
    d.update(defaultProviderId="deepseek", defaultModel="deepseek-chat")  # picked in Kun
    d["revision"] += 1
    f.write(d)
    _move(monkeypatch)
    assert lifecycle.rewire() == ["Kun"]
    d = f.read()
    assert d["profiles"]["openprogram"]["baseUrl"] == MOVED
    assert (d["defaultProviderId"], d["defaultModel"]) == ("deepseek", "deepseek-chat")
    f.server.close()


def test_kun_rewire_keeps_a_model_picked_on_its_connection(kun_dir, monkeypatch) -> None:
    f = FakeKun(kun_dir)
    engine.connect("kun", {"model": "deepseek/pro"})
    d = f.read()
    d.update(defaultModel="deepseek/flash")
    d["profiles"]["openprogram"]["selectedModel"] = "deepseek/flash"
    f.write(d)
    _move(monkeypatch)
    lifecycle.rewire()
    d = f.read()
    assert d["profiles"]["openprogram"]["baseUrl"] == MOVED
    assert (d["defaultProviderId"], d["defaultModel"]) == ("openprogram", "deepseek/flash")
    f.server.close()


def test_kun_new_key_reaches_its_connection(kun_dir, monkeypatch) -> None:
    f = FakeKun(kun_dir)
    engine.connect("kun", {"model": "deepseek/pro"})
    stash.update("kun", key="an-older-key")
    d = f.read()
    d.update(defaultModel="deepseek/flash")
    f.write(d)
    assert lifecycle.rewire() == ["Kun"]
    assert f.made[-1]["credential"] == key("kun") and f.made[-1]["selectedModel"] == "deepseek/flash"
    assert f.read()["defaultModel"] == "deepseek/flash"
    f.server.close()


def test_kun_disconnect_leaves_a_provider_picked_in_kun(kun_dir) -> None:
    f = FakeKun(kun_dir)
    f.add("deepseek", "https://api.deepseek.com", ["deepseek-chat"], True)
    f.add("mine", "https://example.com/v1", ["m1"], False)
    engine.connect("kun", {"model": "deepseek/pro"})
    d = f.read()
    d.update(defaultProviderId="mine", defaultModel="m1")  # picked in Kun since
    d["revision"] += 1
    f.write(d)
    engine.disconnect("kun")
    d = f.read()
    assert "openprogram" not in d["profiles"] and (d["defaultProviderId"], d["defaultModel"]) == ("mine", "m1")
    f.server.close()


def test_nowledge_mem_rewire_keeps_mems_picks(mem, monkeypatch) -> None:
    f = mem(providers={"openai": {"provider_type": "openai", "model": "gpt-5"}}, active="openai", enabled=True)
    engine.connect("nowledge_mem", {"model": "deepseek/pro"})
    f.state["providers"]["magpie"]["model"] = "deepseek/flash"  # picked in Mem
    f.state["active_provider"] = "openai"
    _move(monkeypatch)
    assert lifecycle.rewire() == ["Nowledge Mem"]
    p = f.state["providers"]["magpie"]
    assert p["api_base"] == MOVED and p["model"] == "deepseek/flash"
    assert f.state["active_provider"] == "openai" and f.state["enabled"]


def test_hanako_rewire_keeps_a_chat_model_picked_in_it(hana, monkeypatch) -> None:
    _w(hana / "provider-catalog.json", json.dumps({"catalogVersion": 2, "providers": {}}), 0o600)
    _w(hana / "user" / "preferences.json", '{"primaryAgent":"hana"}')
    cfg = hana / "agents" / "hana" / "config.yaml"
    _w(cfg, "models:\n  chat:\n    id: gpt-5\n    provider: openai\n")
    engine.connect("hanako", {"model": "deepseek/pro"})
    _w(cfg, "models:\n  chat:\n    id: deepseek/flash\n    provider: openprogram\n")  # picked in OpenHanako
    _move(monkeypatch)
    assert lifecycle.rewire() == ["OpenHanako"]
    assert hanako.current(hana)["base_url"] == MOVED
    assert _chat(cfg) == {"id": "deepseek/flash", "provider": "openprogram"}
    engine.connect("hanako", {"model": "deepseek/flash"})  # another pick in OpenProgram is made
    engine.connect("hanako", {"model": "deepseek/pro"})
    assert _chat(cfg) == {"id": "deepseek/pro", "provider": "openprogram"}
