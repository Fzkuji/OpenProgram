"""Crush (``$XDG_CONFIG_HOME/crush/crush.json``, default ``~/.config``).

After yetone/magpie ``internal/agent/agents.go`` ``crush``/``crushAt`` and
``magpieProviderJSONAt`` (its ``"crush"`` shape); MIT License, Copyright (c)
yetone. OpenProgram is a provider of Crush's own, ``providers.openprogram``
in ``crush.json``: type ``openai`` at the gateway's chat completions, with
the catalog as its model list. The entry is OpenProgram's whole: a key the
user adds to it stays while connected, and it all goes on disconnect.

Crush saves the models its own picker chooses to its data file
(``~/.local/share/crush/crush.json``, ``$XDG_DATA_HOME``, or
``%LOCALAPPDATA%\\crush\\crush.json`` on Windows, where that is the config
file too) and reads it after ``crush.json``, so a pick there wins over one
in ``crush.json``. The picks are therefore written where Crush writes them,
as a whole ``models.large`` / ``models.small`` object: Crush's picker keeps
its per-model settings there (``max_tokens``, ``think``,
``reasoning_effort``, sampling) and applies them to whatever model the
object names, so they must not stay attached to the model OpenProgram
picks — a ``max_tokens`` meant for another model can go upstream as an
over-limit request. Crush's picker rewrites the two as one pick: they go
back on disconnect while ``models.large`` still names OpenProgram, and
a sync of the model list never writes them.

The large model's ``reasoning_effort`` is an option field (Crush's schema
takes low, medium or high, for OpenAI-style models), kept with that object.
Each listed model carries its price (catwalk's ``cost_per_1m_*``; Crush
counts a session's cost by it). Crush keeps API keys in its data file, so
one OpenProgram is first to make is made readable by its owner only.
Telemetry off (a kept choice, ``telemetry.py``) is
``options.disable_metrics = true`` in ``crush.json``
(Options in Crush's internal/config/config.go; it also takes DO_NOT_TRACK).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from . import model_rows, telemetry
from .base import Agent, Field, Pick, Wiring, Write

PROVIDER = model_rows.PROVIDER_ID


def config_path() -> Path:
    if sys.platform == "win32":
        local = model_rows.env_dir("LOCALAPPDATA")
        if local:
            return Path(local) / "crush" / "crush.json"
    return Path(model_rows.config_home()) / "crush" / "crush.json"


def data_path() -> Path:
    if sys.platform == "win32" and model_rows.env_dir("LOCALAPPDATA"):
        # %LOCALAPPDATA%\crush is Crush's data folder there
        return config_path()
    data = model_rows.env_dir("XDG_DATA_HOME")
    if data:
        return Path(data) / "crush" / "crush.json"
    return Path(os.path.expanduser("~")) / ".local" / "share" / "crush" / "crush.json"


def models(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The catalog as Crush's (catwalk's) provider models."""
    out = []
    for row in rows:
        # without it Crush caps every reply at 16384 tokens, a model that
        # can write more of them never asked for it
        entry: dict[str, Any] = {
            "id": row["id"], "name": model_rows.name(row),
            "context_window": model_rows.window(row, 200000),
            "default_max_tokens": model_rows.max_tokens(row, 16384),
            "can_reason": bool(model_rows.efforts(row)),
            # supports_attachments is catwalk's word for image input; without
            # it Crush's view tool, file picker and prompt drop every image
            "supports_attachments": model_rows.images(row),
        }
        # Crush counts a session's cost by it (catwalk's cost_per_1m_*, Crush
        # 0.39.3): in_cached is a cache write, out_cached a read
        price = model_rows.price(row)
        if price is not None:
            entry["cost_per_1m_in"], entry["cost_per_1m_out"] = price["input"], price["output"]
            entry["cost_per_1m_in_cached"] = price["cache_write"]
            entry["cost_per_1m_out_cached"] = price["cache_read"]
        out.append(entry)
    return out


def _wire(at: Wiring) -> list[Write]:
    provider = {"type": "openai", "name": model_rows.PROVIDER_NAME, "base_url": at.base("openai-chat"),
                "api_key": at.key, "models": models(at.models)}
    block = f"providers.{PROVIDER}"
    writes = [Write(config_path(), "json", sets={block: provider}, owns=(block,), listing=True)]
    if at.syncing:
        return writes  # the picks are the user's to change in Crush
    # Crush's own picker reads the small model too; the row shows an unset
    # small model as the same model, so that is what Crush is given
    large: dict[str, Any] = {"provider": PROVIDER, "model": at.model}
    if at.values.get("effort"):
        large["reasoning_effort"] = at.values["effort"]
    picks = {"models.large": large,
             "models.small": {"provider": PROVIDER, "model": at.values.get("small") or at.model}}
    # on Windows both are one file: the second write edits what the first leaves
    pick = Pick(("models.large", "models.small"), "models.large.provider", PROVIDER)
    return [*writes, Write(data_path(), "json", sets=picks, picks=(pick,))]


def _on_route(folder: Path, connected: bool, record: dict[str, Any]) -> None:
    """Crush keeps API keys in its data file, so one OpenProgram is first to
    make is made readable by its owner only."""
    if not connected or sys.platform == "win32":
        return
    data = data_path()
    files = record.get("files") or {}
    kept = files.get(str(data)) or files.get(os.path.realpath(data)) or {}
    if kept.get("absent") and data.exists():
        os.chmod(data, 0o600)


def _telemetry() -> list[Write]:
    return [Write(config_path(), "json", sets={"options.disable_metrics": True})]


def _dir() -> Path:
    return config_path().parent


def agent() -> Agent:
    return Agent(
        id="crush", name="Crush", icon="crush", kind="cli", protocol="openai-chat", bin="crush",
        dir=_dir, path=config_path,
        fields=[Field("model", "Large"), Field("small", "Small", quiet=True, follows="model"),
                # the large model's reasoning_effort, which Crush's schema takes
                # as low, medium or high (for OpenAI-style models)
                Field("effort", "Effort", quiet=True, options=model_rows.fixed("low", "medium", "high")),
                telemetry.field()],
        ua=("crush",), wire=_wire, on_route=_on_route, **telemetry.switch("crush", _telemetry),
    )
