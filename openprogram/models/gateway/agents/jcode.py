"""jcode (``$JCODE_HOME/config.toml``, default ``~/.jcode``).

After yetone/magpie ``internal/agent/jcode.go`` (MIT License, Copyright (c)
yetone). jcode (1jehuang/jcode, a coding agent in Rust) takes a provider of
the user's own making as a named profile, ``[providers.<name>]``, which
``jcode provider add`` writes: type ``openai-compatible`` (jcode's chat
completions client), a ``base_url``, a key and its models (``id``,
``context_window``, ``input``, ``reasoning``). jcode starts on
``[provider] default_provider`` and ``default_model``.

OpenProgram adds itself as the profile ``openprogram``: the gateway, with one
model entry per catalog model, so the catalog is in jcode's ``/model``
picker, and jcode is started on the model picked. jcode's ``/model``
rewrites ``[provider]``'s two keys as one pick: what they named before
goes back on disconnect while they still name OpenProgram, and a sync of the
model list never writes them. Every other key of the file is
the user's and stays as written, the user's own keys in the profile too
(headers, extra_body, a model's ``reasoning_effort`` while the model still
has that level). Disconnecting takes the profile out whole, as magpie does.
jcode has no telemetry switch and no effort setting outside its models.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from . import model_rows
from .base import Agent, Field, Pick, Wiring, Write

PROVIDER = model_rows.PROVIDER_ID
PROFILE = f"providers.{PROVIDER}"
#: What jcode is told of a model whose context isn't known; without one it takes 200k.
CONTEXT = 128000


def home() -> Path:
    """jcode's folder: ``$JCODE_HOME``, else ``~/.jcode`` (jcode-storage's ``jcode_dir``)."""
    folder = (os.environ.get("JCODE_HOME") or "").strip()
    return Path(folder) if folder else Path(os.path.expanduser("~")) / ".jcode"


def config_path() -> Path:
    return home() / "config.toml"


def models(rows: list[dict[str, Any]], was: list[Any]) -> list[dict[str, Any]]:
    """The catalog as jcode's model entries (magpie ``jcodeWant``): the window,
    whether it takes images, and reasoning on for a model with effort levels,
    so jcode's ``/effort`` offers them. A ``reasoning_effort`` the user set on
    one of these models stays while the model has that level."""
    kept = {m.get("id"): m.get("reasoning_effort") for m in was if isinstance(m, dict)}
    out = []
    for row in rows:
        entry: dict[str, Any] = {"id": row["id"], "context_window": model_rows.window(row, CONTEXT),
                                 "input": ["text", "image"] if model_rows.images(row) else ["text"]}
        levels = [level for level in model_rows.efforts(row) if level != "none"]
        if levels:
            entry["reasoning"] = True
            if kept.get(row["id"]) in levels:
                entry["reasoning_effort"] = kept[row["id"]]
        out.append(entry)
    return out


def _wire(at: Wiring) -> list[Write]:
    path = config_path()
    profile = (model_rows.read_doc("toml", path).get("providers") or {}).get(PROVIDER) or {}
    sets: dict[str, Any] = {
        f"{PROFILE}.type": "openai-compatible",
        f"{PROFILE}.base_url": at.base("openai-chat"),
        f"{PROFILE}.api_key": at.key,
        # a gateway on loopback is taken by jcode as needing no key, and then
        # it sends none: the key names jcode to the gateway
        f"{PROFILE}.requires_api_key": True,
        f"{PROFILE}.default_model": at.model,
        f"{PROFILE}.models": models(at.models, list(profile.get("models") or [])),
    }
    writes = []
    if _as_tables(path):
        # jcode wrote the profile's models back as [[providers.openprogram.models]]
        # tables: the profile is written whole again (magpie
        # SetTOMLTablesMatching), the user's own keys in it kept
        kept = {f"{PROFILE}.{model_rows.dotted(key)}": value for key, value in profile.items()
                if key not in {k.rsplit(".", 1)[-1] for k in sets if k.startswith(PROFILE + ".")}}
        sets.update(kept)
        writes.append(Write(path, "toml", deletes=[PROFILE]))
    writes.append(Write(path, "toml", sets=sets, listing=True))
    if not at.syncing:
        writes.append(Write(path, "toml", sets={"provider.default_provider": PROVIDER,
                                                "provider.default_model": at.model},
                            picks=(Pick(("provider.default_provider", "provider.default_model"),
                                        "provider.default_provider", PROVIDER),)))
    return writes


def _as_tables(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return re.search(r'(?m)^\s*\[\[\s*providers\s*\.\s*"?' + PROVIDER + r'"?\s*\.\s*models\s*\]\]', text) is not None


def _unwire(record: dict[str, Any]) -> None:
    """The profile whole, its models' tables and the user's own keys in it
    (headers, extra_body) are OpenProgram's profile's and go with it."""
    model_rows.write_now([Write(config_path(), "toml", deletes=[PROFILE])])


def _notice() -> str:
    from .running import any_running
    if any_running(r"(^|/)jcode( |$)"):
        return "jcode reads its settings at start-up — restart open jcode sessions to use this."
    return ""


def agent() -> Agent:
    return Agent(
        id="jcode", name="jcode", icon="jcode", kind="cli", protocol="openai-chat", bin="jcode",
        dir=home, path=config_path, fields=[Field("model", "Model")],
        wire=_wire, notice=_notice, unwire=_unwire,
    )
