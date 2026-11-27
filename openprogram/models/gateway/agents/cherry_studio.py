"""Cherry Studio, CherryHQ's Electron chat client: an import link.

After yetone/magpie ``internal/agent/cherrystudio.go`` (MIT License,
Copyright (c) yetone). Cherry Studio keeps its model providers in its own
SQLite database, so there is nothing for OpenProgram to write. It takes a new
one through its providers link, which opens Settings → Model Provider with a
dialog for the user to confirm::

    cherrystudio://providers/api-keys?v=1&data=<base64 of {id, baseUrl, apiKey, name, type}>

(``handleProvidersProtocolUrl`` in its main process, 1.x and 2.x alike; 2.x's
renderer creates the provider in ``useProviderDeepLinkImport``.) So the row
is that link, the gateway as one OpenAI-compatible provider, and whether
Cherry Studio has it, read from its database and never written.
"""
from __future__ import annotations

import base64
import json
import os
import threading
from pathlib import Path
from urllib.parse import quote_plus

from .base import Agent, App, Wiring, mac_app_removed, user_config_dir
from . import appdb
from .cindy import gateway_root

SCHEME = "cherrystudio://providers/api-keys?"
APP = App("com.kangfenmao.CherryStudio", ("Cherry Studio.app",))


def link(at: Wiring) -> str:
    """The link that opens Cherry Studio's add-provider dialog with the
    gateway as an OpenAI-compatible provider named openprogram.

    Cherry Studio reads data as standard base64: before URLSearchParams sees
    the query it turns a raw + into _ and / into -, and back again after, so
    URL-safe base64 would decode wrong. The data is query-escaped, which its
    URLSearchParams undoes. The base URL ends in /v1, as its own example's
    does; its formatApiHost keeps a URL that already has a version, and type
    openai is its OpenAI chat completions endpoint, whose models it lists
    from <base>/models."""
    payload = {"id": "openprogram", "baseUrl": at.base("openai-chat"), "apiKey": at.key,
               "name": "openprogram", "type": "openai"}
    data = base64.b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii")
    return SCHEME + "v=1&data=" + quote_plus(data)


def user_data() -> Path:
    """Cherry Studio's Electron userData by default: its package name, CherryStudio."""
    return user_config_dir() / "CherryStudio"


def dirs(home: Path | None = None) -> tuple[list[Path], bool]:
    """Every userData Cherry Studio may use here: the default and each one the
    user moved it to, kept in ~/.cherrystudio/boot-config.json under
    app.user_data_path (one per executable). ``ok`` is False when that file
    is there but can't be read."""
    found = [user_data()]
    home = home if home is not None else Path.home()
    try:
        raw = (home / ".cherrystudio" / "boot-config.json").read_text(encoding="utf-8")
    except FileNotFoundError:
        return found, True
    except OSError:
        return found, False
    try:
        moved = json.loads(raw).get("app.user_data_path") or {}
    except (ValueError, AttributeError):
        return found, False
    if not isinstance(moved, dict):
        return found, False
    for folder in moved.values():
        if isinstance(folder, str) and os.path.isabs(folder):
            found.append(Path(os.path.normpath(folder)))
    return found, True


def database(folder: Path) -> Path:
    """Where Cherry Studio 2.x keeps its database in a userData folder."""
    return folder / "Data" / "cherrystudio.sqlite"


def ran(folder: Path) -> bool:
    """Cherry Studio has run with this userData: 2.x's Data folder, or
    Chromium's Local Storage, where 1.x kept its providers."""
    return (folder / "Data").is_dir() or (folder / "Local Storage").is_dir()


def has(path: Path, gateway: str) -> tuple[bool, bool]:
    """Whether Cherry Studio's database at ``path`` has a provider whose base
    URL is the gateway's. endpoint_configs is JSON.stringify'd
    ``{"<endpoint>": {"baseUrl": …}}``; the gateway's address is followed by
    its path or the closing quote, so another port that begins with this
    one's doesn't count."""
    return appdb.count(path, "SELECT count(*) FROM user_provider WHERE instr(endpoint_configs, ?) > 0 "
                             "OR instr(endpoint_configs, ?) > 0", f'"{gateway}/', f'"{gateway}"')


class Status:
    """Each userData's database read through its own :class:`appdb.DbStatus`."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._dbs: dict[str, appdb.DbStatus] = {}

    def read(self, home: Path | None, gateway: str) -> tuple[bool, bool]:
        """Whether any database has the gateway. A userData that has run 1.x
        only (no database) can't be read, so it is unknown, as is a database
        or boot-config that can't be read."""
        folders, known = dirs(home)
        for folder in folders:
            db = database(folder)
            if not db.exists():
                if ran(folder):
                    known = False
                continue
            with self._lock:
                status = self._dbs.setdefault(str(db), appdb.DbStatus(has))
            found, ok = status.read(db, gateway)
            if found:
                return True, True
            if not ok:
                known = False
        return False, known


_status = Status()


def _detect() -> bool:
    folders, _ = dirs()
    return any(ran(folder) for folder in folders) and not mac_app_removed(APP)


def agent() -> Agent:
    return Agent(
        id="cherry_studio", name="Cherry Studio", icon="cherrystudio-color", kind="app",
        aliases=("cherrystudio", "cherry-studio"), fields=[],
        path=user_data, dir=user_data, wire=lambda at: [],
        detect_only=True, detect=_detect, link=link, added=lambda at: _status.read(None, gateway_root(at)),
    )
