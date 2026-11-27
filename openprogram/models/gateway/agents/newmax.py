"""NewMax (``newmax.db`` in ``$NEWMAX_USER_DATA_DIR``, default ``~/.newmax``).

After yetone/magpie ``internal/agent/newmax.go`` (MIT License, Copyright (c)
yetone). NewMax (newmax.cc, 牛马AI) is a desktop agent app on the Claude Agent
SDK. It keeps its settings in its own SQLite database: the row ``app`` of its
table ``settings`` (``key TEXT PRIMARY KEY, value TEXT NOT NULL``) is
``JSON.stringify`` of all its settings, and their providers are a list in it::

    {…, "providerId":"default", "model":"default",
     "providers":[…, {"id":"openprogram-gateway","name":"OpenProgram",
       "baseUrl":"http://127.0.0.1:18100/gateway/anthropic","apiKey":"…",
       "apiKeys":["…"],"apiKeyRotationEnabled":false,
       "apiKeyField":"ANTHROPIC_AUTH_TOKEN","apiFormat":"anthropic",
       "models":[…],"enabledModels":[…],"enabled":true,"canDelete":true}],
     "providerOrder":["default","openprogram-gateway"], …}

That is the entry NewMax's own provider import writes: an Anthropic base URL
without ``/v1`` (it posts to baseUrl + ``/v1/messages`` through the Claude
Code its SDK runs, as ``ANTHROPIC_BASE_URL`` and ``ANTHROPIC_AUTH_TOKEN``), at
most 20 models (``MAX_MODELS``: a primary and 19 back-ups, the picked model
first), and the id added to ``providerOrder``. A provider is listed in the
model picker whether or not ``providerOrder`` has it, so the model is picked
there.

NewMax reads the row when it starts and writes it whole from what it holds
whenever a setting changes, so a change made while it runs is lost the next
time it saves: connecting is refused then, where that can be told (not on
Windows). The row is edited in place, in one transaction
(``edit.SqliteRowText``): the entry and its id in ``providerOrder`` go in and
out, and the rest of the row stays byte for byte. The user's name for the
entry, its on/off and the fields NewMax adds to it (capabilities it tested)
stay through a sync.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from .. import edit
from ..edit import ConfigText, ConfigUnreadable, row_path
from .base import Agent, App, Field, Wiring, Write

PROVIDER_ID = "openprogram-gateway"
MAX_MODELS = 20
APP = App("cc.newmax.desktop", ("NewMax.app",))
#: NewMax's process: the .app's executable, the Linux build's ``newmax``.
PROCESS = (r"^([^ ]*/)?(NewMax|newmax)( |$)", r"NewMax\.app/Contents/MacOS/NewMax( |$)")
#: The entry's keys NewMax's settings own once it is there.
KEPT = ("id", "name", "enabled", "canDelete")


def home() -> Path:
    """NewMax's data folder: ``$NEWMAX_USER_DATA_DIR`` when it names one, else ``~/.newmax``."""
    named = (os.environ.get("NEWMAX_USER_DATA_DIR") or "").strip()
    if named and os.path.isabs(named):
        return Path(named)
    return Path.home() / ".newmax"


def db_path() -> Path:
    return home() / "newmax.db"


def config_path() -> Path:
    """The ``app`` row of the ``settings`` table, read and written as a file's text."""
    return row_path(db_path(), "settings", "app")


def _running() -> bool:
    from .running import any_running
    return sys.platform != "win32" and any_running(*PROCESS)


def _app(text: ConfigText) -> dict[str, Any]:
    """The row as an object; one that isn't, or whose providers aren't a list, is left alone."""
    doc = edit.load("json", text.text, "NewMax's settings") if text.text.strip() else {}
    providers = doc.get("providers")
    if providers is not None and not isinstance(providers, list):
        raise ConfigUnreadable("NewMax's providers aren't a list, so OpenProgram leaves them alone")
    order = doc.get("providerOrder")
    if order is not None and not (isinstance(order, list) and all(isinstance(x, str) for x in order)):
        raise ConfigUnreadable("NewMax's providerOrder isn't a list of names, so OpenProgram leaves it alone")
    return doc


def entry_for(at: Wiring) -> dict[str, Any]:
    """OpenProgram's provider, in the order NewMax's import writes one
    (magpie ``newMaxEntryFor``): its Anthropic Messages, the key that names
    NewMax, and the models, each once, at most 20, the picked one first, with
    the context windows known."""
    entry: dict[str, Any] = {
        "id": PROVIDER_ID, "name": "OpenProgram", "baseUrl": at.base("anthropic"),
        "apiKey": at.key, "apiKeys": [at.key], "apiKeyRotationEnabled": False,
        "apiKeyField": "ANTHROPIC_AUTH_TOKEN", "apiFormat": "anthropic",
        "models": [], "enabledModels": [], "enabled": True, "canDelete": True,
    }
    windows: dict[str, int] = {}
    for row in sorted(at.models, key=lambda row: row["id"] != at.model):
        if row["id"] in entry["models"]:
            continue
        entry["models"].append(row["id"])
        if row.get("context_window"):
            windows[row["id"]] = int(row["context_window"])
        if len(entry["models"]) == MAX_MODELS:
            break
    entry["enabledModels"] = list(entry["models"])
    if windows:
        entry["modelContextWindows"] = windows
    return entry


def _wire(at: Wiring) -> list[Write]:
    if not db_path().is_file():
        raise ConfigUnreadable("NewMax hasn't made its settings yet: open NewMax once, quit it, then try again")
    if _running() and (at.previous.get("values") or {}) != at.values:
        # a sync while it runs is written as well: NewMax's next save puts
        # back what it holds, and the next sync OpenProgram's models
        raise ConfigUnreadable("NewMax writes its settings back over what OpenProgram writes now: "
                               "quit NewMax, then try again")
    path = config_path()
    doc = _app(ConfigText.read(path))
    providers = list(doc.get("providers") or [])
    want = entry_for(at)
    at_index = next((i for i, e in enumerate(providers) if isinstance(e, dict) and e.get("id") == PROVIDER_ID), -1)
    if at_index < 0:
        providers.append(want)
    else:
        # what OpenProgram says, over the entry NewMax keeps
        merged = dict(providers[at_index])
        merged.update({key: value for key, value in want.items() if key not in KEPT})
        providers[at_index] = merged
    order = list(doc.get("providerOrder") or [])
    if PROVIDER_ID not in order:
        order = (order or ["default"]) + [PROVIDER_ID]
    return [Write(path, "json", sets={"providers": providers, "providerOrder": order}, listing=True)]


def _unwire(record: dict[str, Any]) -> None:
    """OpenProgram's provider out of the row, its id out of ``providerOrder``,
    a pick of it back to NewMax's default; a row left with nothing but what
    OpenProgram made goes (magpie ``newMaxUnwire``)."""
    path = config_path()
    text = ConfigText.read(path)
    if not text.exists:
        return
    doc = _app(text)
    providers = list(doc.get("providers") or [])
    left = [e for e in providers if not (isinstance(e, dict) and e.get("id") == PROVIDER_ID)]
    sets: dict[str, Any] = {}
    if len(left) != len(providers):
        sets["providers"] = left
    order = doc.get("providerOrder") or []
    if PROVIDER_ID in order:
        sets["providerOrder"] = [x for x in order if x != PROVIDER_ID]
    if doc.get("providerId") == PROVIDER_ID:
        sets.update({"providerId": "default", "model": "default"})
    if not sets:
        return
    after = {**doc, **sets}
    if set(after) == {"providers", "providerOrder"} and not left and after["providerOrder"] == ["default"]:
        # the row OpenProgram made: its empty list and NewMax's default order
        text.delete()
        return
    try:
        text.write(edit.edit("json", text.text, sets=sets, where="NewMax's settings"))
    except OSError as exc:
        raise ConfigUnreadable(f"NewMax's settings: {exc}") from exc


def _notice() -> str:
    if _running():
        return ("NewMax keeps its settings in memory while it runs and writes them all back when one "
                "changes — quit NewMax, then open it and pick an OpenProgram model in its model picker.")
    return ""


def agent() -> Agent:
    return Agent(
        id="newmax", name="NewMax", icon="", aliases=("newmax-ai", "newmaxai"), kind="app",
        protocol="anthropic", dir=home, path=config_path, fields=[Field("model", "Model")],
        named_key=True, wire=_wire, unwire=_unwire, notice=_notice, app=APP,
        loose=("providers", "providerOrder"), detect=lambda: home().is_dir(),
    )
