"""The ``browser`` service: everything of the built-in browser the core reaches.

The application's ``setup`` provides one :class:`BrowserService` as the
service ``browser``. The core never imports the application; it looks the
service up (``openprogram.programs._host.services.lookup("browser")``) and runs
without a browser when the lookup returns ``None``.

Members:

- ``pages``: the page-resource module (``BrowserResourceStore``, the
  per-conversation projections, resource control, human page input).
- ``tabs``: the tab driver behind ``computer_use_agent(target="browser")``
  (``ACTIONS``, ``driver()``, ``batch()``).
- ``open_tab(url)``: open a web tab for the running conversation.
- ``authorized_page(arguments, window_id)``: the App tab an interface
  command names, checked against the App, for a native presentation command.
- ``browser_of(args)``: ``chrome`` or ``edge`` when a call names the
  user's own browser, else ``""``; ``user_browser_names`` names them.
- ``extension``: the user's Chrome or Edge through the Playwright
  Extension (``ExtensionBrowser``, ``Unavailable``).
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Mapping

from . import extension, pages, tabs


def _session() -> str:
    from openprogram.runtime.execution.run_control import get_current_session_id
    return get_current_session_id() or ""


def open_tab(url: str) -> dict[str, Any]:
    """Open ``url`` in a background tab of the App, as ``open`` of the browser target does."""
    from openprogram.host.resources.providers.builtin import _require_tool

    _require_tool("computer_use_agent")
    return tabs.driver().run("open", {"url": url}, _session())


@contextmanager
def authorized_page(arguments: Mapping[str, Any], window_id: str):
    """The App tab an interface command acts on, as the App knows it now.

    ``arguments.tab`` names it by its id from the browser target's ``tabs``
    (omitted: the tab this conversation used last); the command's first
    argument is that tab and is sent as the App's own tab id.
    """
    from openprogram.host.processes import current_owner
    from openprogram.host.resources import desktop_link
    from openprogram.host.resources.providers.builtin import _require_tool

    # Acting on a web tab is the screen tool's; a turn that may not use it may not do it this way either.
    _require_tool("computer_use_agent")
    try:
        tab = tabs.driver().tab(arguments.get("tab"), _session())
    except tabs.BrowserError as exc:
        raise ValueError(str(exc)) from exc
    descriptor = desktop_link.binding_page_descriptor(tab.binding_id) if tab.binding_id else {}
    args = arguments.get("arguments") or []
    if not args or args[0] not in (tab.id, tab.tab_id):
        raise ValueError("the command's first argument is the tab: its id from computer_use_agent's tabs")
    if (descriptor.get("tab_id") != tab.tab_id or (window_id and descriptor.get("window_id") != window_id)
            or not desktop_link.request_bound_tab(tab.binding_id).get("ok")):
        raise ValueError("page_context_stale")
    session_id, _, _ = current_owner()
    yield {**descriptor, "session_id": session_id}


class BrowserService:
    """The built-in browser, as the core reaches it."""

    pages = pages
    tabs = tabs
    extension = extension
    user_browser_names = extension.NAMES

    open_tab = staticmethod(open_tab)
    authorized_page = staticmethod(authorized_page)

    @staticmethod
    def browser_of(args: Mapping[str, Any]) -> str:
        return extension.browser_of(args)


def get() -> BrowserService | None:
    """The provided ``browser`` service, or ``None`` while the browser is not enabled."""
    from openprogram.programs._host import services as _services
    return _services.lookup("browser")


#: What a caller reports when the browser application is not loaded.
NOT_ENABLED = "the browser is not enabled"
