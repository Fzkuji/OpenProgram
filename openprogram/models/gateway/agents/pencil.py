"""Pencil (pen.dev, the design canvas whose agent runs models): a custom
provider of one's own in ``~/.pencil/models.json``, in Pi's format.

After yetone/magpie ``internal/agent/pencil.go`` (MIT License, Copyright (c)
yetone). Pencil's settings offer a custom provider on OpenAI Completions,
OpenAI Responses or Anthropic Messages, set for the provider as a whole, so
OpenProgram's models are Pi's entries (``pi_like.model_entry``) with every
model on the provider's ``openai-completions`` at the gateway's ``/v1``: a
model's own api and baseUrl, which Pi reads but Pencil's editor never
writes, are left out. Each model carries every field Pencil writes for one,
Pi's defaults where nothing better is known (a 128K window, 16K out) and no
cost. The model is picked in Pencil's agent composer, not in a file, so
connecting puts OpenProgram's models in that picker; the user's other
providers stay as they are. Its requests carry a key that names Pencil, as
its User-Agent isn't known.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .base import Agent, Field, Wiring, Write
from .pi_like import PROVIDER_ID, model_entry, read_doc, theirs_kept


def folder() -> Path:
    return Path.home() / ".pencil"


def config_path() -> Path:
    return folder() / "models.json"


def provider_block(at: Wiring) -> dict[str, Any]:
    """OpenProgram's provider in Pencil's models.json (magpie ``pencilProviderJSON``)."""
    models = []
    for row in at.models:
        entry = model_entry(row, at, native=False)
        entry.setdefault("input", ["text"])
        entry.setdefault("contextWindow", 128000)
        entry.setdefault("maxTokens", min(16384, entry["contextWindow"]))
        entry["cost"] = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}
        entry["compat"] = {}
        models.append(entry)
    return {"name": "OpenProgram", "baseUrl": at.base("openai-chat"), "api": "openai-completions",
            "apiKey": at.key, "models": models}


def _wire(at: Wiring) -> list[Write]:
    key = f"providers.{PROVIDER_ID}"
    block = theirs_kept(read_doc(config_path()), key, provider_block(at))
    # OpenProgram's whole: it goes on disconnect, edited in Pencil or not
    return [Write(config_path(), "json", sets={key: block}, owns=(key,), listing=True)]


def _app() -> bool:
    """Pencil's app where a Mac keeps apps, under its name before and after the pen.dev rename."""
    if sys.platform != "darwin":
        return False
    return any((root / name).exists() for root in (Path("/Applications"), Path.home() / "Applications")
               for name in ("Pencil.app", "pen.dev.app"))


def _notice() -> str:
    from .running import any_running
    if sys.platform == "win32" or any_running(r"Pencil\.app/", r"pen\.dev\.app/", r"(^|/)[Pp]encil( |$)"):
        return "Pencil reads its custom models at start-up — restart Pencil to see OpenProgram's models in its model picker."
    return ""


def agent() -> Agent:
    return Agent(
        id="pencil", name="Pencil", icon="pencil", aliases=("pen.dev", "pen-dev", "pendev"), kind="app",
        protocol="openai-chat", dir=folder, path=config_path, fields=[Field("model", "Model")],
        named_key=True, wire=_wire, notice=_notice,
        # ~/.pencil once it has run, else its app; no command: `pencil` and
        # `pen` are common names
        detect=lambda: folder().is_dir() or _app(), detect_only=True,
    )
