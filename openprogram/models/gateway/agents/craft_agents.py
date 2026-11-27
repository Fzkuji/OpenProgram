"""Craft Agents (lukilabs/craft-agents-oss, an Electron app running Claude and
Pi sessions): its model connections are ``llmConnections`` in
``config.json`` in its config folder, ``$CRAFT_CONFIG_DIR`` else
``~/.craft-agent``; the one new sessions start on is the one
``defaultLlmConnection`` names by slug.

After yetone/magpie ``internal/agent/craftagents.go`` (MIT License,
Copyright (c) yetone). OpenProgram adds a connection of its own, a custom
endpoint (``providerType: pi_compat``) on the gateway's Chat Completions::

    {"slug": "openprogram", "name": "OpenProgram", "providerType": "pi_compat",
     "authType": "none", "baseUrl": "<gateway>/agent/craft-agents.<mac>/openai/v1",
     "customEndpoint": {"api": "openai-completions"},
     "models": [...], "defaultModel": "<pick>",
     "modelSelectionMode": "userDefined3Tier", "createdAt": <ms>}

and makes it the default. Craft keeps keys sealed in ``credentials.enc``,
which OpenProgram doesn't open, so the connection has none:
``authType: none`` is what Craft itself saves for a local endpoint without
a key, and its Pi server then sends a placeholder. magpie's gateway takes
that keyless from this computer; OpenProgram's takes no call without a
key, so Craft's own key is a segment of its address (``Agent.path_keyed``),
which also tells its calls apart, as Craft's Pi sends no User-Agent of its
own.

The name the user gave the connection in Craft stays, and so do their own
connections: ``llmConnections`` holds OpenProgram's entry beside theirs
(``Members``). Disconnecting takes that entry out and puts back the default
they had, where that connection is still there; otherwise Craft picks its
first. Craft reads
config.json afresh on every lookup, so a running app takes the change for
its new sessions. It loads no config.json without ``workspaces``, so none
is made: Craft is opened once first.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from .. import edit
from ..edit import ConfigText, load
from .base import Agent, App, Field, Members, Pick, Wiring, Write, app_bundles
from .engine import AgentConfigError
from .pi_like import PROVIDER_ID, read_doc

CONNECTIONS = "llmConnections"
DEFAULT = "defaultLlmConnection"
MATCH = {"slug": PROVIDER_ID, "providerType": "pi_compat"}
#: the connection's fields the user's value of wins
KEPT = ("name",)
APP = App("com.lukilabs.craft-agent", ("Craft Agents.app",))
#: Craft Agents' process: the app's, and the AppImage's craft-agents.
PROCESSES = (r"(^|/)Craft Agents(\.exe)?( |$)", r"(^|/)craft-agents( |$)")


def folder() -> Path:
    value = os.environ.get("CRAFT_CONFIG_DIR", "").strip()
    return Path(value) if value else Path.home() / ".craft-agent"


def config_path() -> Path:
    return folder() / "config.json"


def _ours(entry: Any) -> bool:
    return isinstance(entry, dict) and entry.get("slug") == PROVIDER_ID and entry.get("providerType") == "pi_compat"


def _usable() -> dict[str, Any]:
    """config.json as Craft loads one, or why it is left alone."""
    path = config_path()
    try:
        doc = read_doc(path)
    except Exception as exc:  # noqa: BLE001 - said as why nothing was written
        raise AgentConfigError(f"{path} isn't a config Craft Agents loads, so it was left alone") from exc
    if not doc:
        raise AgentConfigError(f"open Craft Agents once first: it has no {path} yet")
    if not isinstance(doc.get("workspaces"), list):
        raise AgentConfigError(f"{path} isn't a config Craft Agents loads, so it was left alone")
    return doc


def _models(at: Wiring, model: str) -> list[dict[str, Any]]:
    """The enabled models as Craft's ModelDefinition spells one, the pick among them."""
    out, seen = [], set()

    def add(model_id: str, name: str = "", window: int = 0, images: bool = False) -> None:
        if not model_id or model_id in seen:
            return
        seen.add(model_id)
        entry: dict[str, Any] = {"id": model_id, "name": name or model_id, "shortName": name or model_id,
                                 "description": "", "provider": "pi"}
        if window > 0:
            entry["contextWindow"] = window
        if images:
            entry["supportsImages"] = True
        out.append(entry)

    for row in at.models:
        add(row["id"], row.get("name") or "", int(row.get("context_window") or 0), bool(row.get("vision")))
    add(model)
    return out


def _wire(at: Wiring) -> list[Write]:
    doc = _usable()
    listed = doc.get(CONNECTIONS) if isinstance(doc.get(CONNECTIONS), list) else []
    before = next((c for c in listed if _ours(c)), None) or {}
    name = before.get("name") if isinstance(before.get("name"), str) and before.get("name") else "OpenProgram"
    created = before.get("createdAt")
    if not isinstance(created, (int, float)) or isinstance(created, bool):
        created = int(time.time() * 1000)
    custom = before.get("customEndpoint") if isinstance(before.get("customEndpoint"), dict) else {}
    # the model picked since in Craft stays through a sync (magpie's Sync
    # writes the connection's own defaultModel back); a new pick here wins
    model = at.model
    theirs = before.get("defaultModel")
    if isinstance(theirs, str) and theirs and (at.previous.get("values") or {}).get("model") == at.model:
        model = theirs
    entry: dict[str, Any] = {
        "slug": PROVIDER_ID, "name": name, "providerType": "pi_compat", "authType": "none",
        "baseUrl": at.base("openai-chat"), "customEndpoint": {**custom, "api": "openai-completions"},
        "models": _models(at, model), "defaultModel": model, "modelSelectionMode": "userDefined3Tier",
        "createdAt": created}
    path = config_path()
    return [
        # the user's connections stay as they are; the name they gave
        # OpenProgram's stays, and so does what else they put in it
        Write(path, "json", listing=True,
              members={CONNECTIONS: Members(MATCH, [entry], key="slug", kept=KEPT, drop=("piAuthProvider",),
                                            loose=("defaultModel",))}),
        # Craft's own default, which the user switches in Craft: put back
        # while it still names OpenProgram's connection
        Write(path, "json", sets={DEFAULT: PROVIDER_ID}, picks=(Pick((DEFAULT,), DEFAULT, PROVIDER_ID),)),
    ]


def _unwire(record: dict[str, Any]) -> None:
    """The default OpenProgram named goes where the user's own connection is
    gone too (Craft picks its first then); the engine takes OpenProgram's
    connection out and puts back the default the user had."""
    path = config_path()
    text = ConfigText.read(path)
    if not text.exists:
        return
    doc = load("json", text.text, str(path))
    if not isinstance(doc, dict) or doc.get(DEFAULT) != PROVIDER_ID:
        return
    original = (((record.get("files") or {}).get(str(path)) or {}).get("originals") or {}).get(DEFAULT) or {}
    left = [c for c in doc.get(CONNECTIONS) or [] if not _ours(c)] if isinstance(doc.get(CONNECTIONS), list) else []
    if original.get("had") and any(isinstance(c, dict) and c.get("slug") == original.get("value") for c in left):
        return
    text.write(edit.edit("json", text.text, deletes=[DEFAULT], where=str(path)))


def _notice() -> str:
    from .running import any_running
    if any_running(*PROCESSES):
        return "Craft Agents starts its new sessions on this; a session already open stays on the connection it started on."
    return ""


def agent() -> Agent:
    return Agent(
        id="craft-agents", name="Craft Agents", icon="craft-agents", aliases=("craft", "craftagents", "craft-agent"),
        kind="app", protocol="openai-chat", dir=folder, path=config_path, fields=[Field("model", "Model")],
        path_keyed=True, wire=_wire, unwire=_unwire, notice=_notice,
        detect=lambda: folder().is_dir() or bool(app_bundles(APP)), detect_only=True,
    )
