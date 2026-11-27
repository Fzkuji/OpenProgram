"""OpenHanako (HanaAgent, a desktop personal agent), under ``$HANA_HOME``, ``~/.hanako`` by default.

After yetone/magpie ``internal/agent/hanako.go`` (MIT License, Copyright (c)
yetone). What OpenHanako keeps::

    provider-catalog.json                      {"catalogVersion":2,"providers":{"<id>":{…}},…}
    provider-plugins/<id>/providers/<id>.json  a provider added by hand, moved out of the catalog at start-up
                                               (all but its key and headers, which stay in the catalog)
    user/preferences.json                      primaryAgent: the agent it opens on
    agents/<id>/config.yaml                    that agent's settings; models.chat is {id, provider}
    server-info.json                           {port, token, …}, while it runs

Since 1.0 OpenHanako seals the catalog under a key its desktop app keeps in
the system keychain (``HANA-SECRET-1.AES-256-GCM.…``): OpenProgram can't read
or write it and never writes over it. A provider's definition is in its
plugin file, out of the catalog, so that is still OpenProgram's to keep up
to date; its key stays sealed, and adding or removing the provider needs
OpenHanako running, to do it through its API.

OpenProgram is one provider there, ``openprogram``, spoken to as chat
completions at the gateway with the enabled models; choosing one makes it
the primary agent's chat model. While OpenHanako runs, both go through its
local API, which saves them and reloads its models at once::

    PUT /api/config               {"providers":{"openprogram":{…}|null}}
    PUT /api/agents/:id/config    {"models":{"chat":{"id","provider"}}}
    GET /api/models               {"models":[{"id","provider",…}]}

Otherwise the files are written (comments and all else kept), which it reads
when it starts. The model the agent had is kept and put back on the way out;
since 1.0 the API takes no agent without a model, so with none kept, or one
it no longer has, the agent goes to the first of OpenHanako's own.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from .. import edit
from ..edit import ConfigText, ConfigUnreadable
from . import local_api
from .base import Agent, App, Field, Live, Wiring, app_bundles

ID = "openprogram"
PROBE = 1.5
WAIT = 15.0
APP = App("com.hanako.app", ("HanaAgent.app",))


def home() -> Path:
    raw = os.environ.get("HANA_HOME") or ""
    if not raw:
        return Path.home() / ".hanako"
    if raw == "~" or raw.startswith("~/") or raw.startswith("~" + os.sep):
        raw = str(Path.home()) + raw[1:]
    return Path(os.path.abspath(raw))


def catalog_path(folder: Optional[Path] = None) -> Path:
    return (folder or home()) / "provider-catalog.json"


def plugin_dir(folder: Path) -> Path:
    return folder / "provider-plugins" / ID


def plugin_file(folder: Path) -> Path:
    return plugin_dir(folder) / "providers" / f"{ID}.json"


def _installed() -> bool:
    """OpenHanako's desktop app is installed here (HanaAgent.app, HanaAgent.exe
    under %LOCALAPPDATA%\\Programs, /opt/HanaAgent)."""
    if sys.platform == "darwin":
        return bool(app_bundles(APP))
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        return bool(local) and (Path(local) / "Programs" / "HanaAgent" / "HanaAgent.exe").is_file()
    return Path("/opt/HanaAgent").is_dir()


#: Whether the app is installed; a test can say.
installed: Callable[[], bool] = _installed


def sealed_msg(what: str) -> str:
    return (f"OpenHanako keeps its providers encrypted, so OpenProgram can only {what} it while it runs: "
            "open OpenHanako and try again")


class HanakoError(Exception):
    pass


# Files

def _load(fmt: str, path: Path) -> dict[str, Any]:
    try:
        return edit.load(fmt, ConfigText.read(path).text, str(path))
    except ConfigUnreadable:
        return {}


def _edit(fmt: str, path: Path, sets: Optional[dict[str, Any]] = None, deletes: Optional[list[str]] = None) -> None:
    text = ConfigText.read(path)
    new = edit.edit(fmt, text.text, sets=sets, deletes=deletes, where=str(path))
    if new != text.text or not text.exists:
        text.write(new)


def primary_agent(folder: Path) -> str:
    """The agent OpenHanako opens on: primaryAgent if it has a config, else the
    first agent that does, as its engine picks at start-up."""
    agents = folder / "agents"

    def has(aid: Any) -> bool:
        return isinstance(aid, str) and aid != "" and "/" not in aid and "\\" not in aid \
            and (agents / aid / "config.yaml").is_file()
    prefs = _load("json", folder / "user" / "preferences.json")
    if has(prefs.get("primaryAgent")):
        return prefs["primaryAgent"]
    try:
        names = sorted(entry.name for entry in os.scandir(agents) if entry.is_dir())
    except OSError:
        return ""
    return next((name for name in names if has(name)), "")


def chat_of(folder: Path, aid: str) -> tuple[str, str]:
    """The agent's chat model, ``(provider, id)``."""
    if not aid:
        return "", ""
    chat = (_load("yaml", folder / "agents" / aid / "config.yaml").get("models") or {}).get("chat")
    if isinstance(chat, dict):
        return str(chat.get("provider") or ""), str(chat.get("id") or "")
    if isinstance(chat, str):
        return "", chat  # before OpenHanako's migration #5, a plain id
    return "", ""


def sealed(path: Path) -> bool:
    try:
        raw = path.read_bytes()
    except OSError:
        return False
    return raw.strip().lstrip(b"\xef\xbb\xbf").strip().startswith(b"HANA-SECRET-")


def provider_entry(at: Wiring) -> dict[str, Any]:
    """OpenProgram's entry in the catalog. OpenHanako runs on Pi's SDK, whose
    chat completions carry the effort as reasoning_effort; image and reasoning
    are always said, since a model entry saved over an older one keeps what
    the new one leaves out."""
    models = []
    for row in at.models:
        levels = [level for level in row.get("thinking_levels") or [] if level and level != "off"]
        m: dict[str, Any] = {"id": row["id"]}
        if row.get("name"):
            m["name"] = row["name"]
        if row.get("context_window"):
            m["context"] = int(row["context_window"])
        if row.get("max_tokens"):
            m["maxOutput"] = int(row["max_tokens"])
        m["image"] = bool(row.get("vision"))
        m["reasoning"] = bool(levels)
        if "xhigh" in levels:
            m["xhigh"] = True
        models.append(m)
    return {"display_name": "OpenProgram", "base_url": at.base("openai-chat"), "api": "openai-completions",
            "api_key": at.key, "models": models}


def current(folder: Path) -> Optional[dict[str, Any]]:
    """OpenProgram's provider as OpenHanako sees it: the definition in its
    plugin file, if it has moved there, under what the catalog says."""
    cur: dict[str, Any] = {}
    found = False
    try:
        p = json.loads(plugin_file(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        p = None
    if isinstance(p, dict):
        cur.update({"display_name": p.get("displayName"), "base_url": p.get("defaultBaseUrl"),
                    "api": p.get("defaultApi")})
        if p.get("models") is not None:
            cur["models"] = p["models"]
        found = True
    if not sealed(catalog_path(folder)):
        entry = (_load("json", catalog_path(folder)).get("providers") or {}).get(ID)
        if isinstance(entry, dict):
            cur.update(entry)
            found = True
    return cur if found else None


def same(cur: dict[str, Any], want: dict[str, Any], is_sealed: bool) -> bool:
    """The provider says all that ``want`` does; keys the user added don't
    count, nor, in a sealed catalog, its key, which can't be read."""
    for key, value in want.items():
        if key == "api_key" and is_sealed:
            continue
        if key == "models" and not value and not cur.get(key):
            continue
        if cur.get(key) != value:
            return False
    return True


def deleted(path: Path) -> list[str]:
    ids = (_load("json", path).get("meta") or {}).get("deletedProviders")
    return [i for i in ids if isinstance(i, str)] if isinstance(ids, list) else []


# The running app

class Server:
    def __init__(self, port: int, token: str):
        self.port, self.token = port, token

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def put(self, path: str, body: Any) -> None:
        try:
            local_api.request("PUT", self.url(path), body, headers={"Authorization": f"Bearer {self.token}"},
                              timeout=WAIT)
        except local_api.ApiError as exc:
            raise HanakoError(f"OpenHanako: {exc.status} {path}: {exc}") from exc
        except local_api.NotRunning as exc:
            raise HanakoError(f"OpenHanako: {exc}") from exc

    def models(self) -> list[dict[str, Any]]:
        try:
            out = local_api.request("GET", self.url("/api/models"), headers={"Authorization": f"Bearer {self.token}"},
                                    timeout=WAIT)
        except (local_api.ApiError, local_api.NotRunning) as exc:
            raise HanakoError(f"OpenHanako: /api/models: {exc}") from exc
        return [m for m in (out or {}).get("models") or [] if isinstance(m, dict)]


def live_server(folder: Path) -> Optional[Server]:
    """OpenHanako's API if it answers there with its own token, ``None`` if it
    isn't running: server-info.json outlives a crash."""
    try:
        info = json.loads((folder / "server-info.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(info, dict):
        return None
    port, token = info.get("port"), info.get("token")
    if not isinstance(port, int) or port <= 0 or not isinstance(token, str) or not token:
        return None
    s = Server(port, token)
    try:
        ident = local_api.request("GET", s.url("/api/server/identity"),
                                  headers={"Authorization": f"Bearer {token}"}, timeout=PROBE)
    except (local_api.ApiError, local_api.NotRunning):
        return None
    if not isinstance(ident, dict) or not ident.get("serverId"):
        return None
    return s


# Changes

def save(folder: Path, entry: dict[str, Any]) -> None:
    """Write OpenProgram's provider: through the API while it runs, else into
    the catalog, whence OpenHanako moves the definition into a plugin at
    start-up. An old plugin goes, or its models would be kept beside the
    new ones."""
    s = live_server(folder)
    if s is not None:
        s.put("/api/config", {"providers": {ID: entry}})
        return
    path = catalog_path(folder)
    if sealed(path):
        save_plugin(folder, entry)
        return
    text = ConfigText.read(path)
    if not text.text.strip():
        # it holds keys: only its owner reads it, and a catalog without its
        # version is one OpenHanako refuses
        folder.mkdir(parents=True, exist_ok=True)
        # through a symlinked catalog to its target
        edit.write_atomic(path, '{\n  "catalogVersion": 2,\n  "providers": {}\n}\n', keep_mode=False)
    sets: dict[str, Any] = {f"providers.{ID}": entry}
    ids = deleted(path)
    if ID in ids:
        sets["meta.deletedProviders"] = [i for i in ids if i != ID]
    _edit("json", path, sets=sets)
    shutil.rmtree(plugin_dir(folder), ignore_errors=True)


def save_plugin(folder: Path, entry: dict[str, Any]) -> None:
    """Keep OpenProgram's definition up to date in its plugin file, beside a
    sealed catalog: all but the key, which is the catalog's. With no plugin
    file there is nothing to keep up to date, and only OpenHanako can add it."""
    file = plugin_file(folder)
    if not file.is_file():
        raise HanakoError(sealed_msg("add OpenProgram to"))
    _edit("json", file, sets={"displayName": entry["display_name"], "defaultBaseUrl": entry["base_url"],
                              "defaultApi": entry["api"], "models": entry["models"]})


def remove(folder: Path) -> None:
    """Take OpenProgram's provider out, marked deleted as OpenHanako marks one
    it removes, else its start-up would bring it back from its models.json."""
    if current(folder) is None:
        return
    s = live_server(folder)
    if s is not None:
        s.put("/api/config", {"providers": {ID: None}})
        return
    path = catalog_path(folder)
    if sealed(path):
        raise HanakoError(sealed_msg("take OpenProgram out of"))
    if path.exists():
        _edit("json", path, deletes=[f"providers.{ID}"])
        ids = deleted(path)
        if ID not in ids:
            _edit("json", path, sets={"meta.deletedProviders": [*ids, ID]})
    shutil.rmtree(plugin_dir(folder), ignore_errors=True)


def set_chat(folder: Path, aid: str, value: str) -> None:
    """Set an agent's chat model, ``provider/id`` split at the first slash as
    OpenHanako splits it; ``""`` takes it out."""
    if not aid:
        return
    chat: Any = None
    if "/" in value:
        provider, mid = value.split("/", 1)
        chat = {"id": mid, "provider": provider}
    elif value:
        chat = value
    s = live_server(folder)
    if s is not None:
        s.put(f"/api/agents/{aid}/config", {"models": {"chat": chat}})
        return
    path = folder / "agents" / aid / "config.yaml"
    if chat is None:
        _edit("yaml", path, deletes=["models.chat"])
    else:
        _edit("yaml", path, sets={"models.chat": chat})


def back(folder: Path, aid: str, prev: str) -> None:
    """Put an agent's chat model back off OpenProgram's: ``prev`` if it is one
    of OpenHanako's models (an id alone by its id), else the first model of
    its own. Running, OpenHanako takes only a model it has, as provider/id."""
    s = live_server(folder)
    if s is None or not aid:
        set_chat(folder, aid, prev)
        return
    pick = ""
    has_ref = "/" in prev
    provider, mid = prev.split("/", 1) if has_ref else ("", prev)
    for m in s.models():
        mp, mi = m.get("provider") or "", m.get("id") or ""
        if not mp or mp == ID or not mi:
            continue
        if (has_ref and mp == provider and mi == mid) or (not has_ref and prev and mi == prev):
            pick = f"{mp}/{mi}"
            break
        if not pick and not prev:
            pick = f"{mp}/{mi}"
    if not pick and prev:
        back(folder, aid, "")
        return
    if not pick:
        raise HanakoError("OpenHanako has no model of its own to go back to — add a provider in OpenHanako's "
                          "settings, then turn OpenProgram off again")
    set_chat(folder, aid, pick)


# Live hooks

def _err(exc: Exception):
    from .engine import AgentConfigError
    return AgentConfigError(str(exc))


def _connect(at: Wiring, kept: dict[str, Any]) -> dict[str, Any]:
    from .engine import AgentConfigError
    folder = home()
    aid = primary_agent(folder)
    if not aid:
        raise AgentConfigError("OpenHanako has no agent yet — open it once to make one")
    kept = dict(kept)
    provider, mid = chat_of(folder, aid)
    if provider != ID and mid:
        kept["agent"], kept["chat"] = aid, f"{provider}/{mid}" if provider else mid
    # connected on this model already (a moved port, a new key): the
    # provider only, a chat model picked in OpenHanako since stays
    had = at.same_pick and current(folder) is not None
    try:
        save(folder, provider_entry(at))
        if not had:
            set_chat(folder, aid, f"{ID}/{at.model}")
    except (HanakoError, ConfigUnreadable, OSError) as exc:
        raise _err(exc) from exc
    return kept


def _disconnect(kept: dict[str, Any]) -> None:
    folder = home()
    aid = primary_agent(folder)
    try:
        gone = False
        if current(folder) is not None and sealed(catalog_path(folder)) and live_server(folder) is None:
            if installed():
                # nothing changes rather than half of it, while OpenHanako can be opened to do it all
                raise HanakoError(sealed_msg("take OpenProgram out of"))
            # uninstalled, it can't be opened: the model goes back in its plain
            # config and the provider stays in the sealed catalog
            gone = True
        if chat_of(folder, aid)[0] == ID:
            back(folder, aid, kept.get("chat", "") if kept.get("agent") == aid else "")
        if gone:
            return
        remove(folder)
    except (HanakoError, ConfigUnreadable, OSError) as exc:
        raise _err(exc) from exc


def _sync(at: Wiring, kept: dict[str, Any]) -> dict[str, Any]:
    folder = home()
    cur = current(folder)
    want = provider_entry(at)
    if cur is None or same(cur, want, sealed(catalog_path(folder))):
        return kept
    try:
        save(folder, want)
    except (HanakoError, ConfigUnreadable, OSError) as exc:
        raise _err(exc) from exc
    return kept


def _check(at: Wiring, kept: dict[str, Any]) -> list[str]:
    folder = home()
    if chat_of(folder, primary_agent(folder))[0] != ID:
        return []
    cur = current(folder) or {}
    gone = []
    if cur.get("base_url") != at.base("openai-chat"):
        gone.append("base_url")
    if not sealed(catalog_path(folder)) and cur.get("api_key") != at.key:
        gone.append("api_key")
    return gone


def agent() -> Agent:
    return Agent(
        id="hanako", name="OpenHanako", icon="hanako", kind="app", protocol="openai-chat",
        aliases=("openhanako", "hana", "hanaagent"), ua=("hanaagent",),
        path=catalog_path, dir=home, fields=[Field("model", "Model")],
        wire=lambda at: [], live=Live(connect=_connect, disconnect=_disconnect, sync=_sync, check=_check),
    )
