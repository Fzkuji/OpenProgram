"""Claude Desktop in its 3p gateway mode, after yetone/magpie's
``internal/agent/claudedesktop_test.go``, ``claudedesktop_models_test.go``
and ``internal/gateway/desktop_*_test.go``: what connecting writes into
Desktop's folders and puts back, and the ids and requests the gateway
shapes for it."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from .test_gateway_protocols import REF, client, provider  # noqa: F401 - fixtures

ROWS = [
    {"id": "deepseek/deepseek-chat", "name": "DeepSeek Chat", "provider": "deepseek", "context_window": 128000,
     "thinking_levels": []},
    {"id": "anthropic/claude-opus-5-5", "name": "Claude Opus 5.5", "provider": "anthropic",
     "context_window": 1000000, "thinking_levels": ["low", "high"]},
    {"id": "openrouter/gpt-6", "name": "GPT-6", "provider": "openrouter", "context_window": 400000,
     "thinking_levels": ["low", "medium", "high"]},
    {"id": "anthropic/claude-haiku-5-5", "name": "Claude Haiku 5.5", "provider": "anthropic",
     "context_window": 200000, "thinking_levels": []},
]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "home" / ".config"))
    monkeypatch.setenv("OPENPROGRAM_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setattr("openprogram.models.gateway.address.worker_base_url",
                        lambda stable=False: "http://127.0.0.1:18100")
    monkeypatch.setattr("openprogram.models.gateway.conversion.enabled_model_refs", lambda: [dict(r) for r in ROWS])
    from openprogram.models.gateway import agents, desktop
    monkeypatch.setattr(agents, "MODULES", (*agents.MODULES, "claude_desktop")
                        if "claude_desktop" not in agents.MODULES else agents.MODULES)
    monkeypatch.setattr("openprogram.models.gateway.agents.base.app_bundles", lambda app: [])
    desktop._picked.update(model="", at=0.0, **{"from": ""})
    (tmp_path / "home").mkdir()
    from openprogram.models.gateway.agents.claude_desktop import Paths
    return Paths()


def _json(path: Path) -> dict:
    return json.loads(path.read_text())


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _tree(paths) -> dict[str, str]:
    out = {}
    for root in {paths.dir, paths.dir3p}:
        if not root.exists():
            continue
        out[str(root)] = "dir"
        for path in root.rglob("*"):
            out[str(path)] = "dir" if path.is_dir() else path.read_text()
    return out


def _on(**values):
    from openprogram.models.gateway.agents import engine
    return engine.connect("claude_desktop", {"model": "deepseek/deepseek-chat", **values})


def _off():
    from openprogram.models.gateway.agents import engine
    return engine.disconnect("claude_desktop")


def test_fresh_desktop_is_wired_and_put_back(home) -> None:
    from openprogram.models.gateway.agents import find
    from openprogram.models.gateway.agents.claude_desktop import PROFILE_ID
    from openprogram.models.gateway.keys import token_for

    agent = find("claude-desktop")
    assert agent is not None and agent.id == "claude_desktop" and not agent.installed()
    home.dir.mkdir(parents=True)
    assert agent.installed()
    before = _tree(home)

    row = _on()
    for path in (home.config, home.config3p):
        assert _json(path) == {"deploymentMode": "3p"}
    assert _json(home.prof) == {
        "inferenceProvider": "gateway", "inferenceGatewayBaseUrl": "http://127.0.0.1:18100/gateway/anthropic",
        "inferenceGatewayApiKey": token_for("claude_desktop"), "inferenceGatewayAuthScheme": "bearer",
        "disableDeploymentModeChooser": True,
        "claudeAiImport": {"enabled": True, "bannerBehavior": "detect"},
        "toolSearchEnabled": True, "modelPrefer1mContext": True,
    }
    assert os.stat(home.prof).st_mode & 0o777 == 0o600
    meta = _json(home.meta)
    assert meta == {"entries": [{"id": PROFILE_ID, "name": "OpenProgram"}], "appliedId": PROFILE_ID}
    assert row["config"]["applied"] and not row["config"]["drift"] and not row["config"]["error"]
    assert row["notice"]

    _off()
    assert _tree(home) == before
    _off()  # off again changes nothing
    assert _tree(home) == before


def test_the_users_own_files_are_kept_byte_for_byte(home) -> None:
    from openprogram.models.gateway.agents.claude_desktop import PROFILE_ID
    _write(home.config, '{\n  "globalShortcut": "Alt+Space",\n  "deploymentMode": "1p",\n'
                        '  "mcpServers": {\n    "fs": {\n      "command": "npx"\n    }\n  }\n}\n')
    _write(home.config3p, '{\n  "preferences": {\n    "theme": "dark"\n  }\n}\n')
    _write(home.meta, '{\n  "appliedId": "mine-2",\n  "entries": [\n    {\n      "id": "mine-1",\n'
                      '      "name": "Bedrock"\n    },\n    {\n      "id": "mine-2",\n'
                      '      "name": "Corp gateway"\n    }\n  ]\n}\n')
    mine = home.library / "mine-2.json"
    _write(mine, '{"inferenceProvider":"gateway","inferenceGatewayBaseUrl":"https://corp.example"}')
    before = _tree(home)

    _on()
    config = _json(home.config)
    assert config["deploymentMode"] == "3p" and config["globalShortcut"] == "Alt+Space"
    three = _json(home.config3p)
    assert three["deploymentMode"] == "3p" and three["preferences"] == {"theme": "dark"}
    assert three["mcpServers"] == {"fs": {"command": "npx"}}  # carried into 3p mode
    meta = _json(home.meta)
    assert meta["appliedId"] == PROFILE_ID and [e["id"] for e in meta["entries"]] == ["mine-1", "mine-2", PROFILE_ID]
    assert mine.read_text() == before[str(mine)]
    on = _tree(home)
    _on()
    assert _tree(home) == on  # on twice is on once

    _off()
    assert _tree(home) == before
    _on()
    assert _tree(home) == on  # on after off is the first on again


def test_mode_is_checked_as_desktop_reads_it(home) -> None:
    from openprogram.models.gateway.agents import engine
    from openprogram.models.gateway.edit import edit
    _on()

    def quiet() -> None:
        row = engine.status("claude_desktop")
        assert not row["config"]["error"] and not row["config"]["drift"], row["config"]

    quiet()
    _write(home.config3p, '{"preferences":{"x":1}}')  # Desktop drops it at start
    quiet()
    _write(home.config, "{}")
    quiet()
    _write(home.config3p, '{"deploymentMode":"1p"}')  # the chooser is off: still 3p
    quiet()
    home.prof.write_text(edit("json", home.prof.read_text(), sets={"disableDeploymentModeChooser": False}))
    assert "is 1p now" in engine.status("claude_desktop")["config"]["error"]
    _write(home.config3p, "{}")
    quiet()
    meta = _json(home.meta)
    meta["appliedId"] = "mine"
    _write(home.meta, json.dumps(meta))
    assert "another gateway" in engine.status("claude_desktop")["config"]["error"]


def test_a_sealed_key_is_no_drift(home) -> None:
    from openprogram.models.gateway.agents import engine
    _on()
    prof = _json(home.prof)
    prof["inferenceGatewayApiKey"] = "__sealed__:v1:AAAA"
    home.prof.write_text(json.dumps(prof))
    assert not engine.status("claude_desktop")["config"]["drift"]
    assert engine.owned("claude_desktop")


def test_the_users_settings_in_the_profile_outlive_off(home) -> None:
    from openprogram.models.gateway.edit import edit
    home.dir.mkdir(parents=True)
    _on()
    home.prof.write_text(edit("json", home.prof.read_text(),
                              sets={"userAutoMode": True, "toolSearchEnabled": False}))
    _on()
    prof = _json(home.prof)
    assert prof["userAutoMode"] is True and prof["toolSearchEnabled"] is False
    _off()
    assert not home.prof.exists()
    _on()
    prof = _json(home.prof)
    assert prof["userAutoMode"] is True and prof["toolSearchEnabled"] is False
    assert prof["inferenceProvider"] == "gateway"


def test_language_and_servers_go_back_as_they_were(home) -> None:
    _write(home.app, '{"locale":"zh-CN"}')
    _write(home.config, '{"mcpServers":{"fs":{"command":"npx"},"git.v2":{"command":"git"}}}')
    _write(home.config3p, '{"mcpServers":{"fs":{"command":"own"}}}')
    before = _tree(home)
    _on()
    assert _json(home.app3p) == {"locale": "zh-CN"}
    assert _json(home.config3p)["mcpServers"] == {"fs": {"command": "own"}, "git.v2": {"command": "git"}}
    _off()
    assert _tree(home) == before


def test_tier_picks_go_in_the_profile_list(home) -> None:
    from openprogram.models.gateway.agents import engine
    _on(haiku="deepseek/deepseek-chat", sonnet="deepseek/deepseek-chat")
    prof = _json(home.prof)
    first = prof["inferenceModels"][0]
    assert first["anthropicFamilyTier"] == "sonnet" and first["behavesAs"] == "claude-sonnet-5-5"
    assert first["name"].startswith("anthropic/openprogram-") and first["labelOverride"] == "DeepSeek Chat"
    assert prof["modelDiscoveryEnabled"] is True  # haiku is given by /v1/models
    assert not engine.status("claude_desktop")["config"]["drift"]
    _on()
    prof = _json(home.prof)
    assert "inferenceModels" not in prof and "modelDiscoveryEnabled" not in prof
    # the user's own list is never written over
    from openprogram.models.gateway.edit import edit
    home.prof.write_text(edit("json", home.prof.read_text(), sets={"inferenceModels": [{"name": "mine"}]}))
    _on(sonnet="deepseek/deepseek-chat")
    assert _json(home.prof)["inferenceModels"] == [{"name": "mine"}]


# The gateway's side

def test_desktop_accepts_only_anthropic_looking_ids() -> None:
    from openprogram.models.gateway.desktop import accepts, knows_mythos
    assert accepts("claude-opus-4-8") and accepts("sonnet-4") and accepts("anthropic/openprogram-1")
    assert not accepts("anthropic/deepseek/deepseek-flash") and not accepts("gpt-5") and not accepts("glm-5")
    assert accepts("mythos-openprogram-0123456789")
    assert knows_mythos("Mozilla/5.0 Claude/2.7032.0 Chrome/1") and not knows_mythos("Mozilla/5.0 Claude/1.6608.2")
    assert knows_mythos("claude-cli/2.1.0 (external, claude-desktop)")


def test_models_are_listed_by_ids_desktop_keeps(home) -> None:
    from openprogram.models.gateway import desktop
    rows = desktop.ordered([dict(r) for r in ROWS], {"model": "openrouter/gpt-6"})
    data = desktop.models(rows, {"model": "openrouter/gpt-6"})
    by_name = {m["display_name"]: m for m in data}
    assert data[0]["display_name"] == "GPT-6"  # the picked model first
    gpt = by_name["GPT-6"]
    assert gpt["id"].startswith("mythos-openprogram-") and gpt["id"].endswith("[1m]")
    assert gpt["description"] == "400K context window"
    opus = by_name["Claude Opus 5.5"]
    assert opus["id"].startswith("openprogram-") and opus["id"].endswith(".anthropic.claude-opus-5-5")
    assert opus["description"] == "anthropic"
    assert by_name["DeepSeek Chat"]["id"].startswith("anthropic/openprogram-")
    assert by_name["Claude Haiku 5.5"]["id"] == "anthropic/claude-haiku-5-5"
    assert all(desktop.accepts(m["id"]) for m in data)
    old = desktop.models(rows, {}, mythos=False)
    assert old[0]["id"].startswith("anthropic/openprogram-")
    for row, entry in zip(rows, data):
        assert desktop.catalog_id(entry["id"], rows, {}).removesuffix("[1m]") == row["id"]
    # an alias stays the model's whatever else is listed
    assert desktop.alias_number("deepseek/deepseek-chat") == desktop.alias_number("deepseek/deepseek-chat")
    assert len(desktop.alias_number("x")) == 10


def test_a_model_picked_for_two_tiers_gets_a_row_per_tier(home) -> None:
    from openprogram.models.gateway import desktop
    picks = {"sonnet": "deepseek/deepseek-chat", "haiku": "deepseek/deepseek-chat"}
    rows = [dict(r) for r in ROWS]
    data = desktop.models(rows, picks)
    assert data[0]["anthropic_family_tier"] == "sonnet"
    extra = desktop.tier_rows(rows, data, picks)
    assert len(extra) == 1 and extra[0]["anthropic_family_tier"] == "haiku"
    assert extra[0]["display_name"] == "DeepSeek Chat · haiku" and extra[0]["id"] != data[0]["id"]
    assert desktop.catalog_id(extra[0]["id"], rows, picks) == "deepseek/deepseek-chat"
    assert desktop.discovers(picks)


def test_names_that_read_alike_are_told_apart() -> None:
    from openprogram.models.gateway.desktop import names
    rows = [{"id": "a/x", "name": "X"}, {"id": "b/x", "name": "X"}, {"id": "c/y", "name": "Y"}]
    assert names(rows) == ["X (a/x)", "X (b/x)", "Y"]
    assert names([{"id": "a/x", "name": "X"}, {"id": "b/z", "name": "X"}]) == ["X (x)", "X (z)"]


def test_requests_go_to_the_model_meant(home) -> None:
    from openprogram.models.gateway import desktop
    rows = desktop.ordered([dict(r) for r in ROWS], {"model": "deepseek/deepseek-chat"})
    picks = {"model": "deepseek/deepseek-chat", "haiku": "openrouter/gpt-6"}
    probe = {"max_tokens": 1, "messages": [{"role": "user", "content": "."}]}
    assert desktop.move("claude-sonnet-9-9", probe, by_claude_code=False, by_subagent=False,
                        rows=rows, picks=picks) == "deepseek/deepseek-chat"
    # Claude Code's own id of a tier: the model picked for it
    tools = {"max_tokens": 32000, "tools": [{"name": "Bash"}], "messages": []}
    assert desktop.move("claude-haiku-4-5-20251001", tools, by_claude_code=True, by_subagent=False,
                        rows=rows, picks=picks) == "openrouter/gpt-6"
    # a chat turn is remembered; a model not served goes to it
    assert desktop.move("anthropic/claude-opus-5-5", tools, by_claude_code=True, by_subagent=False,
                        rows=rows, picks=picks) == "anthropic/claude-opus-5-5"
    assert desktop.move("gone/model", tools, by_claude_code=True, by_subagent=False,
                        rows=rows, picks=picks) == "anthropic/claude-opus-5-5"
    # a title call names a Claude model: the chat's
    title = {"max_tokens": 512, "system": "You write short session titles. …", "messages": []}
    assert desktop.move("claude-sonnet-5-5", title, by_claude_code=True, by_subagent=False,
                        rows=rows, picks={"model": "deepseek/deepseek-chat"}) == "anthropic/claude-opus-5-5"
    # Claude Code's own claude-sonnet beside a chat on a Claude model: that provider's sonnet, else its tier
    assert desktop.served_beside("claude-haiku-5-5", "anthropic/claude-opus-5-5", rows, True) \
        == "anthropic/claude-haiku-5-5"


def test_desktop_requests_on_the_gateway(home, client, provider, monkeypatch) -> None:  # noqa: F811
    from openprogram.models.gateway import conversion, desktop
    from openprogram.models.gateway.keys import token_for
    rows = [{"id": REF, "provider": REF.split("/")[0], "name": "Scripted", "context_window": 64000,
             "thinking_levels": []}]
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: [dict(r) for r in rows])
    key = token_for("claude_desktop")
    headers = {"authorization": f"Bearer {key}", "user-agent": "Mozilla/5.0 Claude/2.7032.0"}
    listed = client.get("/gateway/anthropic/v1/models", headers=headers).json()["data"]
    alias = listed[0]["id"]
    assert alias.startswith("anthropic/openprogram-") and listed[0]["display_name"] == "Scripted"
    plain = client.get("/gateway/anthropic/v1/models").json()["data"]
    assert plain[0]["id"] == REF
    provider.replies.append([("text", "hi")])
    reply = client.post("/gateway/anthropic/v1/messages", headers=headers,
                        json={"model": alias, "max_tokens": 64, "messages": [{"role": "user", "content": "hey"}]})
    assert reply.status_code == 200, reply.text
    assert reply.json()["content"][0]["text"] == "hi"
    assert desktop.catalog_id(alias) == REF


# The isolated Desktop (claudedesktop_isolated_test.go, desktopiso_test.go)

@pytest.mark.skipif(os.uname().sysname != "Darwin", reason="Desktop runs in a folder of its own on macOS only")
def test_isolated_desktop_runs_beside_the_users_own(home, tmp_path, monkeypatch) -> None:
    import plistlib

    from openprogram.models.gateway.agents import claude_desktop as cd
    from openprogram.models.gateway.agents.engine import AgentConfigError
    claude = tmp_path / "Claude.app"
    (claude / "Contents" / "Resources").mkdir(parents=True)
    (claude / "Contents" / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleExecutable": "Claude", "CFBundleIconFile": "electron.icns", "CFBundleShortVersionString": "4.4"}))
    (claude / "Contents" / "Resources" / "electron.icns").write_bytes(b"icns")
    monkeypatch.setattr(cd, "claude_app", lambda: claude)
    monkeypatch.setattr(cd, "_register", lambda bundle: None)
    home.dir.mkdir(parents=True)
    _write(home.dir / "config.json", '{"locale": "fr-FR"}')
    _write(home.dir / "claude_desktop_config.json", '{"mcpServers": {"fs": {"command": "npx"}}}')
    own = _tree(home)

    _on(runs_in="isolated")
    iso = cd.Paths(True)
    assert iso.dir3p == Path.home() / "Library" / "Application Support" / "Claude-OpenProgram-3p"
    assert _json(iso.config3p) == {"deploymentMode": "3p", "mcpServers": {"fs": {"command": "npx"}}}
    assert _json(iso.app3p) == {"locale": "fr-FR"} and _json(iso.prof)["inferenceProvider"] == "gateway"
    assert cd.current_paths().prof == iso.prof and cd.config_path() == iso.config3p
    assert _tree(home) == own  # the user's own Desktop stays as it was
    launcher = cd.launcher_path()
    assert cd.launcher_mine(launcher)
    script = (launcher / "Contents" / "MacOS" / cd.LAUNCHER_EXE).read_text()
    assert "--user-data-dir=\"$dir\"" in script and str(iso.dir3p) in script and str(claude) in script
    assert (launcher / "Contents" / "Resources" / f"{cd.LAUNCHER_EXE}.icns").read_bytes() == b"icns"
    assert "beside it" in _on(runs_in="isolated")["notice"]
    # only the copy's process is OpenProgram's to restart
    assert cd.desktop_instance(f"/A/Claude --user-data-dir={iso.dir3p}")
    assert not cd.desktop_instance(f"/A/Claude --user-data-dir={iso.dir3p}-x")
    assert not cd.desktop_instance("/A/Claude")
    # where it runs changes only while off
    with pytest.raises(AgentConfigError, match="turn Claude Desktop off"):
        _on()

    _write(iso.dir3p / "local-agent-mode-sessions" / "a" / "chat.json", "{}")  # the copy ran
    _off()
    assert not launcher.exists() and (iso.dir3p / "local-agent-mode-sessions" / "a" / "chat.json").exists()
    assert not iso.prof.exists() and not iso.config3p.exists()
    assert cd.desktop_instance("/A/Claude") and not cd.desktop_instance(f"/A/Claude --user-data-dir={iso.dir3p}")
    # an app by that name that isn't OpenProgram's is never written over
    (launcher / "Contents").mkdir(parents=True)
    (launcher / "Contents" / "Info.plist").write_text("<string>com.someone.else</string>")
    with pytest.raises(AgentConfigError, match="isn't OpenProgram's"):
        _on(runs_in="isolated")
    assert (launcher / "Contents" / "Info.plist").read_text() == "<string>com.someone.else</string>"


# Where Desktop reaches the gateway (claudedesktop_gateway_test.go)

def test_desktop_pointed_at_the_https_address(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import claude_desktop as cd
    from openprogram.models.gateway.agents import engine
    monkeypatch.setattr(cd, "remote_urls", lambda: [])
    assert cd._gateway_choices() == []
    home.dir.mkdir(parents=True)
    _on()
    assert _json(home.prof)["inferenceGatewayBaseUrl"] == "http://127.0.0.1:18100/gateway/anthropic"
    url = "https://openprogram-mac.tail1.ts.net"
    monkeypatch.setattr(cd, "remote_urls", lambda: [url])
    assert cd._gateway_choices() == [(url, url)]
    row = _on(gateway=url)
    assert _json(home.prof)["inferenceGatewayBaseUrl"] == url + "/gateway/anthropic"
    assert not row["config"]["drift"] and cd._check() == ""
    # the address changed since: the current one; none any more: loopback
    monkeypatch.setattr(cd, "remote_urls", lambda: ["https://other.tail1.ts.net"])
    engine.sync("claude_desktop")
    assert _json(home.prof)["inferenceGatewayBaseUrl"] == "https://other.tail1.ts.net/gateway/anthropic"
    monkeypatch.setattr(cd, "remote_urls", lambda: [])
    engine.sync("claude_desktop")
    assert _json(home.prof)["inferenceGatewayBaseUrl"] == "http://127.0.0.1:18100/gateway/anthropic"
    # no allowlist of SSH hosts is written: that policy is the user's
    assert "sshHostAllowlist" not in _json(home.prof)
