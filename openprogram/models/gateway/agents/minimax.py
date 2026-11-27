"""MiniMax Code (``config.yaml`` in its data folder, default ``~/.minimax``).

After yetone/magpie ``internal/agent/minimax.go`` and ``internal/appdir/
minimax.go`` (MIT License, Copyright (c) yetone). MiniMax Code — MiniMax's
``mcode`` CLI and the desktop app, which run the same runtime — keeps its
settings in config.yaml in its data folder: ``$MINIMAX_DATA_DIR`` (or
``$MAVIS_DATA_DIR``), the folder the desktop app moved it to, or
``~/.minimax``. The model sessions start on is ``defaultModel``,
``<provider>/<model>`` split at the first slash, where a provider the user
added is ``custom_provider:<key>``, an entry of ``custom_provider``.
OpenProgram adds itself there as ``custom_provider.openprogram`` — the
gateway, spoken to as Anthropic Messages, with the enabled models as its
models — so they join MiniMax Code's ``/model``.

A model there is keyed by its ref's flat spelling, ``deepseek~deepseek-chat``
(``conversion.flat_id``), which the gateway takes back to
``deepseek/deepseek-chat``: a SubAgent's model must be ``<provider>/<model>``
with one slash in the whole, so a model through OpenProgram is
``custom_provider:openprogram/deepseek~deepseek-chat``. The default the user
had, and its variant, are stashed and put back on disconnect. MiniMax Code
rewrites the file itself (its ``/model``, its provider commands), so the
entry is edited in place: a key the user or MiniMax Code put on it, or on one
of its models, stays.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .. import edit
from ..edit import ConfigText
from .base import Agent, App, Field, Wiring, Write

PROVIDER_KEY = "openprogram"
PROVIDER = f"custom_provider:{PROVIDER_KEY}"
ENTRY = f"custom_provider.{PROVIDER_KEY}"
#: The User-Agent the entry sends: MiniMax Code 0.5's own was the Anthropic
#: SDK's, which names nothing.
UA = "minimax-code"
#: MiniMax Code Desktop's electron-store files under its userData, the China
#: build's first (magpie ``miniMaxDesktopStores``).
#: MiniMax Code Desktop, the China build and the global one (magpie desktopapp.go).
DESKTOP = (App("com.minimax.agent.cn", ("MiniMax Code.app",)), App("com.minimax.agent", ("MiniMax Code.app",)))
DESKTOP_STORES = (("MiniMax", "minimax-agent-cn-config.json"), ("MiniMax Agent", "minimax-agent-config.json"))


def _desktop_parent() -> str:
    """The parent folder MiniMax Code Desktop moved its data folder to, or ``""``."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        return ""
    for folder, name in DESKTOP_STORES:
        try:
            store = json.loads((base / folder / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        parent = ((store.get("config") or {}) if isinstance(store, dict) else {}).get("localRuntimeDataParentDir")
        # the app takes only a string, trimmed, made absolute
        if isinstance(parent, str) and parent.strip() and os.path.isabs(parent.strip()):
            return os.path.normpath(parent.strip())
    return ""


def home() -> Path:
    for name in ("MINIMAX_DATA_DIR", "MAVIS_DATA_DIR"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return Path(value).expanduser()
    parent = _desktop_parent()
    return Path(parent) / ".minimax" if parent else Path.home() / ".minimax"


def config_path() -> Path:
    return home() / "config.yaml"


def _kept_effort(was: Any, levels: list[str], default: str) -> str:
    """A default effort set by hand stays while the model has it (magpie ``keptEffort``)."""
    return was if isinstance(was, str) and was in levels else default


def entry_for(at: Wiring, current: Any) -> dict[str, Any]:
    """OpenProgram's entry as the models are now, over the one there (magpie
    ``writeMiniMaxEntryAt``): what OpenProgram sets is set, a model gone goes,
    and every other key — the user's, MiniMax Code's — stays."""
    from ..conversion import flat_id
    entry = dict(current) if isinstance(current, dict) else {}
    entry.update({"name": PROVIDER_KEY, "kind": "custom", "enabled": True,
                  # the gateway takes every model as Anthropic Messages, as
                  # Claude Code sends them; MiniMax Code's own API is that too
                  "api": "anthropic-messages"})
    options = dict(entry.get("options") or {}) if isinstance(entry.get("options"), dict) else {}
    options.update({"apiKey": at.key, "baseURL": at.base("anthropic"), "authMode": "api-key"})
    headers = dict(options.get("headers") or {}) if isinstance(options.get("headers"), dict) else {}
    headers["User-Agent"] = UA
    options["headers"] = headers
    entry["options"] = options
    old = entry.get("models") if isinstance(entry.get("models"), dict) else {}
    models: dict[str, Any] = {}
    for row in at.models:
        key = flat_id(row["id"])
        model = dict(old.get(key) or {}) if isinstance(old.get(key), dict) else {}
        model["name"] = row.get("name") or row["id"]
        model.pop("limit", None)
        limit = {}
        if row.get("context_window"):
            limit["context"] = int(row["context_window"])
        if row.get("max_tokens"):
            limit["output"] = int(row["max_tokens"])
        if limit:
            model["limit"] = limit
        # a thinking model's levels, for its effort picker: none is left out,
        # MiniMax Code would ask for it as a level; high where the model has
        # it, as MiniMax Code starts its own models on
        levels = [level for level in row.get("thinking_levels") or [] if level not in ("none", "off")]
        model["reasoning"] = bool(levels)
        was = (model.get("thinking") or {}).get("defaultEffort") if isinstance(model.get("thinking"), dict) else None
        model.pop("thinking", None)
        if levels:
            thinking: dict[str, Any] = {"effortOptions": levels}
            default = _kept_effort(was, levels, "high" if "high" in levels else "")
            if default:
                thinking["defaultEffort"] = default
            model["thinking"] = thinking
        caps = model.get("capabilities") if isinstance(model.get("capabilities"), dict) else None
        if row.get("vision"):
            model["capabilities"] = {**(caps or {}), "support_image": True}
        elif caps is not None:
            caps = {k: v for k, v in caps.items() if k != "support_image"}
            if caps:
                model["capabilities"] = caps
            else:
                model.pop("capabilities", None)
        models[key] = model
    entry["models"] = models
    return entry


def _wire(at: Wiring) -> list[Write]:
    from ..conversion import flat_id
    path = config_path()
    text = ConfigText.read(path)
    doc = edit.load("yaml", text.text, str(path)) if text.text.strip() else {}
    current = edit.get_path(doc, ENTRY)[1]
    sets = {ENTRY: entry_for(at, current), "defaultModel": f"{PROVIDER}/{flat_id(at.model)}"}
    # a variant is the last model's ("thinking"), none of OpenProgram's
    # the entry is OpenProgram's whole: it goes on disconnect, edited or not
    # (magpie DelYAML mcodeEntry)
    return [Write(path, "yaml", sets=sets, deletes=["defaultModelVariant"], owns=(ENTRY,), listing=True)]


def _unwire(record: dict[str, Any]) -> None:
    """The entry out, however MiniMax Code changed it since, and an empty
    ``custom_provider`` where it was the only one."""
    path = config_path()
    text = ConfigText.read(path)
    if not text.exists:
        return
    doc = edit.load("yaml", text.text, str(path))
    if not edit.get_path(doc, ENTRY)[0]:
        return
    deletes = [ENTRY]
    rest = doc.get("custom_provider")
    if isinstance(rest, dict) and set(rest) == {PROVIDER_KEY}:
        deletes = ["custom_provider"]
    text.write(edit.edit("yaml", text.text, deletes=deletes, where=str(path)))


def _check() -> str:
    path = config_path()
    try:
        doc = edit.load("yaml", ConfigText.read(path).text, str(path))
    except edit.ConfigUnreadable:
        return ""
    if not str(doc.get("defaultModel") or "").startswith(PROVIDER + "/"):
        return ""
    present, entry = edit.get_path(doc, ENTRY)
    if not present:
        return f"MiniMax Code's {ENTRY} (config.yaml) is gone, so it no longer reaches OpenProgram"
    if isinstance(entry, dict) and entry.get("enabled") is False:
        return f"MiniMax Code's {ENTRY} (config.yaml) is turned off, so it no longer reaches OpenProgram"
    return ""


def _has_desktop() -> bool:
    from .base import app_bundles
    return any(app_bundles(app) for app in DESKTOP)


def _notice() -> str:
    from .running import any_running
    if _has_desktop():
        return ("MiniMax Code's desktop app keeps the model last picked in it: restart the app, then pick an "
                "OpenProgram model once in its model menu. Open mcode sessions need a restart too.")
    if any_running(r"(^|/)mcode( |$)"):
        return "MiniMax Code reads its settings at start-up — restart open mcode sessions to use this."
    return ""


def agent() -> Agent:
    return Agent(
        id="minimax_code", name="MiniMax Code", icon="minimax", aliases=("minimax-code", "mcode"),
        kind="cli", protocol="anthropic", bin="mcode", dir=home, path=config_path,
        fields=[Field("model", "Model")], ua=(UA, "minimaxcode"), wire=_wire, unwire=_unwire,
        check=_check, notice=_notice, loose=(ENTRY,), detect=_has_desktop,
        # its config is a file others may read: a key that names it
        named_key=True,
    )
