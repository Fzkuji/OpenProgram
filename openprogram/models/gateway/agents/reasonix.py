"""Reasonix Studio 2.x and its CLI (``$REASONIX_HOME/config.toml`` and ``.env``).

After yetone/magpie ``internal/agent/reasonix.go``, ``reasonix_detect.go`` and
``reasonix_studio.go`` (MIT License, Copyright (c) yetone). Reasonix Studio and
its native CLI share ``config.toml`` and ``.env`` under ``REASONIX_HOME``,
``~/.reasonix``, or ``%APPDATA%\\reasonix`` on Windows. OpenProgram owns one
``[[providers]]`` entry, named ``openprogram``, never the user's other
providers: kind ``openai`` at the gateway, its key read from the variable
``OPENPROGRAM_REASONIX_API_KEY`` in ``.env``, the catalog as its models with
a ``model_overrides`` table per model (window, output, vision, the efforts
it takes). ``default_model`` (the executor) is ``openprogram/<provider>/<model>``,
and the planner (``agent.planner_model``) can be put on the gateway too.
Studio's ``desktop.provider_access``, when it lists providers, gets
``openprogram`` added. Project and session overrides stay the user's.

OpenProgram's ``[[providers]]`` entry sits after the user's (``Members``):
disconnecting takes it out and leaves the user's, edited since or not, and
``openprogram`` leaves ``desktop.provider_access`` the same way. The effort
field is the entry's ``effort``, one of the picked model's
``supported_efforts`` (unset, Reasonix picks: magpie's "auto"). Studio is
found from its app archive on macOS and Linux and, on Windows, from its
uninstall entry in the registry (``reasonix_detect_windows.go``).
"""
from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from .base import Agent, Field, Members, Pick, Wiring, Write, find_tool
from .model_rows import PROVIDER_ID, choice, efforts, images, max_tokens, picked, prefixed, read_doc, window

KEY_ENV = "OPENPROGRAM_REASONIX_API_KEY"
NOTICE = ("Reasonix Studio 2.x and the native 1.39.x/2.x CLI read global settings at startup. "
          "Start a new process to load changes. Project/session models can override Executor and Plan.")
#: The efforts Reasonix's ``supported_efforts`` takes.
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def home() -> Path:
    env = os.environ.get("REASONIX_HOME")
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "reasonix"
    return Path.home() / ".reasonix"


def config_path() -> Path:
    return home() / "config.toml"


def env_path() -> Path:
    return home() / ".env"


def levels(model: dict[str, Any]) -> list[str]:
    """The efforts Reasonix takes for ``model`` (its ``supported_efforts``)."""
    out: list[str] = []
    for level in efforts(model):
        level = "none" if level == "off" else level
        if level in EFFORTS and level not in out:
            out.append(level)
    return out


def provider_entry(models: list[dict[str, Any]], selected: str, effort: str, v1: str) -> dict[str, Any]:
    """OpenProgram's ``[[providers]]`` entry (magpie ``reasonixCatalogAt``)."""
    ids = [m["id"] for m in models]
    overrides: dict[str, Any] = {}
    for m in models:
        override: dict[str, Any] = {}
        supported = levels(m)
        if not supported:
            override["reasoning_protocol"] = "none"
        else:
            override["supported_efforts"] = supported
        if images(m):
            override["vision"] = True
        if window(m):
            override["context_window"] = window(m)
        if max_tokens(m):
            override["max_output_tokens"] = max_tokens(m)
        overrides[m["id"]] = override
    default = selected if selected in ids else (ids[0] if ids else "")
    entry: dict[str, Any] = {"name": PROVIDER_ID, "kind": "openai", "base_url": v1, "api_key_env": KEY_ENV,
                             "models": ids, "default": default}
    if effort and effort in (overrides.get(default) or {}).get("supported_efforts", []):
        entry["effort"] = effort
    entry.update({"reasoning_protocol": "openai", "no_proxy": True, "model_overrides": overrides})
    return entry


def _wire(at: Wiring) -> list[Write]:
    from .engine import AgentConfigError
    path = config_path()
    doc = read_doc("toml", path)
    providers = [p for p in (doc.get("providers") or []) if isinstance(p, dict)]
    for p in providers:
        if p.get("name") == PROVIDER_ID and p.get("api_key_env") != KEY_ENV:
            raise AgentConfigError("Reasonix already has a provider named openprogram that OpenProgram does not own")
        if p.get("name") != PROVIDER_ID and p.get("api_key_env") == KEY_ENV:
            raise AgentConfigError(f"Reasonix's {KEY_ENV} is used by another provider")
    try:
        if env_path().read_text(encoding="utf-8").count(f"{KEY_ENV}=") > 1:
            # Studio's dotenv reader resolves repeats differently from the editor
            raise AgentConfigError(f"Reasonix credentials repeat {KEY_ENV}")
    except OSError:
        pass
    effort = at.values.get("effort", "")
    entry = provider_entry(at.models, at.model, "" if effort == "auto" else effort, at.base("openai-chat"))
    members = {"providers": Members(match={"name": PROVIDER_ID}, items=[entry])}
    access = (doc.get("desktop") or {}).get("provider_access") if isinstance(doc.get("desktop"), dict) else None
    if isinstance(access, list):
        members["desktop.provider_access"] = Members(match={"": PROVIDER_ID}, items=[PROVIDER_ID])
    writes = [Write(path, "toml", members=members, listing=True), Write(env_path(), "dotenv", sets={KEY_ENV: at.key})]
    if at.syncing:
        return writes  # the picks are the user's to change in Reasonix
    # each a pick Reasonix's own pickers rewrite
    ours = {"prefix": PROVIDER_ID + "/"}
    sets: dict[str, Any] = {"default_model": prefixed(at.model)}
    picks = [Pick(("default_model",), "default_model", ours)]
    planner = at.values.get("planner", "")
    if planner:
        sets["agent.planner_model"] = prefixed(planner)
        picks.append(Pick(("agent.planner_model",), "agent.planner_model", ours))
    writes.insert(1, Write(path, "toml", sets=sets, picks=tuple(picks)))
    return writes


# Detection (magpie reasonixDetected): config alone can't tell Studio 2.x and
# the native CLI from the historical npm 1.x client.

def studio_package(path: Path) -> bool:
    """Whether the Electron archive at ``path`` is Reasonix Studio 2.x: its
    package.json alone is read from the asar, never the app launched."""
    try:
        with open(path, "rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            prefix = handle.read(16)
            if len(prefix) < 16 or struct.unpack_from("<I", prefix, 0)[0] != 4:
                return False
            header_size = struct.unpack_from("<I", prefix, 4)[0]
            json_size = struct.unpack_from("<I", prefix, 12)[0]
            if header_size < 8 or header_size > 4 << 20 or header_size > size - 8 or json_size < 2 or json_size > header_size - 8:
                return False
            header = json.loads(handle.read(json_size))
            entry = (header.get("files") or {}).get("package.json") or {}
            offset, length = int(entry.get("offset", -1)), int(entry.get("size", 0))
            data_size = size - 8 - header_size
            if entry.get("unpacked") or length <= 0 or length > 64 << 10 or offset < 0 or offset > data_size \
                    or length > data_size - offset:
                return False
            handle.seek(8 + header_size + offset)
            package = json.loads(handle.read(length))
    except (OSError, ValueError, TypeError, AttributeError):
        return False
    version = str(package.get("version") or "").strip().lstrip("v")
    return package.get("name") == "reasonix-studio-electron" and version.startswith("2.")


@lru_cache(maxsize=8)
def _cli_version(path: str, size: int, mtime: float) -> bool:
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    version = out.strip()
    if not version.startswith("reasonix v"):
        return False
    version = version[len("reasonix v"):]
    return version.startswith("2.") or version.startswith("1.39.")


def native_cli(path: str) -> bool:
    """The native Reasonix CLI, 1.39.x or 2.x, probed once per binary revision."""
    try:
        resolved = os.path.realpath(path)
        stat = os.stat(resolved)
    except OSError:
        return False
    return _cli_version(resolved, stat.st_size, stat.st_mtime)


def _registry_dirs() -> list[Path]:
    """Where Reasonix Studio 2.x is installed, by its uninstall entries in
    the Windows registry (magpie ``reasonixStudioDirs``)."""
    if sys.platform != "win32":
        return []
    import winreg  # noqa: PLC0415 - Windows only
    dirs: list[Path] = []
    roots = (r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
             r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall")
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for root in roots:
            try:
                key = winreg.OpenKey(hive, root)
            except OSError:
                continue
            with key:
                index = 0
                while True:
                    try:
                        name = winreg.EnumKey(key, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        with winreg.OpenKey(key, name) as entry:
                            found = _registry_dir(entry)
                    except OSError:
                        continue
                    if found is not None:
                        dirs.append(found)
    return dirs


def _registry_dir(entry: Any) -> Path | None:
    import winreg  # noqa: PLC0415 - Windows only

    def value(name: str) -> str:
        try:
            return str(winreg.QueryValueEx(entry, name)[0] or "")
        except OSError:
            return ""
    if not studio_release(value("DisplayName"), value("DisplayVersion")):
        return None
    folder = value("InstallLocation")
    if not folder:
        icon = value("DisplayIcon")
        if "," in icon:
            icon = icon[:icon.rindex(",")]
        if icon:
            folder = os.path.dirname(icon.strip('"'))
    return Path(folder) if folder and os.path.isabs(folder) else None


def studio_release(display: str, version: str) -> bool:
    """An uninstall entry of Reasonix Studio 2.x (magpie ``reasonixStudioRelease``)."""
    named = display == "Reasonix Studio" or display.startswith("Reasonix Studio ")
    return named and version.strip().removeprefix("v").startswith("2.")


def _studio_archives() -> list[Path]:
    if sys.platform == "darwin":
        return [folder / "Reasonix Studio.app" / "Contents" / "Resources" / "app.asar"
                for folder in (Path("/Applications"), Path.home() / "Applications")]
    if sys.platform.startswith("linux"):
        found = []
        for name in ("reasonix-studio", "reasonix-studio-electron"):
            where = find_tool(name)
            if where:
                found.append(Path(os.path.realpath(where)).parent / "resources" / "app.asar")
        return found
    if sys.platform == "win32":
        return [folder / "resources" / "app.asar" for folder in _registry_dirs()]
    return []


def detected() -> bool:
    if any(studio_package(archive) for archive in _studio_archives()):
        return True
    where = find_tool("reasonix")
    return bool(where) and native_cli(where)


def effort_choices(values: dict[str, str]) -> list[str]:
    """The efforts the picked model takes, after "auto" (Reasonix picks),
    none for a model that doesn't reason (magpie's effort ``Options``)."""
    supported = levels(picked(values))
    return ["auto", *supported] if supported else []


def _held() -> list[Path]:
    return [archive for archive in _studio_archives() if archive.exists()]


def agent() -> Agent:
    return Agent(
        id="reasonix", name="Reasonix Studio", icon="reasonix", aliases=("reasonix-studio",), kind="app",
        protocol="openai-chat", bin="reasonix", dir=home, path=config_path,
        fields=[Field("model", "Executor"), Field("planner", "Planner", quiet=True, follows="model"),
                Field("effort", "Effort", quiet=True, options=choice("reasonix", effort_choices))],
        ua=("reasonix",), before_copy=True, wire=_wire, notice=lambda: NOTICE, held_at=_held,
        # Studio 2.x or the native CLI; config alone can't tell them from npm 1.x
        detect=detected, detect_only=True,
    )
