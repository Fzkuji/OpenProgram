"""Obsidian, through Copilot for Obsidian (each vault's ``.obsidian/plugins/copilot/data.json``).

After yetone/magpie ``internal/agent/obsidian.go`` (MIT License, Copyright
(c) yetone). Obsidian has no AI of its own: its community plugins bring it.
This wires the one that takes an OpenAI-compatible endpoint the user names,
Copilot for Obsidian (logancyang/obsidian-copilot, plugin id ``copilot``), in
every vault that has it.

Obsidian lists its vaults in ``obsidian.json`` in its config folder:
``~/Library/Application Support/obsidian`` on a Mac, ``%APPDATA%\\obsidian``
on Windows, ``$XDG_CONFIG_HOME/obsidian`` on Linux, or the Flatpak's and the
Snap's own config folders. A plugin keeps its settings in
``<vault>/.obsidian/plugins/<id>/data.json``, which Obsidian reads when the
plugin loads and writes whole from what the plugin holds whenever it saves.

Copilot 4 (4.0.19's ``src/settings/model.ts``) keeps its endpoints as::

    "providers": {"<id>": {"providerId", "providerType": "openai-compatible",
      "displayName", "baseUrl", "enableCors", "apiKeyKeychainId",
      "requiresApiKey", "origin": {"kind": "byok"}, "addedAt"}},
    "configuredModels": [{"configuredModelId", "providerId",
      "info": {"id", "displayName", "limits", "modalities", "reasoning", …},
      "configuredAt"}],
    "backends": {"chat": {"enabledModels": ["<configuredModelId>", …]}},
    "defaultModelKey": "<configuredModelId>"

A key lives in Obsidian's keychain, which OpenProgram doesn't write, so its
provider has none: ``requiresApiKey`` false is Copilot's keyless custom
endpoint, whose requests carry no Authorization. The settings file is in the
vault, which is synced and often published, so no key goes in it either: the
address is ``/gateway/agent/obsidian/…`` (``Agent.path_named``), which names
Obsidian, since the window's User-Agent is a browser's, and the gateway lets
in a call there with no key only from Obsidian's own window on this computer
(a loopback peer and Host, Origin ``app://obsidian.md``; owner_auth
``_gateway_keyless_app``), answering that window's CORS (magpie ``cors.go``).

OpenProgram's provider is ``openprogram``; its models' ids are made from
theirs (uuid v5), so a sync finds them again: a model the user turned off in
Copilot stays off, a new one comes on, one gone goes with its place in every
backend. The first time, Copilot's default model becomes OpenProgram's
first, and what it was is stashed to put back on disconnect. Copilot 3 kept
models in ``activeModels``; only Copilot 4's are written, so a vault with
Copilot 3 is left alone and named in the error.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from .base import Agent, Members, Wiring, Write

PLUGIN = "copilot"
PROVIDER = "openprogram"
#: Makes OpenProgram's configured model ids in Copilot, one per model id, the
#: same on every sync (uuid v5 of "openprogram/<id>").
MODEL_SPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/Fzkuji/OpenProgram/obsidian/copilot")


def config_dirs() -> list[Path]:
    """Where Obsidian may keep obsidian.json, the one it uses here first."""
    home = Path.home()
    if sys.platform == "darwin":
        return [home / "Library" / "Application Support" / "obsidian"]
    if sys.platform == "win32":
        return [Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming") / "obsidian"]
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    return [cfg / "obsidian", home / ".var" / "app" / "md.obsidian.Obsidian" / "config" / "obsidian",
            home / "snap" / "obsidian" / "current" / ".config" / "obsidian"]


def config_dir() -> Path:
    dirs = config_dirs()
    return next((d for d in dirs if (d / "obsidian.json").is_file()), dirs[0])


def config_path() -> Path:
    return config_dir() / "obsidian.json"


def vaults() -> list[Path]:
    """The vault folders obsidian.json lists that are still there, each once,
    in the order of their ids."""
    out: list[Path] = []
    for folder in config_dirs():
        try:
            listed = json.loads((folder / "obsidian.json").read_text(encoding="utf-8")).get("vaults") or {}
        except (OSError, ValueError, AttributeError):
            continue
        for vault_id in sorted(listed):
            where = (listed[vault_id] or {}).get("path") if isinstance(listed[vault_id], dict) else None
            if not where or not Path(where).is_dir():
                continue
            vault = Path(os.path.normpath(where))
            if vault not in out:
                out.append(vault)
    return out


def plugin_dir(vault: Path) -> Path:
    return vault / ".obsidian" / "plugins" / PLUGIN


def data_path(vault: Path) -> Path:
    return plugin_dir(vault) / "data.json"


def here(vault: Path) -> bool:
    """The vault has Copilot installed."""
    return (plugin_dir(vault) / "manifest.json").is_file()


def _read(path: Path) -> dict[str, Any]:
    from .. import edit
    try:
        return edit.load("json", edit.ConfigText.read(path).text, str(path))
    except edit.ConfigUnreadable:
        return {}


def ours(provider: Any) -> bool:
    """A provider named openprogram is the one OpenProgram wrote: its baseUrl
    is Obsidian's route on the gateway, on whatever port. One the user added
    by hand under that name is theirs."""
    if not isinstance(provider, dict):
        return False
    return str(provider.get("baseUrl") or "").rstrip("/").endswith("/gateway/agent/obsidian/openai/v1")


def wired(vault: Path) -> bool:
    return ours((_read(data_path(vault)).get("providers") or {}).get(PROVIDER))


def major(vault: Path) -> int:
    """The major version Copilot's manifest says, 0 when it says none."""
    try:
        version = str(json.loads((plugin_dir(vault) / "manifest.json").read_text(encoding="utf-8")).get("version") or "")
        return int(version.split(".", 1)[0])
    except (OSError, ValueError, AttributeError):
        return 0


def model_id(model: str) -> str:
    """OpenProgram's configured model id in Copilot for a model id."""
    return str(uuid.uuid5(MODEL_SPACE, f"{PROVIDER}/{model}"))


def configured(models: list[dict[str, Any]], now: int, had: dict[str, Any]) -> list[dict[str, Any]]:
    """The models as Copilot's configured models; one already there keeps its configuredAt."""
    out, seen = [], set()
    for model in models:
        if not model.get("id") or model["id"] in seen:
            continue
        seen.add(model["id"])
        info: dict[str, Any] = {"id": model["id"], "displayName": model.get("name") or model["id"],
                                "modalities": {"input": ["text", "image"] if model.get("vision") else ["text"],
                                               "output": ["text"]}}
        context, output = int(model.get("context_window") or 0), int(model.get("max_tokens") or 0)
        if context or output:
            info["limits"] = {key: value for key, value in (("context", context), ("output", output)) if value}
        if model.get("thinking_levels"):
            info["reasoning"] = True
            info["reasoningEfforts"] = list(model["thinking_levels"])
        configured_id = model_id(model["id"])
        at = (had.get(configured_id) or {}).get("configuredAt")
        out.append({"configuredModelId": configured_id, "providerId": PROVIDER, "info": info,
                    "configuredAt": at if isinstance(at, (int, float)) and not isinstance(at, bool) else now})
    return out


def _vault_write(at: Wiring, vault: Path) -> Write:
    from .engine import AgentConfigError
    path = data_path(vault)
    doc = _read(path)
    now = int(time.time() * 1000)
    providers = doc.get("providers") or {}
    if not isinstance(providers, dict):
        raise AgentConfigError(f"{path}: providers is not an object")
    old = providers.get(PROVIDER)
    first = old is None
    if not first and not ours(old):
        raise AgentConfigError(f"{path}: Copilot has a provider of its own named openprogram, at another address "
                               "— rename it in Copilot's settings, and OpenProgram adds its own")
    provider: dict[str, Any] = {"providerId": PROVIDER, "providerType": "openai-compatible",
                                "displayName": "OpenProgram", "baseUrl": at.base("openai-chat"),
                                "enableCors": False, "apiKeyKeychainId": None, "requiresApiKey": False,
                                "origin": {"kind": "byok"}, "addedAt": now}
    if not first:
        if old.get("displayName"):
            provider["displayName"] = old["displayName"]
        if isinstance(old.get("addedAt"), (int, float)):
            provider["addedAt"] = old["addedAt"]
        if isinstance(old.get("apiKeyKeychainId"), str):
            provider["apiKeyKeychainId"] = old["apiKeyKeychainId"]

    listed = doc.get("configuredModels") or []
    if not isinstance(listed, list):
        raise AgentConfigError(f"{path}: configuredModels is not a list")
    had = {e.get("configuredModelId"): e for e in listed if isinstance(e, dict) and e.get("providerId") == PROVIDER}
    models = configured(at.models, now, had)
    if not models:
        raise AgentConfigError("OpenProgram has no models to give Copilot for Obsidian: enable a model first")
    mine = {m["configuredModelId"] for m in models}

    # each backend's enabled models: the user's as they were, OpenProgram's
    # that were on still on where they were, OpenProgram's gone out, in chat
    # new ones on. Disconnecting takes out only OpenProgram's ids; the list is
    # the user's to curate in Copilot (a model turned off there is no drift).
    slot = f"providers.{PROVIDER}"
    sets: dict[str, Any] = {slot: provider}
    lists: dict[str, Members] = {"configuredModels": Members({"providerId": PROVIDER}, models)}
    match = {"": {"in": sorted(set(had) | mine)}}
    backends = doc.get("backends") or {}
    if not isinstance(backends, dict):
        raise AgentConfigError(f"{path}: backends is not an object")
    for name, backend in backends.items():
        ids = backend.get("enabledModels") if isinstance(backend, dict) else None
        if ids is None and name != "chat":
            continue
        if ids is not None and not (isinstance(ids, list) and all(isinstance(i, str) for i in ids)):
            raise AgentConfigError(f"{path}: backends.{name}: enabledModels is not a list of ids")
        on = [i for i in ids or [] if i in mine]
        if name == "chat":
            on += [m["configuredModelId"] for m in models
                   if m["configuredModelId"] not in had and m["configuredModelId"] not in on]
        if not on and not any(i in match[""]["in"] for i in ids or []):
            continue  # nothing of OpenProgram's in it, before or now: the user's list as it is
        lists[f"backends.{name.replace('.', chr(92) + '.')}.enabledModels"] = Members(
            match, on, keep_place=True, checked=False)
    if "chat" not in backends:
        lists["backends.chat.enabledModels"] = Members(match, [m["configuredModelId"] for m in models],
                                                       checked=False)

    default = doc.get("defaultModelKey")
    if first or (isinstance(default, str) and default in had and default not in mine):
        # the first time, or the default was a model of OpenProgram's that is gone
        sets["defaultModelKey"] = models[0]["configuredModelId"]
    # the provider is OpenProgram's whole (Write.owns): it goes on disconnect,
    # edited in Copilot or not (magpie obsCopilotUnwire), and a name the user
    # gave it there is no drift
    return Write(path, "json", sets=sets, listing=True, members=lists, owns=(slot,),
                 loose=(f"{slot}.displayName",))


def _wire(at: Wiring) -> list[Write]:
    from .engine import AgentConfigError
    writes, errors = [], []
    for vault in vaults():
        if not here(vault):
            continue
        version = major(vault)
        if 0 < version < 4:
            errors.append(f"{plugin_dir(vault)} is Copilot {version}: OpenProgram sets Copilot for Obsidian 4 "
                          "and later — update it in Obsidian's Settings › Community plugins")
            continue
        writes.append(_vault_write(at, vault))
    if not writes:
        raise AgentConfigError("; ".join(errors) or "no Obsidian vault here has Copilot for Obsidian: install "
                               "Copilot from Obsidian's Settings › Community plugins first")
    return writes


def _notice() -> str:
    from .running import any_running
    if any_running(r"(^|/)(Obsidian|obsidian)( |$)"):
        return ("Obsidian holds Copilot's settings while it runs — reload Copilot in Settings › Community plugins "
                "(or restart Obsidian) to see OpenProgram's models, before changing Copilot's settings there.")
    return ""


def _detect() -> bool:
    """Obsidian has no AI of its own: it is listed when a vault has Copilot
    (installed, or OpenProgram's provider already written)."""
    return any(here(vault) or wired(vault) for vault in vaults())


def agent() -> Agent:
    return Agent(
        id="obsidian", name="Obsidian", icon="obsidian", aliases=("obsidian-copilot",), kind="app",
        protocol="openai-chat", dir=config_dir, path=config_path, path_named=True, detect=_detect, detect_only=True,
        wire=_wire, notice=_notice,
    )
