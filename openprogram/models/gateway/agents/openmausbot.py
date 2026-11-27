"""OpenMausBot (``$OMB_DATA_DIR/config.json``, default ``~/.openmausbot``).

After yetone/magpie ``internal/agent/openmausbot.go`` (MIT License,
Copyright (c) yetone).

OpenMausBot (milind-soni/OpenMausBot, an Electron chat app whose bots run the
CLIs installed on the computer) keeps its settings in config.json in its data
folder: ``$OMB_DATA_DIR``, else ``~/.openmausbot`` on every system. Each
engine a bot can run on is an instance of a driver in that file's
``instances`` map; with none saved it runs its default fleet.

Its Claude bots run Claude Code isolated, with only the sign-in keys of
``~/.claude/settings.json`` carried over, so a Claude Code wired to the
gateway reached it already, but its picker listed Claude's own models alone.
So OpenProgram is an engine of its own there, ``openprogram``, written as
OpenMausBot's own "Claude endpoint" writes one: a claudeAgent instance with
access ``custom``, a Claude home of its own under the data folder,
``requireApiKey``, the gateway's address and a key naming OpenMausBot as
``ANTHROPIC_BASE_URL``, ``ANTHROPIC_AUTH_TOKEN`` and ``ANTHROPIC_API_KEY``,
and every enabled model in ``endpointModels``, its picker. An instance with
any of those keys owns its routing: neither the user's settings nor a
provider switch in OpenMausBot's environment reaches it.

A saved map is the whole fleet: one holding OpenProgram's alone would take
every other engine away. So when the file has none, OpenProgram writes the
default fleet beside its own, as OpenMausBot itself does when the user adds
an engine, and turning it off takes the map out again while it is still that
fleet (the map is written whole as well as the engine's keys, and only a map
still as written is taken out).

OpenMausBot reads the file when it starts, and writes its whole map of
engines, as it holds it, when the user changes one in its Settings. So
connecting is refused while it runs, where OpenProgram can tell; a sync
writes anyway, and one OpenMausBot writes over is written again by the
keeper (:func:`keep`). One running on an older list than the file's offers a
Restart on its row.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from .. import edit
from ..edit import ConfigText, ConfigUnreadable
from .base import Agent, App, Field, Wiring, Write

ENGINE_ID = "openprogram"
ENGINE = f"instances.{ENGINE_ID}"

#: OpenMausBot's DEFAULT_FLEET (server/instance-fleets.ts, API_KEY_FLEET in
#: its place), the engines it runs while its file saves none.
FLEET: dict[str, Any] = json.loads("""{
  "grok": {"driver": "grokAgent"},
  "kimi": {"driver": "kimiAgent"},
  "droid": {"driver": "droidAgent"},
  "cursor": {"driver": "cursorAgent"},
  "githubCopilot": {"driver": "copilotAgent"},
  "commandCode": {"driver": "commandCodeAgent"},
  "claude": {"driver": "claudeAgent"},
  "codex": {"driver": "codex"},
  "chatgpt": {"driver": "codex", "displayName": "ChatGPT plan", "config": {"authMode": "chatgpt-plan"}},
  "antigravity": {"driver": "antigravityAgent"},
  "opencodeGo": {"driver": "opencodeGo"},
  "openaiCompat": {"driver": "openai-compat"},
  "mistral": {"driver": "mistral"},
  "cerebras": {"driver": "cerebras"},
  "groq": {"driver": "groq"},
  "greenference": {"driver": "greenference"},
  "meta": {"driver": "meta"},
  "deepseek": {"driver": "deepseekAgent"},
  "museCode": {"driver": "museCode"},
  "openai": {"driver": "openai-compat", "displayName": "OpenAI", "access": "api", "icon": {"kind": "preset", "preset": "openai"},
    "config": {"url": "https://api.openai.com/v1", "apiKeyEnv": "OMB_OPENAI_API_KEY", "catalog": "openai"}},
  "claudeApi": {"driver": "claudeAgent", "displayName": "Claude (API key)", "access": "api", "icon": {"kind": "preset", "preset": "anthropic"},
    "config": {"requireApiKey": true}},
  "xaiApi": {"driver": "grok", "displayName": "xAI", "access": "api", "icon": {"kind": "preset", "preset": "xai"}},
  "openrouter": {"driver": "openai-compat", "displayName": "OpenRouter", "access": "api", "icon": {"kind": "preset", "preset": "openrouter"},
    "config": {"url": "https://openrouter.ai/api/v1", "apiKeyEnv": "OMB_OPENROUTER_API_KEY"}},
  "qwen": {"driver": "qwenAgent"},
  "hermes": {"driver": "hermesAgent"},
  "cline": {"driver": "clineAgent"},
  "pi": {"driver": "piAgent"}
}""")

#: The model id OpenMausBot takes for a Claude endpoint (CLAUDE_MODEL_ID);
#: one other id in the list makes it refuse the whole engine.
MODEL_ID = re.compile(r"^[a-z0-9][a-z0-9._:/-]*(?:\[1m\])?$", re.IGNORECASE)

#: The environment that picks Claude Code's endpoint and sign-in
#: (CLAUDE_ROUTING_ENV): OpenProgram's on its engine, so none of the user's stays.
ROUTING = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_CUSTOM_HEADERS",
           "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")

#: OpenMausBot's app as ps lists it: the Mac bundle's executable, the Linux one.
PROCESSES = (r"OpenMausBot\.app/Contents/MacOS/OpenMausBot( |$)", r"^([^ ]*/)?openmausbot( |$)")

#: OpenMausBot's macOS bundle (electron-builder.yml's appId).
APP = App("com.openmausbot.app", ("OpenMausBot.app",))


def data_dir() -> Path:
    """``$OMB_DATA_DIR`` (only an absolute path: OpenMausBot takes it as written), else ``~/.openmausbot``."""
    value = (os.environ.get("OMB_DATA_DIR") or "").strip()
    if value and os.path.isabs(value):
        return Path(value)
    return Path.home() / ".openmausbot"


def config_path() -> Path:
    return data_dir() / "config.json"


def windows_app() -> bool:
    """OpenMausBot's per-user Windows install (NSIS, the package's name under Programs)."""
    local = os.environ.get("LOCALAPPDATA")
    return sys.platform == "win32" and bool(local) and \
        (Path(local) / "Programs" / "openmausbot" / "OpenMausBot.exe").is_file()


def running() -> bool:
    """OpenMausBot is open where OpenProgram can tell (not on Windows)."""
    if sys.platform == "win32":
        return False
    from .running import any_running
    return any_running(*PROCESSES)


def models(at: Wiring) -> list[str]:
    """The models OpenMausBot's picker lists for the engine, the one picked
    first; an id OpenMausBot wouldn't take is left out."""
    out: list[str] = []
    for ref in [at.model, *(row["id"] for row in at.models)]:
        if ref and MODEL_ID.match(ref) and ref not in out:
            out.append(ref)
    return out


def _cli(doc: dict[str, Any]) -> str:
    """The Claude Code the user gave OpenMausBot: the first claudeAgent with a
    binary that is no endpoint."""
    instances = doc.get("instances") if isinstance(doc.get("instances"), dict) else {}
    for engine_id in sorted(instances):
        engine = instances[engine_id] if isinstance(instances[engine_id], dict) else {}
        config = engine.get("config") if isinstance(engine.get("config"), dict) else {}
        if engine_id == ENGINE_ID or engine.get("driver") != "claudeAgent" or "endpointModels" in config:
            continue
        if isinstance(config.get("cli"), str) and config["cli"]:
            return config["cli"]
    return ""


def _engine(at: Wiring, doc: dict[str, Any], listed: list[str]) -> dict[str, Any]:
    """The engine's keys OpenProgram writes, dotted under :data:`ENGINE`."""
    key = at.key  # a key naming OpenMausBot: the Claude Code it runs says nothing of it
    sets: dict[str, Any] = {
        "driver": "claudeAgent", "access": "custom",
        "config.configDir": str(data_dir() / "providers" / ENGINE_ID),
        "config.requireApiKey": True,
        "config.endpointModels": listed,
        "environment.ANTHROPIC_BASE_URL": at.base("anthropic"),
        "environment.ANTHROPIC_AUTH_TOKEN": key,
        # Claude Code's own sign-in check needs a key it knows, as on its endpoints
        "environment.ANTHROPIC_API_KEY": key,
        "environment.ANTHROPIC_MODEL": listed[0],
    }
    if not edit.get_path(doc, f"{ENGINE}.displayName")[0]:
        sets["displayName"] = "OpenProgram"  # the user's once they renamed it
    cli = _cli(doc)
    if cli:
        sets["config.cli"] = cli
    return sets


def _wire(at: Wiring) -> list[Write]:
    from .engine import AgentConfigError
    path = config_path()
    if not at.previous and running():
        raise AgentConfigError("OpenMausBot writes its engines back over what OpenProgram writes now: "
                               "quit OpenMausBot, then try again")
    listed = models(at)
    if not listed:
        raise AgentConfigError("no model OpenMausBot can list: enable a model first")
    try:
        doc = edit.load("json", ConfigText.read(path).text, str(path))
    except ConfigUnreadable:
        doc = {}  # the engine reads it again and refuses
    present, instances = edit.get_path(doc, "instances")
    if present and not isinstance(instances, dict):
        raise AgentConfigError(f"{path}'s instances isn't a map of engines, so OpenProgram leaves it alone")
    engine = _engine(at, doc, listed)
    sets: dict[str, Any] = {f"{ENGINE}.{key}": value for key, value in engine.items()}
    deletes = [f"{ENGINE}.environment.{name}" for name in ROUTING
               if name not in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")]
    # the default fleet beside the engine when the file saves none, written
    # whole: off takes it out again while it is still that fleet
    wrote = ((at.previous.get("files") or {}).get(str(path)) or {}).get("wrote") or {}
    ours = wrote.get("instances", {}).get("set") if isinstance(wrote.get("instances"), dict) else None
    if not instances or (ours is not None and instances == ours):
        current = edit.get_path(doc, ENGINE)[1]
        whole = dict(FLEET) if not instances else {k: v for k, v in instances.items() if k != ENGINE_ID}
        mine: dict[str, Any] = dict(current) if isinstance(current, dict) else {}
        for key, value in engine.items():
            edit.set_path(mine, key, value)
        for name in deletes:
            edit.delete_path(mine, name[len(ENGINE) + 1:])
        whole[ENGINE_ID] = mine
        sets = {"instances": whole, **sets}
    return [Write(path, "json", sets=sets, deletes=deletes, listing=True)]


def _notice() -> str:
    if running():
        return ("OpenMausBot reads its engines when it starts and writes back the ones it holds when you change "
                "one in it — quit OpenMausBot before changing this, then open it and pick OpenProgram as a "
                "bot's engine.")
    return ""


def keep() -> None:
    """Write the engine's models again when OpenMausBot wrote a list over them
    (magpie ``KeepOpenMausBotListed``): it writes back the one it held since
    it started when the user changes an engine in it. The model picked stays."""
    from . import find
    from .engine import AgentConfigError, sync, wiring
    from .. import stash
    record = stash.entry("openmausbot") or {}
    agent_ = find("openmausbot")
    if agent_ is None or not record:
        return
    at = wiring(agent_, dict(record.get("values") or {}))
    want = models(at)
    try:
        doc = edit.load("json", ConfigText.read(config_path()).text, str(config_path()))
    except ConfigUnreadable:
        return
    if edit.get_path(doc, f"{ENGINE}.environment.ANTHROPIC_BASE_URL")[1] != at.base("anthropic"):
        return  # not on this instance's gateway: another OpenProgram's, or taken off by hand
    have = edit.get_path(doc, f"{ENGINE}.config.endpointModels")[1]
    if not want or (isinstance(have, list) and sorted(have) == sorted(want)):
        return
    try:
        sync("openmausbot")
    except AgentConfigError:
        pass


def agent() -> Agent:
    return Agent(
        id="openmausbot", name="OpenMausBot", icon="openmausbot", aliases=("omb", "openmaus", "open-maus-bot"),
        kind="app", protocol="anthropic", dir=data_dir, path=config_path, fields=[Field("model", "Model")],
        detect=windows_app, named_key=True, wire=_wire, notice=_notice, app=APP, keep=keep,
    )
