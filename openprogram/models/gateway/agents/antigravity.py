"""Antigravity, Google's desktop app (2.15 and later), on the gateway as its enterprise gateway.

After yetone/magpie ``internal/agent/antigravity.go`` (MIT License,
Copyright (c) yetone). The app sends its requests to a gateway of the
user's instead of Google when its settings file turns one on (Google's
Antigravity Enterprise Gateway)::

    {"gateway": {"enabled": true, "url": …, "apiKey": …, "wireProtocol": "openai"},
     "models": {"defaultModelId": …, "listSource": …}}

On the openai wire protocol the app asks ``<url>/v1/models`` for its picker
and sends a chat to ``<url>/v1/chat/completions`` with apiKey as a bearer
and a User-Agent of ``antigravity/hub/<version>``. Each version reads its
own file alone and an update moves nothing across, so the same block is
written into both: ``~/.gemini/config/config.json`` (2.30 on) and
``~/.antigravity/settings.json`` (up to 2.29). Its gateway discovery reads a
model's id alone and gives one its built-in catalog doesn't know a window of
200K tokens, so ``models.listSource`` points it at the gateway's
``/models?format=antigravity`` (``models_info.antigravity_list``), which
gives each model its window; a ``models.list`` of the user's, read ahead of
it, is taken out while connected. The user's own gateway, default model,
list and catalog are kept and put back on disconnect.

On a gateway the app asks for no Google sign-in and uses none, so what
Google turns on for a signed-in account is off while it is on OpenProgram;
the notice says so. An organisation's admin_settings.json and the
``AGY_LLM_GATEWAY_*`` variables win over this file, field by field: either
is shown as drift. The app's picker gathers a model's variants named
"(Low)", "(High)" … into one row with a menu of levels, so the catalog lists
a model with two or more levels once a level (``<id>:<level>``), and the app
starts on the model at the level its picker would, medium or the weakest
(:func:`start_on`); the gateway asks for it at that level. Its row's
effort (magpie's per-agent effort) starts the model at that level and,
kept for the agent, is the level the gateway asks for when a request
names none.
"""
from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path
from typing import Any

from .. import edit
from ..edit import ConfigText, ConfigUnreadable
from ..agent_effort import effort_options
from .base import Agent, App, Field, Pick, Wiring, Write, app_bundles

#: Every key OpenProgram gives an agent begins so (keys.token_for).
KEY_PREFIX = "opgw-"
MODEL = "models.defaultModelId"
LIST = "models.list"
SOURCE = "models.listSource"
APP = App("com.google.antigravity", ("Antigravity.app",))


def files() -> list[Path]:
    """The files the app takes its gateway from, newest first."""
    home = Path.home()
    return [home / ".gemini" / "config" / "config.json", home / ".antigravity" / "settings.json"]


def admin_path() -> Path:
    """Where an organisation's admin_settings.json is kept, whose gateway outranks the user's."""
    if sys.platform == "darwin":
        return Path("/Library/Application Support/Antigravity/admin_settings.json")
    if sys.platform == "win32":
        return Path(os.environ.get("ProgramData") or "C:\\ProgramData") / "Antigravity" / "admin_settings.json"
    return Path("/etc/antigravity/admin_settings.json")


def gateway_url(at: Wiring) -> str:
    """The address the app appends /v1/models and /v1/chat/completions to."""
    base = at.base("openai-chat")
    return base[: -len("/v1")] if base.endswith("/v1") else base


def catalog_url(at: Wiring) -> str:
    from ..models_info import ANTIGRAVITY_FORMAT
    return at.base("openai-chat") + "/models?format=" + ANTIGRAVITY_FORMAT


def start_on(model: str, current: str, effort: str = "") -> str:
    """The id the app starts on for ``model``, after magpie ``startOn``: the
    level the file starts it on stays when the catalog lists it, else the
    one its picker starts it on (``models_info.antigravity_model``)."""
    from ..conversion import asked_at
    from ..models_info import antigravity_model
    to = antigravity_model(model, effort)
    base, level = asked_at(current)
    if level and base == model and to != model:
        from ..conversion import enabled_model_refs
        from ..models_info import antigravity_fit
        rows = enabled_model_refs()
        for index, levels in antigravity_fit(rows, model):
            if rows[index]["id"] == model and level in levels:
                return current
    return to


def _wire(at: Wiring) -> list[Write]:
    """The gateway block, OpenProgram's whole (``owns``: what the user adds
    inside it stays), the catalog it reads windows from, and the model the
    app starts on, which its own picker rewrites (a ``Pick``): a reconnect
    on the same pick (a new port) leaves the one picked in the app, and
    disconnecting puts the user's back only while the file's gateway still
    carries a key of OpenProgram's (magpie ``leave`` while ``wired()``)."""
    block = {"enabled": True, "url": gateway_url(at), "apiKey": at.key, "wireProtocol": "openai"}
    current = start_model()
    was = at.previous.get("values") or {}
    effort = at.values.get("effort", "")
    if was.get("model") == at.model and current:
        # a new effort starts the model picked in the app at it (magpie startOn)
        from ..conversion import asked_at
        from ..models_info import antigravity_model
        model = current if was.get("effort", "") == effort else antigravity_model(asked_at(current)[0], effort)
    else:
        model = start_on(at.model, current, effort)
    return [Write(path, "json", sets={"gateway": block, SOURCE: catalog_url(at), MODEL: model}, deletes=[LIST],
                  owns=("gateway",), picks=(Pick((MODEL,), "gateway.apiKey", {"prefix": KEY_PREFIX}),))
            for path in files()]


def _load(path: Path) -> dict[str, Any]:
    try:
        return edit.load("json", ConfigText.read(path).text, str(path))
    except ConfigUnreadable:
        return {}


def wired(doc: dict[str, Any]) -> bool:
    """The file's gateway is on and carries a key OpenProgram gave it."""
    gateway = doc.get("gateway") if isinstance(doc.get("gateway"), dict) else {}
    return gateway.get("enabled") is True and str(gateway.get("apiKey") or "").startswith(KEY_PREFIX)


def start_model() -> str:
    """The model the app is set to start on, from the first file on the gateway; ``""`` for none."""
    for path in files():
        doc = _load(path)
        if wired(doc):
            return str(edit.get_path(doc, MODEL)[1] or "")
    return ""


def app_version() -> str:
    for bundle in app_bundles(APP):
        try:
            with open(bundle / "Contents" / "Info.plist", "rb") as handle:
                return str(plistlib.load(handle).get("CFBundleShortVersionString") or "")
        except (OSError, ValueError, plistlib.InvalidFileException):
            continue
    return ""


def _at_least(version: str, floor: str) -> bool:
    def parts(v: str) -> list[int]:
        return [int(p) if p.isdigit() else 0 for p in v.split(".")]
    have, want = parts(version), parts(floor)
    width = max(len(have), len(want))
    return have + [0] * (width - len(have)) >= want + [0] * (width - len(want))


def detect() -> bool:
    """~/.antigravity is also Antigravity IDE's and agy's, so it says nothing of
    the app: the app is its bundle (2.15 or later, the first to take a
    gateway) or the file only its own backend writes."""
    version = app_version()
    if version and _at_least(version, "2.15.0"):
        return True
    return (Path.home() / ".gemini" / "antigravity" / "agyhub_summaries_proto.pb").is_file()


def _check() -> str:
    """What keeps the app off the gateway its files name (magpie ``Check``):
    an organisation's gateway, or one in its environment."""
    url = str(edit.get_path(_load(admin_path()), "gateway.url")[1] or "")
    if url:
        return (f"Antigravity's {admin_path()} sets a gateway of its own, which wins over its settings, "
                "so it doesn't reach OpenProgram")
    env = os.environ.get("AGY_LLM_GATEWAY_URL") or ""
    if env:
        from . import find
        from .base import picked
        from .engine import wiring
        if env != gateway_url(wiring(find("antigravity"), picked("antigravity"))):
            return (f"AGY_LLM_GATEWAY_URL is set to {env}, which wins over its settings, so Antigravity started "
                    "with it doesn't reach OpenProgram")
    return ""


def _notice() -> str:
    return ("Antigravity reads its gateway at start-up — restart Antigravity to use OpenProgram's models. On a "
            "gateway it uses no Google sign-in, so what Google turns on for your account (its experiments, and "
            "commands such as /boost, /teamwork, /plugin and /generative_ui) is gone while it's on OpenProgram; "
            "turning it off here gives that back.")


def agent() -> Agent:
    return Agent(
        id="antigravity", name="Antigravity", icon="antigravity-color", aliases=("antigravity-app", "agy-app"),
        kind="app", protocol="openai-chat", dir=lambda: files()[1].parent, path=lambda: files()[1],
        fields=[Field("model", "Model"),
                # how hard its models reason: the level the app starts on, and
                # what a model its picker offers no levels of is asked for
                Field("effort", "Effort", quiet=True, options=lambda: effort_options("antigravity"), kept=True)],
        ua=("antigravity",), wire=_wire, detect_only=True, detect=detect,
        notice=_notice, app=APP, check=_check,
    )
