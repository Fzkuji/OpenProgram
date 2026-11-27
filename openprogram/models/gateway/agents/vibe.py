"""Mistral Vibe, its CLI and Vibe Desktop (``$VIBE_HOME/config.toml``, default ``~/.vibe``).

After yetone/magpie ``internal/agent/vibe.go`` (MIT License, Copyright (c)
yetone). Vibe (mistralai/mistral-vibe, the ``vibe`` CLI) takes a provider as
a ``[[providers]]`` entry (``name``, ``api_base``, ``api_style`` "openai",
``backend`` "generic", ``extra_headers``) and a model as a ``[[models]]``
entry naming it (``name``, the string sent; ``alias``, what the ``/model``
picker shows and ``active_model`` holds; ``thinking_levels``;
``supports_images``; ``max_context_length``). The model in use is the
top-level ``active_model``, an alias.

OpenProgram adds itself as the provider ``openprogram`` with one model entry
per catalog model, aliased ``openprogram/<provider>/<model>``, and points
``active_model`` at the one picked: Vibe's ``/model`` rewrites it, so it goes
back on disconnect only while it still names one of OpenProgram's, and a
sync of the model list never writes it. Every other entry is the user's and
stays; the file is copied aside before the first write, so disconnecting
puts it back byte for byte when nothing else changed.

Vibe reads a provider's key only from the environment variable its
``api_key_env_var`` names (or the keyring), never from ``config.toml``. A
keyless provider sends its ``extra_headers`` as they are, so the key naming
Vibe to the gateway goes there as an Authorization header: nothing is put in
the user's environment or in ``~/.vibe/.env``.

Vibe Desktop (Mistral's Electron app, ``ai.mistral.lechat.desktop``) runs the
same app server on the same ``config.toml`` and lists its ``[[models]]``, so
one agent stands for both, found by the app too.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import model_rows
from .base import Agent, App, Field, Members, Pick, Wiring, Write, app_bundles

PROVIDER = model_rows.PROVIDER_ID
#: What Vibe is told of a model whose context isn't known: without one it
#: takes a default window with a warning.
CONTEXT = 128000
DESKTOP = App("ai.mistral.lechat.desktop", ("Vibe.app",))


def home() -> Path:
    """Vibe's folder: ``$VIBE_HOME`` (``~`` expanded), else ``~/.vibe``."""
    folder = (os.environ.get("VIBE_HOME") or "").strip()
    return Path(os.path.expanduser(folder)) if folder else Path(os.path.expanduser("~")) / ".vibe"


def config_path() -> Path:
    return home() / "config.toml"


def alias(ref: str) -> str:
    """The alias of a catalog ref's model entry, what ``active_model`` holds."""
    return model_rows.prefixed(ref)


def mine(entry: Any) -> bool:
    return isinstance(entry, dict) and entry.get("provider") == PROVIDER


def models(rows: list[dict[str, Any]], was: list[Any]) -> list[dict[str, Any]]:
    """The catalog as Vibe's model entries (magpie ``vibeWant``): a model with
    effort levels offers them beside off, one without offers off alone; a
    thinking level the user chose on one of them stays while it has it."""
    kept = {entry.get("name"): entry.get("thinking") for entry in was if mine(entry)}
    out = []
    for row in rows:
        entry: dict[str, Any] = {"name": row["id"], "provider": PROVIDER, "alias": alias(row["id"])}
        levels = ["off", *(level for level in model_rows.efforts(row) if level not in ("none", "off"))]
        if len(levels) > 1 and kept.get(row["id"]) in levels:
            entry["thinking"] = kept[row["id"]]
        entry["thinking_levels"] = levels
        entry["supports_images"] = model_rows.images(row)
        entry["max_context_length"] = model_rows.window(row, CONTEXT)
        out.append(entry)
    return out


def _wire(at: Wiring) -> list[Write]:
    path = config_path()
    doc = model_rows.read_doc("toml", path)
    provider = {"name": PROVIDER, "api_base": at.base("openai-chat"), "api_style": "openai",
                "backend": "generic", "extra_headers": {"Authorization": f"Bearer {at.key}"}}
    # the user's own entries stay as they are, in their order; OpenProgram's
    # go after them, and disconnecting takes out OpenProgram's alone, also
    # from lists the user has edited since (magpie vibeUnwire)
    members = {"providers": Members(match={"name": PROVIDER}, items=[provider]),
               "models": Members(match={"provider": PROVIDER},
                                 items=models(at.models, list(doc.get("models") or [])))}
    writes = [Write(path, "toml", members=members, listing=True)]
    if not at.syncing:
        writes.append(Write(path, "toml", sets={"active_model": alias(at.model)},
                            picks=(Pick(("active_model",), "active_model", {"prefix": PROVIDER + "/"}),)))
    return writes


def _notice() -> str:
    # Vibe Desktop's app server reads config.toml when a chat starts and
    # doesn't watch it: a chat open before keeps what it read
    if app_bundles(DESKTOP):
        return ("Vibe Desktop reads its settings when a chat starts — start a new chat there to use this. "
                "Open vibe sessions need a restart too.")
    from .running import any_running
    if any_running(r"(^|/)vibe( |$)"):
        return "Vibe reads its settings at start-up — restart open vibe sessions to use this."
    return ""


def avibe(path: str) -> bool:
    """Avibe's ``vibe`` (a Python package that installs one too) lives under an
    avibe folder; that binary isn't Mistral Vibe's (magpie ``vibeNotAvibe``)."""
    return any("avibe" in candidate.lower().replace("\\", "/")
               for candidate in (path, os.path.realpath(path)))


def agent() -> Agent:
    return Agent(
        id="vibe", name="Mistral Vibe", icon="mistral", aliases=("mistral-vibe",), kind="cli",
        protocol="openai-chat", bin="vibe", dir=home, path=config_path, fields=[Field("model", "Model")],
        # Vibe Desktop, installed but not opened yet, has made no ~/.vibe and needs no CLI
        sign=lambda: bool(app_bundles(DESKTOP)), bin_not=avibe,
        ua=("mistral-vibe",), before_copy=True, wire=_wire, notice=_notice,
    )
