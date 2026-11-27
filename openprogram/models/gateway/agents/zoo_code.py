"""Zoo Code, the VS Code extension (a fork of Roo Code).

After yetone/magpie ``internal/agent/zoocode.go`` (MIT License, Copyright (c)
yetone). Zoo Code keeps its provider profiles in VS Code's secret storage,
out of reach. What it does read from a file is the one its
``zoo-code.autoImportSettingsPath`` setting names: on every start of the
extension it imports that file as its own Settings → Import does::

    {"providerProfiles": {"currentApiConfigName": "openprogram",
      "apiConfigs": {"openprogram": {"id": "openprogram", "apiProvider": "openai",
        "openAiBaseUrl": "<gateway>/openai/v1", "openAiApiKey": "<its key>",
        "openAiModelId": "<model>", "openAiCustomModelInfo": {…}}}}}

Imported profiles are merged over the user's own, which stay, and the
imported currentApiConfigName becomes the profile Zoo Code uses. OpenProgram
writes that file in Zoo Code's own folder of VS Code's and points the setting
at it in VS Code's settings.json, the default profile's and each other
profile's; a value of the user's own is kept and put back on disconnect.

What this route can't do: Zoo Code reads the file only when it starts (the
notice asks for a window reload) and imports it again on each start. A
profile once imported lives in Zoo Code's secret storage, which OpenProgram
can't write, so disconnecting stops the import and leaves the profile
"openprogram" there for the user to switch from or delete. Zoo Code for
JetBrains is handed an empty configuration on every start, so the setting
can't reach it there; it isn't written.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..edit import ConfigUnreadable
from .base import ROOT, Agent, Field, Wiring, Write, taken
from .vscode import STABLE, dirs, get_key, profiles

#: The setting naming the file Zoo Code imports at start-up, escaped.
IMPORT = r"zoo-code\.autoImportSettingsPath"
#: Zoo Code's extension id, as the folder it is installed in begins.
EXTENSION = "zoocodeorganization.zoo-code"
PROFILE = "openprogram"


def import_path(user: Path) -> Path:
    """The file Zoo Code is made to import, in its own folder of VS Code's User folder."""
    return user / "globalStorage" / EXTENSION / "settings" / "openprogram-import.json"


def installed(exts: Path) -> bool:
    try:
        return any(e.is_dir() and e.name.lower().startswith(EXTENSION + "-") for e in os.scandir(exts))
    except OSError:
        return False


def model_info(row: dict[str, Any]) -> dict[str, Any]:
    """A model as Zoo Code's modelInfo: contextWindow and supportsPromptCache
    are the two its schema requires; 128000 is its own default for a model
    it isn't told about (openAiModelInfoSaneDefaults)."""
    info: dict[str, Any] = {"supportsPromptCache": False, "supportsImages": bool(row.get("vision")),
                            "contextWindow": int(row.get("context_window") or 0) or 128_000}
    if int(row.get("max_tokens") or 0) > 0:
        info["maxTokens"] = int(row["max_tokens"])
    return info


def provider_profiles(at: Wiring, ref: str) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "id": PROFILE, "apiProvider": "openai", "openAiBaseUrl": at.base("openai-chat"),
        "openAiApiKey": at.key, "openAiModelId": ref, "openAiStreamingEnabled": True,
    }
    for row in at.models:
        if row["id"] == ref:
            profile["openAiCustomModelInfo"] = model_info(row)
            break
    return {"currentApiConfigName": PROFILE, "apiConfigs": {PROFILE: profile}}


def agent_at(user: Path, exts: Path) -> Agent:
    settings = user / "settings.json"
    file = import_path(user)

    def joined() -> bool:
        found, value = get_key(file, f"providerProfiles.apiConfigs.{PROFILE}")
        return get_key(settings, IMPORT)[1] == str(file) and found and bool(value)

    def wire(at: Wiring) -> list[Write]:
        """The file Zoo Code imports, OpenProgram's whole, and the setting
        naming it in every profile's settings.json; both follow the catalog
        and the key, and a profile made since is pointed at it too, so both
        are the list a sync rewrites."""
        content = json.dumps({"providerProfiles": provider_profiles(at, at.model)}, indent=2) + "\n"
        return [Write(file, "file", content=content, owns=(ROOT,), listing=True),
                *(Write(path, "jsonc", sets={IMPORT: str(file)}, listing=True)
                  for path in [settings, *profiles(user)[0]])]

    def notice() -> str:
        try:
            on = joined()
        except ConfigUnreadable:
            on = False
        return ("Zoo Code reads OpenProgram's profile when it starts — run Developer: Reload Window in VS Code "
                "to use this.") if on else ""

    return Agent(
        id="zoo_code", name="Zoo Code", icon="zoo-code", kind="ide", protocol="openai-chat",
        # its requests say "ZooCode/<version>": usage names them by the word before the slash
        aliases=("zoo-code", "zoocode", "zoo"), dir=lambda: user, path=lambda: settings,
        fields=[Field("model", "Model")], wire=wire,
        detect_only=True, detect=lambda: not taken(user) and installed(exts), notice=notice,
    )


def agent() -> Agent:
    # VS Code's own User and extensions folders: a portable build's are both in its data folder
    return agent_at(*dirs(STABLE))
