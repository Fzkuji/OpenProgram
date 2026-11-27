"""OpenClaw (openclaw/openclaw; ``openclaw.json``, default ``~/.openclaw``).

After yetone/magpie ``internal/agent/openclaw.go`` (MIT License, Copyright
(c) yetone). OpenClaw is a personal assistant that answers on WhatsApp,
Telegram, Discord and the rest. Its file follows OpenClaw's own
``resolveConfigPath`` / ``resolveStateDir``: ``$OPENCLAW_CONFIG_PATH``, else
``openclaw.json`` in ``$OPENCLAW_STATE_DIR``, else in
``~/.openclaw-<profile>`` for a named ``$OPENCLAW_PROFILE``, else in
``~/.openclaw``, ``~`` being ``$OPENCLAW_HOME`` when set.

A provider of the user's own is an entry of ``models.providers``: a
``baseUrl``, an ``apiKey``, an ``api`` (``openai-completions`` is Chat
Completions) and its models, each ``{id, name, input, reasoning,
contextWindow}``. The schema is strict, so only keys OpenClaw knows are
written. A model is ``provider/model`` split at the first slash, so
``openprogram/<provider>/<model>`` is OpenClaw's ref as it is. The model
OpenClaw runs on is ``agents.defaults.model``, a ref or ``{primary,
fallbacks}``; like ``openclaw models set``, the primary is set (fallbacks
kept) and the ref added to ``agents.defaults.models`` where that object is
(a config from before 2026.8.1 reads it as the models allowed). A non-empty
``agents.defaults.modelPolicy.allow`` is the models allowed: ``openprogram/*``
is added to it when nothing of OpenProgram's is in it.

The user's own keys stay as written, in OpenProgram's provider and model
entries too (headers, timeoutSeconds, a model's maxTokens), while it is
connected; disconnecting takes OpenProgram's provider out whole, as magpie
does. OpenClaw reads
JSON5 but writes JSON; a file that isn't JSON is left alone. Its gateway
takes a change to models without a restart, but a chat already pinned to a
model keeps it until ``/model default``. Not wired: a per-agent model
(``agents.entries.*.model``), and a config split with ``$include``.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .base import Agent, Members, Wiring, Write

PROVIDER_ID = "openprogram"
PROVIDER = f"models.providers.{PROVIDER_ID}"
MODEL = "agents.defaults.model"
MODELS = "agents.defaults.models"
ALLOW = "agents.defaults.modelPolicy.allow"
ALL = f"{PROVIDER_ID}/*"
#: What OpenClaw is told of a model whose window isn't known.
CONTEXT = 128000

_PROFILE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$", re.IGNORECASE)


def _tilde(value: str, home: Path) -> Path:
    if value == "~":
        return home
    if value.startswith("~/") or value.startswith("~\\"):
        return home / value[2:]
    return Path(value)


def _home() -> Path:
    """OpenClaw's ``~``: ``$OPENCLAW_HOME``, a leading ``~`` in it the system's home."""
    value = os.environ.get("OPENCLAW_HOME", "").strip()
    if value in ("", "undefined", "null"):
        return Path.home()
    return _tilde(value, Path.home())


def state_dir() -> Path:
    home = _home()
    folder = home / ".openclaw"
    profile = os.environ.get("OPENCLAW_PROFILE", "").strip()
    if profile and profile.lower() != "default" and _PROFILE.match(profile):
        folder = home / f".openclaw-{profile}"
    custom = os.environ.get("OPENCLAW_STATE_DIR", "").strip()
    if custom:
        folder = _tilde(custom, home)
    return folder


def config_path() -> Path:
    custom = os.environ.get("OPENCLAW_CONFIG_PATH", "").strip()
    if custom:
        return _tilde(custom, _home())
    return state_dir() / "openclaw.json"


def _escaped(key: str) -> str:
    return key.replace(".", "\\.")


def model_entries(models: list[dict[str, Any]], had: Any) -> list[dict[str, Any]]:
    """The models as OpenClaw is told them, each laid over the entry of the
    same id there was, so the user's own keys in it stay (magpie ``openClawWant``)."""
    was = {item.get("id"): item for item in (had if isinstance(had, list) else [])
           if isinstance(item, dict) and item.get("id")}
    out = []
    for model in models:
        levels = model.get("thinking_levels") or []
        mine = {"id": model["id"], "name": model["id"],
                "reasoning": any(level != "none" for level in levels) or (not levels and bool(model.get("reasoning"))),
                "input": ["text", *(["image"] if model.get("vision") else [])],
                "contextWindow": int(model.get("context_window") or CONTEXT)}
        out.append({**was.get(model["id"], {}), **mine})
    return out


def _wire(at: Wiring) -> list[Write]:
    from .. import edit
    path = config_path()
    text = edit.ConfigText.read(path)
    doc = edit.load("json", text.text, str(path)) if text.exists else {}
    provider = edit.get_path(doc, PROVIDER)[1]
    provider = provider if isinstance(provider, dict) else {}
    # OpenProgram's provider is its own whole: the user's keys in it (headers,
    # timeoutSeconds) stay while connected and go with it on disconnect
    # (magpie DelJSON of its entry); a model's own keys (maxTokens) are kept
    # by model_entries, so the list is loose: an edit there is no drift
    entry = {"baseUrl": at.base("openai-chat"), "apiKey": at.key, "api": "openai-completions",
             "models": model_entries(at.models, provider.get("models"))}
    writes = [Write(path, "json", listing=True, sets={PROVIDER: entry}, owns=(PROVIDER,),
                    loose=(f"{PROVIDER}.models",))]
    ref = f"{PROVIDER_ID}/{at.model}"
    current = edit.get_path(doc, MODEL)[1]
    sets: dict[str, Any] = {f"{MODEL}.primary" if isinstance(current, dict) else MODEL: ref}
    if isinstance(edit.get_path(doc, MODELS)[1], dict):
        # as `openclaw models set` does
        sets[f"{MODELS}.{_escaped(ref)}"] = edit.get_path(doc, MODELS)[1].get(ref, {})
    members: dict[str, Members] = {}
    allow = edit.get_path(doc, ALLOW)[1]
    if isinstance(allow, list) and allow:  # an empty allow list allows every model
        theirs = [item for item in allow if item != ALL]
        provider_all = f"{PROVIDER_ID}/{at.model.split('/', 1)[0]}/*"
        let_in = not theirs or any(item in ("*", ref, provider_all) for item in theirs)
        members[ALLOW] = Members({"": ALL}, [] if let_in else [ALL])
    writes.append(Write(path, "json", sets=sets, members=members))
    return writes


def _notice() -> str:
    return ("OpenClaw takes this up without a restart. A chat that already has a model keeps it: "
            "send /model default in that chat to move it onto this one.")


def agent() -> Agent:
    return Agent(
        id="openclaw", name="OpenClaw", icon="openclaw", kind="bot", protocol="openai-chat",
        bin="openclaw", dir=state_dir, path=config_path, named_key=True, wire=_wire, notice=_notice,
    )
