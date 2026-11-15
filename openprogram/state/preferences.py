"""Named user preferences kept in ``config.json``.

Typed readers and writers for the preferences the setup wizard, the CLI,
the server's settings routes and the worker share. The file itself is read
and written through ``openprogram.state.config``.
"""

from __future__ import annotations

from typing import Any

from openprogram.state import config as user_config


# Default port. Uncommon 5-digit value in the registered-port range
# (< 49152, so it never collides with the OS ephemeral range); the
# 18xxx block is rarely used by mainstream services. Single-port
# architecture: the FastAPI worker serves the API, /ws AND the frontend
# static export on one port, stored under the ``web_port`` pref.
DEFAULT_WEB_PORT = 18100


def read_search_default_provider() -> str | None:
    """User-pinned default web_search backend, or None to use priority order.

    Stored as ``cfg["search"]["default_provider"]``. Resolved at every
    web_search call so a change in settings takes effect immediately
    without a worker restart.
    """
    cfg = user_config.read_config()
    name = ((cfg.get("search") or {}).get("default_provider") or "").strip()
    return name or None


def write_search_default_provider(name: str | None) -> None:
    """Persist the user's default web_search backend (or clear it)."""

    def _mut(cfg: dict) -> None:
        section = dict(cfg.get("search") or {})
        if name:
            section["default_provider"] = name
        else:
            section.pop("default_provider", None)
        if section:
            cfg["search"] = section
        else:
            cfg.pop("search", None)

    user_config.update_config(_mut)


def read_ui_prefs() -> dict[str, Any]:
    cfg = user_config.read_config()
    ui = cfg.get("ui", {}) or {}
    return {
        "web_port": int(ui.get("web_port") or DEFAULT_WEB_PORT),
        "open_browser": bool(ui.get("open_browser", True)),
    }


def set_ui_ports(
    *,
    web_port: int | None = None,
    open_browser: bool | None = None,
) -> dict[str, Any]:
    """Persist UI prefs to the config. Only the keys passed are
    changed; the rest keep their stored values. Returns the resulting
    ``read_ui_prefs()`` dict. Takes effect on the next ``openprogram web``
    / ``worker`` start; nothing live is rebound here.
    """

    def _mut(cfg: dict) -> None:
        ui = cfg.setdefault("ui", {})
        if web_port is not None:
            ui["web_port"] = int(web_port)
        if open_browser is not None:
            ui["open_browser"] = bool(open_browser)

    user_config.update_config(_mut)
    return read_ui_prefs()
