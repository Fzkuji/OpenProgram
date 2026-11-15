"""The built-in browser, as an application.

The App's browser tabs, the user's Chrome or Edge through the Playwright
Extension, and the page resources recorded for each conversation. The
Agent operates all of it with ``computer_use_agent(target="browser")``; the
application registers no tool of its own.

- ``pages``: page resources (``BrowserResourceStore``) and their projections.
- ``tabs``: the tab driver behind ``computer_use_agent(target="browser")``.
- ``extension``: the user's Chrome or Edge through the Playwright Extension.
- ``page_actions``: argument parsing and page scripts both of them share.
- ``file_inputs``: the approved local file an upload sends.
- ``chrome``: the Chrome/Chromium bootstrap and the App's CDP endpoint.
- ``privacy``: password-value redaction.
- ``prompt``: the system-prompt section about the App's open Pages.
- ``service``: the ``browser`` service the core reaches all of this through.

``setup`` provides the ``browser`` service and adds the open-Pages prompt
section; disposing the application's context removes both.
"""

#: What the Launchpad, Applications page and Abilities call this application
#: (``openprogram.programs._host.identity`` reads them from the source).
TITLE = "Browser"
DESCRIPTION = "The built-in web browser. The Agent uses it to read pages, fill forms and take screenshots."
ICON = "browser"


def setup(ctx) -> None:
    from . import prompt
    from .service import BrowserService

    ctx.provide("browser", BrowserService())
    ctx.context("pages", prompt.pages_section, layer="L2")
