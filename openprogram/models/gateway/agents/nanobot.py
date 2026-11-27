"""nanobot (HKUDS/nanobot, the nanobot-ai package; ``~/.nanobot/config.json``).

After yetone/magpie ``internal/agent/nanobot.go`` (MIT License, Copyright (c)
yetone). nanobot is a personal assistant that answers in chat apps, its CLI
and its WebUI. Its config is plain JSON its pydantic models read with
camelCase or snake_case keys alike. The model it answers with is
``agents.defaults.model``, asked of ``agents.defaults.provider`` ("auto" picks
one by the model's name), unless ``agents.defaults.modelPreset`` names one of
``modelPresets``, which then goes over both. Any key under ``providers`` that
isn't one of its own is an OpenAI-compatible endpoint of the user's at its
``apiBase``, sent ``apiKey`` and ``extraHeaders``, and a model ``<that
key>/<id>`` is sent to it as ``<id>``.

So connecting adds ``providers.openprogram`` at the gateway, with its key and
a User-Agent of nanobot's name (the OpenAI SDK's otherwise, which tells it
from no one), and sets ``agents.defaults`` to provider ``openprogram``, model
``openprogram/<provider>/<model>`` and the window the model takes. What the
user had there, either spelling, and a ``modelPreset`` that would go over it,
is taken out, kept and put back on disconnect; ``providers.openprogram`` goes
again then. The user's other providers, presets and keys stay as they are.

nanobot's gateway (``nanobot gateway``, the WebUI's) watches the file and
takes a change at its next turn; ``nanobot agent`` reads it once. No command
detects it: nanobot is other tools' name too; ``~/.nanobot`` is made by
``nanobot onboard`` and its first run.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import Agent, Wiring, Write

PROVIDER_ID = "openprogram"
#: The User-Agent nanobot is given to send, in its provider's extraHeaders.
USER_AGENT = "nanobot"
#: agents.defaults' keys OpenProgram writes or takes out, camelCase (as
#: nanobot writes them) and the snake_case spellings nanobot reads as well.
DEFAULTS = (("provider", "provider"), ("model", "model"),
            ("contextWindowTokens", "context_window_tokens"), ("modelPreset", "model_preset"))


def home() -> Path:
    return Path.home() / ".nanobot"


def config_path() -> Path:
    return home() / "config.json"


def _window(models: list[dict[str, Any]], ref: str) -> int:
    return next((int(m.get("context_window") or 0) for m in models if m["id"] == ref), 0)


def _wire(at: Wiring) -> list[Write]:
    sets: dict[str, Any] = {
        f"providers.{PROVIDER_ID}": {"apiKey": at.key, "apiBase": at.base("openai-chat"),
                                     "extraHeaders": {"User-Agent": USER_AGENT}},
        "agents.defaults.provider": PROVIDER_ID,
        "agents.defaults.model": f"{PROVIDER_ID}/{at.model}",
    }
    window = _window(at.models, at.model)
    if window > 0:
        sets["agents.defaults.contextWindowTokens"] = window
    deletes = [f"agents.defaults.{name}" for pair in DEFAULTS for name in dict.fromkeys(pair)
               if f"agents.defaults.{name}" not in sets]
    # the provider is OpenProgram's whole: it goes on disconnect, edited or
    # not (magpie DelJSON providers.magpie)
    return [Write(config_path(), "json", sets=sets, deletes=deletes, owns=(f"providers.{PROVIDER_ID}",))]


def _notice() -> str:
    from .running import any_running
    if any_running(r"(^|/)nanobot( |$)"):
        return "nanobot's gateway and WebUI take this at their next reply; restart an open `nanobot agent` chat to use it there."
    return ""


def agent() -> Agent:
    return Agent(
        id="nanobot", name="nanobot", icon="nanobot-color", aliases=("nanobot-ai",), kind="bot",
        protocol="openai-chat", dir=home, path=config_path, ua=(USER_AGENT,), named_key=True, wire=_wire,
        notice=_notice,
    )
