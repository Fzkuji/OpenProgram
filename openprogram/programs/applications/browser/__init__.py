"""The built-in browser, as an application.

The App's browser tabs, the user's own browsers (Chromium browsers through
the Playwright Extension, Safari, Firefox), and the page resources recorded
for each conversation. The Agent operates them with ``computer_use``: the
built-in browser as ``type="browser"``, the user's own as ``app="chrome"``,
``app="safari"``, ``app="firefox"`` and the rest of ``catalog``; the
application registers no tool of its own.

- ``pages``: page resources (``BrowserResourceStore``) and their projections.
- ``tabs``: the tab driver behind ``computer_use(type="browser")``.
- ``catalog``: the user's browsers, their names, engines, programs and profiles.
- ``extension``: the user's browsers behind ``computer_use(type="browser",
  app=...)``: every Chromium browser through the Playwright Extension, and
  ``connect`` to any of them.
- ``safari``: Safari through AppleScript; ``firefox``: Firefox through its
  WebDriver BiDi remote agent; ``script_page``: the page layer both drive.
- ``page_actions``: argument parsing and page scripts all of them share.
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
