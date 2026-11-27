"""Goose, Block's agent, CLI and desktop app alike (``~/.config/goose/config.yaml``).

After yetone/magpie ``internal/agent/goose.go`` and ``agents.go``
``goose``/``gooseIn`` (MIT License, Copyright (c) yetone). Goose reaches the
gateway as a custom provider: a JSON file of its own in ``custom_providers``
beside ``config.yaml``, named by the provider's id, which goose loads at
start-up (``crates/goose/src/config/declarative_providers.rs``). Each model
is a ``ModelInfo``:

- ``context_limit`` is the window goose's ContextLimitResolver takes before
  anything but ``GOOSE_CONTEXT_LIMIT``; without it goose guesses by name, or 128K.
- ``reasoning`` is what the desktop app's picker asks before it shows its
  Thinking Level. goose sends its level only to OpenAI's reasoning models on
  an OpenAI-compatible provider, so only those are declared reasoning.

``dynamic_models`` false keeps goose to this list rather than the gateway's
``/v1/models`` (every model, none of their windows), and
``skip_canonical_filtering`` keeps the list's order and models goose's
registry doesn't know. A key the user set in the file (``timeout_seconds``,
a header, a display name) stays when it is rewritten; what reaches the
gateway, the key and the model list are OpenProgram's.

The model goose uses is in one of two layouts of ``config.yaml``: the old
flat ``GOOSE_PROVIDER`` / ``GOOSE_MODEL``, or the new ``active_provider``
with an entry in ``providers`` holding that provider's model, which goose
migrates a config to on its next write. goose reads the new one first and
its migration deletes the flat keys, so a model set with the flat keys alone
on a migrated config is never used. It is written in the layout the config
is in. goose's own picker rewrites the provider and model as one pick: they
go back on disconnect while the provider is still OpenProgram, and a sync of
the model list never writes them. The provider file is OpenProgram's whole
and goes on disconnect. ``GOOSE_THINKING_EFFORT`` is a choice; telemetry
off (a kept choice, ``telemetry.py``) is ``GOOSE_TELEMETRY_ENABLED: false``.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from . import model_rows, telemetry
from .base import ROOT, Agent, Field, Pick, Wiring, Write

PROVIDER = model_rows.PROVIDER_ID

#: The model names goose sends its thinking level for on an OpenAI-compatible
#: provider: OpenAI's reasoning models (goose's ``is_openai_responses_model``).
#: For any other model goose keeps the level to itself, so a level offered
#: for it would change nothing.
THINKS = re.compile(r"(?i)(?:^|[-/])(?:o\d+(?:$|-)|gpt-(?:5|6)(?:$|[-.]))")


def config_path() -> Path:
    if sys.platform == "win32":
        app = model_rows.env_dir("APPDATA")
        if app:
            return Path(app) / "Block" / "goose" / "config" / "config.yaml"
    return Path(model_rows.config_home()) / "goose" / "config.yaml"


def provider_path() -> Path:
    return config_path().parent / "custom_providers" / f"{PROVIDER}.json"


def provider_json(at: Wiring) -> dict[str, Any]:
    """OpenProgram as a custom provider of Goose's (magpie ``gooseProviderJSON``)."""
    rows = []
    for row in at.models:
        entry: dict[str, Any] = {"name": row["id"], "reasoning": bool(THINKS.search(row["id"]))}
        if model_rows.window(row):
            entry["context_limit"] = model_rows.window(row)
        rows.append(entry)
    provider: dict[str, Any] = {
        "name": PROVIDER, "engine": "openai", "display_name": model_rows.PROVIDER_NAME,
        "description": "Every model OpenProgram has, through its local gateway",
        "api_key_env": "", "base_url": at.base("openai-chat"),
        # goose's HTTP client sends no User-Agent, so the key names Goose
        "headers": {"Authorization": f"Bearer {at.key}"},
        "requires_auth": False, "skip_canonical_filtering": True, "models": rows,
    }
    # goose refuses a provider whose static list is empty
    if rows:
        provider["dynamic_models"] = False
    return provider


#: What the file's own keys are, rewritten whatever the user set in them.
_OURS = ("name", "engine", "api_key_env", "base_url", "requires_auth", "dynamic_models", "models", "headers")


def provider_kept(at: Wiring) -> dict[str, Any]:
    """The provider laid over the file there now (magpie ``gooseProviderKept``):
    goose's own editor carries keys it doesn't show into the file it writes,
    and the user sets timeout_seconds, toolshim, a header or a name there."""
    want = provider_json(at)
    try:
        have = json.loads(provider_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return want
    if not isinstance(have, dict):
        return want
    if isinstance(have.get("display_name"), str) and have["display_name"].strip():
        want["display_name"] = have["display_name"]
    if isinstance(have.get("headers"), dict):
        want["headers"] = {**have["headers"], **want["headers"]}
    merged = {**want, **{key: value for key, value in have.items() if key not in _OURS}}
    for key in _OURS:
        merged.pop(key, None)
        if key in want:
            merged[key] = want[key]
    merged["display_name"] = want["display_name"]
    return merged


def new_layout(doc: dict[str, Any]) -> bool:
    """The config has a providers map or an active_provider (goose's new layout)."""
    return "providers" in doc or "active_provider" in doc


def _wire(at: Wiring) -> list[Write]:
    path = config_path()
    text = json.dumps(provider_kept(at), indent=2, ensure_ascii=False) + "\n"
    listing = Write(provider_path(), "file", content=text, listing=True, owns=(ROOT,))
    if at.syncing:
        return [listing]  # the pick is the user's to change in goose
    effort = {"GOOSE_THINKING_EFFORT": at.values["effort"]} if at.values.get("effort") else {}
    if new_layout(model_rows.read_doc("yaml", path)):
        # as goose's own set_active_provider writes it, other providers' entries
        # kept; flat keys left beside the map would be read by no goose that
        # reads the map, and deleted by the next one that writes. The entry is
        # OpenProgram's, its model goose's picker's
        entry = f"providers.{PROVIDER}"
        write = Write(path, "yaml", sets={"active_provider": PROVIDER, **effort,
                                          entry: {"enabled": True, "model": at.model, "configured": True}},
                      deletes=["GOOSE_PROVIDER", "GOOSE_MODEL"], owns=(entry,), loose=(entry + ".model",),
                      picks=(Pick(("active_provider", *effort), "active_provider", PROVIDER),))
    else:
        write = Write(path, "yaml", sets={"GOOSE_PROVIDER": PROVIDER, "GOOSE_MODEL": at.model, **effort},
                      picks=(Pick(("GOOSE_PROVIDER", "GOOSE_MODEL", *effort), "GOOSE_PROVIDER", PROVIDER),))
    return [write, listing]


def _notice() -> str:
    from .running import any_running
    if any_running(r"Goose\.app/", r"(^|/)goose( |$)"):
        return ("Goose loads its providers at start-up — quit and reopen Goose "
                "(and open goose sessions) to use OpenProgram's models.")
    return ""


_GO_MAGIC = b"\xff Go buildinf:"


def go_program(path: str) -> bool:
    """Whether the binary at ``path`` is a Go program (magpie ``goProgram``):
    pressly's database migration tool is a Go ``goose``, Block's is Rust."""
    try:
        with open(path, "rb") as handle:
            return _GO_MAGIC in handle.read(64 << 20)
    except OSError:
        return False


def _telemetry() -> list[Write]:
    # the key goose's `goose configure` telemetry choice writes
    return [Write(config_path(), "yaml", sets={"GOOSE_TELEMETRY_ENABLED": False})]


def agent() -> Agent:
    return Agent(
        id="goose", name="Goose", icon="goose", kind="cli", protocol="openai-chat", bin="goose",
        dir=lambda: config_path().parent, path=config_path,
        fields=[Field("model", "Model"),
                # GOOSE_THINKING_EFFORT, the effort goose asks of a model that
                # thinks, for every provider
                Field("effort", "Effort", quiet=True,
                      options=model_rows.fixed("off", "low", "medium", "high", "max")),
                telemetry.field()],
        ua=("goose",), wire=_wire, notice=_notice, bin_not=go_program, **telemetry.switch("goose", _telemetry),
    )
