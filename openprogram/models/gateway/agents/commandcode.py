"""Command Code (commandcode.ai; ``~/.commandcode/settings.json`` and ``providers.json``).

After yetone/magpie ``internal/agent/commandcode.go`` (MIT License, Copyright
(c) yetone). Command Code keeps its settings in ``~/.commandcode/settings.json``,
the model as ``provider/model``, and takes providers of the user's own in
``~/.commandcode/providers.json``. Connecting adds the provider
``openprogram`` there (``provider.openprogram``), keyless, with the catalog as
its models; a model through OpenProgram is ``openprogram/<provider>/<model>``,
which Command Code splits at the first ``/``, and ``modelProvider`` is set to
``openprogram``. The key is ``false``: Command Code refuses one written out,
so the gateway key is a segment of the base URL (``Agent.path_keyed``).
What the user set inside OpenProgram's provider entry stays, except its model
list. Command Code still wants its own sign-in (``cmd login``) for these.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import Agent, Wiring, Write

PROVIDER_ID = "openprogram"
#: The reasoning levels Command Code knows.
EFFORTS = ("low", "medium", "high", "xhigh", "max")


def home() -> Path:
    return Path.home() / ".commandcode"


def config_path() -> Path:
    return home() / "settings.json"


def providers_path() -> Path:
    return home() / "providers.json"


def provider_entry(models: list[dict[str, Any]], v1: str) -> dict[str, Any]:
    """OpenProgram's entry in providers.json (magpie ``ccProviderJSONAt``)."""
    listed: dict[str, Any] = {}
    for model in models:
        entry: dict[str, Any] = {"name": model.get("name") or model["id"]}
        levels = list(model.get("thinking_levels") or [])
        efforts = [level for level in levels if level in EFFORTS]
        # a model that thinks with no levels OpenProgram knows is marked
        # reasoning alone, which Command Code takes for low, medium and high
        # (its BYOK reference); the gateway fits them to the model
        if efforts:
            entry["reasoning"] = True
            entry["reasoningEfforts"] = efforts
        elif not levels and model.get("reasoning"):
            entry["reasoning"] = True
        # Command Code reads the window and the reply limit from this file
        # alone — its own catalog keys on the bare vendor id and never matches
        # "openprogram/provider/model" — and without them serves every model
        # as its 200K default, compacting a 1M-window one long before it has to
        if model.get("context_window"):
            entry["contextWindow"] = int(model["context_window"])
        if model.get("max_tokens"):
            out, window = int(model["max_tokens"]), int(model.get("context_window") or 0)
            entry["maxOutput"] = min(out, window) if window else out
        listed[model["id"]] = entry
    return {"name": "OpenProgram", "api": "openai-completions", "baseURL": v1, "apiKey": False, "models": listed}


def _wire(at: Wiring) -> list[Write]:
    path = providers_path()
    key = f"provider.{PROVIDER_ID}"
    # the entry is OpenProgram's whole (Write.owns): what the user added inside
    # it stays while connected and is no drift (magpie theirsKept), and it goes
    # whole on disconnect, edited or not (magpie DelJSON)
    return [
        Write(path, "json", listing=True, sets={key: provider_entry(at.models, at.base("openai-chat"))},
              owns=(key,)),
        Write(config_path(), "json", sets={"model": f"{PROVIDER_ID}/{at.model}", "modelProvider": PROVIDER_ID}),
    ]


def _notice() -> str:
    from .running import any_running
    notes = ["Command Code wants its own sign-in (cmd login) even for models through OpenProgram."]
    if any_running(r"(^|/)(cmd|cmdc|command-code|commandcode)( |$)"):
        notes.append("It reads its settings at start-up — restart open Command Code sessions to use this.")
    return " ".join(notes)


def agent() -> Agent:
    return Agent(
        id="commandcode", name="Command Code", icon="commandcode", aliases=("command-code", "cmd"), kind="cli",
        protocol="openai-chat", bin="command-code", dir=home, path=config_path,
        ua=("command-code", "commandcode"), path_keyed=True, wire=_wire, notice=_notice,
    )
