"""Alma, a desktop AI chat app, through its own local REST API.

After yetone/magpie ``internal/agent/alma.go`` (MIT License, Copyright (c)
yetone). Alma keeps its providers and settings in the app, not in a file
OpenProgram can edit: they are reached through its local API
(http://localhost:23001, while Alma runs):

  GET  /api/providers              [{"id","name","type","baseURL","enabled","models":[…],"availableModels":[…]},…]
  POST /api/providers              {"name","type","apiKey","baseURL","enabled"} → the provider
  PUT  /api/providers/:id          the fields to change
  PUT  /api/providers/:id/models   {"models":[ids],"availableModels":[{"id","name","capabilityOverrides"}]}
  GET  /api/settings, PUT it back  the whole settings; chat.defaultModel is "<providerId>:<model>"

OpenProgram is one provider there, named ``openprogram``, of type openai at
the gateway's Chat Completions base, made under the id of Alma's own Magpie
preset (``PRESET_ID``, ``"magpie"``) when no provider has that id yet, as
magpie does: Alma takes a provider of that id as its preset set up and
offers no second one. The id is Alma's own and kept as Alma ships it; the
name is OpenProgram's. A provider the user set up in Alma first at the
gateway's address is used in its place and kept (only turned off again if
it was off) when OpenProgram steps out (``made``). Choosing one of its
models makes it Alma's default model. Alma not running is no error while
reading or keeping the list current; a change asked of it while it isn't
running says so.

magpie's image-generation pick (its "drawer") and model pricing have no
counterpart in OpenProgram's catalog and are left out.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from . import local_api
from .base import Agent, App, AppGone, Field, Live, Wiring, app_bundles, find_tool, user_config_dir

#: Where Alma's API answers.
API = "http://localhost:23001"
TIMEOUT = 1.5
#: The id of Alma's own Magpie provider preset (magpie ``almaPresetID``).
PRESET_ID = "magpie"
#: The name OpenProgram's provider has in Alma (magpie names its own ``magpie``).
NAME = "openprogram"
#: The reasoning levels Alma knows, in its order.
LEVELS = ("low", "medium", "high", "xhigh", "max")
#: Overrides whose value a model already has in Alma is the user's own.
USER_FIRST = ("functionCalling",)
DOWN = "Alma isn't running — open Alma and try again"
APP = App("com.yetone.alma", ("Alma.app",))


def _call(method: str, path: str, body: Any = None) -> Any:
    from .engine import AgentConfigError
    try:
        return local_api.request(method, API.rstrip("/") + path, body, timeout=TIMEOUT)
    except local_api.NotRunning as exc:
        raise _Down(DOWN) from exc
    except local_api.ApiError as exc:
        raise AgentConfigError(f"Alma: {method} {path}: {exc.status} {exc}") from exc


class _Down(Exception):
    """Alma does not answer."""


def _model_ids(raw: Any) -> list[str]:
    """A provider's models as ids: Alma keeps ids or model objects."""
    out = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, str) and item:
            out.append(item)
        elif isinstance(item, dict) and item.get("id") is not None:
            out.append(str(item["id"]))
    return out


def _providers() -> list[dict[str, Any]]:
    data = _call("GET", "/api/providers")
    return [p for p in data if isinstance(p, dict)] if isinstance(data, list) else []


def _hostport(url: str) -> tuple[str, Optional[int]]:
    try:
        parsed = urlparse(url or "")
        host = (parsed.hostname or "").lower()
        return ("127.0.0.1" if host in ("localhost", "::1") else host), parsed.port
    except ValueError:
        return "", None


def _loopback(url: str) -> bool:
    return _hostport(url)[0] in ("127.0.0.1", "")


def ours(providers: list[dict[str, Any]], v1: str) -> Optional[dict[str, Any]]:
    """OpenProgram's provider among Alma's: the one named ``openprogram`` (not
    on another machine), else an OpenAI-shaped one at the gateway (magpie ``almaMagpie``)."""
    for p in providers:
        if p.get("name") == NAME and p.get("type") in ("openai", "custom") and _loopback(p.get("baseURL") or ""):
            return p
    for p in providers:
        if p.get("type") in ("openai", "custom") and p.get("baseURL") and _hostport(p["baseURL"]) == _hostport(v1):
            return p
    return None


def made(p: dict[str, Any]) -> bool:
    """The provider is the one OpenProgram made (magpie ``almaMade``)."""
    return p.get("name") == NAME


def caps(row: dict[str, Any]) -> dict[str, Any]:
    """What Alma is told of a model as its capabilityOverrides (magpie ``almaCaps``):
    Alma looks capabilities up on models.dev by the id, which OpenProgram's
    ``provider/model`` ids are not."""
    out: dict[str, Any] = {}
    efforts = [str(level) for level in row.get("thinking_levels") or []]
    if row.get("reasoning") or [e for e in efforts if e != "off"]:
        out["reasoning"] = True
        levels = [level for level in LEVELS if level in efforts]
        if levels:
            out["reasoningLevels"] = levels
    if row.get("vision"):
        out["vision"] = True
    if int(row.get("context_window") or 0) > 0:
        out["contextWindow"] = int(row["context_window"])
    if int(row.get("max_tokens") or 0) > 0:
        out["maxOutputTokens"] = int(row["max_tokens"])
    return out


def models(at: Wiring) -> tuple[list[str], list[dict[str, Any]]]:
    """The catalog as Alma is shown it: the ids it offers, and each one's name
    and capabilities. Every model the gateway lists takes tools, which Alma
    would otherwise put in the prompt for an id models.dev doesn't know."""
    ids, known = [], []
    for row in at.models:
        ids.append(row["id"])
        overrides = caps(row)
        overrides["functionCalling"] = True
        known.append({"id": row["id"], "name": row.get("name") or row["id"], "capabilityOverrides": overrides})
    return ids, known


def with_overrides(p: dict[str, Any], known: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``known`` with each model's overrides laid over the ones it has in Alma:
    the overrides sent replace a model's, so one the user set that OpenProgram
    says nothing of is sent back, and a :data:`USER_FIRST` one stays the user's."""
    had = {k.get("id"): k.get("capabilityOverrides") for k in p.get("availableModels") or []
           if isinstance(k, dict) and isinstance(k.get("capabilityOverrides"), dict)}
    out = []
    for k in known:
        old = had.get(k["id"])
        mine = k.get("capabilityOverrides")
        if not old or not isinstance(mine, dict):
            out.append(k)
            continue
        merged = dict(old)
        for key, value in mine.items():
            if key in old and key in USER_FIRST:
                continue
            merged[key] = value
        out.append({**k, "capabilityOverrides": merged})
    return out


def _same(a: Any, b: Any) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def same_models(p: dict[str, Any], ids: list[str], known: list[dict[str, Any]]) -> bool:
    """Alma offers these models already, in this order, each named and described so."""
    have = [k for k in p.get("availableModels") or [] if isinstance(k, dict)]
    if _model_ids(p.get("models")) != ids or len(have) != len(known):
        return False
    for had, k in zip(have, known):
        if had.get("id") != k["id"] or had.get("name") != k["name"]:
            return False
        old = had.get("capabilityOverrides") if isinstance(had.get("capabilityOverrides"), dict) else {}
        for key, value in (k.get("capabilityOverrides") or {}).items():
            if key in old and key in USER_FIRST:
                continue
            if not _same(old.get(key), value):
                return False
    return True


def sync_models(at: Wiring, p: dict[str, Any]) -> None:
    ids, known = models(at)
    if same_models(p, ids, known):
        return
    _call("PUT", f"/api/providers/{p['id']}/models", {"models": ids, "availableModels": with_overrides(p, known)})


def _keyed(at: Wiring, p: dict[str, Any]) -> None:
    """Give the provider Alma's key in place of an older gateway key; a key
    of the user's own, or one Alma gives back encrypted, is left be."""
    key = p.get("apiKey") or ""
    if key == at.key or not key.startswith("opgw-"):
        return
    _call("PUT", f"/api/providers/{p['id']}", {"apiKey": at.key})


def wire(at: Wiring, kept: dict[str, Any]) -> str:
    """Alma has OpenProgram's provider, at the gateway, on, listing the
    catalog; its id (magpie ``almaWire``)."""
    from .engine import AgentConfigError
    v1 = at.base("openai-chat")
    providers = _providers()
    p = ours(providers, v1)
    if p is None:
        body: dict[str, Any] = {"name": NAME, "type": "openai", "apiKey": at.key, "baseURL": v1, "enabled": True}
        # under the id of Alma's own Magpie preset, so Alma offers no second
        # one; a provider of that id already there is the user's, and keeps it
        if not any(o.get("id") == PRESET_ID for o in providers):
            body["id"] = PRESET_ID
        new = _call("POST", "/api/providers", body)
        if not isinstance(new, dict) or not new.get("id"):
            p = ours(_providers(), v1)
            if p is None:
                raise AgentConfigError("Alma didn't keep OpenProgram's provider")
        else:
            p = new
    elif p.get("baseURL") != v1 or not p.get("enabled"):
        if not p.get("enabled") and not made(p):
            # a provider of the user's own, turned off: off again when OpenProgram is
            kept["was_off"] = p["id"]
        _call("PUT", f"/api/providers/{p['id']}", {"baseURL": v1, "apiKey": at.key, "enabled": True})
    else:
        _keyed(at, p)
    sync_models(at, p)
    return p["id"]


def _settings() -> dict[str, Any]:
    data = _call("GET", "/api/settings")
    return data if isinstance(data, dict) else {}


def default_model(settings: dict[str, Any]) -> str:
    chat = settings.get("chat")
    value = chat.get("defaultModel") if isinstance(chat, dict) else None
    return value if isinstance(value, str) else ""


def set_default(value: str) -> None:
    """chat.defaultModel, the rest of the settings put back as they were:
    Alma takes only the whole object."""
    settings = _settings()
    if default_model(settings) == value:
        return
    chat = settings.get("chat")
    if not isinstance(chat, dict):
        chat = settings["chat"] = {}
    chat["defaultModel"] = value
    _call("PUT", "/api/settings", settings)


def data_dir() -> Path:
    """Alma's data: the desktop's folder under the user config folder, or on
    Linux alma-server's ($ALMA_DATA_DIR, else $XDG_DATA_HOME/alma)."""
    dirs = [user_config_dir() / "alma"]
    if sys.platform not in ("darwin", "win32"):
        if os.environ.get("ALMA_DATA_DIR"):
            dirs.append(Path(os.environ["ALMA_DATA_DIR"]))
        xdg = os.environ.get("XDG_DATA_HOME")
        dirs.append(Path(xdg) / "alma" if xdg else Path.home() / ".local" / "share" / "alma")
    for folder in dirs:
        if folder.is_dir():
            return folder
    return dirs[0]


def installed() -> bool:
    """Alma's desktop app, its data folder, or on Linux alma-server on PATH."""
    if app_bundles(APP) or data_dir().is_dir():
        return True
    return sys.platform not in ("darwin", "win32") and bool(find_tool("alma-server"))


# Live hooks

def _connect(at: Wiring, kept: dict[str, Any]) -> dict[str, Any]:
    from .engine import AgentConfigError
    kept = dict(kept)
    try:
        if "default" not in kept:
            # Alma's default model before OpenProgram's, put back on disconnect
            kept["default"] = default_model(_settings())
        had = at.same_pick and ours(_providers(), at.base("openai-chat")) is not None
        pid = wire(at, kept)
        if not had:
            # connected on this model already (a moved port, a new key): only
            # the provider's address and key, a default picked in Alma since stays
            set_default(f"{pid}:{at.model}")
    except _Down as exc:
        raise AgentConfigError(str(exc)) from exc
    kept["provider"], kept["base"] = pid, at.base("openai-chat")
    return kept


def _disconnect(kept: dict[str, Any]) -> None:
    """Back to Alma's own: the default model it had before, while still
    OpenProgram's (none when that one's provider is gone), and OpenProgram's
    provider comes out; one of the user's own stays (turned off again if it was)."""
    from .engine import AgentConfigError
    try:
        settings = _settings()
    except _Down as exc:
        if not installed():
            # Alma uninstalled keeps its data, which only Alma changes:
            # OpenProgram's provider comes out once it is back (magpie almaTakeOut)
            raise AppGone("Alma is uninstalled") from exc
        raise AgentConfigError(str(exc)) from exc
    try:
        providers = _providers()
        p = next((o for o in providers if o.get("id") == kept.get("provider")), None) \
            or ours(providers, kept.get("base") or "")
        if p is None:
            return
        if default_model(settings).split(":", 1)[0] == p["id"]:
            # still OpenProgram's: the default Alma had before, while its
            # provider is still there (and not the one going), else none
            was = str(kept.get("default") or "")
            owner = was.split(":", 1)[0]
            stays = owner != p["id"] or not made(p)
            set_default(was if was and stays and any(o.get("id") == owner for o in providers) else "")
        if not made(p):
            if kept.get("was_off") == p["id"]:
                _call("PUT", f"/api/providers/{p['id']}", {"enabled": False})
            return
        _call("DELETE", f"/api/providers/{p['id']}")
    except _Down as exc:
        raise AgentConfigError(str(exc)) from exc


def _sync(at: Wiring, kept: dict[str, Any]) -> dict[str, Any]:
    try:
        p = ours(_providers(), at.base("openai-chat"))
        if p is None:
            return kept
        _keyed(at, p)
        sync_models(at, p)
    except _Down:
        pass  # not running: nothing to keep current
    return kept


def _check(at: Wiring, kept: dict[str, Any]) -> list[str]:
    try:
        settings = _settings()
        p = ours(_providers(), at.base("openai-chat"))
    except _Down:
        return []
    if p is None or default_model(settings).split(":", 1)[0] != p["id"]:
        return []
    if not p.get("enabled"):
        return ["providers.enabled"]
    if p.get("baseURL") != at.base("openai-chat"):
        return ["providers.baseURL"]
    return []


def agent() -> Agent:
    return Agent(
        id="alma", name="Alma", icon="alma", kind="app", protocol="openai-chat",
        path=data_dir, dir=data_dir, detect_only=True, detect=installed, ua=("alma",),
        fields=[Field("model", "Model")], wire=lambda at: [],
        live=Live(connect=_connect, disconnect=_disconnect, sync=_sync, check=_check),
    )
