"""AionUi, an Electron app that runs Claude Code, Codex, Gemini CLI and its own chat: an import link.

After yetone/magpie ``internal/agent/aionui.go`` (MIT License, Copyright (c)
yetone). AionUi keeps its model providers in its own database, their keys
encrypted, so there is nothing for OpenProgram to write. It takes a new one
through its deeplink, which opens its add-model form filled in for the user
to confirm::

    aionui://add-provider?api_key=…&base_url=…&name=…&platform=…

(``parseDeepLinkUrl`` in AionUi 2.2.2's main process; the renderer takes
action "add-provider" to Settings → Model with base_url, api_key, name and
platform.) So the row is that link, the gateway as one OpenAI-compatible
("custom") provider, and whether AionUi has it, read from its database and
never written.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from . import appdb
from .base import Agent, App, Wiring, mac_app_removed, user_config_dir
from .cindy import gateway_root

SCHEME = "aionui://add-provider?"
APP = App("com.aionui.app", ("AionUi.app",))


def link(at: Wiring) -> str:
    """The link that opens AionUi's add-model form with the gateway as an
    OpenAI-compatible provider named openprogram, keyed so its requests are
    counted as AionUi's. Its OpenAI-compatible base URLs end in /v1, as its
    own OpenAI preset does."""
    query = sorted({"api_key": at.key, "base_url": at.base("openai-chat"),
                    "name": "openprogram", "platform": "custom"}.items())
    return SCHEME + urlencode(query)


def folder() -> Path:
    """AionUi's Electron userData; its database is aionui/aionui-backend.db in it."""
    return user_config_dir() / "AionUi"


def has(path: Path, gateway: str) -> tuple[bool, bool]:
    """Whether AionUi's database at ``path`` has a provider whose base URL is
    the gateway's (magpie ``aionuiHas``)."""
    return appdb.count(path, "SELECT count(*) FROM providers WHERE instr(base_url, ?) > 0", gateway)


_status = appdb.DbStatus(has)


def agent() -> Agent:
    return Agent(
        id="aionui", name="AionUi", icon="aionui", kind="app", fields=[],
        path=folder, dir=folder, wire=lambda at: [],
        detect_only=True, detect=lambda: (folder() / "aionui").is_dir() and not mac_app_removed(APP),
        link=link, added=lambda at: _status.read(folder() / "aionui" / "aionui-backend.db", gateway_root(at)),
    )
