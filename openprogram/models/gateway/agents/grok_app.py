"""Grok App (``<data>/agent-home/config.toml``), a desktop front end for the grok CLI.

After yetone/magpie ``internal/agent/grokapp.go`` (MIT License, Copyright (c)
yetone). Grok App (RongleCat/grok-app, Tauri) starts the grok CLI over ACP.
Its models are its own: its composer lists only Grok Build's official
catalog, never a ``[model."<id>"]`` table of ``~/.grok/config.toml``, and a
model of the user's choosing is a "provider channel" of its Settings ›
Providers page, a ``[model.<id>]`` table with the App's own keys in
``agent-home/config.toml`` under its data folder. That file is the CLI's
config.toml while a channel is on: ``[models] default`` naming a channel is
what the App takes as the route, and it then starts the CLI with
``GROK_HOME`` at agent-home (grok-app's providers.rs,
ensure_independent_for_custom_route).

So OpenProgram adds itself as the channel ``openprogram``, written as the App
writes one (upsert_custom_provider): the gateway as ``base_url`` with Grok
App's own key, chat completions, ``app_models`` a JSON list of the enabled
models with each one's context window, images and effort levels, so
Accounts › Custom providers lists it and the composer can offer the models
and their efforts. It does not set ``[models] default``. That key is the
route, and making it the channel takes the App over: the App then drops its
own sign-in and the session no longer runs. The route stays whatever the App
had; pressing Use on the provider in the App is what switches it, and that
choice is the App's.

The route the App is on when OpenProgram connects is remembered. If the
user pressed Use on OpenProgram, disconnecting puts that route back (or
clears it), since a route naming a channel that is gone does not run; any
other route is the App's and stays. Disconnecting takes OpenProgram's
channel out whole, keys the App added to it too.

Not ported: magpie's one-time release of a route an older magpie set to
itself (no OpenProgram ever set the route), and its model field offering
the App's own routes (Grok official, the user's channels): OpenProgram's
field picks only its own models, and the App's routes are picked in the App.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .base import Agent, Field, Wiring, Write
from .model_rows import PROVIDER_ID, PROVIDER_NAME, efforts, images, name, read_doc, window, write_now

AGENT_ID = "grok_app"
#: The route the App starts on.
ROUTE = "models.default"
#: OpenProgram's channel's table in the App's config.toml.
CHANNEL = f"model.{PROVIDER_ID}"


def data_dir(platform: str | None = None) -> Path:
    """The App's data folder (magpie ``grokAppDir``): ``$GROK_APP_HOME``, else
    where the directories crate's ProjectDirs("com", "grokapp", "grok-app")
    puts its data (grok-app's paths.rs app_data_root) — on a Mac
    ~/Library/Application Support/com.grokapp.grok-app, on Windows
    %APPDATA%\\grokapp\\grok-app\\data, on Linux $XDG_DATA_HOME/grok-app. The
    App's README names ~/.grok-app on Linux and %APPDATA%\\grokapp\\grok-app
    on Windows, and the App falls back to ~/.grok-app (%APPDATA%\\grok-app)
    itself, so those are looked for too; the first that is there wins, else
    the App's own default."""
    moved = os.environ.get("GROK_APP_HOME", "").strip()
    if moved:
        return Path(moved).expanduser()
    home = Path.home()
    platform = platform or sys.platform
    if platform == "darwin":
        dirs = [home / "Library" / "Application Support" / "com.grokapp.grok-app", home / ".grok-app"]
    elif platform == "win32":
        app = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
        dirs = [app / "grokapp" / "grok-app" / "data", app / "grokapp" / "grok-app", app / "grok-app"]
    else:
        data = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
        dirs = [data / "grok-app", home / ".grok-app"]
    return next((d for d in dirs if d.is_dir()), dirs[0])


def config_path() -> Path:
    return data_dir() / "agent-home" / "config.toml"


def default_level(levels: list[str]) -> str:
    """The level the composer starts on (magpie ``zcodeDefaultLevel``):
    medium, else the middle one."""
    if "medium" in levels:
        return "medium"
    on = [level for level in levels if level != "disabled"]
    return on[len(on) // 2] if on else levels[0]


def app_models(models: list[dict[str, Any]], was: str) -> list[dict[str, Any]]:
    """The enabled models as the App is told them (magpie ``grokAppWant``),
    ``was`` the ``app_models`` the channel has now. Grok Build has no effort
    "none" of a channel's: the levels are the model's others, and the one
    the composer starts on is the default the user marked in the App's
    Providers page while the model has it, else medium."""
    try:
        before = json.loads(was) if was else []
    except ValueError:
        before = []  # a list not the App's: no defaults kept
    kept: dict[str, str] = {}
    for entry in before if isinstance(before, list) else []:
        for effort in (entry.get("efforts") or []) if isinstance(entry, dict) else []:
            if isinstance(effort, dict) and effort.get("isDefault"):
                kept[str(entry.get("id"))] = str(effort.get("id"))
    out = []
    for model in models:
        entry: dict[str, Any] = {"id": model["id"], "name": name(model) or model["id"]}
        if window(model):
            entry["contextWindow"] = window(model)
        if images(model):
            entry["supportsVision"] = True
        levels = [level for level in efforts(model) if level != "none"]
        if levels:
            chosen = kept.get(model["id"]) if kept.get(model["id"]) in levels else default_level(levels)
            entry["efforts"] = [{"id": level, "name": level, "isDefault": level == chosen} for level in levels]
        out.append(entry)
    return out


def _compact(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _route(doc: dict[str, Any]) -> Any:
    return (doc.get("models") or {}).get("default")


def _remember_route(path: Path, route: Any) -> None:
    """Keep the App's route, once, so a disconnect can put it back where the
    route became OpenProgram's channel (magpie ``rememberRoute``). Kept as an
    original never written, so a restore alone never touches it."""
    if not isinstance(route, str) or not route or route == PROVIDER_ID:
        return
    from .. import stash
    try:
        stash.remember(AGENT_ID, os.path.realpath(path), {ROUTE: (True, route)}, fmt="toml")
    except stash.StashUnreadable:
        pass


def _wire(at: Wiring) -> list[Write]:
    path = config_path()
    doc = read_doc("toml", path)
    _remember_route(path, _route(doc))
    channel = (doc.get("model") or {}).get(PROVIDER_ID) or {}
    want = app_models(at.models, str(channel.get("app_models") or ""))
    if not want:
        return []
    # a model no longer enabled: the channel moves to the first
    picked = next((m for m in want if m["id"] == at.model), want[0])
    sets: dict[str, Any] = {
        f"{CHANNEL}.model": picked["id"],
        f"{CHANNEL}.base_url": at.base("openai-chat"),
        f"{CHANNEL}.name": PROVIDER_NAME,
        f"{CHANNEL}.api_key": at.key,
        f"{CHANNEL}.api_backend": "chat_completions",
        f"{CHANNEL}.app_provider_mode": "generic",
        f"{CHANNEL}.app_models": _compact(want),
        f"{CHANNEL}.app_efforts": _compact(picked.get("efforts") or []),
        # a TOML bool and integer, as the App and Grok Build read them
        f"{CHANNEL}.supports_reasoning_effort": bool(picked.get("efforts")),
    }
    deletes = []
    if picked.get("supportsVision"):
        sets[f"{CHANNEL}.app_supports_vision"] = "true"
    else:
        deletes.append(f"{CHANNEL}.app_supports_vision")
    if picked.get("contextWindow"):
        sets[f"{CHANNEL}.context_window"] = picked["contextWindow"]
    else:
        deletes.append(f"{CHANNEL}.context_window")
    return [Write(path, "toml", sets=sets, deletes=deletes, listing=True)]


def _unwire(record: dict[str, Any]) -> None:
    """OpenProgram's channel goes whole (magpie ``unwire``); a route still
    naming it, which no longer runs, goes back to the one remembered, or out."""
    path = config_path()
    doc = read_doc("toml", path)
    if not doc:
        return
    sets: dict[str, Any] = {}
    deletes = [CHANNEL] if PROVIDER_ID in (doc.get("model") or {}) else []
    if _route(doc) == PROVIDER_ID:
        files = record.get("files") or {}
        kept = (files.get(os.path.realpath(path)) or files.get(str(path)) or {}).get("originals") or {}
        original = kept.get(ROUTE) or {}
        if original.get("had") and original.get("value"):
            sets[ROUTE] = original["value"]
        else:
            deletes.append(ROUTE)
    write_now([Write(path, "toml", sets=sets, deletes=deletes)])


def _notice() -> str:
    from .running import any_running
    if any_running(r"(^|/)grok-app( |$)"):
        return ("OpenProgram is a custom provider in Grok App's Settings › Account. A chat can pick its models "
                "in its model picker, and Grok App stays on its current route until you press Use on OpenProgram there.")
    return ""


def agent() -> Agent:
    return Agent(
        id=AGENT_ID, name="Grok App", icon="xai", aliases=("grok-app", "grokapp"), kind="app",
        protocol="openai-chat", dir=data_dir, path=config_path, fields=[Field("model", "Model")],
        wire=_wire, unwire=_unwire, notice=_notice,
    )
