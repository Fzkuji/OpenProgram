"""Claude Science, T3 Code and OpenMausBot, after yetone/magpie's tests of the
same agents (``internal/agent/claudescience_test.go``, ``t3code_test.go``,
``openmausbot_test.go``, ``internal/gateway/science_test.go``; MIT License)."""
from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from openprogram.models.providers.catalog.api_registry import get_api_provider, register_api_provider
from openprogram.models.providers.types import Model
from tests.component.providers.gateway.test_gateway_protocols import FakeProvider

GW = "http://127.0.0.1:18100"
ROWS = [
    {"id": "gateway-test/m1", "name": "M One", "provider": "gateway-test", "context_window": 64000,
     "max_tokens": 8000, "thinking_levels": ["low", "medium", "high"]},
    {"id": "gateway-test/deepseek-v4-pro", "name": "deepseek-v4-pro", "provider": "gateway-test",
     "context_window": 128000, "thinking_levels": []},
]
MODULES = ("codex", "claude_code", "claude_science", "t3code", "openmausbot")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENPROGRAM_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("T3CODE_HOME", str(tmp_path / "home" / ".t3"))
    monkeypatch.setenv("OMB_DATA_DIR", str(tmp_path / "home" / ".openmausbot"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "home" / ".codex"))
    (tmp_path / "home").mkdir()
    from openprogram.models.gateway import address, agents, conversion
    monkeypatch.setattr(agents, "MODULES", MODULES)
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: GW)
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: [dict(row) for row in ROWS])
    from openprogram.models.gateway import codex_app
    monkeypatch.setattr(codex_app, "base_instructions", lambda _versions: "You are Codex.")
    return tmp_path / "home"


# Claude Science: the daemon

class FakeScience:
    """claude-science as the agent runs it: status, stop, serve, --version."""

    def __init__(self, folder, running=True, pid=40, port=8123):
        self.dir, self.running, self.pid, self.port = folder, running, pid, port
        self.env: dict[str, str] | None = None
        self.calls: list[str] = []
        self.daemon: dict = {"active_frames": 0, "active_conversations": 0, "launch_context": "terminal"}
        self.version = "0.1.72"
        self.next_pid = 100

    def lock(self):
        path = self.dir / "operon.lock"
        if self.running:
            path.write_text(json.dumps({"pid": self.pid, "port": self.port}))
        else:
            path.unlink(missing_ok=True)

    def run(self, env, _bin, *args):
        self.calls.append(" ".join(args))
        if args[0] == "status":
            return json.dumps({"running": self.running, "pid": self.pid if self.running else 0, "port": self.port,
                               "version": self.version, "daemon": self.daemon}).encode()
        if args[0] == "stop":
            self.running = False
            self.lock()
            return b""
        if args[0] == "serve":
            self.running, self.env, self.pid = True, dict(env or {}), self.next_pid
            self.next_pid += 1
            if "--port" in args:
                self.port = int(args[args.index("--port") + 1])
            self.lock()
            return b""
        if args[0] == "--version":
            return f"claude-science {self.version} (release, public)".encode()
        raise AssertionError(args)

    def base(self):
        return (self.env or {}).get("ANTHROPIC_BASE_URL")


@pytest.fixture
def science(home, monkeypatch, tmp_path):
    from openprogram.models.gateway.agents import claude_science as cs
    folder = tmp_path / "science"
    folder.mkdir()
    fake = FakeScience(folder)
    fake.lock()
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://elsewhere.example")
    monkeypatch.setattr(cs, "science_run", fake.run)
    monkeypatch.setattr(cs, "science_bin", lambda: "/bin/claude-science")
    monkeypatch.setattr(cs, "science_dir", lambda: folder)
    monkeypatch.setattr(cs, "WAIT", 1.0)
    monkeypatch.setattr(cs, "_keep", cs._Keeper())
    return fake


def test_claude_science_restarts_its_daemon(science, monkeypatch) -> None:
    from openprogram.models.gateway.agents import claude_science as cs
    from openprogram.models.gateway.agents import engine
    from openprogram.models.gateway.keys import path_key, token_for

    assert not engine.status(cs.ID)["config"]["applied"] and engine.status(cs.ID)["installed"]
    engine.connect(cs.ID, {"model": "gateway-test/m1"})
    want = f"{GW}/gateway/agent/{path_key(token_for(cs.ID))}/anthropic"
    assert science.base() == want
    assert science.calls == ["status", "stop", "serve --detached --no-browser --port 8123", "status"]
    row = engine.status(cs.ID)
    assert row["config"]["applied"] and "Claude account" in row["notice"] and not row["config"]["drift"]

    # set again: the daemon it started, on this gateway, is left running
    science.calls = []
    cs.on(True, want)
    assert science.calls == ["status"]
    # the note of the daemon holds the keyed address: only its owner reads it
    assert cs.daemon_path().stat().st_mode & 0o077 == 0

    # the window, opened while none ran, started its own, and a session runs
    # in it: said, and the keeper leaves it running
    science.pid, science.env, science.calls = 77, None, []
    science.daemon = {"active_frames": 1, "active_conversations": 1, "launch_context": "app-child"}
    science.lock()
    assert "without OpenProgram's gateway" in engine.status(cs.ID)["notice"]
    cs.keep_once()
    assert science.calls == ["status"] and science.pid == 77
    # none running (the computer restarted): started on the gateway, on its port
    science.running, science.calls = False, []
    science.lock()
    cs.keep_once()
    assert science.running and science.base() == want and science.port == 8123
    assert "without" not in engine.status(cs.ID)["notice"]

    # disconnected: running again, without the variable even where
    # OpenProgram's own environment has it
    science.calls = []
    engine.disconnect(cs.ID)
    assert science.running and science.base() is None
    assert not engine.status(cs.ID)["config"]["applied"] and not cs.mark_path().exists()
    assert not cs.daemon_path().exists()
    # and the keeper starts nothing now
    science.running, science.calls = False, []
    science.lock()
    cs.keep_once()
    assert not science.running and science.calls == []
    # and with none installed there is nothing to connect
    monkeypatch.setattr(cs, "science_bin", lambda: "")
    with pytest.raises(engine.AgentConfigError):
        engine.connect(cs.ID, {"model": "gateway-test/m1"})
    assert not engine.status(cs.ID)["config"]["applied"] and not engine.status(cs.ID)["installed"]


def test_claude_science_waits_for_its_sessions_and_keeps_secrets(science, monkeypatch) -> None:
    """Connecting while a session runs leaves the daemon running till the
    keeper finds it idle; the daemon gets no key of OpenProgram's process and
    none of its variables, and the keyed address is never shown."""
    from openprogram.models.gateway.agents import claude_science as cs
    from openprogram.models.gateway.agents import engine
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-of-openprogram")
    monkeypatch.setenv("OPENPROGRAM_SECRET", "x")
    science.daemon = {"active_frames": 1, "active_conversations": 1, "launch_context": "terminal"}
    engine.connect(cs.ID, {"model": "gateway-test/m1"})
    assert science.calls == ["status"] and science.pid == 40 and science.base() is None
    notice = engine.status(cs.ID)["notice"]
    assert "without OpenProgram's gateway" in notice and "/gateway/agent/" not in notice
    science.daemon = {"active_frames": 0, "active_conversations": 0, "launch_context": "terminal"}
    cs.keep_once()
    assert science.pid != 40 and science.base() and science.port == 8123
    assert "ANTHROPIC_API_KEY" not in science.env and not any(k.startswith("OPENPROGRAM_") for k in science.env)
    # a daemon started for an earlier address is said without the address
    note = json.loads(cs.daemon_path().read_text())
    cs.write_daemon(note["url"].replace("/anthropic", "/old"), cs.Status(pid=note["pid"], port=note["port"]))
    assert cs.check() and "/gateway/" not in cs.check()


def test_claude_science_takes_back_from_its_app(science) -> None:
    """An idle daemon the app started in place of OpenProgram's, or OpenProgram's
    own older than the program installed, is replaced; a kept one never."""
    from openprogram.models.gateway.agents import claude_science as cs
    from openprogram.models.gateway.agents import engine
    engine.connect(cs.ID, {"model": "gateway-test/m1"})
    want = science.base()
    science.pid, science.env = 9, None
    science.lock()
    science.calls = []
    cs.keep_once()
    assert science.base() == want and science.pid != 9
    assert science.calls[:2] == ["status", "stop"]
    # a service manager would start it again without the variable
    science.pid, science.env = 10, None
    science.daemon = {"active_frames": 0, "active_conversations": 0, "launch_context": "launchd"}
    science.lock()
    cs.keep_once()
    assert science.pid == 10


def test_older_version() -> None:
    from openprogram.models.gateway.agents.claude_science import older_version
    for a, b, want in [("0.1.72", "0.1.73", True), ("0.1.9", "0.1.72", True), ("0.1.72", "0.1.72", False),
                       ("0.2.0", "0.1.99", False), ("", "0.1.72", False), ("0.1.72", "", False),
                       ("x", "0.1.72", False), ("0.1", "0.1.1", True)]:
        assert older_version(a, b) is want, (a, b)


# Claude Science: the gateway

class RecordingProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.models: list[str] = []

    async def stream_simple(self, model, context, options=None):
        self.models.append(model.id)
        async for event in super().stream_simple(model, context, options):
            yield event


@pytest.fixture
def gateway_app(home, monkeypatch) -> Iterator[tuple[TestClient, RecordingProvider]]:
    from openprogram.interfaces.server.owner_auth import OwnerAuthMiddleware, OwnerAuthState
    from openprogram.interfaces.server.routes import gateway
    from openprogram.models.gateway import conversion
    api = "gateway-science-api"
    fake = RecordingProvider()
    previous = get_api_provider(api)
    register_api_provider(api, fake)
    models = {row["id"]: Model(id=row["id"].split("/", 1)[1], name=row["name"], api=api, provider="gateway-test",
                               base_url="http://gateway.invalid", context_window=64000) for row in ROWS}

    def resolve(ref):
        value = str(ref or "").removeprefix("openprogram:")
        if value not in models:
            raise conversion.GatewayError(404, "not_found", f"model {value!r} is not set up in OpenProgram")
        return models[value]

    monkeypatch.setattr(conversion, "lookup_model", resolve)
    app = FastAPI()
    gateway.register(app)
    state = OwnerAuthState.from_raw_token(bytes(range(32)), owner_principal_id="owner/install/0123456789abcdef",
                                          bind_host="127.0.0.1", port=18100, allowed_origins=())
    try:
        with TestClient(OwnerAuthMiddleware(app, auth_state=state), base_url=GW,
                        client=("127.0.0.1", 50000)) as client:
            yield client, fake
    finally:
        from openprogram.models.providers.catalog import api_registry
        if previous is None:
            api_registry._registry.pop(api, None)
        else:
            register_api_provider(api, previous)


SIGN_IN = "Bearer sk-ant-oat01-" + "s" * 24  # made up, of a sign-in's shape


def _science_headers() -> dict[str, str]:
    return {"User-Agent": "Anthropic/JS 0.71.0", "Authorization": SIGN_IN, "X-Anthropic-Surface": "operon",
            "Anthropic-Beta": "oauth-2025-04-20", "Anthropic-Version": "2023-06-01"}


def test_claude_science_is_served_openprograms_models(gateway_app) -> None:
    from openprogram.models.gateway import science
    from openprogram.models.gateway.keys import path_key, token_for
    client, fake = gateway_app
    prefix = f"/gateway/agent/{path_key(token_for(science.SCIENCE_ID))}/anthropic"

    # its sign-in alone opens nothing: only the address that carries its key
    assert client.get("/gateway/anthropic/v1/models", headers=_science_headers()).status_code == 401
    assert client.get("/gateway/agent/claude-science.0000/anthropic/v1/models",
                      headers=_science_headers()).status_code == 401

    listed = client.get(prefix + "/v1/models?limit=1000", headers=_science_headers())
    assert listed.status_code == 200
    data = listed.json()["data"]
    assert len(data) == 2 and not listed.json()["has_more"]
    alias = {}
    for entry in data:
        assert entry["id"].startswith("claude-") and not science._SLUG.match(entry["display_name"])
        alias[science.aliased(entry["id"])] = entry["id"]
    assert set(alias) == {"gateway-test/m1", "gateway-test/deepseek-v4-pro"}
    assert next(e for e in data if e["id"] == alias["gateway-test/m1"])["max_tokens"] == 8000
    one = client.get(prefix + "/v1/models/" + alias["gateway-test/m1"], headers=_science_headers())
    assert one.status_code == 200 and one.json()["id"] == alias["gateway-test/m1"]

    def turn(model: str, tools: bool) -> str:
        body: dict = {"model": model, "max_tokens": 50, "stream": True, "messages": [{"role": "user", "content": "hi"}]}
        if tools:
            body["tools"] = [{"name": "run_python", "description": "run a cell", "input_schema": {"type": "object"}}]
        fake.replies.append([("text", "ok")])
        reply = client.post(prefix + "/v1/messages", headers=_science_headers(), json=body)
        assert reply.status_code == 200 and "message_stop" in reply.text, reply.text
        return fake.models[-1]

    # before any pick, its default goes to the first model it is listed
    assert turn("claude-opus-5", True) == "m1"
    # a turn on a model it picked, which is then the one picked
    assert turn(alias["gateway-test/deepseek-v4-pro"], True) == "deepseek-v4-pro"
    # its small task on Anthropic's haiku goes there too
    assert turn("claude-haiku-4-5-20251001", False) == "deepseek-v4-pro"

    # another Anthropic SDK client is no Claude Science's
    from openprogram.models.gateway.keys import gateway_key
    plain = client.get("/gateway/anthropic/v1/models",
                       headers={"User-Agent": "Anthropic/JS 0.71.0", "x-api-key": gateway_key()})
    assert plain.status_code == 200 and science.ALIAS not in plain.text


def test_science_names_and_aliases() -> None:
    from openprogram.models.gateway import science
    assert science.science_name("deepseek-v4-pro") == "Deepseek-v4-pro"
    assert science.science_name("GLM 5") == "GLM 5"
    assert science.names([{"id": "a/x", "name": "Same"}, {"id": "b/y", "name": "Same"}]) == ["Same (x)", "Same (y)"]
    assert science.names([{"id": "a/x", "name": "Same"}, {"id": "b/x", "name": "Same"}]) == ["Same (a/x)", "Same (b/x)"]
    number = science.alias_number("fake/m1")
    assert len(number) == 10 and number.isdigit() and number == science.alias_number("fake/m1")


# T3 Code

def test_t3_claude_args() -> None:
    from openprogram.models.gateway.agents.t3code import claude_args, settings_of
    gw, key = GW + "/gateway/anthropic", "opgw-k"
    for given, keep in [
        ("", ""),
        ("--add-dir /work", "--add-dir /work"),
        ('--append-system-prompt "be brief" --model x', '--append-system-prompt "be brief" --model x'),
        ('--settings "{\\"permissions\\":{\\"allow\\":[\\"Bash(ls)\\"]},\\"env\\":{\\"FOO\\":\\"it\'s\\"}}" --add-dir /w',
         "--add-dir /w"),
        ("--settings='{\"env\":{\"ANTHROPIC_BASE_URL\":\"http://127.0.0.1:9\"}}'", ""),
        ("--model x --settings '{}' --", "--model x"),
        ("--append-system-prompt --settings", "--append-system-prompt --settings"),
    ]:
        out, ok = claude_args(given, gw, key)
        assert ok and keep in out, (given, out)
        env = (settings_of(out)[3] or {}).get("env") or {}
        assert env["ANTHROPIC_BASE_URL"] == gw and env["ANTHROPIC_AUTH_TOKEN"] == key, (given, out)
        if "permissions" in given:
            assert env["FOO"] == "it's" and settings_of(out)[3]["permissions"]
        assert claude_args(out, gw, key)[0] == out
    assert claude_args("--settings ./s.json", gw, key) == ("--settings ./s.json", False)


def test_t3_code_instance(home) -> None:
    from openprogram.models.gateway.agents import engine, t3code
    path = home / ".t3" / "userdata" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "providerInstances": {"claudeAgent": {"driver": "claudeAgent", "config": {"binaryPath": "/opt/claude"}},
                              "mine": {"driver": "codex", "displayName": "Mine"}},
        "theme": "dark"}, indent=2))
    assert engine.status("t3code")["installed"]
    engine.connect("t3code", {"model": "gateway-test/m1"})
    doc = json.loads(path.read_text())
    ours = doc["providerInstances"]["openprogram"]
    assert ours["driver"] == "claudeAgent" and ours["displayName"] == "OpenProgram" and ours["enabled"] is True
    env = {e["name"]: e["value"] for e in ours["environment"]}
    assert env["ANTHROPIC_BASE_URL"] == GW + "/gateway/anthropic"
    # the agent's own key, never the gateway's, and marked secret
    from openprogram.models.gateway.keys import gateway_key, token_for
    assert env["ANTHROPIC_AUTH_TOKEN"] == token_for("t3code") != gateway_key()
    assert next(e for e in ours["environment"] if e["name"] == "ANTHROPIC_AUTH_TOKEN")["sensitive"] is True
    assert gateway_key() not in json.dumps(doc)
    inline = (t3code.settings_of(ours["config"]["launchArgs"])[3] or {})["env"]
    assert inline["ANTHROPIC_AUTH_TOKEN"] == token_for("t3code")
    from openprogram.models.gateway.agents.claude_code import LAUNCH_BLANKS
    assert all(inline[name] == "" for name in LAUNCH_BLANKS)
    assert env[t3code.REVIEW_ENV] == "0" and env[t3code.SEARCH_ENV] == "true"
    slugs = [m["slug"] for m in ours["config"]["customModels"]]
    assert slugs == ["gateway-test/m1", "gateway-test/deepseek-v4-pro"]
    effort = ours["config"]["customModels"][0]["capabilities"]["optionDescriptors"]
    assert effort[0]["options"] == [{"id": "low", "label": "Low"}, {"id": "medium", "label": "Medium", "isDefault": True},
                                    {"id": "high", "label": "High"}]
    assert effort[1] == {"id": "thinking", "label": "Thinking", "type": "boolean", "currentValue": True}
    assert "capabilities" not in ours["config"]["customModels"][1]
    assert ours["config"]["binaryPath"] == "/opt/claude"
    assert "ANTHROPIC_BASE_URL" in ours["config"]["launchArgs"]
    codex = doc["providerInstances"]["openprogram-codex"]
    assert codex["driver"] == "codex" and codex["displayName"] == "OpenProgram · Codex"
    assert '-c "model_provider=\\"openprogram\\""' in codex["config"]["launchArgs"]
    assert json.loads((path.parent / t3code.CATALOG_NAME).read_text())["models"]
    assert doc["providerInstances"]["mine"] == {"driver": "codex", "displayName": "Mine"} and doc["theme"] == "dark"
    assert not engine.status("t3code")["config"]["drift"]

    # the user renames it and adds a variable of their own: both stay
    doc["providerInstances"]["openprogram"]["displayName"] = "Gateway"
    doc["providerInstances"]["openprogram"]["environment"].append({"name": "FOO", "value": "1", "sensitive": False})
    # and removes the Codex instance in T3: not brought back while connected
    del doc["providerInstances"]["openprogram-codex"]
    path.write_text(json.dumps(doc, indent=2))
    engine.connect("t3code", {"model": "gateway-test/deepseek-v4-pro"})
    doc = json.loads(path.read_text())
    ours = doc["providerInstances"]["openprogram"]
    assert ours["displayName"] == "Gateway" and {"name": "FOO", "value": "1", "sensitive": False} in ours["environment"]
    assert ours["config"]["customModels"][0]["slug"] == "gateway-test/deepseek-v4-pro"
    assert "openprogram-codex" not in doc["providerInstances"]

    engine.disconnect("t3code")
    doc = json.loads(path.read_text())
    assert "openprogram-codex" not in doc["providerInstances"]
    assert doc["providerInstances"]["claudeAgent"] == {"driver": "claudeAgent", "config": {"binaryPath": "/opt/claude"}}
    assert doc["providerInstances"]["mine"] == {"driver": "codex", "displayName": "Mine"}
    assert not (path.parent / t3code.CATALOG_NAME).exists()


def test_t3_code_off_takes_only_its_own(home) -> None:
    from openprogram.models.gateway.agents import engine
    path = home / ".t3" / "userdata" / "settings.json"
    path.parent.mkdir(parents=True)
    before = '{\n  "theme": "dark"\n}\n'
    path.write_text(before)
    engine.connect("t3code", {"model": "gateway-test/m1"})
    engine.disconnect("t3code")
    assert json.loads(path.read_text()) == {"theme": "dark"}


def test_t3_home(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import t3code
    monkeypatch.setenv("T3CODE_HOME", "~/elsewhere")
    assert t3code.base() == home / "elsewhere"
    monkeypatch.delenv("T3CODE_HOME")
    assert t3code.config_path() == home / ".t3" / "userdata" / "settings.json"


# OpenMausBot

@pytest.fixture
def omb(home, monkeypatch):
    from openprogram.models.gateway.agents import openmausbot
    monkeypatch.setattr(openmausbot, "running", lambda: False)
    path = home / ".openmausbot" / "config.json"
    path.parent.mkdir(parents=True)
    return path


def test_openmausbot_engine(omb) -> None:
    from openprogram.models.gateway.agents import engine, openmausbot
    omb.write_text('{\n  "theme": "dark"\n}\n')
    engine.connect("openmausbot", {"model": "gateway-test/deepseek-v4-pro"})
    doc = json.loads(omb.read_text())
    instances = doc["instances"]
    # the default fleet beside its own: a map holding one engine would be the whole fleet
    assert set(instances) == set(openmausbot.FLEET) | {"openprogram"}
    ours = instances["openprogram"]
    assert ours["driver"] == "claudeAgent" and ours["access"] == "custom" and ours["displayName"] == "OpenProgram"
    assert ours["config"]["requireApiKey"] is True
    assert ours["config"]["configDir"].endswith("providers/openprogram")
    assert ours["config"]["endpointModels"] == ["gateway-test/deepseek-v4-pro", "gateway-test/m1"]
    env = ours["environment"]
    assert env["ANTHROPIC_BASE_URL"] == GW + "/gateway/anthropic"
    assert env["ANTHROPIC_AUTH_TOKEN"] == env["ANTHROPIC_API_KEY"] and env["ANTHROPIC_AUTH_TOKEN"].startswith("opgw-agent-openmausbot.")
    assert env["ANTHROPIC_MODEL"] == "gateway-test/deepseek-v4-pro"
    assert not engine.status("openmausbot")["config"]["drift"]
    # a sync with the same picks keeps it as it is
    engine.connect("openmausbot", {"model": "gateway-test/deepseek-v4-pro"})
    assert json.loads(omb.read_text()) == doc

    engine.disconnect("openmausbot")
    assert json.loads(omb.read_text()) == {"theme": "dark"}


def test_openmausbot_keeps_the_users(omb) -> None:
    from openprogram.models.gateway.agents import engine
    omb.write_text(json.dumps({"instances": {
        "claude": {"driver": "claudeAgent", "config": {"cli": "/opt/claude"}},
        "openprogram": {"driver": "claudeAgent", "displayName": "Mine", "color": "red",
                        "environment": {"ANTHROPIC_CUSTOM_HEADERS": "x: y", "FOO": "bar"}}}}, indent=2))
    engine.connect("openmausbot", {"model": "gateway-test/m1"})
    instances = json.loads(omb.read_text())["instances"]
    assert set(instances) == {"claude", "openprogram"}
    ours = instances["openprogram"]
    assert ours["displayName"] == "Mine" and ours["color"] == "red" and ours["config"]["cli"] == "/opt/claude"
    assert ours["environment"]["FOO"] == "bar" and "ANTHROPIC_CUSTOM_HEADERS" not in ours["environment"]
    engine.disconnect("openmausbot")
    instances = json.loads(omb.read_text())["instances"]
    assert instances["openprogram"] == {"driver": "claudeAgent", "displayName": "Mine", "color": "red",
                                        "environment": {"ANTHROPIC_CUSTOM_HEADERS": "x: y", "FOO": "bar"}}


def test_openmausbot_fleet_changed(omb) -> None:
    """A fleet the user changed in OpenMausBot since stays when it is turned off; its own engine goes."""
    from openprogram.models.gateway.agents import engine
    engine.connect("openmausbot", {"model": "gateway-test/m1"})
    doc = json.loads(omb.read_text())
    doc["instances"]["grok"]["displayName"] = "My Grok"
    omb.write_text(json.dumps(doc, indent=2))
    engine.disconnect("openmausbot")
    instances = json.loads(omb.read_text())["instances"]
    assert "openprogram" not in instances and instances["grok"]["displayName"] == "My Grok"


def test_openmausbot_refuses(omb, monkeypatch) -> None:
    from openprogram.models.gateway.agents import engine, openmausbot
    monkeypatch.setattr(openmausbot, "running", lambda: True)
    omb.write_text("{}\n")
    with pytest.raises(engine.AgentConfigError, match="quit OpenMausBot"):
        engine.connect("openmausbot", {"model": "gateway-test/m1"})
    assert omb.read_text() == "{}\n"
    monkeypatch.setattr(openmausbot, "running", lambda: False)
    omb.write_text('{"instances": []}')
    with pytest.raises(engine.AgentConfigError, match="isn't a map"):
        engine.connect("openmausbot", {"model": "gateway-test/m1"})


def test_openmausbot_dir(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import openmausbot
    monkeypatch.setenv("OMB_DATA_DIR", "relative/dir")
    assert openmausbot.data_dir() == home / ".openmausbot"
    monkeypatch.setenv("OMB_DATA_DIR", str(home / "x"))
    assert openmausbot.config_path() == home / "x" / "config.json"


def test_openmausbot_model_ids() -> None:
    from openprogram.models.gateway.agents.openmausbot import MODEL_ID
    for good in ("relay/glm-4.6", "claude-opus-4-7[1m]", "or/x-ai/grok-4:free"):
        assert MODEL_ID.match(good)
    for bad in ("relay/glm 4.6", "-x", "a@b", "m+1", ""):
        assert not MODEL_ID.match(bad)


def test_openmausbot_list_written_again(omb) -> None:
    """OpenMausBot wrote back the list it held since it started: the keeper writes the models again."""
    from openprogram.models.gateway.agents import engine, lifecycle
    engine.connect("openmausbot", {"model": "gateway-test/m1"})
    doc = json.loads(omb.read_text())
    doc["instances"]["openprogram"]["config"]["endpointModels"] = ["gateway-test/m1"]
    omb.write_text(json.dumps(doc, indent=2))
    lifecycle.keep_once()
    models = json.loads(omb.read_text())["instances"]["openprogram"]["config"]["endpointModels"]
    assert models == ["gateway-test/m1", "gateway-test/deepseek-v4-pro"]
