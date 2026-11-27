"""JetBrains Air, JetBrains' agentic development environment.

After yetone/magpie ``internal/agent/air.go`` (MIT License, Copyright (c)
yetone). Air runs its own agents (Claude Agent, Codex, Gemini CLI, Junie) on
the accounts signed in under Settings | Account | AI Providers, with no base
URL of its own to point at the gateway. What it does take is any agent that
speaks the Agent Client Protocol, listed in ``acp.json`` in its config
folder — ``~/Library/Application Support/JetBrains/Air`` on macOS,
``$XDG_CONFIG_HOME/JetBrains/Air`` on Linux, ``%APPDATA%\\JetBrains\\Air`` on
Windows — as::

    {"agent_servers": {"<name in Air's agent menu>": {"command": …, "args": […], "env": {…}}}}

and the models such an agent reports at session/new are its model menu in
Air. So OpenProgram adds an agent of its own there, "OpenProgram": OpenCode's
ACP server (``opencode acp``), handed with ``OPENCODE_CONFIG`` a config file
kept beside acp.json that has OpenProgram's provider, its models, the one
picked as the model a task starts on, and ``enabled_providers`` narrowed to
OpenProgram, so Air's menu lists OpenProgram's models and nothing of
OpenCode's own. OpenCode merges that file over the user's own config and
leaves the rest of it as it is. Air's own agents, and the accounts they run
on, aren't touched.

One gap is OpenCode's: when a provider's key is in the environment it is
started with (OPENAI_API_KEY, DEEPSEEK_API_KEY, … set, not empty), 2.0.20's
ACP server lists that provider and none of its config's, so OpenProgram's
models don't show.

Not ported: magpie's Air in WSL (``air_wsl.go``); OpenProgram has no WSL agents.
"""
from __future__ import annotations

import glob
import os
import sys
from pathlib import Path
from typing import Callable

from .base import Agent, Field, Wiring, Write, find_tool
from .opencode_format import LEGACY_KEY, PROVIDER_ID, config_home, kept, provider_block, read_doc, ref

#: The name OpenProgram's agent has in acp.json, and so in Air's agent menu.
AIR_AGENT = "OpenProgram"
ENTRY = f"agent_servers.{AIR_AGENT}"
#: The OpenCode config OpenProgram's agent runs on, beside acp.json.
OPENCODE_FILE = "openprogram-opencode.json"


def home() -> Path:
    """Where Air keeps its settings and acp.json."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "JetBrains" / "Air"
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / "JetBrains" / "Air"
    return config_home() / "JetBrains" / "Air"


def config_path() -> Path:
    return home() / "acp.json"


def acp_opencode(name: str = "opencode") -> str:
    """An OpenCode a JetBrains IDE or Zed installed from the ACP registry,
    unpacked into the editor's own folder and on no PATH; the newest when
    there are several, ``""`` when none (magpie ``acpOpenCode``)."""
    user = Path.home()
    if sys.platform == "darwin":
        patterns = [user / "Library" / "Caches" / "JetBrains" / "*" / "acp-agents" / "opencode" / "*" / name,
                    user / "Library" / "Application Support" / "Zed" / "external_agents" / "registry" / "opencode" / "*" / name]
    elif sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA") or str(user / "AppData" / "Local"))
        patterns = [local / "JetBrains" / "*" / "acp-agents" / "opencode" / "*" / name,
                    local / "Zed" / "external_agents" / "registry" / "opencode" / "*" / name]
    else:
        cache = Path(os.environ.get("XDG_CACHE_HOME") or str(user / ".cache"))
        data = Path(os.environ.get("XDG_DATA_HOME") or str(user / ".local" / "share"))
        patterns = [cache / "JetBrains" / "*" / "acp-agents" / "opencode" / "*" / name,
                    data / "zed" / "external_agents" / "registry" / "opencode" / "*" / name]
    best, at = "", 0.0
    for pattern in patterns:
        for found in glob.glob(str(pattern)):
            try:
                stat = os.stat(found)
            except OSError:
                continue
            if Path(found).is_file() and (not best or stat.st_mtime > at):
                best, at = found, stat.st_mtime
    return best


def air_opencode() -> str:
    """The OpenCode Air is to start, as a full path, since Air, an app, may
    not have the shell's PATH: the one on PATH, else the one OpenCode's
    installer puts in ``~/.opencode/bin``, else one from the ACP registry;
    ``""`` when there is none."""
    found = find_tool("opencode")
    if found:
        return os.path.abspath(found)
    name = "opencode.exe" if sys.platform == "win32" else "opencode"
    own = Path.home() / ".opencode" / "bin" / name
    if own.is_file():
        return str(own)
    return acp_opencode(name)


def air_wire(acp: Path) -> Callable[[Wiring], list[Write]]:
    """The writes of an Air agent on the acp.json at ``acp``: OpenProgram's
    entry there and its OpenCode config beside it."""
    def wire(at: Wiring) -> list[Write]:
        oc_path = acp.parent / OPENCODE_FILE
        block = kept(read_doc(oc_path), provider_block(at.models, at.base("openai-chat"), at.key))
        model = ref(at.model)
        opencode_sets = {
            "enabled_providers": [PROVIDER_ID],
            # titles and summaries too, as the user's own small model may be a
            # provider left out here
            "small_model": model,
            "model": model,
            "$schema": "https://opencode.ai/config.json",
        }
        entry = {"command": air_opencode() or "opencode", "args": ["acp"], "env": {"OPENCODE_CONFIG": str(oc_path)}}
        # the provider is the model list a sync rewrites, the rest a write of its own
        # the provider is OpenProgram's whole: it goes on disconnect, edited or not
        return [Write(oc_path, "json", sets={LEGACY_KEY: block}, owns=(LEGACY_KEY,), listing=True),
                Write(oc_path, "json", sets=opencode_sets),
                Write(acp, "json", sets={ENTRY: entry})]
    return wire


def air_notice(acp: Path, done: str) -> Callable[[], str]:
    def notice() -> str:
        if not air_opencode():
            return ("Air runs OpenProgram's models through OpenCode's ACP server, and OpenCode isn't installed here: "
                    "install OpenCode, then pick OpenProgram in a new Air task.")
        return done
    return notice


def agent() -> Agent:
    # no bin: air is other tools' name too (Go's live reloader); its requests
    # are its OpenCode's, and counted as OpenCode's
    return Agent(
        id="air", name="JetBrains Air", icon="air", aliases=("jetbrains-air",), kind="app",
        protocol="openai-chat", dir=home, path=config_path, fields=[Field("model", "Model")],
        wire=air_wire(config_path()),
        # its OpenCode config is a file others may read: a key that names it
        named_key=True,
        notice=air_notice(config_path(), "In Air, start a new task and pick OpenProgram in its agent menu: "
                                         "OpenProgram's models are in its model menu."),
    )
