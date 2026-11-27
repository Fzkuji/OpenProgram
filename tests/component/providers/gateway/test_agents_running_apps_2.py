"""Alma, Shelley, miniQ and Avibe: apps changed through their own running API,
after yetone/magpie's ``alma_test.go``, ``alma_caps_test.go``,
``shelley_test.go``, ``miniq_test.go`` and ``avibe_test.go`` (MIT), each
against a fake of the app's local API."""
from __future__ import annotations

import json
import shutil
import socketserver
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

import pytest

MODELS = [
    {"id": "deepseek/deepseek-chat", "name": "DeepSeek Chat", "provider": "deepseek", "context_window": 128000,
     "max_tokens": 8192, "vision": False, "reasoning": False, "thinking_levels": []},
    {"id": "openai/gpt-5", "name": "GPT-5", "provider": "openai", "context_window": 400000, "max_tokens": 128000,
     "vision": True, "reasoning": True, "thinking_levels": ["low", "medium", "high", "minimal"]},
]
V1 = "http://127.0.0.1:18100/gateway/openai/v1"


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENPROGRAM_DATA_DIR", str(tmp_path / "state"))
    from openprogram.models.gateway import address, conversion
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18100")
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: [dict(m) for m in MODELS])
    return tmp_path


def _registry(monkeypatch, module) -> None:
    from openprogram.models.gateway import agents
    monkeypatch.setattr(agents, "all_agents", lambda: [module.agent()])


Route = Callable[[str, str, Any], tuple[int, Any]]


def _handler(route: Route):
    class Handler(BaseHTTPRequestHandler):
        def _serve(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length)) if length else None
            self.server.seen.append((self.command, self.path, body, dict(self.headers)))  # type: ignore[attr-defined]
            status, out = route(self.command, self.path, body)
            raw = b"" if out is None else json.dumps(out).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        do_GET = do_POST = do_PUT = do_DELETE = _serve

        def address_string(self) -> str:
            return "local"

        def log_message(self, *args) -> None:
            pass
    return Handler


class _UnixHTTP(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def _serve(server) -> Any:
    server.seen = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def short_dir():
    folder = tempfile.mkdtemp(dir="/tmp", prefix="opgw-")  # Unix socket paths are short
    yield Path(folder)
    shutil.rmtree(folder, ignore_errors=True)


# Alma

class FakeAlma:
    def __init__(self) -> None:
        self.providers: list[dict[str, Any]] = []
        self.settings: dict[str, Any] = {"chat": {"defaultModel": "openai-x:gpt-4o"}, "theme": "dark"}
        self.made = 0

    def route(self, method: str, path: str, body: Any) -> tuple[int, Any]:
        if path == "/api/providers" and method == "GET":
            return 200, self.providers
        if path == "/api/providers" and method == "POST":
            self.made += 1
            made = {"id": body.pop("id", f"p{self.made}"), "models": [], "availableModels": [], **body}
            self.providers.append(made)
            return 200, made
        if path == "/api/settings":
            if method == "PUT":
                self.settings = body
            return 200, self.settings
        parts = path.split("/")
        provider = next((p for p in self.providers if p["id"] == parts[3]), None)
        if provider is None:
            return 404, {"error": "no such provider"}
        if method == "DELETE":
            self.providers.remove(provider)
        elif len(parts) == 5:
            provider["models"], provider["availableModels"] = body["models"], body["availableModels"]
        else:
            provider.update(body)
        return 200, {}


@pytest.fixture
def alma(state, monkeypatch):
    from openprogram.models.gateway.agents import alma as module
    fake = FakeAlma()
    server = _serve(ThreadingHTTPServer(("127.0.0.1", 0), _handler(fake.route)))
    monkeypatch.setattr(module, "API", f"http://127.0.0.1:{server.server_address[1]}")
    _registry(monkeypatch, module)
    yield fake
    server.shutdown()


def test_alma_gets_openprogram_as_its_magpie_preset_and_gives_it_back(alma) -> None:
    from openprogram.models.gateway.agents import engine
    from openprogram.models.gateway.keys import token_for
    engine.connect("alma", {"model": "openai/gpt-5"})
    [p] = alma.providers
    assert p["id"] == "magpie" and p["name"] == "openprogram" and p["type"] == "openai"
    assert p["baseURL"] == V1 and p["apiKey"] == token_for("alma") and p["enabled"] is True
    assert p["models"] == ["deepseek/deepseek-chat", "openai/gpt-5"]
    gpt = p["availableModels"][1]["capabilityOverrides"]
    assert gpt == {"reasoning": True, "reasoningLevels": ["low", "medium", "high"], "vision": True,
                   "contextWindow": 400000, "maxOutputTokens": 128000, "functionCalling": True}
    assert "reasoning" not in p["availableModels"][0]["capabilityOverrides"]
    assert alma.settings == {"chat": {"defaultModel": "magpie:openai/gpt-5"}, "theme": "dark"}
    assert engine.status("alma")["config"]["applied"]

    engine.connect("alma", {"model": "deepseek/deepseek-chat"})
    assert len(alma.providers) == 1 and alma.settings["chat"]["defaultModel"] == "magpie:deepseek/deepseek-chat"

    engine.disconnect("alma")
    assert alma.providers == [] and alma.settings["chat"]["defaultModel"] == ""


def test_alma_uses_a_provider_of_the_users_and_keeps_it(alma) -> None:
    from openprogram.models.gateway.agents import engine
    alma.providers.append({"id": "mine", "name": "Mine", "type": "custom", "baseURL": "http://localhost:18100/v1",
                           "apiKey": "sk-mine", "enabled": False, "models": [], "availableModels": []})
    alma.providers.append({"id": "magpie", "name": "Magpie", "type": "custom",
                           "baseURL": "http://192.168.1.9:3425/v1", "enabled": True})
    engine.connect("alma", {"model": "deepseek/deepseek-chat"})
    mine = alma.providers[0]
    assert len(alma.providers) == 2 and mine["enabled"] and mine["baseURL"] == V1
    assert alma.settings["chat"]["defaultModel"] == "mine:deepseek/deepseek-chat"
    engine.disconnect("alma")
    assert len(alma.providers) == 2 and mine["enabled"] is False
    assert alma.settings["chat"]["defaultModel"] == ""


def test_alma_keeps_the_users_tool_calling_and_reads_model_objects(alma) -> None:
    from openprogram.models.gateway.agents import alma as module
    from openprogram.models.gateway.agents.engine import wiring
    assert module._model_ids(["a", {"id": "b", "enabled": True}, {"name": "x"}, 3]) == ["a", "b"]
    provider = {"id": "magpie", "availableModels": [
        {"id": "deepseek/deepseek-chat", "capabilityOverrides": {"functionCalling": False, "pricing": {"input": 1}}}]}
    _, known = module.models(wiring(module.agent(), {}))
    merged = module.with_overrides(provider, known)[0]["capabilityOverrides"]
    assert merged["functionCalling"] is False and merged["pricing"] == {"input": 1}
    assert merged["contextWindow"] == 128000


def test_alma_down(state, monkeypatch) -> None:
    from openprogram.models.gateway import stash
    from openprogram.models.gateway.agents import alma as module, engine
    monkeypatch.setattr(module, "API", "http://127.0.0.1:9")
    _registry(monkeypatch, module)
    with pytest.raises(engine.AgentConfigError, match="isn't running"):
        engine.connect("alma", {"model": "deepseek/deepseek-chat"})
    assert stash.entry("alma") is None
    stash.put("alma", {"files": {}, "values": {"model": "deepseek/deepseek-chat"}, "live": {"provider": "magpie"}})
    monkeypatch.setattr(module, "installed", lambda: True)
    with pytest.raises(engine.AgentConfigError):
        engine.disconnect("alma")
    assert stash.entry("alma") is not None
    monkeypatch.setattr(module, "installed", lambda: False)
    engine.disconnect("alma")  # uninstalled: nothing of it to change now
    assert stash.entry("alma") is None and stash.take_outs() == {"alma": {"provider": "magpie"}}
    assert stash.agents() == []
    assert engine.status("alma")["config"]["drift"] == []


def test_alma_uninstalled_while_connected_comes_back(alma, monkeypatch) -> None:
    """Alma uninstalled keeps its data: turned off then, OpenProgram's provider
    is taken out once Alma answers again (magpie almaTakeOut)."""
    from openprogram.models.gateway import stash
    from openprogram.models.gateway.agents import alma as module, engine, lifecycle
    engine.connect("alma", {"model": "deepseek/deepseek-chat"})
    api = module.API
    monkeypatch.setattr(module, "API", "http://127.0.0.1:9")
    monkeypatch.setattr(module, "installed", lambda: False)
    engine.disconnect("alma")
    assert stash.entry("alma") is None and "alma" in stash.take_outs()
    assert lifecycle.take_out() == [] and len(alma.providers) == 1  # still away
    monkeypatch.setattr(module, "API", api)
    assert lifecycle.take_out() == ["alma"]
    assert alma.providers == [] and alma.settings["chat"]["defaultModel"] == "" and stash.take_outs() == {}


def test_alma_on_openprogram_again_has_nothing_to_take_out(alma, monkeypatch) -> None:
    from openprogram.models.gateway import stash
    from openprogram.models.gateway.agents import alma as module, engine
    stash.keep_take_out("alma", {"provider": "magpie"})
    engine.connect("alma", {"model": "deepseek/deepseek-chat"})
    assert stash.take_outs() == {} and len(alma.providers) == 1


# Shelley

class FakeShelley:
    def __init__(self) -> None:
        self.custom: list[dict[str, Any]] = [{"model_id": "by-hand", "model_name": "deepseek/deepseek-chat",
                                              "endpoint": V1, "provider_type": "openai"}]
        self.profiles = [{"name": "default", "default": True, "model": "claude-sonnet", "thinking_level": "high"}]
        self.ready = {"claude-sonnet"}
        self.made = 0

    def route(self, method: str, path: str, body: Any) -> tuple[int, Any]:
        if path == "/api/custom-models":
            if method == "POST":
                self.made += 1
                model = {"model_id": f"custom-{self.made}", **body}
                self.custom.append(model)
                return 201, model
            return 200, self.custom
        if path.startswith("/api/custom-models/"):
            model = next((m for m in self.custom if m["model_id"] == path.rsplit("/", 1)[1]), None)
            if model is None:
                return 404, {"error": "no"}
            if method == "DELETE":
                self.custom.remove(model)
            else:
                model.update(body)
            return 200, {}
        if path == "/api/models":
            return 200, [{"id": i, "ready": True} for i in self.ready]
        if path == "/api/profiles":
            return 200, self.profiles
        if path.startswith("/api/profiles/") and method == "PUT":
            self.profiles = [body]
            return 200, body
        return 404, None


@pytest.fixture
def shelley(state, monkeypatch, short_dir):
    from openprogram.models.gateway.agents import shelley as module
    fake = FakeShelley()
    sock = short_dir / "shelley.sock"
    server = _serve(_UnixHTTP(str(sock), _handler(fake.route)))
    monkeypatch.setenv("SHELLEY_SOCKET", str(sock))
    _registry(monkeypatch, module)
    yield fake
    server.shutdown()


def test_shelley_profile_on_openprogram_and_back(shelley) -> None:
    from openprogram.models.gateway.agents import engine
    from openprogram.models.gateway.keys import token_for
    engine.connect("shelley", {"model": "deepseek/deepseek-chat"})
    made = shelley.custom[-1]
    assert made["model_id"] == "custom-1" and made["endpoint"] == V1 and made["api_key"] == token_for("shelley")
    assert made["model_name"] == "deepseek/deepseek-chat" and made["reasoning_support"] == "no"
    # a model that doesn't reason takes no thinking level
    assert shelley.profiles[0]["model"] == "custom-1" and shelley.profiles[0]["thinking_level"] == ""

    engine.connect("shelley", {"model": "openai/gpt-5"})
    assert [m["model_id"] for m in shelley.custom] == ["by-hand", "custom-1", "custom-2"]
    assert shelley.profiles[0]["model"] == "custom-2"

    engine.disconnect("shelley")
    assert [m["model_id"] for m in shelley.custom] == ["by-hand"]  # the user's own stays
    assert shelley.profiles[0]["model"] == "claude-sonnet" and shelley.profiles[0]["thinking_level"] == "high"


def test_shelley_own_model_gone(shelley) -> None:
    from openprogram.models.gateway.agents import engine
    engine.connect("shelley", {"model": "openai/gpt-5"})
    shelley.ready.clear()
    engine.disconnect("shelley")
    assert shelley.profiles[0]["model"] == ""


def test_shelley_uninstalled_while_connected_comes_back(shelley, monkeypatch, short_dir) -> None:
    """magpie shelleyTakeOut: OpenProgram's models left in a Shelley that was gone
    when it was turned off come out once it answers again."""
    from openprogram.models.gateway import stash
    from openprogram.models.gateway.agents import engine, lifecycle, shelley as module
    engine.connect("shelley", {"model": "deepseek/deepseek-chat"})
    sock = str(short_dir / "shelley.sock")
    monkeypatch.setenv("SHELLEY_SOCKET", str(short_dir / "gone.sock"))
    monkeypatch.setattr(module, "here", lambda: False)
    engine.disconnect("shelley")
    assert stash.entry("shelley") is None and "shelley" in stash.take_outs()
    assert lifecycle.take_out() == []
    monkeypatch.setenv("SHELLEY_SOCKET", sock)
    assert lifecycle.take_out() == ["shelley"]
    assert [m["model_id"] for m in shelley.custom] == ["by-hand"] and stash.take_outs() == {}
    assert shelley.profiles[0]["model"] == "claude-sonnet"


def test_shelley_down(state, monkeypatch, short_dir) -> None:
    from openprogram.models.gateway.agents import engine, shelley as module
    monkeypatch.setenv("SHELLEY_SOCKET", str(short_dir / "gone.sock"))
    _registry(monkeypatch, module)
    with pytest.raises(engine.AgentConfigError, match="isn't running"):
        engine.connect("shelley", {"model": "deepseek/deepseek-chat"})


# miniQ

@pytest.fixture
def miniq(state, monkeypatch):
    from openprogram.models.gateway.agents import miniq as module
    folder = state / "miniq"
    monkeypatch.setenv("MINIQ_DATA_DIR", str(folder))
    _registry(monkeypatch, module)
    return folder


def _daemon(folder: Path, seen: list) -> Any:
    from websockets.sync.server import serve

    def handle(ws) -> None:
        for message in ws:
            request = json.loads(message)
            seen.append(request)
            ws.send(json.dumps({"jsonrpc": "2.0", "method": "event", "params": {}}))
            ws.send(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {"ok": True}}))
    server = serve(handle, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "daemon.json").write_text(json.dumps({"port": server.socket.getsockname()[1], "token": "t k"}))
    return server


def test_miniq_provider_written_told_and_put_back(miniq) -> None:
    from openprogram.models.gateway.agents import engine
    from openprogram.models.gateway.keys import token_for
    seen: list = []
    server = _daemon(miniq, seen)
    try:
        mine = {"baseUrl": "https://api.example.com/v1", "apiKey": "sk-mine", "model": "m1"}
        (miniq / "settings.json").write_text(json.dumps({"provider": mine, "mcpServers": {"x": {}}}, indent=2))
        engine.connect("miniq", {"model": "deepseek/deepseek-chat"})
        doc = json.loads((miniq / "settings.json").read_text())
        assert doc["provider"] == {"baseUrl": V1, "apiKey": token_for("miniq"), "model": "deepseek/deepseek-chat",
                                   "apiProtocol": "chat_completions"}
        assert doc["mcpServers"] == {"x": {}}
        updates = [r for r in seen if r["method"] == "settings.update"]
        assert updates[-1]["params"]["provider"]["baseUrl"] == V1
        engine.disconnect("miniq")
        assert json.loads((miniq / "settings.json").read_text()) == {"provider": mine, "mcpServers": {"x": {}}}
        updates = [r for r in seen if r["method"] == "settings.update"]
        assert updates[-1]["params"]["provider"] == {"baseUrl": mine["baseUrl"], "model": "m1",
                                                  "apiProtocol": "auto", "apiKey": "sk-mine"}
    finally:
        server.shutdown()


def test_miniq_made_file_goes_and_data_dir(miniq, monkeypatch, tmp_path) -> None:
    from openprogram.models.gateway.agents import engine, miniq as module
    engine.connect("miniq", {"model": "deepseek/deepseek-chat"})  # no daemon: written all the same
    assert (miniq / "settings.json").exists()
    engine.disconnect("miniq")
    assert not (miniq / "settings.json").exists()

    monkeypatch.delenv("MINIQ_DATA_DIR")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert module.data_dir() == tmp_path / ".local" / "share" / "miniq"
    (tmp_path / ".local" / "share").mkdir(parents=True)
    (tmp_path / ".local" / "share" / "miniq-data-dir.json").write_text(json.dumps({"data_dir": "/moved/miniq"}))
    assert module.data_dir() == Path("/moved/miniq")


def test_miniq_provider_is_ours_whole_and_its_pick_stays(miniq) -> None:
    """What the user adds inside OpenProgram's provider stays while connected,
    a model picked in miniQ stays through a reconnect on the same pick, and
    the provider goes whole on disconnect."""
    from openprogram.models.gateway.agents import engine
    mine = {"baseUrl": "https://api.example.com/v1", "apiKey": "sk-mine", "model": "m1"}
    path = miniq / "settings.json"
    miniq.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"provider": mine}))
    engine.connect("miniq", {"model": "deepseek/deepseek-chat"})
    doc = json.loads(path.read_text())
    doc["provider"]["temperature"] = 0.2
    doc["provider"]["model"] = "openrouter/x"
    path.write_text(json.dumps(doc))
    engine.connect("miniq", {"model": "deepseek/deepseek-chat"})
    provider = json.loads(path.read_text())["provider"]
    assert provider["temperature"] == 0.2 and provider["model"] == "openrouter/x"
    engine.disconnect("miniq")
    assert json.loads(path.read_text()) == {"provider": mine}


# Avibe

class FakeAvibe:
    def __init__(self, home: Path) -> None:
        self.config = home / "config" / "config.json"
        self.config.parent.mkdir(parents=True)
        self.sources: list[dict[str, Any]] = [{"id": "anthropic-1", "vendor": "anthropic"}]
        self.made = 0
        self._save()

    def _save(self) -> None:
        self.config.write_text(json.dumps({"model_hub": {"sources": self.sources}}))

    def route(self, method: str, path: str, body: Any) -> tuple[int, Any]:
        op, payload = body["operation"], body["payload"]
        if op == "list_sources":
            return 200, {"ok": True, "result": {"sources": self.sources}}
        if op == "create_source":
            self.made += 1
            source = {**payload["source"], "id": f"src-{self.made}"}
            source.pop("key")
            self.sources.append(source)
            self._save()
            return 200, {"ok": True, "result": {"source": source}}
        if op == "delete_source":
            if "would_remove_hops" not in payload:
                return 409, {"ok": False, "error": "confirm_required", "would_remove_hops": [{"agent": "vibey"}]}
            self.sources = [s for s in self.sources if s["id"] != payload["source_id"]]
            self._save()
            return 200, {"ok": True, "result": {}}
        if op == "refresh_source":
            return 200, {"ok": True, "result": {}}
        return 400, {"ok": False, "error": "unknown"}


@pytest.fixture
def avibe(state, monkeypatch, short_dir):
    from openprogram.models.gateway.agents import avibe as module
    monkeypatch.setenv("AVIBE_HOME", str(short_dir))
    fake = FakeAvibe(short_dir)
    (short_dir / "state").mkdir()
    server = _serve(_UnixHTTP(str(short_dir / "state" / "dispatch.sock"), _handler(fake.route)))
    _registry(monkeypatch, module)
    yield fake, server
    server.shutdown()


def test_avibe_adds_openprogram_as_a_model_source(avibe) -> None:
    from openprogram.models.gateway import stash
    from openprogram.models.gateway.agents import engine
    from openprogram.models.gateway.keys import token_for
    fake, server = avibe
    row = engine.connect("avibe", {})
    assert row["switch"] and row["config"]["applied"]
    create = next(b for m, p, b, h in server.seen if b["operation"] == "create_source")["payload"]["source"]
    assert create["base_url"] == V1 and create["key"] == token_for("avibe") and create["protocol"] == "openai_chat"
    assert create["vendor"] == "custom" and create["save_unverified"] is True
    assert stash.entry("avibe")["live"] == {"source": "src-1"}

    engine.connect("avibe", {})  # already there at the gateway: nothing made again
    assert fake.made == 1
    from openprogram.models.gateway.agents.engine import sync
    sync("avibe")
    assert server.seen[-1][2]["operation"] == "refresh_source"

    engine.disconnect("avibe")
    assert [s["id"] for s in fake.sources] == ["anthropic-1"]
    assert stash.entry("avibe") is None


def test_avibe_follows_the_gateway(avibe, monkeypatch) -> None:
    from openprogram.models.gateway import address
    from openprogram.models.gateway.agents import avibe as module, engine
    fake, _server = avibe
    engine.connect("avibe", {})
    assert module.moved("http://127.0.0.1:18000/gateway/openai/v1", V1)
    assert not module.moved("http://10.0.0.2:18000/gateway/openai/v1", V1)
    assert not module.moved("http://127.0.0.1:8787/bili/gateway/openai/v1", V1)
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18200")
    assert engine.status("avibe")["config"]["drift"] == ["model_hub.sources.base_url"]
    engine.sync("avibe")
    assert [s["id"] for s in fake.sources] == ["anthropic-1", "src-2"]
    assert fake.sources[-1]["base_url"] == "http://127.0.0.1:18200/gateway/openai/v1"


def test_avibe_needs_it_running_and_its_home(state, monkeypatch, short_dir, tmp_path) -> None:
    from openprogram.models.gateway.agents import avibe as module, engine
    monkeypatch.setenv("AVIBE_HOME", str(short_dir))
    _registry(monkeypatch, module)
    with pytest.raises(engine.AgentConfigError, match="only while it runs"):
        engine.connect("avibe", {})
    monkeypatch.setenv("AVIBE_HOME", "~/elsewhere")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert module.home() == tmp_path / "elsewhere"
    monkeypatch.delenv("AVIBE_HOME")
    (tmp_path / ".vibe_remote").mkdir()
    assert module.home() == tmp_path / ".vibe_remote"
    (tmp_path / ".avibe").mkdir()
    assert module.home() == tmp_path / ".avibe"


# A reconnect on the same model (lifecycle.rewire after a moved port or a
# new key) changes only the address and key: a pick made in the app stays

MOVED = "http://127.0.0.1:18200/gateway/openai/v1"


def _move(monkeypatch) -> None:
    from openprogram.models.gateway import address
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18200")


def test_alma_rewire_keeps_a_default_picked_in_alma(alma, monkeypatch) -> None:
    from openprogram.models.gateway.agents import engine, lifecycle
    engine.connect("alma", {"model": "openai/gpt-5"})
    alma.settings["chat"]["defaultModel"] = "magpie:deepseek/deepseek-chat"  # picked in Alma
    _move(monkeypatch)
    assert lifecycle.rewire() == ["Alma"]
    assert alma.providers[0]["baseURL"] == MOVED
    assert alma.settings["chat"]["defaultModel"] == "magpie:deepseek/deepseek-chat"


def test_alma_disconnect_puts_its_default_back(alma) -> None:
    from openprogram.models.gateway.agents import engine
    alma.providers.append({"id": "openai-x", "name": "OpenAI", "type": "openai",
                           "baseURL": "https://api.openai.com/v1", "enabled": True})
    engine.connect("alma", {"model": "openai/gpt-5"})
    engine.connect("alma", {"model": "deepseek/deepseek-chat"})  # kept once, from the first
    assert alma.settings["chat"]["defaultModel"] == "magpie:deepseek/deepseek-chat"
    engine.disconnect("alma")
    assert alma.settings == {"chat": {"defaultModel": "openai-x:gpt-4o"}, "theme": "dark"}
    # a default picked in Alma since stays
    engine.connect("alma", {"model": "openai/gpt-5"})
    alma.settings["chat"]["defaultModel"] = "openai-x:gpt-5"
    engine.disconnect("alma")
    assert alma.settings["chat"]["defaultModel"] == "openai-x:gpt-5"


def test_shelley_rewire_keeps_a_model_picked_in_shelley(shelley, monkeypatch) -> None:
    from openprogram.models.gateway.agents import engine, lifecycle
    engine.connect("shelley", {"model": "openai/gpt-5"})
    shelley.profiles[0]["model"] = "claude-sonnet"  # picked in Shelley
    _move(monkeypatch)
    assert lifecycle.rewire() == ["Shelley"]
    assert all(m["endpoint"] == MOVED for m in shelley.custom if m["model_id"] != "by-hand")
    assert shelley.profiles[0]["model"] == "claude-sonnet"


def test_miniq_daemon_told_the_pick_the_file_keeps(miniq) -> None:
    from openprogram.models.gateway.agents import engine
    seen: list = []
    server = _daemon(miniq, seen)
    try:
        engine.connect("miniq", {"model": "deepseek/deepseek-chat"})
        path = miniq / "settings.json"
        doc = json.loads(path.read_text())
        doc["provider"]["model"] = "openai/gpt-5"  # picked in miniQ
        path.write_text(json.dumps(doc))
        engine.connect("miniq", {"model": "deepseek/deepseek-chat"})
        updates = [r for r in seen if r["method"] == "settings.update"]
        assert updates[-1]["params"]["provider"]["model"] == "openai/gpt-5"
        assert json.loads(path.read_text())["provider"]["model"] == "openai/gpt-5"
    finally:
        server.shutdown()
