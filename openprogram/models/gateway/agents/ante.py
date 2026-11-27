"""Ante (antigma.ai; ``$ANTE_HOME/catalog.json`` and ``settings.json``, default ``~/.ante``).

After yetone/magpie ``internal/agent/ante.go`` (MIT License, Copyright (c)
yetone). Ante takes a provider of one's own in its catalog, merged on top of
the providers it ships (docs.antigma.ai, reference/catalog-reference)::

    {"providers": {"<id>": {"display_name", "base_url",
       "wire_style": "OpenAiCompatible", "auth", "http_headers", "extra_body",
       "preferred_models": [{"id", "display_name", "context_limit",
         "max_tokens", "supported_efforts", ...}]}},
     "models": {...}}

The providers map's key is what ``--provider`` takes. Connecting adds the
entry ``openprogram`` at the gateway's ``/v1`` on ``OpenAiCompatible`` (the
wire style Ante speaks Chat Completions with) and lists the catalog in its
``preferred_models``, Ante's model picker. The user's own providers and the
models section stay, and so does everything the user added inside
OpenProgram's own entry (an auth block, http_headers, extra_body), which only
``preferred_models`` is written over.

No auth of OpenProgram's own: Ante counts a provider with no ``auth`` block as
authenticated, and writing a bearer ``env_key`` would mean putting a key into
Ante's environment, which the user would have to do. The gateway key is a
segment of the base URL instead (``Agent.path_keyed``).

Being in the catalog is not enough: which provider a session starts on is
``provider`` and ``model`` of ``settings.json``, else the first authenticated
provider in catalog order, and Ante's shipped providers come first. So both
are set as well; what was there is stashed and put back on disconnect.

Ante reads the catalog once per process and parses it leniently: an entry
that doesn't deserialize is skipped and the rest still load.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .base import Agent, Wiring, Write

PROVIDER_ID = "openprogram"
ENTRY = f"providers.{PROVIDER_ID}"

#: Ante's effort scale, ascending, and the level each rung is. Ante's ``min``
#: is sent as ``minimal``; ``none`` (no thinking at all) has no rung, so a
#: model that takes only ``none`` gets ``[]``: no slider, no effort sent.
#: No default ``effort`` is written — a written default is a sent effort.
LADDER = (("min", "minimal"), ("low", "low"), ("medium", "medium"),
          ("high", "high"), ("xhigh", "xhigh"), ("max", "max"))


def home() -> Path:
    folder = (os.environ.get("ANTE_HOME") or "").strip()
    return Path(folder).expanduser() if folder else Path.home() / ".ante"


def catalog_path() -> Path:
    return home() / "catalog.json"


def settings_path() -> Path:
    return home() / "settings.json"


def efforts(levels: list[str]) -> list[str]:
    """The levels Ante is told a model accepts, on its ladder (magpie ``anteEfforts``)."""
    return [rung for rung, level in LADDER if level in levels]


def max_tokens(model: dict[str, Any]) -> int:
    """The reply limit under the window, never equal to it: Ante skips an
    entry whose ``max_tokens`` is not less than its ``context_limit``
    (magpie ``anteMaxTokens``, #1179)."""
    window, output = int(model.get("context_window") or 0), int(model.get("max_tokens") or 0)
    if window and output > window:
        output = window
    if window and output >= window:
        output = window - 1
    return output


def model_entry(model: dict[str, Any]) -> dict[str, Any]:
    """One model as Ante's ``preferred_models`` lists it: what is known of it
    and nothing else; ``support_vision`` is left to Ante, which takes a model
    as seeing images unless told otherwise."""
    entry: dict[str, Any] = {"id": model["id"]}
    if model.get("name") and model["name"] != model["id"]:
        entry["display_name"] = model["name"]
    if model.get("context_window"):
        entry["context_limit"] = int(model["context_window"])
    if max_tokens(model) > 0:
        entry["max_tokens"] = max_tokens(model)
    if model.get("thinking_levels"):
        entry["supported_efforts"] = efforts(list(model["thinking_levels"]))
    return entry


def _wire(at: Wiring) -> list[Write]:
    # OpenProgram's entry is its own whole (Write.owns): what the user added
    # inside it (an auth block, http_headers, extra_body) stays while it is
    # connected (magpie theirsKept), and the entry goes whole on disconnect
    entry: dict[str, Any] = {"display_name": "OpenProgram", "base_url": at.base("openai-chat"),
                             "wire_style": "OpenAiCompatible"}
    listed = [model_entry(model) for model in at.models]
    if listed:
        entry["preferred_models"] = listed
    return [
        Write(catalog_path(), "json", sets={ENTRY: entry}, owns=(ENTRY,), listing=True),
        Write(settings_path(), "json", sets={"provider": PROVIDER_ID, "model": at.model}),
    ]


def _notice() -> str:
    from .running import any_running
    if any_running(r"(^|/)ante( |$)"):
        return "Ante reads its catalog at start-up — restart open ante sessions to use this."
    return ""


def agent() -> Agent:
    return Agent(
        id="ante", name="Ante", icon="generic", kind="cli", protocol="openai-chat",
        bin="ante", dir=home, path=catalog_path, path_keyed=True, wire=_wire, notice=_notice,
    )
