"""Paimon (aisk/paimon, ``uv tool install paimon``; ``$PAIMON_CONFIG_HOME/config.json``,
default ``~/.config/paimon/config.json``).

After yetone/magpie ``internal/agent/paimon.go`` (MIT License, Copyright (c)
yetone). Paimon is a coding agent in the terminal built on pydantic-ai. Its
settings are one JSON file (Windows too: ``Path.home()``): ``model`` is
``provider:name`` (or the older ``provider/name``), and ``providers`` keeps
each provider's ``api_base`` and ``api_key``, so a model is sent with its own
provider's credentials only.

OpenProgram uses pydantic-ai's ``litellm`` provider, an OpenAI-compatible
proxy that profiles a ``vendor/model`` name by its vendor, as the gateway's
ids are spelled. Paimon's login doesn't offer it, so it is almost never one of
the user's own. Connecting sets ``model`` to ``litellm:<provider>/<model>``,
``providers.litellm`` to the gateway with a key naming Paimon, and
``compaction.context_window`` to the model's window: Paimon compacts against
that only for a name genai-prices doesn't know, which the gateway's ids
aren't, and doesn't compact at all without it; for a model whose window isn't
known the user's own stays. What the user had in those three is put back on
disconnect; the rest of the file is left alone. A file OpenProgram makes is
private (0600), as Paimon makes it, since Paimon keeps keys in it.

Paimon reads the file when it starts, and its own saves change only the keys
they name, so a running Paimon keeps its model until it starts again.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .base import Agent, Wiring, Write

#: The pydantic-ai provider the gateway's models go through.
PROVIDER = "litellm"


def home() -> Path:
    """``$PAIMON_CONFIG_HOME`` when it is a whole path, else ``~/.config/paimon``."""
    named = os.environ.get("PAIMON_CONFIG_HOME")
    if named and os.path.isabs(named):
        return Path(named)
    return Path.home() / ".config" / "paimon"


def config_path() -> Path:
    return home() / "config.json"


def _wire(at: Wiring) -> list[Write]:
    sets: dict[str, Any] = {
        "model": f"{PROVIDER}:{at.model}",
        f"providers.{PROVIDER}": {"api_base": at.base("openai-chat"), "api_key": at.key},
    }
    window = next((int(m.get("context_window") or 0) for m in at.models if m["id"] == at.model), 0)
    if window > 0:
        sets["compaction.context_window"] = window
    # the provider is OpenProgram's whole: it goes on disconnect, edited or
    # not (magpie DelJSON providers.magpie)
    return [Write(config_path(), "json", sets=sets, owns=(f"providers.{PROVIDER}",))]


def _notice() -> str:
    from .running import any_running
    if any_running(r"(^|/)paimon( |$)"):
        return "Paimon reads its model at start-up — restart open paimon sessions to use this."
    return ""


def agent() -> Agent:
    return Agent(
        id="paimon", name="Paimon", icon="paimon", kind="cli", protocol="openai-chat",
        bin="paimon", dir=home, path=config_path,
        # pydantic-ai's requests carry paimon/<version> (llm.user_agent)
        ua=("paimon/",), named_key=True, wire=_wire, notice=_notice,
    )
