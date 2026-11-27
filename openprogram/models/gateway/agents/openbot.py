"""OpenBot (nightly-labs/openbot), an Electron workspace that runs Codex,
Claude, OpenCode and other agents and lists the user's own OpenAI-compatible
endpoints in its model picker.

After yetone/magpie ``internal/agent/openbot.go`` (MIT License, Copyright (c)
yetone). OpenBot keeps those endpoints in ``openbot-custom-providers-v1.json``
in Electron's userData folder: ``~/Library/Application Support/OpenBot`` on
a Mac, ``%APPDATA%\\OpenBot`` on Windows, ``~/.config/OpenBot`` on Linux
(src/main/index.ts; v0.58.0). The file (custom-provider-store.ts)::

    {"version":1,"providers":[{"id":"openprogram","name":"OpenProgram",
      "baseUrl":"http://127.0.0.1:<port>/gateway/agent/openbot.<mac>/openai/v1",
      "models":[{"id":"<model>","name":"<name>","contextWindow":<n>},…],
      "secret":null}]}

``version`` must be 1: a file OpenBot can't parse leaves it with no
endpoints and refuses every save, so a file it couldn't read is left alone
the same way. An entry's key and headers are one ciphertext, ``secret``,
sealed with Electron's safeStorage (the Keychain, DPAPI), which OpenProgram
doesn't open, so its entry has none: OpenBot hands it to OpenCode keyless.
magpie's gateway takes a keyless request from this computer; OpenProgram's
takes none, so OpenBot's own key is a segment of the address it is given
(``Agent.path_keyed``), which also names OpenBot where OpenCode sends
OpenCode's User-Agent. OpenProgram's entry sits in the list beside the
user's own (``Members``), and a secret, a name or a harness the user gave
it in OpenBot stays.

OpenBot's list decoders fail closed: one entry or model it wouldn't accept
empties the whole list. So the models are at most 64, each with an id
OpenBot takes as ``openprogram/<id>`` (ASCII letters, digits and
``._:/[],=-``, at most 160), and a context window only inside its 1,024 to
100,000,000. OpenBot reads the file once, when it starts: a change shows
after a restart. The model is picked in OpenBot's own picker; what
OpenProgram sets is whether its models are in it.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from .base import Agent, App, Field, Members, Wiring, Write
from .opencode_format import PROVIDER_ID, config_home, unreadable

FILE = "openbot-custom-providers-v1.json"
MAX_MODELS = 64
MODEL_ID = re.compile(r"^[A-Za-z0-9._:/\[\],=-]+$")
APP = App("app.openbot.desktop", ("OpenBot.app",))


def home() -> Path:
    """OpenBot's userData folder, as Electron finds it."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "OpenBot"
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / "OpenBot"
    return config_home() / "OpenBot"


def config_path() -> Path:
    return home() / FILE


def _current(path: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The file as it is and OpenProgram's entry in it; one OpenBot wouldn't
    read is left alone."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, None
    except OSError as exc:
        raise unreadable(path, str(exc)) from exc
    if not raw.strip():
        return {}, None
    try:
        doc = json.loads(raw)
    except ValueError:
        doc = None
    if not isinstance(doc, dict):
        raise unreadable(path, "isn't JSON OpenBot reads")
    if doc.get("version") != 1 or isinstance(doc.get("version"), bool):
        raise unreadable(path, f"says version {doc.get('version')!r}, not the 1 OpenBot reads, so it is left alone")
    providers = doc.get("providers")
    if not isinstance(providers, list):
        raise unreadable(path, "providers is not a list")
    ours = next((p for p in providers if isinstance(p, dict) and p.get("id") == PROVIDER_ID), None)
    return doc, ours


def entry(at: Wiring, have: dict[str, Any] | None = None) -> dict[str, Any]:
    """OpenProgram's entry: the gateway's Chat Completions on OpenBot's own
    path, its key in the address and none in ``secret``, and the models OpenBot would list, at most 64; a name,
    harness or secret the user gave it there kept."""
    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for model in at.models:
        model_id = model.get("id") or ""
        if (not model_id or model_id in seen or len(model_id) > 128
                or len(PROVIDER_ID) + 1 + len(model_id) > 160 or not MODEL_ID.match(model_id)):
            continue
        seen.add(model_id)
        name = model.get("name") or ""
        if not name or len(name) > 160:
            name = model_id
        item: dict[str, Any] = {"id": model_id, "name": name}
        window = int(model.get("context_window") or 0)
        if 1024 <= window <= 100_000_000:
            item["contextWindow"] = window
        models.append(item)
        if len(models) == MAX_MODELS:
            break
    out: dict[str, Any] = {"id": PROVIDER_ID, "name": "OpenProgram", "baseUrl": at.base("openai-chat"),
                           "models": models, "secret": None}
    have = have or {}
    if isinstance(have.get("name"), str) and have["name"]:
        out["name"] = have["name"]
    if isinstance(have.get("harness"), str):
        out["harness"] = have["harness"]
    if isinstance(have.get("secret"), str):
        out["secret"] = have["secret"]
    return out


def _wire(at: Wiring) -> list[Write]:
    path = config_path()
    doc, ours = _current(path)
    # the version stays: OpenBot reads no file without it, and a provider of
    # the user's own may be beside OpenProgram's by the time it goes
    keep: dict[str, Any] = {} if "version" in doc else {"version": 1}
    ours_only = Members(match={"id": PROVIDER_ID}, items=[entry(at, ours)], keep_place=True, key="id",
                        kept=("name", "harness", "secret"))
    return [Write(path, "json", keep=keep, members={"providers": ours_only}, listing=True)]


def _unwire(record: dict[str, Any]) -> None:
    """OpenProgram's entry out of the file, however OpenBot has rewritten it
    since, and the file too where OpenProgram made it and nothing but the
    version and an empty list is left, which OpenBot starts on without a
    file as well (magpie ``openBotUnwire``)."""
    from .. import edit
    from ..edit import ConfigText, ConfigUnreadable
    path = config_path()
    kept = (record.get("files") or {}).get(str(path)) or {}
    try:
        doc, ours = _current(path)
    except ConfigUnreadable:  # a file OpenBot can't read is left as it is
        return
    if ours is None:
        return
    providers = [p for p in doc["providers"] if not (isinstance(p, dict) and p.get("id") == PROVIDER_ID)]
    if not providers and kept.get("absent") and not (set(doc) - {"version", "providers"}):
        path.unlink(missing_ok=True)
        return
    text = ConfigText.read(path)
    text.write(edit.edit("json", text.text, sets={"providers": providers}, where=str(path)))


def _notice() -> str:
    from .running import any_running
    if any_running(r"(^|/)(OpenBot|openbot)( |$)"):
        return ("OpenBot reads its custom endpoints at start-up — restart OpenBot to see OpenProgram's models in its "
                "model picker, before adding or editing an endpoint there.")
    return ""


def agent() -> Agent:
    return Agent(
        id="openbot", name="OpenBot", icon="openbot", aliases=("open-bot",), kind="app",
        protocol="openai-chat", dir=home, path=config_path, fields=[Field("model", "Model")],
        path_keyed=True, app=APP, wire=_wire, unwire=_unwire, notice=_notice,
    )
