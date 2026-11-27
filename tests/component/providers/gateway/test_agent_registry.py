"""The outside-agent registry and what it rests on, after yetone/magpie's tests
of the same pieces: the config editors (``internal/edit/*_test.go``), the
line notes (``mark_test.go``), the stash (``stash_test.go``), per-agent keys
and paths (``agentpath_test.go``), reseat and rewire (``reseat_test.go``),
the registry (``agents_test.go``) and the running-app checks
(``apprestart_test.go``, ``gone_test.go``)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import Request

from openprogram.models.gateway import edit
from openprogram.models.gateway.edit import ConfigUnreadable


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENPROGRAM_DATA_DIR", str(tmp_path / "state"))
    from openprogram.models.gateway import address, conversion
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18100")
    monkeypatch.setattr(conversion, "enabled_model_refs",
                        lambda: [{"id": "deepseek/deepseek-chat", "name": "DS", "provider": "deepseek"}])
    return tmp_path


# Editors

def test_jsonc_keeps_comments_and_trailing_commas() -> None:
    text = '{\n  // mine\n  "a": 1, /* why */\n  "env": {\n    "X": "y",\n  },\n}\n'
    out = edit.edit("jsonc", text, sets={"env.TOKEN": "k", "model": "m"}, deletes=["a"])
    assert "// mine" in out and '"X": "y",' in out
    assert edit.load("jsonc", out) == {"model": "m", "env": {"TOKEN": "k", "X": "y"}}
    assert edit.edit("jsonc", out, deletes=["env.TOKEN", "env.X"]).count('"env": {}') == 1
    with pytest.raises(ConfigUnreadable):
        edit.edit("jsonc", '{"a": ', sets={"b": 1})


def test_json_dotted_key_escapes() -> None:
    out = edit.edit("json", "{}\n", sets={r"a\.b.c": 1})
    assert json.loads(out) == {"a.b": {"c": 1}}


def test_yaml_sets_and_deletes_in_place() -> None:
    text = "# top\nname: me  # who\nmodel:\n  id: old\n  keep: yes\nlist:\n  - 1\n"
    out = edit.edit("yaml", text, sets={"model.id": "new", "model.base": "http://x", "extra.deep": 2})
    loaded = edit.load("yaml", out)
    assert loaded["model"] == {"id": "new", "keep": True, "base": "http://x"} and loaded["extra"] == {"deep": 2}
    assert out.startswith("# top\nname: me  # who\n") and "  - 1\n" in out
    back = edit.edit("yaml", out, deletes=["model.base", "extra"])
    assert edit.load("yaml", back) == {"name": "me", "model": {"id": "new", "keep": True}, "list": [1]}
    with pytest.raises(ConfigUnreadable):
        edit.edit("yaml", "a: {b: 1}\n", sets={"a.c": 2})


def test_dotenv_quotes_and_keeps_lines() -> None:
    text = "# keys\nexport A=1\nB='two words'\n"
    out = edit.edit("dotenv", text, sets={"C": "has space", "A": "9"}, deletes=["B"])
    assert edit.load("dotenv", out) == {"A": "9", "C": "has space"}
    assert out.startswith("# keys\n") and "C='has space'" in out


def test_toml_tables_and_marks(state) -> None:
    from openprogram.models.gateway.agents import all_agents
    all_agents()  # installs the line notes
    text = '# mine\nmodel = "gpt-5"\n\n[projects."/r"]\ntrust_level = "trusted"\n'
    out = edit.edit("toml", text, sets={"model_provider": "openprogram",
                                        "model_providers.openprogram.base_url": "http://127.0.0.1:18100/gateway/openai/v1"})
    assert "# openprogram: Codex on OpenProgram's local gateway" in out
    assert out.count("# openprogram: ") == 2 and '[projects."/r"]' in out
    assert edit.load("toml", out)["model_providers"]["openprogram"]["base_url"].endswith("/openai/v1")
    # marking is idempotent: an edit of a marked file adds no second note
    again = edit.edit("toml", out, sets={"model": "deepseek/deepseek-chat"})
    assert again.count("# openprogram: ") == 2


_OURS = {"providers": lambda entry: isinstance(entry, dict) and entry.get("name") == "openprogram"}

_TABLES = '''# top
[[providers]]
name = "mine"   # my relay
url = "u"

# about OpenProgram's entry
[[providers]]
name = "openprogram"
url = "a"

[[providers]]
name = "mine2"
url = "v"
models = [
  "x",
] # keep me

# notes about the editor
[editor]
theme = "dark"
'''


def test_toml_array_tables_change_only_openprograms_blocks() -> None:
    """magpie toml_array_test.go: only the matching [[key]] blocks change, in
    place; the user's blocks, and every comment between them, stay as written."""
    from openprogram.models.gateway.edit.toml_text import toml_edit
    mine = {"name": "mine", "url": "u"}
    mine2 = {"name": "mine2", "url": "v", "models": ["x"]}
    out = toml_edit(_TABLES, sets={"providers": [mine, mine2, {"name": "openprogram", "url": "b"}]},
                    members=_OURS)
    assert out == _TABLES.replace('url = "a"', 'url = "b"')
    # taken out: the user's blocks and the comments after the entry stay
    gone = toml_edit(out, sets={"providers": [mine, mine2]}, members=_OURS)
    assert gone == _TABLES.replace('[[providers]]\nname = "openprogram"\nurl = "a"\n', "")
    # none yet: added after the file, nothing else touched
    added = toml_edit(gone, sets={"providers": [mine, mine2, {"name": "openprogram", "url": "c"}]}, members=_OURS)
    assert added.startswith(gone) and added.endswith('\n\n[[providers]]\nname = "openprogram"\nurl = "c"\n')
    # without a match every entry is written again, but the comments after them stay
    whole = toml_edit("[[p]]\nname = \"a\"\n\n# notes about the editor\n[editor]\ntheme = \"dark\"\n",
                      sets={"p": [{"name": "b"}]})
    assert whole == "[[p]]\nname = \"b\"\n\n# notes about the editor\n[editor]\ntheme = \"dark\"\n"


def test_toml_multiline_value_keeps_its_trailing_comment() -> None:
    from openprogram.models.gateway.edit.toml_text import toml_edit
    text = '[x]\nmodels = [\n  "a",  # first\n  "b",\n] # keep me\nother = 1\n'
    assert toml_edit(text, sets={"x.models": ["c"]}) == '[x]\nmodels = ["c"] # keep me\nother = 1\n'


# Stash and keys

def test_damaged_stash_refuses(state) -> None:
    from openprogram.models.gateway import stash
    from openprogram.models.gateway.agents import engine
    path = state / "state" / "gateway" / "harness-stash.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    with pytest.raises(stash.StashUnreadable):
        stash.load()
    with pytest.raises(stash.StashUnreadable):
        stash.remember("codex", "/x/config.toml", {"model": (True, "gpt-5")}, fmt="toml")
    with pytest.raises(engine.AgentConfigError):
        engine.disconnect("codex")
    assert engine.status("codex")["config"]["error"]
    assert path.read_text() == "{not json"


def test_old_stash_record_is_read(state) -> None:
    """A record from before the registry (one file, no format) restores the user's own settings."""
    from openprogram.models.gateway import stash
    from openprogram.models.gateway.agents import engine
    home = state / "claude"
    home.mkdir()
    settings = home / "settings.json"
    settings.write_text(json.dumps({"model": "deepseek/deepseek-chat", "theme": "dark"}))
    path = state / "state" / "gateway" / "harness-stash.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"claude_code": {"path": str(settings),
                                                "originals": {"model": {"had": True, "value": "opus"}},
                                                "wrote": {"model": {"set": "deepseek/deepseek-chat"}}}}))
    record = stash.entry("claude_code")
    assert record["files"][str(settings)] == {"format": "json", "originals": {"model": {"had": True, "value": "opus"}},
                                             "wrote": {"model": {"set": "deepseek/deepseek-chat"}}, "absent": False}
    assert record["values"] == {"model": "deepseek/deepseek-chat"}
    engine.disconnect("claude_code")
    assert json.loads(settings.read_text()) == {"model": "opus", "theme": "dark"}
    assert stash.entry("claude_code") is None


def test_agent_keys_name_their_agent(state) -> None:
    from openprogram.models.gateway.keys import agent_of_key, gateway_key, token_for, verify_gateway_key
    key = token_for("codex")
    assert key.startswith("opgw-agent-codex.") and gateway_key() not in key and verify_gateway_key(key)
    assert agent_of_key(key) == "codex" and agent_of_key(gateway_key()) is None
    assert agent_of_key(token_for("nobody")) is None and not verify_gateway_key(token_for("nobody"))
    forged = key[:-1] + ("0" if key[-1] != "0" else "1")
    assert agent_of_key(forged) is None and not verify_gateway_key(forged)
    assert agent_of_key(gateway_key() + "-codex") is None and not verify_gateway_key(gateway_key() + "-codex")


def test_agent_path_and_attribution(state) -> None:
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from openprogram.interfaces.server.owner_auth import OwnerAuthMiddleware, OwnerAuthState
    from openprogram.models.gateway.attribution import agent_of
    from openprogram.models.gateway.keys import gateway_key, token_for

    app = FastAPI()

    @app.get("/gateway/openai/v1/models")
    def models(request: Request):
        return {"agent": agent_of(request, "openai-chat"), "path": request.url.path}

    @app.get("/gateway/gemini/v1beta/models")
    def gemini_models(request: Request):
        return {"agent": agent_of(request, "gemini")}

    owner = OwnerAuthState.from_raw_token(bytes(range(32)), owner_principal_id="owner/install/0123456789abcdef",
                                          bind_host="127.0.0.1", port=18100, allowed_origins=())
    guarded = OwnerAuthMiddleware(app, auth_state=owner)
    key = gateway_key()
    with TestClient(guarded, base_url="http://127.0.0.1:18100", client=("127.0.0.1", 50000)) as client:
        by_path = client.get("/gateway/agent/codex/openai/v1/models", headers={"Authorization": f"Bearer {key}"}).json()
        assert by_path == {"agent": "codex", "path": "/gateway/openai/v1/models"}
        by_key = client.get("/gateway/openai/v1/models", headers={"Authorization": f"Bearer {token_for('claude_code')}"})
        assert by_key.json()["agent"] == "claude_code"
        by_ua = client.get("/gateway/openai/v1/models", headers={"x-api-key": key, "user-agent": "codex_cli_rs/0.50"})
        assert by_ua.json()["agent"] == "codex"
        plain = client.get("/gateway/openai/v1/models", headers={"x-api-key": key, "user-agent": "curl/8"})
        assert plain.json()["agent"] == "openai-chat"
        assert client.get(f"/gateway/gemini/v1beta/models?key={key}").status_code == 200
        assert client.get("/gateway/gemini/v1beta/models", headers={"x-goog-api-key": key}).status_code == 200
        refused = client.get("/gateway/gemini/v1beta/models")
        assert refused.status_code == 401 and refused.json()["error"]["status"] == "UNAUTHENTICATED"
        assert client.get(f"/gateway/openai/v1/models?key={key}").status_code == 401
        assert client.get(f"/gateway/gemini/v1beta/models?key={owner.token}").status_code == 401
        assert client.get("/gateway/gemini/v1beta/models", headers={"x-goog-api-key": owner.token}).status_code == 200
        unknown = client.get("/gateway/agent/nobody/openai/v1/models", headers={"x-api-key": key})
        assert unknown.status_code == 404


# Registry

def test_registry_find_and_user_agents() -> None:
    from openprogram.models.gateway.agents import agent_of_ua, all_agents, find
    agents = all_agents()
    ids = [a.id for a in agents]
    assert ids[:2] == ["codex", "claude_code"] and len(set(ids)) == len(ids)
    assert ids[-4:] == ["cindy", "aionui", "cherry_studio", "lody"]
    assert find("cc").id == "claude_code" and find("Claude").id == "claude_code" and find("codex-cli").id == "codex"
    assert find("") is None and find("zzz") is None
    assert agent_of_ua("claude-cli/2.0 (external, cli)") == "claude_code"
    assert agent_of_ua("codex_cli_rs/0.50") == "codex" and agent_of_ua("curl/8") is None


def _fake_agent(home: Path, **extra):
    from openprogram.models.gateway.agents import Agent, Write

    def wire(at):
        sets = {"provider.base_url": at.base("openai-chat"), "provider.api_key": at.key, "provider.model": at.model}
        if at.options.get("catalog"):
            sets["provider.catalog"] = at.options["catalog"]
        return [Write(home / "config.yaml", "yaml", sets=sets)]
    return Agent(id="fake", name="Fake", icon="", path=lambda: home / "config.yaml", wire=wire,
                 named_key=True, path_named=True, **extra)


@pytest.fixture
def fake(state, monkeypatch):
    from openprogram.models.gateway import agents
    home = state / "fake"
    home.mkdir()
    made = {}

    def all_agents():
        return [made["agent"]]
    monkeypatch.setattr(agents, "all_agents", all_agents)

    def build(**extra):
        made["agent"] = _fake_agent(home, **extra)
        return home / "config.yaml"
    return build


def test_connect_restores_an_absent_file(fake) -> None:
    from openprogram.models.gateway.agents import engine
    from openprogram.models.gateway.keys import token_for
    path = fake()
    engine.connect("fake", {"model": "deepseek/deepseek-chat"})
    written = edit.load("yaml", path.read_text())["provider"]
    assert written == {"base_url": "http://127.0.0.1:18100/gateway/agent/fake/openai/v1",
                       "api_key": token_for("fake"), "model": "deepseek/deepseek-chat"}
    assert engine.status("fake")["config"]["applied"] is True
    engine.disconnect("fake")
    assert not path.exists()


def test_before_copy_and_drift(fake) -> None:
    from openprogram.models.gateway.agents import engine
    path = fake(before_copy=True)
    path.write_text("# keep me\nprovider:\n  model: mine\nother: 1\n")
    engine.connect("fake", {"model": "deepseek/deepseek-chat"})
    path.write_text(path.read_text().replace("deepseek/deepseek-chat", "edited/elsewhere"))
    assert engine.status("fake")["config"]["drift"]
    engine.disconnect("fake")
    assert edit.load("yaml", path.read_text()) == {"provider": {"model": "edited/elsewhere"}, "other": 1}


def test_reseat_and_rewire(fake, monkeypatch) -> None:
    from openprogram.models.gateway import address
    from openprogram.models.gateway.agents import engine, lifecycle
    path = fake()
    engine.connect("fake", {"model": "deepseek/deepseek-chat"})
    moves = lifecycle.reseat(["other/DeepSeek_Chat-20250101"])
    assert [(m.from_, m.to) for m in moves] == [("deepseek/deepseek-chat", "other/DeepSeek_Chat-20250101")]
    assert edit.load("yaml", path.read_text())["provider"]["model"] == "other/DeepSeek_Chat-20250101"

    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18200")
    assert lifecycle.rewire() == ["Fake"]
    assert edit.load("yaml", path.read_text())["provider"]["base_url"].startswith("http://127.0.0.1:18200/")
    assert lifecycle.rewire() == []

    # a main model nothing serves stays, reported; the agent is never disconnected
    [move] = lifecycle.reseat(["unrelated/model"])
    assert move.to == "" and move.error
    assert edit.load("yaml", path.read_text())["provider"]["model"] == "other/DeepSeek_Chat-20250101"


def test_lifecycle_leaves_files_another_instance_wired(fake) -> None:
    from openprogram.models.gateway.agents import engine, lifecycle
    path = fake()
    engine.connect("fake", {"model": "deepseek/deepseek-chat"})
    assert engine.owned("fake")
    path.write_text(path.read_text().replace("127.0.0.1:18100", "127.0.0.1:18200"))
    assert not engine.owned("fake")
    before = path.read_text()
    assert lifecycle.reseat(["other/deepseek-chat"]) == [] and lifecycle.rewire() == []
    assert path.read_text() == before


def test_a_reapply_keeps_what_it_wrote_before(fake) -> None:
    from openprogram.models.gateway import stash
    from openprogram.models.gateway.agents import engine
    path = fake()
    engine.connect("fake", {"model": "deepseek/deepseek-chat"}, options={"catalog": "/x/catalog.json"})
    engine.connect("fake", {"model": "deepseek/deepseek-chat"})
    wrote = stash.entry("fake")["files"][str(path)]["wrote"]
    assert wrote["provider.catalog"] == {"set": "/x/catalog.json"}
    at = stash.entry("fake").get("at")
    engine.connect("fake", {"model": "deepseek/deepseek-chat"})
    assert stash.entry("fake").get("at") == at  # nothing changed, nothing stamped
    engine.disconnect("fake")
    assert not path.exists()


def test_a_failed_write_takes_back_its_stash_record(fake, monkeypatch) -> None:
    from openprogram.models.gateway import stash
    from openprogram.models.gateway.agents import engine
    from openprogram.models.gateway.edit import text

    def refuse(self, new):
        raise OSError("read-only")
    path = fake()
    monkeypatch.setattr(text.ConfigText, "write", refuse)
    with pytest.raises(engine.AgentConfigError):
        engine.connect("fake", {"model": "deepseek/deepseek-chat"})
    assert stash.entry("fake") is None and not path.exists()
    assert engine.status("fake")["config"]["applied"] is False


def test_model_keys() -> None:
    from openprogram.models.gateway.agents.lifecycle import model_key, same_model
    assert model_key("anthropic/Claude_Sonnet-4.5-20250929") == "claude-sonnet-4-5"
    assert same_model("a/gpt-5", ["b/x", "c/gpt-5"]) == "c/gpt-5"
    assert same_model("a/gpt.5", ["b/GPT-5"]) == "b/GPT-5"
    assert same_model("a/gpt-5", ["b/gpt-4"]) == ""


def test_running_and_gone(tmp_path, monkeypatch) -> None:
    from openprogram.models.gateway.agents import Agent
    from openprogram.models.gateway.agents.running import _elapsed
    assert _elapsed("05:03") == 303 and _elapsed("1-02:00:00") == 93600 and _elapsed("x") is None

    folder = tmp_path / ".tool"
    folder.mkdir()
    (folder / "settings.json").write_text("{}")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setenv("HOME", str(tmp_path))
    agent = Agent(id="tool", name="Tool", icon="", path=lambda: folder / "settings.json", wire=lambda at: [],
                  bin="definitely-not-a-tool-here", dir=lambda: folder)
    assert agent.installed() and agent.cli_gone()
    held = tmp_path / "held"
    held.mkdir()
    agent.held_at = lambda: [held]
    assert not agent.cli_gone()


def test_a_key_in_the_address_and_an_app_window(state) -> None:
    """An agent that sends no key carries its own in its address; Obsidian's
    window on this computer is answered CORS at its own path with no key
    (magpie ``cors.go`` appOrigins), and nothing else is."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from openprogram.interfaces.server.owner_auth import OwnerAuthMiddleware, OwnerAuthState
    from openprogram.models.gateway.attribution import agent_of
    from openprogram.models.gateway.keys import path_key, token_for

    app = FastAPI()

    @app.post("/gateway/openai/v1/chat/completions")
    def chat(request: Request):
        return {"agent": agent_of(request, "openai-chat"), "path": request.url.path}

    owner = OwnerAuthState.from_raw_token(bytes(range(32)), owner_principal_id="owner/install/0123456789abcdef",
                                          bind_host="127.0.0.1", port=18100, allowed_origins=())
    guarded = OwnerAuthMiddleware(app, auth_state=owner)
    key = token_for("fx")
    with TestClient(guarded, base_url="http://127.0.0.1:18100", client=("127.0.0.1", 50000)) as client:
        ok = client.post(f"/gateway/agent/{path_key(key)}/openai/v1/chat/completions", json={})
        assert ok.json() == {"agent": "fx", "path": "/gateway/openai/v1/chat/completions"}
        assert client.post("/gateway/agent/fx/openai/v1/chat/completions", json={}).status_code == 401
        assert client.post(f"/gateway/agent/{path_key(key)[:-1]}x/openai/v1/chat/completions", json={}).status_code == 401

        # Obsidian's window on this computer, at its own path, gets in with no key
        window = {"origin": "app://obsidian.md"}
        url = "/gateway/agent/obsidian/openai/v1/chat/completions"
        asked = client.options(url, headers={**window, "access-control-request-method": "POST",
                                             "access-control-request-headers": "content-type"})
        assert asked.status_code == 204 and asked.headers["access-control-allow-origin"] == "app://obsidian.md"
        assert asked.headers["access-control-allow-headers"] == "content-type"
        answered = client.post(url, headers=window, json={})
        assert answered.json() == {"agent": "obsidian", "path": "/gateway/openai/v1/chat/completions"}
        assert answered.headers["access-control-allow-origin"] == "app://obsidian.md"
        # no key and: another Origin, none, another agent's path, the plain gateway path, a forwarded request
        assert client.post(url, headers={"origin": "https://example.com"}, json={}).status_code == 403
        assert client.post(url, headers={"origin": "app://obsidian.md.evil"}, json={}).status_code == 403
        assert client.post(url, json={}).status_code == 401
        assert client.post("/gateway/agent/fx/openai/v1/chat/completions", headers=window, json={}).status_code == 401
        assert client.post("/gateway/openai/v1/chat/completions", headers=window, json={}).status_code == 401
        assert client.post(url, headers={**window, "x-forwarded-for": "203.0.113.9"}, json={}).status_code == 401
    # nor from a peer that isn't this computer
    with TestClient(guarded, base_url="http://127.0.0.1:18100", client=("192.168.1.20", 50000)) as client:
        assert client.post(url, headers=window, json={}).status_code in (401, 403)
