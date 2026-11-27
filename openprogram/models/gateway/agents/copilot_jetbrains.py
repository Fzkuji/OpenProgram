"""GitHub Copilot in JetBrains IDEs, Bring Your Own Key (the language server's ``byok.json``).

After yetone/magpie ``internal/agent/copilotjetbrains.go`` (MIT License,
Copyright (c) yetone). The github-copilot-intellij plugin (IntelliJ IDEA,
PyCharm, GoLand, …, and Android Studio) takes models of other vendors
through Copilot Chat's model picker, Manage Models…, Add Custom Endpoint
Provider. The plugin keeps none of it itself: the Copilot language server it
bundles keeps every provider in one JSON object, ``byok.json`` in
``$XDG_CONFIG_HOME/github-copilot`` when that is set (and absolute),
``%USERPROFILE%\\AppData\\Local\\github-copilot`` on Windows and
``~/.config/github-copilot`` elsewhere, read again at every use. A custom
endpoint provider ``<name>`` is three keys there, as the server's own
``copilot/byok/saveCustomProviderConfig`` and ``saveModel`` write them::

    "<name>-provider-config": {"groupName": <name>, "apiType": "chatCompletions"}
    "<name>-api-key": "<key, in plain text>"
    "<name>-models-config": {"<model id>": {"deploymentUrl": …, "isRegistered": true,
      "isCustomModel": true, "modelCapabilities": {"name": …, "maxInputTokens": …,
      "maxOutputTokens": …, "toolCalling": true, "vision": …}}}

Connecting adds the provider ``openprogram``: the catalog, each model at the
gateway's ``/chat/completions``, sent with a key naming this agent as a
Bearer token. Tool calling is on, as Copilot's agent mode lists only models
that call tools; a model's ``maxInputTokens`` is the window the server
fills, its output kept within it. A model the user hid in the IDE's Manage
Models (``isRegistered: false``) stays hidden. The user's other providers and
keys stay as they are.

The IDE lists the server's providers when Copilot signs in, so the models
show after the IDE restarts. The model is picked in Copilot Chat, so what is
set here is whether OpenProgram's models are in that picker. BYOK needs a
GitHub sign-in with Copilot (Free, Pro, Pro+; Business or Enterprise where
the organisation allows it); that sign-in is the IDE's and never reaches the
gateway. Its requests say ``GithubCopilot/<server version>`` as every client
of that server does, so they are told apart by their key. Xcode's and
Eclipse's Copilot run the same server and read the same file.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from .base import Agent, Wiring, Write

ID = "copilot-jetbrains"
CONFIG = "openprogram-provider-config"
KEY = "openprogram-api-key"
MODELS = "openprogram-models-config"
#: The folder the plugin is installed in, under an IDE's plugins folder.
PLUGIN = "github-copilot-intellij"


def config_path() -> Path:
    """byok.json where the language server keeps it (``getXdgConfigPath``)."""
    xdg = os.environ.get("XDG_CONFIG_HOME") or ""
    if xdg and os.path.isabs(xdg):
        return Path(xdg) / "github-copilot" / "byok.json"
    home = Path.home()
    if sys.platform == "win32":
        # the server's os.homedir(), USERPROFILE
        return home / "AppData" / "Local" / "github-copilot" / "byok.json"
    return home / ".config" / "github-copilot" / "byok.json"


def plugin_installed() -> bool:
    """Some JetBrains IDE, or Android Studio, has the Copilot plugin: in the
    plugins folder of an IDE's config folder on macOS and Windows, in the
    IDE's data folder itself on Linux."""
    home = Path.home()
    if sys.platform == "darwin":
        base, sub = home / "Library" / "Application Support", "plugins"
    elif sys.platform == "win32":
        base, sub = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming"), "plugins"
    else:
        base, sub = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share"), ""
    for vendor in ("JetBrains", "Google"):
        pattern = f"*/{sub}/{PLUGIN}" if sub else f"*/{PLUGIN}"
        if any(found.is_dir() for found in (base / vendor).glob(pattern)):
            return True
    return False


def _wired() -> bool:
    from .. import edit
    try:
        doc = edit.load("json", edit.ConfigText.read(config_path()).text, str(config_path()))
    except edit.ConfigUnreadable:
        return False
    return CONFIG in doc


def _hidden(path: Path) -> set[str]:
    """The models of OpenProgram's the user hid in the IDE's Manage Models."""
    from .. import edit
    try:
        doc = edit.load("json", edit.ConfigText.read(path).text, str(path))
    except edit.ConfigUnreadable:
        return set()
    current = doc.get(MODELS)
    if not isinstance(current, dict):
        return set()
    return {key for key, value in current.items()
            if isinstance(value, dict) and "isRegistered" in value and not value["isRegistered"]}


def models_config(at: Wiring, hidden: set[str]) -> dict[str, Any]:
    """The catalog as Copilot (JetBrains) is shown it, keyed by model id."""
    url = at.base("openai-chat") + "/chat/completions"
    out = {}
    for model in at.models:
        context = int(model.get("context_window") or 0) or 128000  # the server's default for a custom endpoint
        output = int(model.get("max_tokens") or 0)
        output = min(output, context) if output > 0 else 16000
        # the output is reserved out of the window the prompt fills
        output = min(output, context // 4)
        out[model["id"]] = {
            "deploymentUrl": url, "isRegistered": model["id"] not in hidden, "isCustomModel": True,
            "modelCapabilities": {"name": model.get("name") or model["id"], "maxInputTokens": context,
                                  "maxOutputTokens": output, "toolCalling": True, "vision": bool(model.get("vision"))},
        }
    return out


def _wire(at: Wiring) -> list[Write]:
    path = config_path()
    # made private, as the language server makes it when there is none
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    sets = {CONFIG: {"groupName": "openprogram", "apiType": "chatCompletions"}, KEY: at.key,
            MODELS: models_config(at, _hidden(path))}
    # the three keys are OpenProgram's whole (Write.owns): they go on
    # disconnect, edited in the IDE or not (magpie DelJSON), and a model
    # hidden in Manage Models is no drift
    return [Write(path, "json", sets=sets, listing=True, owns=(CONFIG, KEY, MODELS),
                  loose=(f"{MODELS}.*.isRegistered",))]


def _notice() -> str:
    return ("OpenProgram's models are in Copilot Chat's model picker in your JetBrains IDE, under openprogram, "
            "once the IDE restarts. They need Copilot signed in to GitHub on a plan with Bring Your Own Key: "
            "Copilot Free, Pro and Pro+ have it, Business and Enterprise when the organisation's policy allows it.")


def _detect() -> bool:
    """The folder is every Copilot's (VS Code's sign-in too): the plugin
    installed in an IDE, or OpenProgram's provider written, says it is here."""
    return plugin_installed() or _wired()


def agent() -> Agent:
    return Agent(
        id=ID, name="Copilot (JetBrains)", icon="githubcopilot", kind="ide",
        aliases=("jetbrains", "jetbrains-copilot", "copilot-idea", "copilot-intellij", "intellij", "idea"),
        protocol="openai-chat", dir=lambda: config_path().parent, path=config_path,
        named_key=True, detect=_detect, detect_only=True, wire=_wire, notice=_notice,
    )
