"""A plugin is declarative: its manifest contents mount through one context and
disable or reload undoes all of it; a manifest that names code is refused."""
from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

import pytest

from openprogram.runtime.events import get_event_bus, make_event
from openprogram.extensions.plugins import installer, loader, trust


@pytest.fixture
def plugin_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    from openprogram.programs._host import runtime as _runtime
    saved = _runtime.snapshot_registry()
    with loader._lock:
        loader._plugins.clear()
        loader._errors.clear()
    yield tmp_path
    for p in list(loader._plugins.values()):
        if p.loaded:
            loader._deactivate(p)
    loader._plugins.clear()
    _runtime.restore_registry(saved)


def write_plugin(root: Path, name: str, **manifest) -> Path:
    """A plugin with one slash command, plus whatever ``manifest`` adds."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "commands").mkdir(exist_ok=True)
    (root / "commands" / "greet.md").write_text(
        "---\ndescription: Greets\n---\nSay hello\n", encoding="utf-8")
    (root / "plugin.json").write_text(json.dumps(
        {"name": name, "commands": "./commands", **manifest}), encoding="utf-8")
    return root


def install(root: Path, name: str):
    result = installer.install("path", str(root))
    assert result["success"], result
    trust.set_trust(name, "community")
    loader.rescan()
    return loader.load_plugin(name)


def gate_script(root: Path, body: str) -> str:
    script = root / "gate.py"
    script.write_text(body, encoding="utf-8")
    # The hook runs through the platform shell: cmd.exe knows no single quotes.
    python = f'"{sys.executable}"' if os.name == "nt" else shlex.quote(sys.executable)
    return f"{python} ${{CLAUDE_PLUGIN_ROOT}}/gate.py"


DENY_BASH = "import json,sys\ne=json.load(sys.stdin)\nprint('no bash', file=sys.stderr)\nsys.exit(2)\n"


def test_contents_mount_and_disable_undoes_everything(plugin_home):
    root = write_plugin(plugin_home / "src" / "demo", "demo")
    command = gate_script(plugin_home / "src" / "demo", DENY_BASH)
    (root / "plugin.json").write_text(json.dumps({
        "name": "demo", "commands": "./commands",
        "hooks": {"hooks": {"PreToolUse": [{"matcher": "bash", "hooks": [
            {"type": "command", "command": command}]}]}},
    }), encoding="utf-8")
    p = install(root, "demo")

    assert p.loaded and p.contrib["_commands"][0]["name"] == "greet"
    assert sorted(p.ctx.contributions) == ["commands", "gates"]
    bash = make_event("tool.before", "tool", {"tool": "bash", "args": {}})
    read = make_event("tool.before", "tool", {"tool": "read", "args": {}})
    from openprogram.runtime.events import decide_tool_gate
    assert decide_tool_gate(bash) == "no bash"
    assert decide_tool_gate(read) is None

    loader.unload_plugin("demo")
    assert decide_tool_gate(bash) is None
    assert not p.contrib


def test_a_manifest_with_code_is_refused_at_install_and_at_load(plugin_home):
    from openprogram.programs._host.runtime import all_tools
    before = {t.name for t in all_tools()}
    root = write_plugin(plugin_home / "src" / "coder", "coder", python="./plugin.py")
    (root / "plugin.py").write_text("def setup(ctx):\n    raise SystemExit('ran')\n", encoding="utf-8")

    refused = installer.install("path", str(root))
    assert not refused["success"]
    assert "contains code (python)" in refused["log"]
    assert "openprogram programs install" in refused["log"]
    assert not (plugin_home / "home" / ".openprogram" / "plugins" / "coder").exists()

    # Placed in the plugins directory by hand: listed with the refusal, never run.
    (loader.paths.plugins_dir() / "coder").symlink_to(root, target_is_directory=True)
    trust.set_trust("coder", "verified")
    loader.rescan()
    assert "application.json" in loader.get_plugin("coder").error
    from openprogram.extensions.plugins.manifest import PluginContainsCode
    with pytest.raises(PluginContainsCode, match="contains code"):
        loader.load_plugin("coder")
    assert not loader.get_plugin("coder").loaded
    assert {t.name for t in all_tools()} == before


@pytest.mark.parametrize("extra, named", [
    ({"tools": "./tools"}, "tools"),
    ({"client": "./client.js"}, "client"),
    ({"web": "./web"}, "web"),
    ({"hooks": "hooks_module:HOOKS"}, "hooks (a Python mapping)"),
    ({"sidebar": [{"label": "Demo"}]}, "sidebar"),
])
def test_every_code_key_is_named_in_the_refusal(plugin_home, extra, named):
    from openprogram.extensions.plugins.manifest import code_refusal, parse_manifest_dir
    root = write_plugin(plugin_home / "src" / "x", "x", **extra)
    assert named in code_refusal(parse_manifest_dir(root))


def test_a_hermes_python_plugin_is_refused(plugin_home):
    from openprogram.extensions.plugins.manifest import parse_manifest_dir
    root = plugin_home / "src" / "hermes"
    root.mkdir(parents=True)
    (root / "plugin.yaml").write_text("name: hermes-demo\n", encoding="utf-8")
    (root / "__init__.py").write_text("def register(ctx):\n    pass\n", encoding="utf-8")
    assert "Hermes Python plugin" in parse_manifest_dir(root).code[0]


def test_pip_is_not_a_plugin_source(plugin_home):
    result = installer.install("pip", "some-package")
    assert not result["success"] and "application" in result["log"]


def test_reload_reads_the_files_now_on_disk(plugin_home):
    root = write_plugin(plugin_home / "src" / "demo", "demo")
    install(root, "demo")
    (root / "commands" / "greet.md").write_text("---\ndescription: New\n---\nHi\n", encoding="utf-8")
    p = loader.reload_plugin("demo")
    assert p.contrib["_commands"][0]["prompt"] == "Hi"


def test_rescan_keeps_a_running_plugin_running(plugin_home):
    root = write_plugin(plugin_home / "src" / "demo", "demo")
    install(root, "demo")
    loader.rescan()
    p = loader.get_plugin("demo")
    assert p.loaded and p.ctx is not None
    loader.unload_plugin("demo")
    assert not p.loaded


def test_enabled_plugins_load_at_startup_dependencies_first(plugin_home):
    for name, requires in (("base", []), ("addon", ["base"])):
        write_plugin(plugin_home / "src" / name, name, requires=requires)
        installer.install("path", str(plugin_home / "src" / name))
        trust.set_trust(name, "community")
    (Path(plugin_home / "home" / ".openprogram" / "plugins-enabled.json")
     .write_text(json.dumps(["addon", "base"]), encoding="utf-8"))
    assert loader.load_enabled() == {}
    assert loader.get_plugin("base").loaded and loader.get_plugin("addon").loaded


def test_path_install_links_and_uninstall_removes_only_the_link(plugin_home):
    root = write_plugin(plugin_home / "src" / "demo", "demo")
    install(root, "demo")
    link = plugin_home / "home" / ".openprogram" / "plugins" / "demo"
    assert link.is_symlink() and loader.get_plugin("demo").manifest.source_kind == "link"
    loader.unload_plugin("demo")
    assert installer.uninstall("demo")["success"]
    assert not link.exists() and (root / "plugin.json").is_file()


def test_provider_directories_come_and_go_with_the_plugin(plugin_home):
    from openprogram.models.providers.catalog import metadata
    root = write_plugin(plugin_home / "src" / "prov", "prov", providers="./providers")
    (root / "providers" / "acme").mkdir(parents=True)
    (root / "providers" / "acme" / "provider.json").write_text(json.dumps({
        "id": "acme", "endpoints": {"default": {"api": "openai-completions",
                                                "base_url": "https://acme.test/v1"}}}))
    install(root, "prov")
    assert "acme" in metadata.shipped_provider_ids()
    assert metadata.provider_base_url("acme") == "https://acme.test/v1"
    loader.unload_plugin("prov")
    assert "acme" not in metadata.shipped_provider_ids()


def test_claude_style_mcp_entries_become_our_config():
    from openprogram.programs._host.context import normalize_mcp_entry
    assert normalize_mcp_entry({"command": "npx", "args": ["-y", "srv"]}) == {
        "command": ["npx", "-y", "srv"], "type": "local"}
    assert normalize_mcp_entry({"url": "https://x.test/mcp"})["type"] == "http"


def test_events_are_checked_against_their_kind(plugin_home):
    from openprogram.programs._host.context import ProgramContext
    ctx = ProgramContext("p", plugin_home)
    with pytest.raises(ValueError, match="use ctx.gate"):
        ctx.on("tool.before", lambda e: None)
    with pytest.raises(ValueError, match="unknown event"):
        ctx.on("no.such.event", lambda e: None)
    ctx.dispose()


def test_tool_result_rewriters_change_only_the_text():
    from types import SimpleNamespace
    from openprogram.runtime.agent.loop import _rewrite_tool_result
    from openprogram.models.providers.types import AgentToolResult
    from openprogram.models.providers.types import TextContent
    call = SimpleNamespace(name="read", id="c1", arguments={})
    result = AgentToolResult(content=[TextContent(text="secret: 42")])
    assert _rewrite_tool_result(call, result) is result
    dispose = get_event_bus().subscribe_transform(
        "tool.result", lambda event, text: text.replace("42", "**"))
    try:
        assert _rewrite_tool_result(call, result).content[0].text == "secret: **"
    finally:
        dispose()


def test_inspect_lists_the_declarative_keys_and_the_tiers():
    from openprogram.extensions.plugins.inspect import describe
    facts = describe()
    assert set(facts["manifest"]) >= {"skills", "commands", "hooks", "mcpServers"}
    assert "python" not in facts["manifest"] and "client_slots" not in facts
    assert set(facts["tiers"]) == {"resident", "deferred", "hidden"}
    by_name = {row["name"]: row for row in facts["entries"]}
    assert by_name["tool_search"]["tier"] == "resident"


def test_the_agent_tool_installs_and_reloads_a_plugin(plugin_home, monkeypatch):
    from openprogram.host import sandbox
    monkeypatch.setattr(sandbox, "resolve_policy", lambda: None)
    from openprogram.programs.tools.runtime.plugin import plugin as tool

    def plugin_tool(**args):
        import asyncio
        result = asyncio.run(tool.execute("plugin-test", args, None, None))
        text = result.content[0].text
        value = json.loads(text) if text.lstrip().startswith("{") else {"guide": text}
        return value, result.is_error

    root = write_plugin(plugin_home / "src" / "agent-made", "agent-made")
    assert "hooks" in plugin_tool(action="guide")[0]["guide"]
    installed, failed = plugin_tool(action="install", path=str(root))
    assert not failed and installed["plugin"]["loaded"], installed
    assert installed["plugin"]["contributions"]["commands"] == ["greet"]
    (root / "commands" / "bye.md").write_text("Say goodbye\n", encoding="utf-8")
    reloaded, _ = plugin_tool(action="reload", name="agent-made")
    assert sorted(reloaded["plugin"]["contributions"]["commands"]) == ["bye", "greet"]
    assert plugin_tool(action="uninstall", name="agent-made")[0]["ok"]
    assert (root / "plugin.json").is_file()

    coder = write_plugin(plugin_home / "src" / "coder", "coder", tools="./tools")
    refused, failed = plugin_tool(action="install", path=str(coder))
    assert failed and "application" in refused["error"]
