"""Agents changed through a running app's own API: Dim, Kun, Nowledge Mem,
OpenHanako. After yetone/magpie's ``dim_test.go``, ``dim_daemon_test.go``,
``kun_test.go``, ``nowledgemem_test.go`` and ``hanako_test.go`` (MIT License,
Copyright (c) yetone): each app is stood up as a fake of the API magpie
talks to (a JSON-RPC daemon on a Unix socket, HTTP servers on loopback)."""
from __future__ import annotations

import json
import os
import shutil
import socket
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

from openprogram.models.gateway import stash
from openprogram.models.gateway.agents import dim, engine, hanako, kun, nowledge_mem

GW = "http://127.0.0.1:18100/gateway/openai/v1"
MODELS = [
    {"id": "deepseek/pro", "name": "Pro", "provider": "deepseek", "context_window": 0, "max_tokens": 0},
    {"id": "deepseek/flash", "name": "Flash", "provider": "deepseek", "context_window": 64000, "max_tokens": 8000},
]


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENPROGRAM_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path))
    from openprogram.models.gateway import address, conversion
    from openprogram.models.gateway import agents
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18100")
    models = [dict(m) for m in MODELS]
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: models)
    monkeypatch.setattr(agents, "all_agents", lambda: [dim.agent(), kun.agent(), nowledge_mem.agent(), hanako.agent()])
    return tmp_path


def key(agent_id: str) -> str:
    from openprogram.models.gateway.keys import token_for
    return token_for(agent_id)


class Server:
    """A loopback HTTP server answering with ``handle(method, path, query, body, headers) -> (code, json)``."""

    def __init__(self, handle: Callable[..., tuple[int, Any]]):
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _go(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                body = json.loads(raw) if raw else None
                u = urlparse(self.path)
                outer.calls.append((self.command, u.path, body, self.headers.get("Authorization")))
                code, out = handle(self.command, u.path, parse_qs(u.query), body, self.headers)
                data = json.dumps(out).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _go

        self.calls: list[tuple] = []
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


# Dim

class FakeDim:
    """Dim alpha's daemon as OpenProgram talks to it: provider records with
    versions every write is checked against, a key stored and never read
    back, provider.refresh listing what the gateway would, one global pick."""

    def __init__(self):
        self.providers = {
            "magpie": {"version": 1, "enabled": False, "base": dim.DEFAULT, "key": "", "models": []},
            "deepseek": {"version": 7, "enabled": True, "base": "https://api.deepseek.com/v1", "key": "sk-users",
                         "models": [{"id": "deepseek-chat"}]},
        }
        self.sel: dict | None = {"provider_id": "deepseek", "model_id": "deepseek-chat", "version": 3}
        self.listed: list[dict] = []
        self.calls: list[str] = []
        self.lock = threading.Lock()

    def record(self, pid):
        p = self.providers[pid]
        return {"providerId": pid, "version": p["version"], "enabled": p["enabled"],
                "connection": {"baseUrl": p["base"], "credentialConfigured": p["key"] != ""}}

    def answer(self, method, params):
        with self.lock:
            self.calls.append(method)
            pid = params.get("providerId")
            pr = self.providers.get(pid)
            ev = params.get("expectedVersion")
            if method == "hello":
                return {"protocolVersion": 4}, ""
            if method == "provider.management":
                return ({"providers": [self.record(pid)]}, "") if pr else (None, "provider_not_found")
            if method == "provider.update":
                if pr is None:
                    return None, "provider_not_found"
                if ev != pr["version"]:
                    return None, "provider_conflict"
                patch = params.get("patch") or {}
                if "baseUrl" in patch:
                    pr["base"] = patch["baseUrl"]
                if "enabled" in patch:
                    pr["enabled"] = patch["enabled"]
                cred = patch.get("credential")
                if cred:
                    if cred["action"] == "set":
                        pr["key"] = cred["value"]
                    elif pr["enabled"]:
                        return None, "provider_invalid_config"
                    else:
                        pr["key"] = ""
                pr["version"] += 1
                return self.record(pid), ""
            if method == "provider.refresh":
                if pr is None or not pr["key"] or not pr["enabled"]:
                    return None, "provider_invalid_config"
                pr["models"] = [dict(m) for m in self.listed]
                pr["version"] += 1
                return {"models": pr["models"], "version": pr["version"]}, ""
            if method == "provider.models.edit":
                if ev != pr["version"]:
                    return None, "provider_conflict"
                for m in pr["models"]:
                    if m["id"] == params["modelId"]:
                        m["missingLimits"] = [k for k in m.get("missingLimits") or [] if not params["edit"].get(k)]
                pr["version"] += 1
                return {"models": pr["models"], "version": pr["version"]}, ""
            if method == "provider.selection":
                return {"selection": self.sel}, ""
            if method == "provider.select":
                have = self.sel["version"] if self.sel else None
                if have != ev:
                    return None, "provider_conflict"
                if pr is None:
                    return None, "provider_not_found"
                m = next((m for m in pr["models"] if m["id"] == params["modelId"]), None)
                if m is None:
                    return None, "provider_not_found"
                if m.get("missingLimits"):
                    return None, "provider_invalid_config"
                self.sel = {"provider_id": pid, "model_id": params["modelId"], "version": (have or 0) + 1}
                return self.sel, ""
            return None, "method_not_found"

    def serve(self, folder: Path):
        path = dim.socket_path(folder)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(path))
        self.sock.listen(8)

        def conn(c):
            with c, c.makefile("rb") as r:
                for line in r:
                    m = json.loads(line)
                    res, code = self.answer(m["method"], m.get("params") or {})
                    out = {"jsonrpc": "2.0", "id": m["id"]}
                    if code:
                        out["error"] = {"code": -32000, "message": code, "data": {"appError": code}}
                    else:
                        out["result"] = res
                    c.sendall((json.dumps(out) + "\n").encode())

        def loop():
            while True:
                try:
                    c, _ = self.sock.accept()
                except OSError:
                    return
                threading.Thread(target=conn, args=(c,), daemon=True).start()
        threading.Thread(target=loop, daemon=True).start()

    def close(self):
        self.sock.close()


@pytest.fixture
def dimd(state, monkeypatch):
    # a short folder: a Unix socket's path has a small limit
    folder = Path(tempfile.mkdtemp(prefix="dim"))
    monkeypatch.setenv("DIMCODE_HOME", str(folder))
    f = FakeDim()
    f.serve(folder)
    yield f
    f.close()
    shutil.rmtree(folder, ignore_errors=True)


def test_dim_fills_its_provider_and_puts_it_back(dimd) -> None:
    dimd.listed = [{"id": "deepseek/pro", "missingLimits": ["contextWindow", "maxOutputTokens"]}, {"id": "deepseek/flash"}]
    engine.connect("dim", {"model": "deepseek/pro"})
    m = dimd.providers["magpie"]
    assert m["key"] == key("dim") and m["enabled"] and m["base"] == GW
    assert dimd.sel["provider_id"] == "magpie" and dimd.sel["model_id"] == "deepseek/pro"
    assert "provider.models.edit" in dimd.calls
    d = dimd.providers["deepseek"]
    assert (d["version"], d["key"], d["enabled"]) == (7, "sk-users", True)
    # another model: picked, the key not set again
    updates = dimd.calls.count("provider.update")
    engine.connect("dim", {"model": "deepseek/flash"})
    assert dimd.sel["model_id"] == "deepseek/flash" and dimd.calls.count("provider.update") == updates

    engine.disconnect("dim")
    assert dimd.sel["provider_id"] == "deepseek" and dimd.sel["model_id"] == "deepseek-chat"
    m = dimd.providers["magpie"]
    assert m["key"] == "" and not m["enabled"] and m["base"] == dim.DEFAULT
    assert stash.entry("dim") is None


def test_dim_follows_the_port(dimd, monkeypatch) -> None:
    from openprogram.models.gateway import address
    from openprogram.models.gateway.agents import lifecycle
    dimd.sel = None
    dimd.listed = [{"id": "deepseek/pro"}]
    engine.connect("dim", {"model": "deepseek/pro"})
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18200")
    assert lifecycle.rewire() == ["Dim"]
    assert dimd.providers["magpie"]["base"] == "http://127.0.0.1:18200/gateway/openai/v1"
    engine.disconnect("dim")
    m = dimd.providers["magpie"]
    assert m["base"] == dim.DEFAULT and m["key"] == "" and not m["enabled"]


def test_dim_leaves_the_users_own_key(dimd) -> None:
    mp = dimd.providers["magpie"]
    mp.update(key="users-own-key", enabled=True)
    dimd.listed = [{"id": "deepseek/pro"}]
    engine.connect("dim", {"model": "deepseek/pro"})
    assert mp["key"] == "users-own-key" and mp["base"] == dim.DEFAULT and "provider.update" not in dimd.calls
    assert dimd.sel["provider_id"] == "magpie"
    engine.disconnect("dim")
    assert mp["key"] == "users-own-key" and mp["enabled"] and "provider.update" not in dimd.calls
    assert dimd.sel["provider_id"] == "deepseek"


@pytest.fixture
def zshrc(state, monkeypatch):
    """The user's one shell, zsh, its profile in the test's home."""
    from openprogram.models.gateway.agents import shellfn
    monkeypatch.setenv("HOME", str(state))
    path = state / ".zshrc"
    path.write_text("export A=1\n")
    monkeypatch.setattr(shellfn, "shell_profiles", lambda: [shellfn.Profile("posix", path, True)])
    return path


def test_dim_users_own_address_starts_dima_on_its_variables(dimd, zshrc) -> None:
    """With an address of the user's own and no key, Dim lists nothing: the
    pick can't be made, the user's address stays, and dima is started on the
    gateway's variables by a dima function instead (magpie
    TestDimMagpieProviderUsersOwn)."""
    mp = dimd.providers["magpie"]
    mp.update(base="http://192.168.1.9:3425/v1", enabled=True)
    before = dict(mp)
    dimd.listed = [{"id": "deepseek/pro"}]
    engine.connect("dim", {"model": "deepseek/pro"})
    assert mp == before and dimd.sel["provider_id"] == "deepseek"
    assert "dima() {" in zshrc.read_text() and "# >>> openprogram dima >>>" in zshrc.read_text()
    assert " dim run \"$@\"" in zshrc.read_text()
    env = dict(line.split("=", 1) for line in dim.env_lines())
    assert env == {"model": "deepseek/pro", "DIM_BASE_URL": GW, "DIM_API_FORMAT": "openai-compatible", "DIM_API_KEY": key("dim"),
                   "DIM_MODEL": "deepseek/pro", "DIM_MAX_CONTEXT_TOKENS": "128000", "DIM_MAX_OUTPUT_TOKENS": "32000"}
    assert "dima function" in engine.status("dim")["notice"]
    engine.disconnect("dim")
    assert mp == before and dimd.sel["provider_id"] == "deepseek"
    assert zshrc.read_text() == "export A=1\n" and dim.env_lines() == []


def test_dim_sync_moves_to_its_provider(state, zshrc, monkeypatch) -> None:
    """A Dim connected while its daemon was away is on the dima function; a
    sync with no daemon keeps it, and one while the daemon runs fills the
    provider in and takes the function out (magpie TestDimSyncMovesToProvider)."""
    folder = Path(tempfile.mkdtemp(prefix="dim"))
    monkeypatch.setenv("DIMCODE_HOME", str(folder))

    up = []
    real = dim.open_daemon

    def away(folder, start):
        if not up:
            raise dim.DimAway("gone")
        return real(folder, start)
    monkeypatch.setattr(dim, "open_daemon", away)
    engine.connect("dim", {"model": "deepseek/pro"})
    assert "dima() {" in zshrc.read_text()
    engine.sync("dim")
    assert "dima() {" in zshrc.read_text()
    up.append(True)
    f = FakeDim()
    f.serve(folder)
    try:
        f.listed = [{"id": "deepseek/pro"}]
        engine.sync("dim")
        assert f.sel["provider_id"] == "magpie" and f.providers["magpie"]["key"]
        assert zshrc.read_text() == "export A=1\n" and dim.env_lines() == []
        assert engine.status("dim")["notice"] == ""
    finally:
        f.close()
        shutil.rmtree(folder, ignore_errors=True)


def test_dim_pipe_name() -> None:
    import hashlib
    name = dim.pipe_name(Path("/x/rust"))
    assert name.startswith("\\\\.\\pipe\\dim-rust-") and len(name.rsplit("-", 1)[1]) == 32
    import os
    canonical = "\\\\?\\" + os.path.realpath("/x/rust")
    assert name.endswith(hashlib.sha256(("dimcode:" + canonical).encode()).hexdigest()[:32])


def test_dim_disconnect_with_dim_gone(dimd, monkeypatch, tmp_path) -> None:
    dimd.listed = [{"id": "deepseek/pro"}]
    engine.connect("dim", {"model": "deepseek/pro"})

    def away(folder, start):
        raise dim.DimAway("gone")
    monkeypatch.setattr(dim, "open_daemon", away)
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    monkeypatch.setattr(dim, "find_tool", lambda name: "")
    engine.disconnect("dim")
    assert stash.entry("dim") is None


def test_dima_runs_on_the_variables(dimd, zshrc, monkeypatch, tmp_path) -> None:
    """``openprogram dim run`` (the function dima) starts the real dima with
    the gateway's variables while Dim is on the function, and as it is
    otherwise (magpie launchSpec, cli/agy_cli.go)."""
    import os
    from openprogram.interfaces.cli.application import _agent_passthrough
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "dima").write_text("#!/bin/sh\n")
    (bin_dir / "dima").chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    started = []
    monkeypatch.setattr(os, "execve", lambda path, argv, env: started.append((argv, env)))
    monkeypatch.setenv("DIM_MODEL", "mine")
    dimd.providers["magpie"].update(base="http://192.168.1.9:3425/v1", enabled=True)
    engine.connect("dim", {"model": "deepseek/pro"})
    assert _agent_passthrough(["dim", "run", "chat", "hi"]) == 0
    argv, env = started[-1]
    assert argv == ["dima", "chat", "hi"] and env["DIM_MODEL"] == "deepseek/pro" and env["DIM_API_KEY"] == key("dim")
    from openprogram.models.gateway.agents.shellfn import cli_program
    ps = dim.function("powershell", cli_program())
    assert " dim env" in ps and "'DIM_API_KEY'" in ps and "--model" not in ps
    assert dim.launch_command(False).endswith(" dima") and "DIM_BASE_URL=" in dim.launch_command(False)
    engine.disconnect("dim")
    dim.run(["chat"])
    assert started[-1][0] == ["dima", "chat"] and started[-1][1]["DIM_MODEL"] == "mine"


def test_dim_lists_again_when_names_change(dimd) -> None:
    from openprogram.models.gateway import conversion
    dimd.listed = [{"id": "deepseek/pro"}]
    engine.connect("dim", {"model": "deepseek/pro"})
    n = dimd.calls.count("provider.refresh")
    assert not engine.sync("dim") and dimd.calls.count("provider.refresh") == n
    conversion.enabled_model_refs()[0]["name"] = "Pro · DeepSeek Official"
    assert engine.sync("dim") and dimd.calls.count("provider.refresh") == n + 1
    assert dimd.sel == {"provider_id": "magpie", "model_id": "deepseek/pro", "version": 4}


def test_dim_home_and_limits(state, monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("DIMCODE_HOME", raising=False)
    assert dim.home() == Path(str(tmp_path)) / ".dimcode-alpha"
    monkeypatch.setenv("DIMCODE_HOME", str(tmp_path / "elsewhere"))
    assert dim.home() == tmp_path / "elsewhere"
    at = engine.wiring(dim.agent(), {"model": "nobody/none"})
    ctx, out = dim.limits(at, "nobody/none")
    assert 0 < out <= ctx
    assert dim.limits(at, "deepseek/flash") == (64000, 8000)


# Kun

class FakeKun:
    """A Kun runtime's model-connections admin API, keeping its connections in
    model-connections.v2.json: a change names the revision it was made against
    (409 on another), an id is its name's (``-2`` when taken), and the
    connection the default names goes only in a provider-config transaction."""

    def __init__(self, folder: Path, token: str = "rt-token"):
        self.folder, self.token = folder, token
        self.made: list[dict] = []
        self.previews: dict[str, list] = {}
        self.write(self.read())
        self.server = Server(self.handle)
        (folder / "runtime.json").write_text(json.dumps({"baseUrl": self.server.url, "runtimeToken": token}))

    def read(self) -> dict:
        try:
            d = json.loads((self.folder / kun.REGISTRY).read_text())
        except OSError:
            d = {}
        d.setdefault("revision", 0)
        d.setdefault("profiles", {})
        return d

    def write(self, d: dict) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / kun.REGISTRY).write_text(json.dumps(d))

    def add(self, pid, base, models, default):
        d = self.read()
        d["profiles"][pid] = {"id": pid, "name": pid, "kind": "http", "baseUrl": base, "models": models,
                              "selectedModel": models[0]}
        if default:
            d.update(defaultProviderId=pid, defaultAccountId="default", defaultModel=models[0])
        d["revision"] += 1
        self.write(d)

    def snapshot(self, d):
        return {"revision": d["revision"], "providers": [d["profiles"][k] for k in sorted(d["profiles"])],
                "defaultProviderId": d.get("defaultProviderId", ""), "defaultModel": d.get("defaultModel", "")}

    def handle(self, method, path, query, body, headers):
        if headers.get("Authorization") != f"Bearer {self.token}":
            return 401, {"code": "unauthorized", "message": "missing or invalid runtime token"}
        d = self.read()
        body = body or {}
        want = query.get("expected_revision", [str(body.get("expectedRevision"))])[0]
        stale = want != str(d["revision"])
        if method == "GET" and path == "/v1/model-connections":
            pass
        elif path == "/v1/model-connections/connect":
            if stale:
                return 409, {"code": "revision_conflict", "message": "stale"}
            pid = body["id"]
            base, n = pid, 2
            while pid in d["profiles"]:
                pid, n = f"{base}-{n}", n + 1
            self.made.append(body)
            d["profiles"][pid] = {"id": pid, "name": body["name"], "kind": body["kind"], "baseUrl": body["baseUrl"],
                                  "models": body["models"], "selectedModel": body["selectedModel"]}
            if body.get("select"):
                d.update(defaultProviderId=pid, defaultModel=body["selectedModel"])
            d["revision"] += 2
        elif path == "/v1/model-connections/select":
            if stale:
                return 409, {"code": "revision_conflict", "message": "stale"}
            p = d["profiles"].get(body["providerId"])
            if p is None or (p["models"] and body["model"] not in p["models"]):
                return 400, {"code": "invalid_request", "message": "model is not available for this provider"}
            p["selectedModel"] = body["model"]
            d.update(defaultProviderId=body["providerId"], defaultModel=body["model"])
            d["revision"] += 1
        elif path == "/v1/provider-config/transactions/preview":
            if stale:
                return 409, {"code": "revision_conflict", "message": "stale"}
            pid = f"preview-{len(self.previews) + 1}"
            self.previews[pid] = body["operations"]
            return 200, {"previewId": pid, "expectedRevision": d["revision"]}
        elif path == "/v1/provider-config/transactions/commit":
            if stale:
                return 409, {"code": "revision_conflict", "message": "stale"}
            for op in self.previews.pop(body["previewId"]):
                if op["kind"] == "set-default-selection":
                    d.update(defaultProviderId="", defaultModel="")
                else:
                    d["profiles"].pop(op["connectionId"])
            d["revision"] += 1
            self.write(d)
            return 200, {"applied": True}
        elif path.startswith("/v1/model-connections/"):
            pid = path.rsplit("/", 1)[1]
            if pid not in d["profiles"]:
                return 404, {"code": "not_found", "message": "provider not found"}
            if stale:
                return 409, {"code": "revision_conflict", "message": "stale"}
            if method == "PATCH":
                d["profiles"][pid]["models"] = body["models"]
            elif d.get("defaultProviderId") == pid:
                return 400, {"code": "invalid_request", "message": "Deleted connection still has references"}
            else:
                del d["profiles"][pid]
            d["revision"] += 1
        else:
            return 404, {"code": "not_found", "message": ""}
        self.write(d)
        return 200, self.snapshot(d)


@pytest.fixture
def kun_dir(state, monkeypatch):
    folder = state / ".kun" / "data"
    folder.mkdir(parents=True)
    monkeypatch.setenv("KUN_DATA_DIR", str(folder))
    return folder


def test_kun_adds_openprogram_as_a_provider(kun_dir) -> None:
    f = FakeKun(kun_dir)
    f.add("deepseek", "https://api.deepseek.com", ["deepseek-chat", "deepseek-reasoner"], True)
    assert kun.agent().installed()
    for _ in range(2):  # twice: one connection, Kun's own default kept once
        engine.connect("kun", {"model": "deepseek/pro"})
    assert len(f.made) == 1
    c = f.made[0]
    assert {k: c[k] for k in ("id", "kind", "authType", "baseUrl", "endpointFormat", "credential", "selectedModel",
                               "probe", "select")} == {
        "id": "openprogram", "kind": "http", "authType": "api-key", "baseUrl": GW,
        "endpointFormat": "chat_completions", "credential": key("kun"), "selectedModel": "deepseek/pro",
        "probe": False, "select": True}
    assert c["models"] == ["deepseek/pro", "deepseek/flash"]
    assert engine.status("kun")["config"]["drift"] == []
    engine.connect("kun", {"model": "deepseek/flash"})
    assert len(f.made) == 1 and f.read()["defaultModel"] == "deepseek/flash"
    engine.disconnect("kun")
    d = f.read()
    assert "openprogram" not in d["profiles"] and d["defaultProviderId"] == "deepseek"
    assert d["defaultModel"] == "deepseek-chat" and stash.entry("kun") is None
    f.server.close()


def test_kun_from_nothing_and_its_own_connection(kun_dir) -> None:
    f = FakeKun(kun_dir)
    f.add("openprogram", "https://example.com/v1", ["mine"], True)
    engine.connect("kun", {"model": "deepseek/pro"})
    d = f.read()
    assert d["defaultProviderId"] == "openprogram-2" and stash.entry("kun")["live"]["id"] == "openprogram-2"
    engine.disconnect("kun")
    d = f.read()
    assert set(d["profiles"]) == {"openprogram"} and d["defaultProviderId"] == "openprogram"
    f.server.close()


def test_kun_follows_the_gateway(kun_dir, monkeypatch) -> None:
    from openprogram.models.gateway import address
    from openprogram.models.gateway.agents import lifecycle
    f = FakeKun(kun_dir)
    engine.connect("kun", {"model": "deepseek/pro"})
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18200")
    assert engine.sync("kun") is False  # same kept record: nothing recorded, the address moved in Kun
    p = next(p for p in f.read()["profiles"].values())
    assert p["baseUrl"] == "http://127.0.0.1:18200/gateway/openai/v1" and f.read()["defaultModel"] == "deepseek/pro"
    assert kun.moved(GW, "http://127.0.0.1:18200/gateway/openai/v1")
    assert not kun.moved("http://192.168.1.2:18100/gateway/openai/v1", GW)
    lifecycle.rewire()
    f.server.close()


def test_kun_needs_it_running(kun_dir) -> None:
    with pytest.raises(engine.AgentConfigError, match="open Kun"):
        engine.connect("kun", {"model": "deepseek/pro"})
    assert stash.entry("kun") is None
    # a runtime.json naming another machine is never sent its token
    (kun_dir / "runtime.json").write_text(json.dumps({"baseUrl": "http://192.168.1.9:1234", "runtimeToken": "t"}))
    assert kun.live_client(kun_dir) is None


def test_kun_data_dir(state, monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("KUN_DATA_DIR", raising=False)
    assert kun.data_dir() == tmp_path / ".kun" / "data"
    monkeypatch.setenv("KUN_DATA_DIR", str(tmp_path / "k"))
    assert kun.data_dir() == tmp_path / "k"


# Nowledge Mem

class FakeMem:
    """Mem's backend (nmem-server) as OpenProgram uses it: providers saved with
    POST /remote-llm/config (always made active), keys never read back."""

    def __init__(self, providers=None, active="", enabled=False):
        self.state = {"enabled": enabled, "active_provider": active, "providers": providers or {}}
        self.keys: dict[str, str] = {}
        self.server = Server(self.handle)

    def handle(self, method, path, query, body, headers):
        s = self.state
        if path == "/remote-llm/status":
            return 200, {"ok": True}
        if method == "GET" and path == "/remote-llm/config":
            return 200, s
        if path == "/remote-llm/config":
            p = dict(s["providers"].get(body["provider"]) or {})
            p.update(provider_type=body["provider_type"], model=body["model"])
            for k in ("temperature", "max_tokens", "timeout"):
                p[k] = body.get(k, {"temperature": 0.7, "max_tokens": 8192, "timeout": 60}[k])
            if "api_base" in body:
                p["api_base"] = body["api_base"]
            if "api_key" in body:
                self.keys[body["provider"]] = body["api_key"]
            p["has_api_key"] = body["provider"] in self.keys
            s["providers"][body["provider"]] = p
            s.update(enabled=True, active_provider=body["provider"])
        elif path == "/remote-llm/activate":
            s["active_provider"] = body["provider"]
        elif path == "/remote-llm/provider/remove":
            s["providers"].pop(body["provider"], None)
            if s["active_provider"] == body["provider"]:
                s["active_provider"] = ""
        elif path == "/remote-llm/enabled":
            s["enabled"] = body["enabled"]
        else:
            return 404, {"detail": "not found"}
        return 200, {"ok": True}


@pytest.fixture
def mem(state, monkeypatch):
    made = []

    def make(**kw):
        f = FakeMem(**kw)
        monkeypatch.setenv("NMEM_API_URL", f.server.url)
        made.append(f)
        return f
    monkeypatch.setenv("NMEM_APP_CONFIG_DIR", str(state / "mem"))
    yield make
    for f in made:
        f.server.close()


def test_nowledge_mem_uses_the_gateway(mem) -> None:
    f = mem(providers={"openai": {"provider_type": "openai", "model": "gpt-5", "temperature": 0.2,
                                  "max_tokens": 4000, "timeout": 30}}, active="openai", enabled=True)
    engine.connect("nowledge_mem", {"model": "deepseek/pro"})
    p = f.state["providers"]["magpie"]
    assert p["provider_type"] == "magpie" and p["api_base"] == GW and p["model"] == "deepseek/pro"
    assert f.keys["magpie"] == key("nowledge_mem") and f.state["active_provider"] == "magpie"
    assert engine.status("nowledge_mem")["config"]["drift"] == []
    engine.disconnect("nowledge_mem")
    assert "magpie" not in f.state["providers"] and f.state["active_provider"] == "openai" and f.state["enabled"]
    assert f.state["providers"]["openai"]["temperature"] == 0.2


def test_nowledge_mem_keeps_the_users_magpie(mem) -> None:
    mine = {"provider_type": "magpie", "model": "own/model", "api_base": "http://127.0.0.1:3425/v1",
            "temperature": 0.3, "max_tokens": 1000, "timeout": 10}
    f = mem(providers={"magpie": dict(mine)}, active="magpie", enabled=True)
    engine.connect("nowledge_mem", {"model": "deepseek/pro"})
    assert "magpie" not in f.keys  # a key on the user's own provider stays theirs
    engine.disconnect("nowledge_mem")
    p = f.state["providers"]["magpie"]
    assert p["model"] == "own/model" and p["api_base"] == mine["api_base"] and p["temperature"] == 0.3


def test_nowledge_mem_from_nothing(mem) -> None:
    f = mem()
    engine.connect("nowledge_mem", {"model": "deepseek/pro"})
    assert f.state["enabled"]
    engine.disconnect("nowledge_mem")
    assert f.state["providers"] == {} and not f.state["enabled"]


def test_nowledge_mem_keeps_a_later_pick(mem) -> None:
    f = mem(providers={"openai": {"provider_type": "openai", "model": "gpt-5"},
                       "anthropic": {"provider_type": "anthropic", "model": "x"}}, active="openai", enabled=True)
    engine.connect("nowledge_mem", {"model": "deepseek/pro"})
    f.state["active_provider"] = "anthropic"  # picked in Mem since
    engine.disconnect("nowledge_mem")
    assert f.state["active_provider"] == "anthropic" and "magpie" not in f.state["providers"]


def test_nowledge_mem_follows_the_gateway(mem, monkeypatch) -> None:
    from openprogram.models.gateway import address
    f = mem()
    engine.connect("nowledge_mem", {"model": "deepseek/pro"})
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18200")
    engine.sync("nowledge_mem")
    assert f.state["providers"]["magpie"]["api_base"] == "http://127.0.0.1:18200/gateway/openai/v1"


def test_nowledge_mem_server_imported_over_is_the_users(mem) -> None:
    f = mem()
    engine.connect("nowledge_mem", {"model": "deepseek/pro"})
    f.state["providers"]["magpie"]["api_base"] = "https://magpie.example.com/v1"  # the user's server since
    engine.disconnect("nowledge_mem")
    assert f.state["providers"]["magpie"]["api_base"] == "https://magpie.example.com/v1"


def test_nowledge_mem_needs_it_running(state, monkeypatch) -> None:
    monkeypatch.setenv("NMEM_APP_CONFIG_DIR", str(state / "mem"))
    monkeypatch.setenv("NMEM_API_URL", "http://127.0.0.1:9")
    with pytest.raises(engine.AgentConfigError, match="open Nowledge Mem"):
        engine.connect("nowledge_mem", {"model": "deepseek/pro"})
    monkeypatch.setenv("NMEM_API_URL", "http://10.0.0.5:14242")  # only on this computer
    assert nowledge_mem.live_client() is None


# OpenHanako

def _w(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.chmod(path, mode)


def _chat(path: Path) -> Any:
    return ((yaml.safe_load(path.read_text()) or {}).get("models") or {}).get("chat")


def _want() -> dict:
    return {"display_name": "OpenProgram", "base_url": GW, "api": "openai-completions", "api_key": key("hanako"),
            "models": [{"id": "deepseek/pro", "name": "Pro", "image": False, "reasoning": False},
                       {"id": "deepseek/flash", "name": "Flash", "context": 64000, "maxOutput": 8000,
                        "image": False, "reasoning": False}]}


@pytest.fixture
def hana(state, monkeypatch):
    folder = state / "hana"
    monkeypatch.setenv("HANA_HOME", str(folder))
    return folder


def test_hanako_home(state, monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("HANA_HOME", raising=False)
    assert hanako.home() == tmp_path / ".hanako"
    monkeypatch.setenv("HANA_HOME", "~/hana")
    assert hanako.home() == tmp_path / "hana"
    monkeypatch.setenv("HANA_HOME", str(tmp_path / "x"))
    assert hanako.agent().path() == tmp_path / "x" / "provider-catalog.json" and not hanako.agent().installed()
    (tmp_path / "x").mkdir()
    assert hanako.agent().installed()


def test_hanako_files(hana) -> None:
    catalog = hana / "provider-catalog.json"
    _w(catalog, json.dumps({"catalogVersion": 2, "providers": {"openai": {"api_key": "sk-x"}},
                            "capabilities": {"x": 1}, "meta": {"deletedProviders": ["openprogram", "old"]}}), 0o600)
    stale = hana / "provider-plugins" / "openprogram" / "providers" / "openprogram.json"
    _w(stale, '{"id":"openprogram","defaultBaseUrl":"http://127.0.0.1:1/v1","models":[{"id":"gone"}]}')
    _w(hana / "provider-plugins" / "other" / "providers" / "other.json", '{"id":"other"}')
    _w(hana / "user" / "preferences.json", '{"primaryAgent":"hana"}')
    cfg = hana / "agents" / "hana" / "config.yaml"
    _w(cfg, "# mine\nagent:\n  name: Hana\nmodels:\n  chat:\n    id: gpt-5\n    provider: openai\n  utility: x\n")
    first = hana / "agents" / "aaa" / "config.yaml"
    _w(first, "models:\n  chat: {id: m1, provider: mine}\n")

    engine.connect("hanako", {"model": "deepseek/pro"})
    c = json.loads(catalog.read_text())
    assert c["providers"]["openprogram"] == _want()
    assert c["providers"]["openai"] == {"api_key": "sk-x"} and c["capabilities"] == {"x": 1}
    assert c["meta"]["deletedProviders"] == ["old"]
    assert (catalog.stat().st_mode & 0o777) == 0o600
    assert not stale.parent.parent.exists() and (hana / "provider-plugins" / "other").exists()
    raw = cfg.read_text()
    assert _chat(cfg) == {"id": "deepseek/pro", "provider": "openprogram"}
    assert "# mine" in raw and "utility: x" in raw and "name: Hana" in raw
    assert first.read_text() == "models:\n  chat: {id: m1, provider: mine}\n"
    assert engine.status("hanako")["config"]["drift"] == []
    engine.connect("hanako", {"model": "deepseek/flash"})  # keeps what was kept first

    # its key changed behind OpenProgram's back
    c["providers"]["openprogram"]["api_key"] = "sk-other"
    catalog.write_text(json.dumps(c))
    assert "api_key" in engine.status("hanako")["config"]["drift"]
    engine.sync("hanako")
    assert json.loads(catalog.read_text())["providers"]["openprogram"]["api_key"] == key("hanako")

    engine.disconnect("hanako")
    c = json.loads(catalog.read_text())
    assert "openprogram" not in c["providers"] and c["providers"]["openai"]
    assert c["meta"]["deletedProviders"] == ["old", "openprogram"]
    assert hanako.current(hana) is None
    assert _chat(cfg) == {"id": "gpt-5", "provider": "openai"} and "# mine" in cfg.read_text()


def test_hanako_fresh(hana) -> None:
    with pytest.raises(engine.AgentConfigError, match="no agent yet"):
        engine.connect("hanako", {"model": "deepseek/pro"})
    assert not (hana / "provider-catalog.json").exists()
    _w(hana / "agents" / "b" / "config.yaml", "agent:\n  name: B\n")
    _w(hana / "agents" / "a" / "config.yaml", "agent:\n  name: A\n")
    _w(hana / "user" / "preferences.json", '{"primaryAgent":"gone"}')
    engine.connect("hanako", {"model": "deepseek/pro"})
    path = hana / "provider-catalog.json"
    c = json.loads(path.read_text())
    assert c["catalogVersion"] == 2 and len(c) == 2 and (path.stat().st_mode & 0o777) == 0o600
    assert _chat(hana / "agents" / "a" / "config.yaml") == {"id": "deepseek/pro", "provider": "openprogram"}
    assert (hana / "agents" / "b" / "config.yaml").read_text() == "agent:\n  name: B\n"
    engine.disconnect("hanako")  # nothing was kept: the model goes, as does the provider
    assert _chat(hana / "agents" / "a" / "config.yaml") is None
    c = json.loads(path.read_text())
    assert c["providers"] == {} and c["meta"]["deletedProviders"] == ["openprogram"]
    engine.connect("hanako", {"model": "deepseek/pro"})
    assert json.loads(path.read_text())["meta"]["deletedProviders"] == []


class FakeHanako:
    def __init__(self, folder: Path, token: str, answer: Callable[[str, str, Any], tuple[int, Any]]):
        self.token = token

        def handle(method, path, query, body, headers):
            if headers.get("Authorization") != f"Bearer {token}":
                return 403, {"error": "forbidden"}
            if method == "GET" and path == "/api/server/identity":
                return 200, {"serverId": "srv-1"}
            return answer(method, path, body)
        self.server = Server(handle)
        _w(folder / "server-info.json", json.dumps({"port": self.server.port, "token": "tok"}), 0o600)

    def puts(self) -> list[tuple]:
        out = [(p, b) for (m, p, b, _a) in self.server.calls if m == "PUT"]
        self.server.calls.clear()
        return out


def test_hanako_live(hana) -> None:
    catalog = hana / "provider-catalog.json"
    catalog_json = '{"catalogVersion":2,"providers":{"openai":{"api_key":"sk-x"}}}'
    _w(catalog, catalog_json, 0o600)
    cfg = hana / "agents" / "hana" / "config.yaml"
    cfg_yaml = "models:\n  chat:\n    id: gpt-5\n    provider: openai\n"
    _w(cfg, cfg_yaml)

    def answer(method, path, body):
        if path == "/api/models":
            return 200, {"models": [{"id": "deepseek/pro", "provider": "openprogram"}, {"id": "gpt-5", "provider": "openai"}]}
        return 200, {"ok": True}
    f = FakeHanako(hana, "tok", answer)
    engine.connect("hanako", {"model": "deepseek/pro"})
    assert f.puts() == [("/api/config", {"providers": {"openprogram": _want()}}),
                        ("/api/agents/hana/config", {"models": {"chat": {"id": "deepseek/pro", "provider": "openprogram"}}})]
    assert catalog.read_text() == catalog_json and cfg.read_text() == cfg_yaml
    # as OpenHanako would have saved it
    _w(catalog, json.dumps({"catalogVersion": 2, "providers": {"openai": {"api_key": "sk-x"},
                                                               "openprogram": {"api_key": key("hanako")}}}), 0o600)
    _w(hana / "provider-plugins" / "openprogram" / "providers" / "openprogram.json",
       json.dumps({"id": "openprogram", "displayName": "OpenProgram", "defaultBaseUrl": GW,
                   "defaultApi": "openai-completions", "models": [{"id": "old"}]}))
    _w(cfg, "models:\n  chat:\n    id: deepseek/pro\n    provider: openprogram\n")
    engine.sync("hanako")
    assert f.puts() == [("/api/config", {"providers": {"openprogram": _want()}})]
    engine.disconnect("hanako")
    assert f.puts() == [("/api/agents/hana/config", {"models": {"chat": {"id": "gpt-5", "provider": "openai"}}}),
                        ("/api/config", {"providers": {"openprogram": None}})]
    assert (hana / "provider-plugins" / "openprogram").exists()
    f.server.close()


def test_hanako_live_refused_and_stale(hana) -> None:
    _w(hana / "agents" / "hana" / "config.yaml", "agent:\n  name: Hana\n")
    f = FakeHanako(hana, "tok", lambda m, p, b: (400, {"error": "base_url is not allowed"}))
    with pytest.raises(engine.AgentConfigError, match="base_url is not allowed"):
        engine.connect("hanako", {"model": "deepseek/pro"})
    assert not (hana / "provider-catalog.json").exists()
    f.server.close()
    # server-info.json left by a crash: the files are written
    g = FakeHanako(hana, "another", lambda m, p, b: (200, {}))
    engine.connect("hanako", {"model": "deepseek/pro"})
    assert all(m == "GET" for (m, *_rest) in g.server.calls)
    assert _chat(hana / "agents" / "hana" / "config.yaml")["provider"] == "openprogram"
    g.server.close()


SEALED = "HANA-SECRET-1.AES-256-GCM.AQEBAQEBAQEBAQEB.c2VhbGVk\n"


def test_hanako_sealed(hana, monkeypatch) -> None:
    catalog = hana / "provider-catalog.json"
    _w(catalog, SEALED, 0o600)
    plugin = hana / "provider-plugins" / "openprogram" / "providers" / "openprogram.json"
    _w(plugin, json.dumps({"id": "openprogram", "displayName": "OpenProgram", "authType": "api-key",
                           "defaultBaseUrl": "http://127.0.0.1:1/v1", "defaultApi": "openai-completions",
                           "models": [{"id": "old"}]}), 0o600)
    cfg = hana / "agents" / "hana" / "config.yaml"
    cfg_yaml = "models:\n  chat:\n    id: deepseek/pro\n    provider: openprogram\n"
    _w(cfg, cfg_yaml)
    # as if it had been connected: a record to sync and check
    stash.put("hanako", {"files": {}, "values": {"model": "deepseek/pro"}, "live": {}})
    assert engine.status("hanako")["config"]["drift"] == ["base_url"]
    engine.sync("hanako")
    p = json.loads(plugin.read_text())
    assert p["defaultBaseUrl"] == GW and p["authType"] == "api-key" and p["models"] == _want()["models"]
    assert catalog.read_text() == SEALED and engine.status("hanako")["config"]["drift"] == []

    monkeypatch.setattr(hanako, "installed", lambda: True)
    with pytest.raises(engine.AgentConfigError, match="open OpenHanako"):
        engine.disconnect("hanako")
    assert catalog.read_text() == SEALED and cfg.read_text() == cfg_yaml and plugin.exists()

    # nor can it be added with no plugin to keep it in
    stash.put("hanako", None)
    shutil.rmtree(hana / "provider-plugins")
    _w(cfg, "models:\n  chat:\n    id: gpt-5\n    provider: openai\n")
    with pytest.raises(engine.AgentConfigError, match="open OpenHanako"):
        engine.connect("hanako", {"model": "deepseek/pro"})
    assert catalog.read_text() == SEALED


@pytest.mark.parametrize("kept,models,want,error", [
    ("", ["openprogram/deepseek/pro", "openai/gpt-5", "zai/glm-4.6"], {"id": "gpt-5", "provider": "openai"}, ""),
    ("glm-4.6", ["openprogram/deepseek/pro", "openai/gpt-5", "zai/glm-4.6"], {"id": "glm-4.6", "provider": "zai"}, ""),
    ("openrouter/anthropic/claude", ["openai/gpt-5", "openrouter/anthropic/claude"],
     {"id": "anthropic/claude", "provider": "openrouter"}, ""),
    ("gone/x", ["openprogram/deepseek/pro", "openai/gpt-5"], {"id": "gpt-5", "provider": "openai"}, ""),
    ("gone/x", ["openprogram/deepseek/pro"], None, "no model of its own"),
])
def test_hanako_live_back_needs_provider_id(hana, kept, models, want, error) -> None:
    _w(hana / "provider-catalog.json", json.dumps({"catalogVersion": 2, "providers": {
        "openai": {"api_key": "sk-x"}, "openprogram": {"api_key": "k"}}}), 0o600)
    _w(hana / "agents" / "xiaojing" / "config.yaml", "models:\n  chat:\n    id: deepseek/pro\n    provider: openprogram\n")
    listed = [{"id": m.split("/", 1)[1], "provider": m.split("/", 1)[0]} for m in models]

    def answer(method, path, body):
        if method == "GET" and path == "/api/models":
            return 200, {"models": listed}
        chat = ((body or {}).get("models") or {}).get("chat") if path.startswith("/api/agents/") else None
        if path.startswith("/api/agents/") and (not isinstance(chat, dict)
                                                or f"{chat['provider']}/{chat['id']}" not in models):
            return 400, {"error": "models.chat requires provider/id"}
        return 200, {"ok": True}
    f = FakeHanako(hana, "tok", answer)
    stash.put("hanako", {"files": {}, "values": {"model": "deepseek/pro"},
                         "live": {"agent": "xiaojing", "chat": kept} if kept else {}})
    if error:
        with pytest.raises(engine.AgentConfigError, match=error):
            engine.disconnect("hanako")
        assert f.puts() == [] and stash.entry("hanako")["live"]["chat"] == kept
    else:
        engine.disconnect("hanako")
        assert f.puts() == [("/api/agents/xiaojing/config", {"models": {"chat": want}}),
                            ("/api/config", {"providers": {"openprogram": None}})]
        assert stash.entry("hanako") is None
    f.server.close()


def test_hanako_sealed_uninstalled(hana, monkeypatch) -> None:
    catalog = hana / "provider-catalog.json"
    _w(catalog, SEALED, 0o600)
    _w(hana / "provider-plugins" / "openprogram" / "providers" / "openprogram.json",
       json.dumps({"id": "openprogram", "defaultBaseUrl": GW, "models": [{"id": "deepseek/pro"}]}), 0o600)
    cfg = hana / "agents" / "hana" / "config.yaml"
    _w(cfg, "models:\n  chat:\n    id: deepseek/pro\n    provider: openprogram\n")
    stash.put("hanako", {"files": {}, "values": {"model": "deepseek/pro"}, "live": {"agent": "hana", "chat": "openai/gpt-5"}})
    monkeypatch.setattr(hanako, "installed", lambda: False)
    engine.disconnect("hanako")
    assert _chat(cfg) == {"id": "gpt-5", "provider": "openai"} and catalog.read_text() == SEALED
    assert stash.entry("hanako") is None


def test_hanako_own_provider_named_like_a_gateway_model(hana) -> None:
    _w(hana / "provider-catalog.json", json.dumps({"catalogVersion": 2, "providers": {
        "deepseek": {"base_url": "https://x/v1", "api_key": "k", "models": ["pro"]}}}), 0o600)
    _w(hana / "user" / "preferences.json", '{"primaryAgent":"hana"}')
    cfg = hana / "agents" / "hana" / "config.yaml"
    _w(cfg, "models:\n  chat:\n    id: pro\n    provider: deepseek\n")
    engine.connect("hanako", {"model": "deepseek/pro"})
    assert _chat(cfg) == {"id": "deepseek/pro", "provider": "openprogram"}
    engine.disconnect("hanako")
    assert _chat(cfg) == {"id": "pro", "provider": "deepseek"} and hanako.current(hana) is None
