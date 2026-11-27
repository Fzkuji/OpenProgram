"""AtomCode, a Rust terminal agent (``~/.atomcode/config.toml``, ``$ATOMCODE_HOME``).

After yetone/magpie ``internal/agent/atomcode.go`` (MIT License, Copyright (c)
yetone). A model is a ``[models."<key>"]`` table naming a
``[provider_accounts.<account>]`` table — what AtomCode's own CodingPlan
sign-in writes since 5.0.7: the account holds the protocol, the gateway's
``/v1`` and the key; the model table holds the model string, the context
window (AtomCode compacts as it nears it), the output limit, whether it sees
images, the reasoning levels its picker offers and the one in use.

Connecting adds the account ``openprogram`` and one model table per catalog
model, keyed ``openprogram/<provider>/<model>`` — the model string goes to the
gateway as it is, for it to resolve — and points ``default_provider`` and
``default_model`` at the one picked, ``default_model`` winning when both are
set. AtomCode's picker rewrites the two as one pick: the defaults the user
had are put back on disconnect while they still name OpenProgram's and
AtomCode still has them, and a sync of the model list never writes them. Requests are Chat Completions with Bearer auth and a
User-Agent of ``atomcode/<version>``; the key is AtomCode's own, so a request
is its agent's even when the header is overridden.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .base import Agent, Field, Pick, Wiring, Write
from .model_rows import (PROVIDER_ID, absent_before, choice, dotted, efforts, images, max_tokens, picked, prefixed,
                         read_doc, window, write_now, wrote_before)

AGENT_ID = "atomcode"
ACCOUNT = f"provider_accounts.{PROVIDER_ID}"
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
#: What AtomCode is told of a model whose context isn't known: it has to be
#: told one, and compacts as it nears it (magpie ``atomcodeContext``).
ATOMCODE_CONTEXT = 128000


def home() -> Path:
    value = os.environ.get("ATOMCODE_HOME") or ""
    if value and os.path.isabs(value):
        return Path(os.path.normpath(value))
    return Path.home() / ".atomcode"


def config_path() -> Path:
    return home() / "config.toml"


def supported_efforts(levels: list[str]) -> list[str]:
    """The model's levels AtomCode takes, in its order (magpie ``atomcodeSupportedEfforts``)."""
    return [level for level in EFFORT_LEVELS if level in levels]


def model_tables(models: list[dict[str, Any]], doc: dict[str, Any]) -> dict[str, Any]:
    """The leaves of OpenProgram's model tables (magpie ``atomcodeTablesAt``).
    No reasoning_effort is picked for the user — the vendor's default stands
    until they choose one; one they chose stays."""
    tables = doc.get("models") if isinstance(doc.get("models"), dict) else {}
    sets: dict[str, Any] = {}
    for model in models:
        key = prefixed(model["id"])
        context = window(model) or ATOMCODE_CONTEXT
        leaves: dict[str, Any] = {"account": PROVIDER_ID, "model": model["id"], "context_window": context}
        # an output above the window is cut to it — the window AtomCode is
        # told when the model's isn't known is ATOMCODE_CONTEXT
        output = max_tokens(model)
        if output:
            leaves["max_tokens"] = min(output, context)
        leaves["supports_vision"] = images(model)
        levels = supported_efforts(efforts(model))
        if levels:
            leaves["reasoning_effort_levels"] = levels
        was = tables.get(key) if isinstance(tables.get(key), dict) else {}
        if was.get("reasoning_effort"):
            leaves["reasoning_effort"] = was["reasoning_effort"]
        for leaf, value in leaves.items():
            sets[dotted("models", key, leaf)] = value
    return sets


def _wire(at: Wiring) -> list[Write]:
    path = config_path()
    doc = read_doc("toml", path)
    # openai-compatible is the documented preset for a custom endpoint
    sets: dict[str, Any] = {f"{ACCOUNT}.provider": "openai-compatible",
                            f"{ACCOUNT}.base_url": at.base("openai-chat"),
                            f"{ACCOUNT}.api_key": at.key}
    sets.update(model_tables(at.models, doc))
    listed = {prefixed(model["id"]) for model in at.models}
    gone = [dotted("models", key) for key in (doc.get("models") or {})
            if key.startswith(PROVIDER_ID + "/") and key not in listed]
    writes = [Write(path, "toml", sets=sets, deletes=absent_before(AGENT_ID, path, gone), listing=True)]
    if at.syncing:
        return writes  # the picks are the user's to change in AtomCode
    defaults = ("default_provider", "default_model")
    writes.append(Write(path, "toml", sets={key: prefixed(at.model) for key in defaults},
                        picks=(Pick(defaults, "default_model", {"prefix": PROVIDER_ID + "/"}),)))
    # the picked model table's reasoning_effort, sent with every request to
    # it: one table per model keeps one with each; a level the model doesn't
    # take is never written (magpie's effort field)
    table = dotted("models", prefixed(at.model), "reasoning_effort")
    effort = at.values.get("effort", "")
    model = next((m for m in at.models if m["id"] == at.model), {})
    if effort and effort in supported_efforts(efforts(model)):
        writes.append(Write(path, "toml", sets={table: effort}))
    elif not effort and table in wrote_before(AGENT_ID, path):
        writes.append(Write(path, "toml", deletes=[table]))
    return writes


def effort_choices(values: dict[str, str]) -> list[str]:
    """The levels AtomCode takes for the picked model (magpie ``atomcodeSupportedEfforts``)."""
    return supported_efforts(efforts(picked(values)))


def _own(doc: dict[str, Any], key: str) -> bool:
    """AtomCode still has a model table, or a legacy ``providers.*`` entry, by this key."""
    return any(isinstance(doc.get(table), dict) and key in doc[table] for table in ("models", "providers"))


def _unwire(record: dict[str, Any]) -> None:
    """Before the restore: OpenProgram's tables go, keys the user added to
    them or not, and a default of OpenProgram's goes rather than back to one
    naming a model AtomCode no longer has: magpie puts the user's defaults
    back only where AtomCode still has them."""
    path = config_path()
    doc = read_doc("toml", path)
    if not doc:
        return
    deletes = [dotted("models", key) for key in (doc.get("models") or {}) if str(key).startswith(PROVIDER_ID + "/")]
    if PROVIDER_ID in (doc.get("provider_accounts") or {}):
        deletes.append(ACCOUNT)
    files = record.get("files") or {}
    originals = (files.get(str(path)) or files.get(os.path.realpath(path)) or {}).get("originals") or {}
    for key in ("default_provider", "default_model"):
        value = doc.get(key)
        if not (isinstance(value, str) and value.startswith(PROVIDER_ID + "/")):
            continue
        original = originals.get(key) or {}
        was = original.get("value") if original.get("had") else None
        if not (isinstance(was, str) and was and not was.startswith(PROVIDER_ID + "/") and _own(doc, was)):
            deletes.append(key)
    write_now([Write(path, "toml", deletes=deletes)])


def _notice() -> str:
    from .running import any_running
    if any_running(r"(^|/)atomcode( |$)"):
        return "AtomCode reads its settings at start-up — restart open atomcode sessions to use this."
    return ""


def agent() -> Agent:
    return Agent(
        id=AGENT_ID, name="AtomCode", icon="atomcode", kind="cli", protocol="openai-chat",
        bin="atomcode", dir=home, path=config_path,
        fields=[Field("model", "Model"), Field("effort", "Effort", quiet=True, options=choice(AGENT_ID, effort_choices))],
        ua=("atomcode",), wire=_wire, unwire=_unwire, notice=_notice,
    )
