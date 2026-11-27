"""Zed (``settings.json`` in ~/.config/zed, %APPDATA%\\Zed or $XDG_CONFIG_HOME/zed).

After yetone/magpie ``internal/agent/zed.go`` and ``zed_credential_*.go``
(MIT License, Copyright (c) yetone). Connecting writes an OpenAI-compatible
provider, ``language_models.openai_compatible.openprogram``, at the
gateway's ``/openai/v1`` with the enabled models (their windows, tools,
images and reasoning effort), and ``agent.default_model`` naming it. Zed
reads a provider's API key not from its settings but from the system
credential store, keyed by the API URL, so the gateway key Zed sends is saved
there first (``keychain.py``); a credential that can't be saved leaves the
settings alone. Disconnecting puts back the provider and model Zed had; the
saved credential stays (Zed finds nothing at that URL once the provider is
gone).

The provider may reach the gateway through a proxy in front of it (magpie
``zedReach``; ``reach.py``): a company's reverse proxy, or
billion-context's ``/bili/`` prefix. Its ``api_url`` is kept while the gateway
answers through it and a bili prefix stays when the gateway's port moves.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from ..edit import ConfigText, ConfigUnreadable
from . import keychain, reach
from .base import Agent, Field, Live, Pick, Wiring, Write, find_tool

PROVIDER = "language_models.openai_compatible.openprogram"
MODEL = "agent.default_model"
NAME = "openprogram"
#: The reasoning_effort values written for Zed, weakest first: those every
#: Zed that reads the field takes (a value a Zed doesn't know is reported as
#: a settings error; max and none came after).
LEVELS = ("minimal", "low", "medium", "high")
PROCESSES = r"(^|/)(zed|zeditor|zed-editor)( |$)"


def home() -> Path:
    if sys.platform == "darwin":
        return Path.home() / ".config" / "zed"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Zed"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg and os.path.isabs(xdg) else Path.home() / ".config") / "zed"


def config_path() -> Path:
    return home() / "settings.json"


def effort(row: dict[str, Any]) -> str:
    """The reasoning_effort a model is written with, ``""`` for one that doesn't
    think. Zed offers its thinking switch, and its levels, only for a model
    whose reasoning_effort is set, and starts on that level: high, or the
    strongest of the model's own when they all fall short of it."""
    levels = [str(level) for level in row.get("thinking_levels") or [] if level and level != "off"]
    if not row.get("reasoning") and not levels:
        return ""
    best = -1
    for level in levels:
        index = LEVELS.index(level) if level in LEVELS else (len(LEVELS) - 1 if level in ("xhigh", "max", "ultra") else -1)
        best = max(best, index)
    return "high" if best < 0 else LEVELS[best]


def _kept_efforts(path: Path) -> dict[str, Any]:
    """A reasoning_effort already on a model in Zed's settings is kept: the
    user may have picked another level, or "none" to keep it from thinking."""
    from .. import edit
    try:
        doc = edit.load("jsonc", ConfigText.read(path).text, str(path))
    except ConfigUnreadable:
        return {}
    present, listed = edit.get_path(doc, PROVIDER + ".available_models")
    kept = {}
    for entry in listed if present and isinstance(listed, list) else []:
        if isinstance(entry, dict) and entry.get("reasoning_effort") is not None:
            kept[entry.get("name")] = entry["reasoning_effort"]
    return kept


def provider_json(at: Wiring, path: Path) -> dict[str, Any]:
    kept = _kept_efforts(path)
    listed = []
    for row in at.models:
        window = int(row.get("context_window") or 0)
        context = window or 128000  # Zed requires a context window for every custom model
        # Zed's max_tokens is the window a prompt and its reply share: it keeps
        # max_output_tokens of it for the reply and lets the prompt fill the rest
        entry: dict[str, Any] = {
            "name": row["id"], "display_name": row.get("name") or row["id"], "max_tokens": context,
            "capabilities": {"tools": True, "images": bool(row.get("vision")), "parallel_tool_calls": False,
                             "prompt_cache_key": False, "chat_completions": True},
        }
        output = int(row.get("max_tokens") or 0)
        if output > 0:
            if not window:
                output = min(output, context)  # the fallback reply cap while the prompt limit is unknown
            entry["max_output_tokens"] = output
            entry["max_tokens"] = context + output
        if row["id"] in kept:
            entry["reasoning_effort"] = kept[row["id"]]
        elif effort(row):
            entry["reasoning_effort"] = effort(row)
        listed.append(entry)
    return {"api_url": zed_reach(at, path), "available_models": listed}


def zed_url(path: Path) -> str:
    """The api_url of Zed's OpenProgram provider, ``""`` without one (magpie ``ZedURL``)."""
    from .. import edit
    try:
        doc = edit.load("jsonc", ConfigText.read(path).text, str(path))
    except ConfigUnreadable:
        return ""
    value = edit.get_path(doc, PROVIDER + ".api_url")[1]
    return value if isinstance(value, str) else ""


def zed_reach(at: Wiring, path: Path) -> str:
    """The address the provider reaches the gateway at (magpie ``zedReach``):
    its api_url where that is the gateway's or a proxy the gateway answers
    through, else the gateway's own (through a bili prefix that was in front
    of it before its port moved)."""
    return reach.reach(zed_url(path), at.base("openai-chat"), at.key)


def _model_now(path: Path) -> Any:
    """``agent.default_model`` as Zed has it now, ``None`` for none."""
    from .. import edit
    try:
        present, value = edit.get_path(edit.load("jsonc", ConfigText.read(path).text, str(path)), MODEL)
    except ConfigUnreadable:
        return None
    return value if present and isinstance(value, dict) else None


def _wire(at: Wiring) -> list[Write]:
    """OpenProgram's provider, its whole (``owns``), the model list a sync
    rewrites; and the model a new thread starts on, which Zed's own picker
    rewrites (a ``Pick``): a reconnect on the same pick (a new port) leaves
    one picked in Zed, and disconnecting puts the user's back only while it
    still names OpenProgram's provider."""
    path = config_path()
    model: Any = {"provider": NAME, "model": at.model}
    if (at.previous.get("values") or {}).get("model") == at.model:
        model = _model_now(path) or model
    return [Write(path, "jsonc", sets={PROVIDER: provider_json(at, path)}, owns=(PROVIDER,), listing=True),
            Write(path, "jsonc", sets={MODEL: model}, picks=(Pick((MODEL,), f"{MODEL}.provider", NAME),))]


def app_path() -> str:
    """The installed Zed bundle, trusted with its credential (a CLI symlink's bundle first)."""
    candidates = [Path.home() / "Applications" / "Zed.app", Path.home() / "Applications" / "Zed Preview.app",
                  Path("/Applications/Zed.app"), Path("/Applications/Zed Preview.app")]
    found = find_tool("zed")
    if found:
        current = Path(os.path.realpath(found))
        for parent in current.parents:
            if parent.name.endswith(".app"):
                candidates.insert(0, parent)
                break
    for candidate in candidates:
        if candidate.is_dir():
            return str(candidate)
    return ""


def save_credential(url: str, key: str) -> None:
    """Zed's key for ``url`` in the system credential store, where GPUI looks it up."""
    from .engine import AgentConfigError
    trusted: list[str] = []
    if sys.platform == "darwin":
        app = app_path()
        if not app:
            raise AgentConfigError("configure Zed gateway credential: cannot find Zed.app to authorize access")
        trusted = [app, "/usr/bin/security"]
    try:
        keychain.save_url_password(url, "Bearer", key, label="zed-github-account", trusted=trusted,
                                   windows_target=f"zed:url={url}")
    except keychain.CredentialError as exc:
        raise AgentConfigError(f"configure Zed gateway credential: {exc}") from exc


def _connect(at: Wiring, kept: dict[str, Any]) -> dict[str, Any]:
    url = zed_reach(at, config_path())
    save_credential(url, at.key)
    return {"credential": url}


def _notice() -> str:
    from .running import any_running
    if any_running(PROCESSES):
        return ("Restart Zed if it still asks for an API key: OpenProgram has saved its gateway credential "
                "in the system credential store.")
    return ""


def agent() -> Agent:
    return Agent(
        id="zed", name="Zed", icon="zed", kind="ide", protocol="openai-chat", bin="zed", dir=home,
        path=config_path, fields=[Field("model", "Model")], ua=("zed",), wire=_wire,
        notice=_notice, live=Live(connect=_connect, disconnect=lambda kept: None),
    )
