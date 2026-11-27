"""Qwen Code (``qwen``, QwenLM/qwen-code; ``$QWEN_HOME/settings.json``, default ``~/.qwen``).

After yetone/magpie ``internal/agent/qwen.go`` (MIT License, Copyright (c)
yetone). Qwen Code is a Gemini CLI fork. Models of the user's own are the
``modelProviders.<protocol>`` arrays, each ``{baseUrl, envKey, id, name}``:
``id`` is the model asked for, ``name`` what its ``/model`` picker shows, and
``envKey`` names a variable in the same file's ``env`` object (or
``~/.qwen/.env``) holding the key. The model sessions start on is
``settings.model`` ``{name, baseUrl}``, ``baseUrl`` telling two entries of
one id apart, and ``security.auth.selectedType = "openai"`` skips the
``/auth`` prompt on start.

Connecting appends one entry per catalog model to ``modelProviders.openai``,
id ``<provider>/<model>`` as the gateway takes it, every one on the env key
of OpenProgram's own (``OPENPROGRAM_QWEN_API_KEY``), which is how an entry of
OpenProgram's is told from the user's; the user's entries stay in front, so
their ids don't move. The key lives in the settings' ``env`` object: a
shell-exported or ``~/.qwen/.env`` variable of the same name would win over
it, which the Qwen-specific name makes unlikely. ``settings.model`` and the
auth type point at the pick; what the user had there is stashed and put
back on disconnect. Qwen Code's requests carry ``QwenCode/<version>``.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .base import Agent, Members, Wiring, Write

MODELS = "modelProviders.openai"
#: The variable OpenProgram's entries take their key from, set in the
#: settings' env object; it also names an entry as OpenProgram's.
ENV_KEY = "OPENPROGRAM_QWEN_API_KEY"


def home() -> Path:
    return Path(os.environ.get("QWEN_HOME") or str(Path.home() / ".qwen")).expanduser()


def config_path() -> Path:
    return home() / "settings.json"


def entries(models: list[dict[str, Any]], v1: str) -> list[dict[str, Any]]:
    """OpenProgram's ``modelProviders.openai`` entries, keys in Qwen Code's
    own (alphabetical) order (magpie ``qwenEntriesAt``)."""
    out = []
    for model in models:
        entry: dict[str, Any] = {"baseUrl": v1, "envKey": ENV_KEY}
        if model.get("context_window"):
            entry["generationConfig"] = {"contextWindowSize": int(model["context_window"])}
        entry["id"] = model["id"]
        entry["name"] = model.get("name") or model["id"]
        out.append(entry)
    return out


def _wire(at: Wiring) -> list[Write]:
    path, v1 = config_path(), at.base("openai-chat")
    return [
        Write(path, "json", listing=True,
              members={MODELS: Members({"envKey": ENV_KEY}, entries(at.models, v1))}),
        # baseUrl tells the pick's entry from a user's entry of the same id
        Write(path, "json", sets={f"env.{ENV_KEY}": at.key, "security.auth.selectedType": "openai",
                                  "model.name": at.model, "model.baseUrl": v1}),
    ]


def _notice() -> str:
    from .running import any_running
    if any_running(r"(^|/)qwen( |$)"):
        return "Qwen Code reads a session's model at start-up — start a new session, or /model anew, to use this."
    return ""


def agent() -> Agent:
    return Agent(
        id="qwen", name="Qwen Code", icon="qwen-color", aliases=("qwen-code", "qwen-cli"), kind="cli",
        protocol="openai-chat", bin="qwen", dir=home, path=config_path,
        # QwenCode/<version> (core/openaiContentGenerator/provider/default.ts buildHeaders)
        # a key naming Qwen Code, not the gateway key, in its settings file
        ua=("qwencode",), named_key=True, wire=_wire, notice=_notice,
    )
