"""OpenPencil (ZSeven-W/openpencil, the openpencil-desktop app; ``<config dir>/openpencil/settings.json``).

After yetone/magpie ``internal/agent/openpencil.go`` (MIT License, Copyright
(c) yetone). OpenPencil is a design canvas whose chat runs models through
"builtin agents" of the user's own: an Anthropic or OpenAI-compatible
endpoint, a key and a list of models, each listed in its chat's model
picker. It keeps them in one settings.json in the platform's config folder,
``dirs::config_dir()`` joined with ``openpencil``: ``~/Library/Application
Support/openpencil`` on a Mac, ``$XDG_CONFIG_HOME/openpencil``
(``~/.config/openpencil``) on Linux, ``%APPDATA%\\openpencil`` on Windows
(op-editor-host-core's settings_io.rs)::

    {"version": 1, …,
     "builtin_agents": [{"id": "openprogram", "preset": "custom", "display_name": "OpenProgram",
       "kind": "openai-compat", "api_key": "<key naming OpenPencil>",
       "model": "<first model>", "models": ["<model>", …],
       "base_url": "http://127.0.0.1:<port>/…/v1", "enabled": true}], …}

``version`` is required (no serde default): a file without it fails to
parse, is set aside as ``settings.json.corrupt-<time>``, and OpenPencil then
refuses to save at all; a version other than 1 has the whole file ignored.
So OpenProgram writes version 1 in a file it makes, and leaves alone a file
whose version it doesn't know. ``kind`` openai-compat posts to ``base_url +
/chat/completions``. ``models`` is the list (at most 64, written only when
there are two or more, the first again as ``model``); v0.8.4 knows only
``model`` and lists the first model alone.

OpenPencil reads the file once, when it starts, and writes the whole of it
back from what it holds when a setting changes and when it quits. A change
made while it runs is written over when it quits, so one is refused then,
where that can be told (not on Windows, where the notice says so instead).

The model is picked in OpenPencil's own chat picker
(``builtin:openprogram:<model>``), not in a file, so what connecting sets is
whether OpenProgram's models are in that picker: one entry of
``builtin_agents``, id ``openprogram``. The user's other entries and settings
stay, and so do the name and the on/off they give OpenProgram's entry in
OpenPencil's settings. Its User-Agent isn't known, so its requests are told
apart by their key.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import Agent, App, Members, Wiring, Write, app_bundles

ID = "openprogram"
#: How many models OpenPencil keeps for one entry (MAX_BUILTIN_AGENT_MODELS);
#: it drops the rest when it next saves.
MAX_MODELS = 64
APP = App("com.zseven-w.openpencil", ("OpenPencil.app",))
#: Its process, its binary's name on every system.
PROCESS = r"(^|/)openpencil-desktop( |$)"


def home(platform: str | None = None) -> Path:
    """Where Rust's ``dirs::config_dir()`` puts OpenPencil's folder."""
    platform = platform or sys.platform
    user = Path.home()
    if platform == "darwin":
        return user / "Library" / "Application Support" / "openpencil"
    if platform == "win32":
        return Path(os.environ.get("APPDATA") or str(user / "AppData" / "Roaming")) / "openpencil"
    return Path(os.environ.get("XDG_CONFIG_HOME") or str(user / ".config")) / "openpencil"


def config_path() -> Path:
    return home() / "settings.json"


def entry(models: list[dict[str, Any]], key: str, v1: str) -> dict[str, Any]:
    """OpenProgram's entry, fields in the order OpenPencil writes one, the
    models as it keeps a list (normalize_builtin_models)."""
    ids: list[str] = []
    for model in models:
        mid = model.get("id") or ""
        if not mid or mid in ids or len(mid) > 256:
            continue
        ids.append(mid)
        if len(ids) == MAX_MODELS:
            break
    out: dict[str, Any] = {"id": ID, "preset": "custom", "display_name": "OpenProgram", "kind": "openai-compat",
                           "api_key": key, "model": ids[0] if ids else ""}
    if len(ids) > 1:
        out["models"] = ids
    out["base_url"] = v1
    out["enabled"] = True
    return out


def running() -> bool:
    """Open where that can be told; on Windows, no, and the notice asks to quit it first."""
    from .running import any_running
    return sys.platform != "win32" and any_running(PROCESS)


def _wire(at: Wiring) -> list[Write]:
    from .engine import AgentConfigError
    path = config_path()
    if running():
        raise AgentConfigError("OpenPencil writes its settings back when it quits, over what OpenProgram writes now: "
                               "quit OpenPencil, then try again")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    want = entry(at.models, at.key, at.base("openai-chat"))
    sets: dict[str, Any] = {}
    if text.strip():
        try:
            doc = json.loads(text)
        except ValueError:
            doc = None
        if not isinstance(doc, dict):
            raise AgentConfigError(f"{path} isn't JSON OpenPencil reads")
        version = doc.get("version")
        if type(version) is not int or version != 1:
            raise AgentConfigError(f"{path} says version {json.dumps(version)}, not the 1 OpenPencil reads, "
                                   "so OpenProgram leaves it alone")
        agents = doc.get("builtin_agents")
        if agents is not None and not isinstance(agents, list):
            raise AgentConfigError(f"{path}: builtin_agents is not a list")
        # an entry there already keeps the name and the on/off the user gave it
        for was in agents or []:
            if isinstance(was, dict) and was.get("id") == ID:
                if isinstance(was.get("display_name"), str):
                    want["display_name"] = was["display_name"]
                if isinstance(was.get("enabled"), bool):
                    want["enabled"] = was["enabled"]
    else:
        sets["version"] = 1
    return [Write(path, "json", listing=True, sets=sets, members={"builtin_agents": Members(
        # a name or an on/off the user gives it in OpenPencil is no drift
        {"id": ID}, [want], loose=("display_name", "enabled"))})]


def _notice() -> str:
    from .running import any_running
    if any_running(PROCESS):
        return ("OpenPencil reads its settings when it starts and writes them all back when it quits — quit OpenPencil "
                "before changing this, then open it and pick an OpenProgram model in its chat's model picker.")
    return ""


def _windows_app() -> bool:
    """Its installer puts it in %ProgramFiles%\\OpenPencil."""
    if sys.platform != "win32":
        return False
    for name in ("ProgramW6432", "ProgramFiles"):
        folder = os.environ.get(name)
        if folder and (Path(folder) / "OpenPencil" / "openpencil-desktop.exe").is_file():
            return True
    return False


def _detect() -> bool:
    """Its settings folder once it has run, ~/.openpencil once its op CLI or
    MCP server has, else its app. No command: op is its CLI's name, and other
    tools' too."""
    return (home().is_dir() or (Path.home() / ".openpencil").is_dir() or bool(app_bundles(APP))
            or _windows_app())


def agent() -> Agent:
    return Agent(
        id="openpencil", name="OpenPencil", icon="generic", aliases=("open-pencil", "openpencil-desktop"),
        kind="app", protocol="openai-chat", dir=home, path=config_path, named_key=True, detect=_detect,
        wire=_wire, notice=_notice,
    )
