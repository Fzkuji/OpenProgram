"""Tuanjie Cowork (codely.tuanjie.cn, Unity China's coworker app; ``~/.codely/settings.json``).

After yetone/magpie ``internal/agent/tuanjiecowork.go`` (MIT License,
Copyright (c) yetone). Tuanjie Cowork is a Tauri app,
``dev.codelycowork.desktop``, whose core runs Codely sessions. Its models of
one's own are its Settings' Custom Models, kept in ``~/.codely/settings.json``
(the core's settings path; read at 2.1.6-stable). The file is read afresh on
every lookup and written whole, JSON with two-space indent, other keys kept::

    "customModelProviders": [{"id": "custom-provider", "name": …, "baseUrl": …, "apiKey": …,
      "authType": "openai", "flashModelId"?, "multimodalModelId"?}, …],
    "customModels": [{"id": …, "model": …, "providerId": …, "displayName": …,
      "roles": ["model"], "wireApi": "chat"|"responses"|"messages", "supportsMultimodal": …}, …]

A model's id is unique across providers. A session on one is started with
its provider's baseUrl and key as ``OPENAI_BASE_URL`` and ``OPENAI_API_KEY``,
and Cowork lists a provider's models at ``baseUrl + /models`` when baseUrl
ends in ``/v1``.

So connecting adds a provider and a model of OpenProgram's own, both with id
``openprogram``: the gateway's ``/v1`` with a key naming Cowork, and the
model picked, on chat completions. A name, roles or other fields given them
in Cowork stay. The user's providers and models stay as they are, and an id
``openprogram`` of the user's own is theirs: nothing is written over it.
Which model a chat is on is Cowork's own pick in its model menu (per
workspace, by model id), which OpenProgram doesn't write: the user picks
"<model> (openprogram)" there once, and since the id stays ``openprogram``, a
model changed here later is the one Cowork's pick then sends. Cowork's
startup migrations only move models with no providerId, so these entries are
left alone.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import Agent, App, Members, Wiring, Write, app_bundles

ID = "openprogram"
PROVIDERS = "customModelProviders"
MODELS = "customModels"
APP = App("dev.codelycowork.desktop", ("Tuanjie Cowork.app",))
#: Its app process: the .app's cowork, the Windows build's cowork.exe.
PROCESS = (r"Tuanjie Cowork\.app/Contents/MacOS/cowork", r"(^|[/\\])cowork\.exe")


def home() -> Path:
    return Path.home() / ".codely"


def config_path() -> Path:
    return home() / "settings.json"


def _find(doc: dict[str, Any], key: str, field: str, value: str) -> dict[str, Any] | None:
    for entry in doc.get(key) or []:
        if isinstance(entry, dict) and entry.get(field) == value:
            return entry
    return None


def _wire(at: Wiring) -> list[Write]:
    from .engine import AgentConfigError
    path, v1 = config_path(), at.base("openai-chat")
    try:
        doc = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        doc = {}
    if not isinstance(doc, dict):
        raise AgentConfigError(f"{path} isn't settings Tuanjie Cowork reads, so OpenProgram leaves it alone")
    provider = _find(doc, PROVIDERS, "id", ID)
    # the provider with OpenProgram's id is OpenProgram's by its key, or by the gateway's address
    if provider is not None and provider.get("apiKey") != at.key and provider.get("baseUrl") != v1:
        raise AgentConfigError(f"Tuanjie Cowork has a model provider of its own with the id {ID} ({path}), "
                               "so OpenProgram leaves it alone")
    model = _find(doc, MODELS, "id", ID)
    if model is not None and model.get("providerId") != ID:
        raise AgentConfigError(f"Tuanjie Cowork has a model of its own with the id {ID} ({path}), "
                               "so OpenProgram leaves it alone")
    model = _find(doc, MODELS, "providerId", ID) if model is None else model
    pv: dict[str, Any] = dict(provider or {})
    pv.update({"id": ID, "baseUrl": v1, "apiKey": at.key, "authType": "openai"})
    if not pv.get("name"):
        pv["name"] = ID
    sees = next((bool(m.get("vision")) for m in at.models if m["id"] == at.model), False)
    mv: dict[str, Any] = dict(model or {})
    mv.update({"id": ID, "providerId": ID, "model": at.model, "displayName": at.model,
               "wireApi": "chat", "supportsMultimodal": sees})
    if not isinstance(mv.get("roles"), list) or not mv["roles"]:
        mv["roles"] = ["model"]
    # only where the entries point says they are this gateway's; the rest
    # Tuanjie Cowork's own settings change, and that is no drift
    return [Write(path, "json", members={
        PROVIDERS: Members({"id": ID}, [dict(sorted(pv.items()))], loose=_loose(pv)),
        MODELS: Members({"providerId": ID}, [dict(sorted(mv.items()))], loose=_loose(mv)),
    })]


#: The fields of OpenProgram's entries that say they are this gateway's.
CHECKED = ("id", "baseUrl", "apiKey", "authType", "providerId", "model")


def _loose(entry: dict[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(name for name in entry if name not in CHECKED))


def _notice() -> str:
    from .running import any_running
    if any_running(*PROCESS):
        return "Pick OpenProgram's model in Tuanjie Cowork's model menu; if it isn't there yet, restart Tuanjie Cowork."
    return ""


def _detect() -> bool:
    """Its folder once it has run, else its app."""
    return home().is_dir() or bool(app_bundles(APP))


def agent() -> Agent:
    return Agent(
        id="tuanjie-cowork", name="Tuanjie Cowork", icon="tuanjie-cowork-color",
        aliases=("tuanjie", "tjcowork", "codely-cowork", "tuanjiecowork"), kind="app", protocol="openai-chat",
        dir=home, path=config_path, named_key=True, detect=_detect, wire=_wire, notice=_notice,
    )
