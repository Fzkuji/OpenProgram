"""VS Code's own chat (GitHub Copilot Chat), and VS Code Insiders and VSCodium beside it.

After yetone/magpie ``internal/agent/vscode.go`` (MIT License, Copyright (c)
yetone). VS Code's chat takes models of other vendors through its Custom
Endpoint provider (VS Code 1.122 and later), with no GitHub sign-in or
Copilot plan needed: groups of models in ``chatLanguageModels.json`` beside
its ``settings.json`` (the default profile's in its User folder, and each
other profile's in its own: :func:`profiles`), a JSONC array VS Code
watches and reads again when it changes. OpenProgram adds a group of its own
there, ``{"vendor": "customendpoint", "name": "openprogram"}``, listing the
enabled models, each at the gateway's ``/chat/completions`` with tool
calling on (agent mode lists only models that call tools). A group's apiKey
can't be written in the file (VS Code reads it from its secret storage
alone), so each model sends the gateway key in ``requestHeaders``'
Authorization, a key of VS Code's own, never the gateway's. The model a
new chat starts on is ``chat.defaultModel``; the one it had is kept and put
back on disconnect while the one there is still one of OpenProgram's.

VSCodium has no Copilot Chat of its own: its users install the
Marketplace's, whose last release (0.48.1) has no Custom Endpoint provider
but an OpenAI Compatible one, ``customoai``, which takes the same group with
each model's window as ``maxInputTokens`` beside ``maxOutputTokens`` and
drops an Authorization in requestHeaders, so the key goes as ``x-api-key``
there. It registers its providers only while Copilot Chat is signed in to
GitHub with a personal Copilot plan; the notice says so. Which of the two is
written is the one the installed extension's package.json declares
(:func:`chat_of`).

The group's model list is OpenProgram's whole; every other key of a group
already in the file stays (VS Code writes the reasoning effort picked in
Chat's model picker into it as ``settings``).
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .. import edit
from ..edit import ConfigText, ConfigUnreadable
from .base import ROOT, Agent, App, Field, Members, Pick, Wiring, Write, app_bundles, find_tool, taken

GROUP_NAME = "openprogram"
#: chat.defaultModel, a key with a dot in it, escaped for dotted key paths.
DEFAULT = r"chat\.defaultModel"
#: How VS Code's chat names itself to a Custom Endpoint: its fetcher's
#: GitHubCopilotChat/<version>, which a model's requestHeaders can't change.
UA = "githubcopilotchat"
#: VS Code's Agents window runs ``codex app-server`` with clientInfo named
#: vscode_agent_host, which Codex makes the start of its User-Agent.
AGENT_HOST_UA = "vscode_agent_host"
ENDPOINT = "customendpoint"
OAI = "customoai"
VENDORS = (ENDPOINT, OAI)
CHAT_ID = "github.copilot-chat"


@dataclass(frozen=True)
class Kind:
    """One build of VS Code, each with its own User folder, app and row."""

    id: str
    name: str
    folder: str
    bin: str
    aliases: tuple[str, ...]
    ua: tuple[str, ...] = ()
    #: for a build without a Copilot Chat of its own: the folder in home its
    #: extensions are installed in
    exts: str = ""
    app: Optional[App] = None

    @property
    def ext_folder(self) -> str:
        if self.exts:
            return self.exts
        return ".vscode-insiders" if self.id == INSIDERS.id else ".vscode"


STABLE = Kind("vscode", "VS Code", "Code", "code", ("vs-code", "copilot-chat", "vscode-chat"),
              ua=(UA, AGENT_HOST_UA), app=App("com.microsoft.VSCode", ("Visual Studio Code.app",)))
# Insiders' chat names itself as Stable's does, so it is told by its key.
INSIDERS = Kind("vscode_insiders", "VS Code Insiders", "Code - Insiders", "code-insiders",
                ("vscode-insiders", "vs-code-insiders", "code-insiders"),
                app=App("com.microsoft.VSCodeInsiders", ("Visual Studio Code - Insiders.app",)))
VSCODIUM = Kind("vscodium", "VSCodium", "VSCodium", "codium", ("codium", "vscodium-chat"), exts=".vscode-oss",
                app=App("com.vscodium", ("VSCodium.app",)))

#: The system whose VS Code layout is read; tests lay out another.
OS = sys.platform


# Where a build keeps its files

def dirs(kind: Kind) -> tuple[Path, Path]:
    """The User folder of ``kind`` (its settings.json) and its extensions
    folder: a portable build keeps both in its data folder."""
    data = portable_data(kind)
    if data is not None:
        return data / "user-data" / "User", data / "extensions"
    home = Path.home()
    if OS == "darwin":
        cfg = home / "Library" / "Application Support"
    elif OS == "win32":
        cfg = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        cfg = Path(xdg) if xdg and os.path.isabs(xdg) else home / ".config"
    return cfg / kind.folder / "User", home / kind.ext_folder / "extensions"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def scoop_shim(bin_path: str) -> str:
    """The launcher a Scoop shim stands for (the ``path =`` line of the .shim beside it)."""
    try:
        text = Path(os.path.splitext(bin_path)[0] + ".shim").read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "path" and parts[1] == "=":
            return " ".join(parts[2:]).strip('"')
    return ""


def app_root(kind: Kind, bin_path: str) -> tuple[Optional[Path], Optional[Path]]:
    """The app folder (resources/app, where product.json is) of the build whose
    launcher is ``bin_path``, and the build's own folder; ``(None, None)``
    for a launcher outside any build's bin folder."""
    folder = Path(bin_path).parent
    if folder.name.lower() != "bin":
        return None, None
    if OS != "darwin":
        install = folder.parent
        root = install / "resources" / "app"
        if not (root / "product.json").exists():
            found = sorted(install.glob("*/resources/app/product.json"))
            if found:
                root = found[-1].parent
        return root, install
    root = folder.parent
    bundle = root.parent.parent.parent
    if Path(bin_path).name != kind.bin or not bundle.name.endswith(".app") or root != bundle / "Contents" / "Resources" / "app":
        return None, None
    return root, bundle


def portable_at(kind: Kind, root: Path, app_path: Path) -> tuple[Optional[Path], bool]:
    """The data folder of the build at ``root`` when it is portable, as VS
    Code's configurePortable decides it; ``ours`` False when its product.json
    names another editor (a fork's launcher called code)."""
    product = _read_json(root / "product.json")
    read = isinstance(product, dict)
    name = (product or {}).get("applicationName") or "" if read else ""
    if read and name and name != kind.bin:
        return None, False
    if read and product.get("target") is not None:
        return None, True
    if OS == "darwin":
        if not read or not name:
            return None, True
        data = app_path.parent / (product.get("portable") or f"{name}-portable-data")
    else:
        data = app_path / "data"
    return (data if data.is_dir() else None), True


def portable_data(kind: Kind) -> Optional[Path]:
    """The data folder of a portable ``kind``, ``None`` for an installed build:
    the build its launcher is, followed through a Scoop shim and symlinks;
    on a Mac with no launcher of the build's, its bundles, taken only when
    every one found is the same portable build."""
    bin_path = find_tool(kind.bin)
    if bin_path:
        bin_path = scoop_shim(bin_path) or bin_path
        bin_path = os.path.realpath(bin_path)
        root, app_path = app_root(kind, bin_path)
        if root is not None:
            data, ours = portable_at(kind, root, app_path)
            if ours:
                return data
    if OS != "darwin" or kind.app is None:
        return None
    found: Optional[Path] = None
    for bundle in app_bundles(kind.app):
        data, _ = portable_at(kind, bundle / "Contents" / "Resources" / "app", bundle)
        if data is None or (found is not None and data != found):
            return None
        found = data
    return found


def name_short(short: str) -> bool:
    """Whether a product.json's nameShort is one of VS Code's own builds, not a fork's."""
    s = short.lower()
    return s == "code" or s.startswith("code - ") or s.startswith("visual studio code") or s == "vscodium"


def code_binary(bin_path: str, app: str) -> bool:
    """A launcher counts as the build whose command is ``app`` only when its
    product.json names that build: forks (Cursor, Qoder) ship a launcher
    named code beside their own. A wrapper without readable product
    metadata keeps the PATH fallback."""
    try:
        resolved = os.path.realpath(bin_path)
    except OSError:
        return True
    folder = Path(resolved).parent
    if folder.name.lower() != "bin":
        return True
    for path in (folder.parent / "product.json", folder.parent / "resources" / "app" / "product.json"):
        product = _read_json(path)
        if not isinstance(product, dict):
            continue
        name = str(product.get("applicationName") or "").strip()
        if name:
            return name.lower() == app.lower()
        short = str(product.get("nameShort") or "").strip()
        if short:
            return name_short(short)
    return True


def profiles(user: Path) -> tuple[list[Path], list[Path]]:
    """The settings.json and chatLanguageModels.json of each profile besides
    the default: VS Code lists them in globalStorage/storage.json's
    userDataProfiles; a profile set to use the default's settings or models
    (useDefaultFlags) has none of that kind, one whose folder is gone none."""
    data = _read_json(user / "globalStorage" / "storage.json")
    settings: list[Path] = []
    models: list[Path] = []
    if not isinstance(data, dict):
        return settings, models
    for profile in data.get("userDataProfiles") or []:
        if not isinstance(profile, dict):
            continue
        loc = profile.get("location")
        at = ""
        if isinstance(loc, dict):
            at = loc.get("fsPath") or ""
            if not at:
                at = loc.get("path") or ""
                if len(at) > 2 and at[0] == "/" and at[2] == ":":
                    at = at[1:]
        elif isinstance(loc, str) and loc:
            at = str(user / "profiles" / loc)
        if not at or not Path(at).is_dir():
            continue
        flags = profile.get("useDefaultFlags") or {}
        if not flags.get("settings"):
            settings.append(Path(at) / "settings.json")
        if not flags.get("languageModels"):
            models.append(Path(at) / "chatLanguageModels.json")
    return settings, models


# The Copilot Chat a build runs

@dataclass
class Chat:
    vendor: str
    version: str = ""
    none: bool = False
    unsupported: bool = False

    def notice(self, name: str) -> str:
        setup = (f"{name}'s Chat features must be enabled (chat.disableAIFeatures=false) and its product.json "
                 f"must include defaultChatAgent and trustedExtensionAuthAccess for GitHub.copilot-chat. "
                 f"Then run Developer: Reload Window in {name}.")
        if self.none:
            return f"Install GitHub Copilot Chat in {name} to see OpenProgram's models in its Chat. {setup}"
        if self.unsupported:
            return (f"{name}'s GitHub Copilot Chat {self.version} has neither a Custom Endpoint nor an OpenAI "
                    "Compatible provider, so it can't list OpenProgram's models.")
        if self.vendor == OAI:
            return (f"OpenProgram's models are in {name}'s Chat model picker under openprogram, from GitHub "
                    f"Copilot Chat {self.version}'s OpenAI Compatible provider, which it offers only while it "
                    f"is signed in to GitHub with a personal Copilot plan. {setup}")
        return setup


def newer(a: str, b: str) -> bool:
    """Whether extension version ``a`` is later than ``b``, number by number."""
    xs, ys = a.split("."), b.split(".")
    for i in range(max(len(xs), len(ys))):
        x = int(xs[i]) if i < len(xs) and xs[i].isdigit() else 0
        y = int(ys[i]) if i < len(ys) and ys[i].isdigit() else 0
        if x != y:
            return x > y
    return False


def chat_installed(folder: Path) -> tuple[Optional[Path], str, bool]:
    """The folder of the Copilot Chat installed in an extensions folder: the
    one extensions.json lists, else the highest version not marked obsolete.
    ``found`` is False only when the folder was read and has none."""
    listed = _read_json(folder / "extensions.json")
    if isinstance(listed, list):
        for entry in listed:
            if not isinstance(entry, dict):
                continue
            if str((entry.get("identifier") or {}).get("id") or "").lower() != CHAT_ID:
                continue
            rel = entry.get("relativeLocation") or ""
            if rel:
                return folder / rel, str(entry.get("version") or ""), True
            at = (entry.get("location") or {}).get("fsPath") or ""
            if at:
                return Path(at), str(entry.get("version") or ""), True
    try:
        entries = list(os.scandir(folder))
    except FileNotFoundError:
        return None, "", False
    except OSError:
        return None, "", True
    obsolete = _read_json(folder / ".obsolete") or {}
    at: Optional[Path] = None
    version = ""
    for entry in entries:
        lowered = entry.name.lower()
        if not entry.is_dir() or not lowered.startswith(CHAT_ID + "-") or obsolete.get(entry.name):
            continue
        v = lowered[len(CHAT_ID) + 1:]
        if "-" in v[1:]:  # a platform build: <version>-<platform>
            v = v[:v.index("-", 1)]
        if at is None or newer(v, version):
            at, version = folder / entry.name, v
    return at, version, at is not None


def chat_of(folder: Path) -> Chat:
    at, version, found = chat_installed(folder)
    if not found:
        return Chat(ENDPOINT, none=True)
    package = _read_json(at / "package.json") if at is not None else None
    if not isinstance(package, dict):
        return Chat(ENDPOINT, version)
    version = version or str(package.get("version") or "")
    vendors = {p.get("vendor") for p in ((package.get("contributes") or {}).get("languageModelChatProviders") or [])
               if isinstance(p, dict)}
    if ENDPOINT in vendors:
        return Chat(ENDPOINT, version)
    if OAI in vendors:
        return Chat(OAI, version)
    return Chat(ENDPOINT, version, unsupported=True)


def chat(kind: Kind, exts: Path) -> Chat:
    """A build with Copilot Chat built in has the Custom Endpoint provider;
    one without has the extension installed in its extensions folder."""
    return chat_of(exts) if kind.exts else Chat(ENDPOINT)


# JSON files, edited in place

def read_json(path: Path) -> dict[str, Any]:
    return edit.load("jsonc", ConfigText.read(path).text, str(path))


def get_key(path: Path, key: str) -> tuple[bool, Any]:
    try:
        return edit.get_path(read_json(path), key)
    except ConfigUnreadable:
        return False, None


# The group

def models_url(at: Wiring) -> str:
    """The gateway's Chat Completions URL: a Custom Endpoint model's url that
    ends in /chat/completions is asked as it is."""
    return at.base("openai-chat") + "/chat/completions"


def group_of(vendor: str) -> dict[str, str]:
    return {"vendor": vendor, "name": GROUP_NAME}


def group_json(at: Wiring, vendor: str) -> dict[str, Any]:
    """OpenProgram's group in chatLanguageModels.json for ``vendor``."""
    listed = []
    for row in at.models:
        window = int(row.get("context_window") or 0) or 128000  # what VS Code takes an unknown window for
        # VS Code keeps the output budget out of the window the prompt fills,
        # so an output limit near the window would leave the prompt no room
        output = int(row.get("max_tokens") or 0)
        if output <= 0:
            output = 8192
        output = min(output, window // 4)
        entry: dict[str, Any] = {
            "id": row["id"], "name": row.get("name") or row["id"], "url": models_url(at),
            "toolCalling": True, "vision": bool(row.get("vision")),
            "contextWindow": window, "maxOutputTokens": output,
            "requestHeaders": {"Authorization": "Bearer " + at.key},
        }
        if vendor == OAI:
            # its window is maxInputTokens plus maxOutputTokens, and an
            # Authorization of the model's own is dropped
            del entry["contextWindow"]
            entry["maxInputTokens"] = window - output
            entry["requestHeaders"] = {"x-api-key": at.key}
        levels = [str(level) for level in row.get("thinking_levels") or [] if level and level != "off"]
        if levels:
            entry["supportsReasoningEffort"] = levels
            entry["reasoningEffortFormat"] = "chat-completions"
        listed.append(entry)
    if vendor == OAI:
        return {"name": GROUP_NAME, "vendor": OAI, "models": listed}
    return {"name": GROUP_NAME, "vendor": ENDPOINT, "apiType": "chat-completions", "models": listed}


def ours(models: Path) -> tuple[Optional[dict[str, Any]], str]:
    """OpenProgram's group in a chatLanguageModels.json, whichever vendor it was written for."""
    try:
        text = ConfigText.read(models).text
    except ConfigUnreadable:
        return None, ""
    if not text.strip():
        return None, ""
    import json
    from ..edit.jsonc import to_json
    try:
        groups = json.loads(to_json(text) or "")
    except ValueError:
        return None, ""
    for group in groups if isinstance(groups, list) else []:
        if isinstance(group, dict) and group.get("name") == GROUP_NAME and group.get("vendor") in VENDORS:
            return group, str(group["vendor"])
    return None, ""


#: OpenProgram's group in a chatLanguageModels.json, for either vendor.
MATCH = {"name": GROUP_NAME, "vendor": {"in": list(VENDORS)}}


# The agent

def agent_of(kind: Kind) -> Agent:
    user, exts = dirs(kind)
    settings = user / "settings.json"
    models = user / "chatLanguageModels.json"

    def in_group(model_id: Any) -> bool:
        group, _ = ours(models)
        if group is None or not isinstance(model_id, str) or not model_id:
            return False
        return any(isinstance(m, dict) and str(m.get("id", "")).lower() == model_id.lower()
                   for m in group.get("models") or [])

    def files() -> tuple[list[Path], list[Path]]:
        more_settings, more_models = profiles(user)
        return [settings, *more_settings], [models, *more_models]

    def wire(at: Wiring) -> list[Write]:
        """OpenProgram's group in each profile's chatLanguageModels.json, beside
        the user's own groups and where it was (``Members``), the list a sync
        rewrites, a profile made since included; and the model a new chat in
        each profile starts on. What VS Code writes into the group (the effort
        picked in Chat's model picker, ``settings``) stays."""
        setting_files, model_files = files()
        vendor = chat(kind, exts).vendor
        group = group_json(at, vendor)
        writes = []
        for path in model_files:
            have, was = ours(path)
            item = {**have, **group} if have is not None and was == vendor else group
            writes.append(Write(path, "jsonc", listing=True,
                                members={ROOT: Members(MATCH, [item], keep_place=True)}))
        same = (at.previous.get("values") or {}).get("model") == at.model
        for path in setting_files:
            model = at.model
            current = get_key(path, DEFAULT)[1]
            if same and in_group(current):
                model = current  # a reconnect on the same pick (a new port) keeps one set since
            # put back on disconnect while it is still one of OpenProgram's (magpie in_group)
            writes.append(Write(path, "jsonc", sets={DEFAULT: model},
                                picks=(Pick((DEFAULT,), DEFAULT, {"in": [row["id"] for row in at.models]}),)))
        return writes

    def check() -> str:
        if ours(models)[0] is None:
            return ""
        found = chat(kind, exts)
        if found.unsupported:
            return found.notice(kind.name)
        _, vendor = ours(models)
        if found.vendor != vendor:
            return (f"{kind.name}'s Copilot Chat {found.version} lists a group for {found.vendor} and "
                    f"OpenProgram's is for {vendor}")
        return ""

    def detect() -> bool:
        if taken(user):
            return False
        if user.is_dir():
            return True
        found = find_tool(kind.bin)
        return bool(found) and code_binary(found, kind.bin)

    def notice() -> str:
        if ours(models)[0] is None:
            return ""
        if kind.exts:
            return chat(kind, exts).notice(kind.name)
        return (f"OpenProgram's models are in {kind.name}'s Chat model picker, under openprogram, in each of "
                f"its profiles (VS Code 1.122 or later). If they don't show, run Developer: Reload Window "
                f"in {kind.name}.")

    return Agent(
        id=kind.id, name=kind.name, icon="vscode", aliases=kind.aliases, kind="ide", protocol="openai-chat",
        bin=kind.bin, dir=lambda: user, path=lambda: settings, fields=[Field("model", "Model")],
        ua=kind.ua, wire=wire, check=check,
        detect_only=True, detect=detect, notice=notice,
    )


def agent() -> Agent:
    return agent_of(STABLE)
