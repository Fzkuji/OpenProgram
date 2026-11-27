"""AstrBot (AstrBotDevs/AstrBot; ``<root>/data/cmd_config.json``).

After yetone/magpie ``internal/agent/astrbot.go`` (MIT License, Copyright (c)
yetone). AstrBot is a chatbot framework for QQ, Telegram, Discord, Lark and
the rest. It keeps every setting in one file, ``<root>/data/cmd_config.json``,
the root being ``$ASTRBOT_ROOT``, else ``~/.astrbot`` for the desktop app,
else a folder ``astrbot init`` marked with a ``.astrbot`` file (``astrbot
run`` there sets ``ASTRBOT_ROOT`` to it).

Since 4.10 the file splits a chat provider into two lists:
``provider_sources`` (an endpoint and its keys) and ``provider`` (one model
of a source each, ``id`` ``"<source>/<model>"``); what AstrBot runs is the
two merged, the model's keys winning. Connecting adds a source
``openprogram``, OpenAI Compatible at the gateway with a key naming AstrBot
(its requests carry no User-Agent of its own), and an entry
``openprogram/<provider>/<model>`` per enabled model, and makes the pick
AstrBot's default chat model: ``agent_runner.config.model.provider_id``
(read when ``agent_runner.runner_type`` is ``local``, 4.28 on), or
``provider_settings.default_provider_id`` in a file from before
``agent_runner``. The user's own sources and models stay, and so does what
they changed in OpenProgram's entries in AstrBot's WebUI (a source's
timeout or proxy, a model switched off, its extra body). The file is copied
aside before the first write, and its UTF-8 BOM (AstrBot writes
``utf-8-sig``) is kept.

AstrBot reads the file once, at start, and its WebUI writes the whole of it
back on every save: a change needs AstrBot restarted, before anything is
saved in its WebUI. AstrBot in Docker is not wired: its data folder is a
volume, and this machine's loopback is not the container's.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .base import Agent, Members, Wiring, Write

SOURCES = "provider_sources"
MODELS = "provider"
#: Where AstrBot's default chat model is named, from 4.28 and before it.
RUNNER = "agent_runner.config.model.provider_id"
LEGACY = "provider_settings.default_provider_id"
SOURCE_ID = "openprogram"

#: The keys of OpenProgram's entries that are OpenProgram's to say; every
#: other key is the user's to change in AstrBot's WebUI, and stays.
SOURCE_OWNED = ("id", "provider", "type", "provider_type", "key", "api_base")
MODEL_OWNED = ("id", "provider_source_id", "model", "modalities", "max_context_tokens")
#: The keys of those entries AstrBot's WebUI changes (default.py's schema):
#: a change there is the user's, not drift.
SOURCE_LOOSE = ("enable", "timeout", "proxy", "custom_headers", "max_retries", "hint")
MODEL_LOOSE = ("enable", "custom_extra_body", "hint", "temperature", "max_tokens")


def root() -> Path:
    """``$ASTRBOT_ROOT``, else the desktop app's ``~/.astrbot``, else a folder
    ``astrbot init`` was run in that can be known: the home itself (a
    ``.astrbot`` file there), or ``~/astrbot`` / ``~/AstrBot``, where its
    README and a clone of its source put it (magpie ``astrbotRoot``)."""
    env = os.environ.get("ASTRBOT_ROOT", "").strip()
    if env:
        return Path(env).expanduser()
    home = Path.home()
    desktop = home / ".astrbot"
    if (desktop / "data" / "cmd_config.json").is_file():
        return desktop
    if desktop.is_file():
        return home
    for name in ("astrbot", "AstrBot"):
        folder = home / name
        if (folder / "data" / "cmd_config.json").is_file() or (folder / ".astrbot").is_file():
            return folder
    return desktop


def data_dir() -> Path:
    return root() / "data"


def config_path() -> Path:
    return data_dir() / "cmd_config.json"


def source_entry(v1: str, key: str) -> dict[str, Any]:
    """OpenProgram's provider source: AstrBot's OpenAI Compatible template
    (default.py) at the gateway, with a key naming AstrBot."""
    return {"id": SOURCE_ID, "provider": "openai", "type": "openai_chat_completion",
            "provider_type": "chat_completion", "enable": True, "key": [key], "api_base": v1,
            "timeout": 120, "proxy": "", "custom_headers": {}}


def model_entry(model: dict[str, Any]) -> dict[str, Any]:
    """One model as an entry of AstrBot's ``provider`` list. Every one takes
    tools: AstrBot drops a model's tools when ``tool_use`` isn't among its
    modalities, and the gateway hands a tool call on whatever the model. A
    window not known is 0, which has AstrBot look it up itself."""
    modalities = ["text", *(["image"] if model.get("vision") else []), "tool_use"]
    return {"id": f"{SOURCE_ID}/{model['id']}", "enable": True, "provider_source_id": SOURCE_ID,
            "model": model["id"], "modalities": modalities, "custom_extra_body": {},
            "max_context_tokens": int(model.get("context_window") or 0)}


def _kept(have: list[Any], want: list[dict[str, Any]], owned: tuple[str, ...]) -> list[dict[str, Any]]:
    """``want``, each with the keys not OpenProgram's to say of the entry of
    the same id already there kept as the user left them (magpie ``astrbotKeep``)."""
    by_id = {item.get("id"): item for item in have if isinstance(item, dict)}
    out = []
    for entry in want:
        for key, value in (by_id.get(entry["id"]) or {}).items():
            if key not in owned:
                entry[key] = value
        out.append(entry)
    return out


def _wire(at: Wiring) -> list[Write]:
    from .. import edit
    from .engine import AgentConfigError
    path = config_path()
    text = edit.ConfigText.read(path)
    if not text.exists:
        raise AgentConfigError(f"AstrBot has no {path} yet: start AstrBot once so it writes its settings "
                               "(or start OpenProgram with ASTRBOT_ROOT set to the folder AstrBot runs in), "
                               "then connect it")
    doc = edit.load("json", text.text, str(path))
    if SOURCES not in doc:
        raise AgentConfigError(f"this AstrBot keeps no provider_sources in {path}: update AstrBot to 4.10 or "
                               "later, start it once, then connect it")
    sources = doc.get(SOURCES) if isinstance(doc.get(SOURCES), list) else []
    models = doc.get(MODELS) if isinstance(doc.get(MODELS), list) else []
    source = _kept(sources, [source_entry(at.base("openai-chat"), at.key)], SOURCE_OWNED)
    listed = _kept(models, [model_entry(m) for m in at.models], MODEL_OWNED)
    pick = RUNNER if "agent_runner" in doc else LEGACY
    return [
        # what the WebUI changes in OpenProgram's entries is loose: no drift,
        # and sync and rewire go on; disconnecting takes the entries out
        # whole, edited or not (Members removes by match)
        Write(path, "json", listing=True, members={
            SOURCES: Members({"id": SOURCE_ID}, source, loose=SOURCE_LOOSE),
            MODELS: Members({"provider_source_id": SOURCE_ID}, listed, loose=MODEL_LOOSE),
        }),
        Write(path, "json", sets={pick: f"{SOURCE_ID}/{at.model}"}),
    ]


def _notice() -> str:
    return ("AstrBot reads cmd_config.json at start-up and writes it back whole when its WebUI saves "
            "settings — restart AstrBot to use this, before saving anything in its WebUI.")


def agent() -> Agent:
    return Agent(
        id="astrbot", name="AstrBot", icon="astrbot", kind="bot", protocol="openai-chat",
        bin="astrbot", dir=data_dir, path=config_path, named_key=True, before_copy=True,
        wire=_wire, notice=_notice,
    )
