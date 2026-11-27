"""Empryo (``~/.empryo/config.json``).

After yetone/magpie ``internal/agent/empryo.go`` (MIT License, Copyright (c)
yetone). Empryo's own providers are a ``providers`` array of ``{id, name,
baseURL, envVar, models, modelsAPI}``, OpenAI-compatible endpoints it lists
models from (``modelsAPI``, else ``baseURL``'s ``/models``) and asks
``/chat/completions`` of; with no ``envVar`` it sends no key of the user's. The
model sessions start on is ``defaultModel``, ``<provider id>/<model id>``,
split at the first ``/`` (a project's ``.empryo/config.json`` goes over it).

Connecting adds a provider of OpenProgram's own, id ``openprogram``, at the
gateway, and sets ``defaultModel`` to ``openprogram/<provider>/<model>``; the
``defaultModel`` the user had is put back on disconnect, and the provider
goes again. The user's providers and other keys are left as they are. With no
``envVar`` Empryo only lists a provider whose baseURL answers (2xx, 401 or
403), which the gateway's ``GET /v1`` does. With no key sent, the gateway
key is a segment of the base URL (``Agent.path_keyed``).
"""
from __future__ import annotations

from pathlib import Path

from .base import Agent, Members, Wiring, Write

PROVIDER_ID = "openprogram"


def home() -> Path:
    return Path.home() / ".empryo"


def config_path() -> Path:
    return home() / "config.json"


def _wire(at: Wiring) -> list[Write]:
    v1 = at.base("openai-chat")
    mine = {"id": PROVIDER_ID, "name": "OpenProgram", "baseURL": v1, "modelsAPI": v1 + "/models"}
    return [Write(config_path(), "json", sets={"defaultModel": f"{PROVIDER_ID}/{at.model}"},
                  members={"providers": Members({"id": PROVIDER_ID}, [mine])})]


def _notice() -> str:
    from .running import any_running
    if any_running(r"(^|/)empryo( |$)"):
        return "Empryo reads its config at start-up — restart open empryo sessions to use this."
    return ""


def agent() -> Agent:
    return Agent(
        id="empryo", name="Empryo", icon="empryo-color", aliases=("soulforge",), kind="cli",
        protocol="openai-chat", bin="empryo", dir=home, path=config_path, path_keyed=True, wire=_wire,
        notice=_notice,
    )
