"""fx (fx.sh, Vercel Labs; ``~/.fx/settings.json``).

After yetone/magpie ``internal/agent/fx.go`` (MIT License, Copyright (c)
yetone). fx keeps the provider it talks to under ``provider`` (gateway, codex,
grok or one of the user's own), each provider's model under ``models``, and
providers of the user's own under ``providers``. Connecting adds the provider
``openprogram``: keyless (``auth`` type ``none``), speaking Chat Completions,
the catalog as its ``model_metadata``; ``provider`` becomes ``openprogram``
and ``models.openprogram`` the pick, ``<provider>/<model>``. Each model's
reasoning levels go in its ``reasoning_efforts``, which fx 0.0.17 on lists in
``/effort`` and Ctrl+T and sends as ``reasoning_effort``; an older fx refuses
a key it doesn't know in ``model_metadata``, and with it the whole settings
file, so they are written only for an fx known to take them. fx's own effort
setting, one for every provider, stays the user's.

What the user set on OpenProgram's provider in fx stays: a ``reviewer_model``
(the model its auto permission mode asks) and a ``tool_choice_mode`` turned
to ``omit``. Its protocol, address, auth and ``model_metadata`` are
OpenProgram's; ``auth`` is written whole, as fx refuses an env beside type
none, so the gateway key is a segment of the base URL (``Agent.path_keyed``).
fx is found by ``~/.fx`` only: ``fx`` is a common command name (the
JSON viewer has it too).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from .base import Agent, Wiring, Write, find_tool
from .json_kept import read_json

PROVIDER_ID = "openprogram"
#: How many models fx takes in a provider's model_metadata; past it fx
#: refuses the whole settings file.
MAX_MODELS = 256
#: The first fx that reads a model's reasoning_efforts.
EFFORTS_SINCE = (0, 0, 17)
#: How many levels fx takes for one model; past it fx refuses the file.
MAX_EFFORTS = 16
#: A level's name as fx takes it; auto, adaptive and default are its words for none.
EFFORT_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def home() -> Path:
    return Path.home() / ".fx"


def config_path() -> Path:
    return home() / "settings.json"


def _installed_version() -> str:
    """The fx agent's version: the installer's ``~/.local/bin/fx``, else the fx
    on PATH. A version that isn't 0.x is another program's (the JSON viewer's
    fx is 30 on); "" when unknown."""
    for binary in (Path.home() / ".local" / "bin" / "fx", find_tool("fx")):
        if not binary or not Path(binary).is_file():
            continue
        try:
            said = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        found = re.search(r"\d+\.\d+\.\d+", said or "")
        if found and found.group(0).startswith("0."):
            return found.group(0)
    return ""


#: fx's version (a name tests replace).
fx_version = _installed_version


def takes_efforts(version: str) -> bool:
    """Whether fx at ``version`` reads reasoning_efforts. One whose version
    isn't known is taken for an older one: a key it refuses costs every
    setting it has, a level left out only the pick."""
    try:
        return bool(version) and tuple(int(part) for part in version.split(".")[:3]) >= EFFORTS_SINCE
    except ValueError:
        return False


def efforts_of(levels: list[str]) -> dict[str, Any]:
    """A model's reasoning_efforts: each level, weakest first, asked as reasoning_effort."""
    out: dict[str, Any] = {}
    for level in levels:
        if level.lower() in ("auto", "adaptive", "default") or not EFFORT_NAME.match(level):
            continue
        if len(out) == MAX_EFFORTS or level in out:
            continue
        out[level] = {"reasoning_effort": level}
    return out


def provider_entry(models: list[dict[str, Any]], current: str, v1: str, efforts: bool) -> dict[str, Any]:
    """OpenProgram's entry in ``providers`` (magpie ``fxProviderAt``): the model
    in use first, so it is never the one cut at fx's limit."""
    ordered = sorted(models, key=lambda model: model["id"] != current)
    listed: dict[str, Any] = {}
    for model in ordered:
        if len(listed) == MAX_MODELS:
            break
        entry: dict[str, Any] = {"supports_tool_use": True, "supports_vision": bool(model.get("vision"))}
        window, out = int(model.get("context_window") or 0), int(model.get("max_tokens") or 0)
        if window > 0:
            entry["context_window"] = window
        # fx refuses an output limit that isn't below the context window
        if out > 0 and (window == 0 or out < window):
            entry["max_output_tokens"] = out
        levels = efforts_of(list(model.get("thinking_levels") or []))
        if efforts and levels:
            entry["reasoning_efforts"] = levels
        listed[model["id"]] = entry
    return {"protocol": "openai-chat-completions", "base_url": v1, "auth": {"type": "none"},
            "tool_choice_mode": "send", "model_metadata": listed}


def _wire(at: Wiring) -> list[Write]:
    path, key = config_path(), f"providers.{PROVIDER_ID}"
    doc = read_json(path)
    want = provider_entry(at.models, at.model, at.base("openai-chat"), takes_efforts(fx_version()))
    mode = ((doc.get("providers") or {}).get(PROVIDER_ID) or {}).get("tool_choice_mode") \
        if isinstance(doc.get("providers"), dict) else None
    if mode in ("omit", "send"):
        want["tool_choice_mode"] = mode
    # the entry is OpenProgram's whole (Write.owns): what the user added
    # inside it stays while connected, and it goes whole on disconnect, edited
    # or not (magpie DelJSON); the fields fx's own settings change are no drift
    return [
        Write(path, "json", listing=True, sets={key: want}, owns=(key,),
              loose=(f"{key}.reviewer_model", f"{key}.tool_choice_mode")),
        Write(path, "json", sets={"provider": PROVIDER_ID, f"models.{PROVIDER_ID}": at.model}),
    ]


def _notice() -> str:
    from .running import any_running
    if any_running(r"(^|/)fx( |$)"):
        return "fx reads its settings at start-up — restart open fx sessions to use this."
    return ""


def agent() -> Agent:
    return Agent(
        id="fx", name="fx", icon="fx", kind="cli", protocol="openai-chat",
        dir=home, path=config_path, path_keyed=True, wire=_wire, notice=_notice,
    )
