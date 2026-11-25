"""The user's own browsers, with the browser family's actions.

``computer_use(type="browser", app=...)`` reaches the user's browser with
their tabs and sign-ins. Every Chromium browser of the catalog
(``catalog.py``: Chrome and Edge with their channels, Brave, Vivaldi, Opera,
Arc, Chromium) is reached here through Playwright MCP in extension mode: the
server opens a relay and the Playwright Extension in that browser connects
to it, so the user's real tabs are driven with their sign-ins. Safari
(``safari.py``) and Firefox (``firefox.py``) have no such extension; they are
driven through the page-script layer (``script_page.py``). :func:`connect`
gives the one connection of a conversation to any of them; each answers the
same ``tabs``, ``select`` and ``run``. The actions are the browser family's
(``get_state``, ``click``, ``type_text``, ...), the same as in OpenProgram's
own browser.

Each action is translated here into Playwright MCP's tools, from the same
arguments the built-in browser's tab driver reads. An element is named as
there: by its ref (``element``), or by ``role`` and ``name``, which reach
Playwright MCP as the locator ``getByRole(role, { name, exact: true })``;
its ``target`` takes a ref or a locator.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
from typing import Any, Mapping

from . import catalog
from . import page_actions as pa

_log = logging.getLogger(__name__)

# The Chromium browsers, by name, with the Playwright channel of each ("" for one found by its executable).
BROWSERS = {browser.name: browser.channel for browser in catalog.BROWSERS if browser.engine == "chromium"}
NAMES = {browser.name: browser.label for browser in catalog.BROWSERS}
EXTENSION_ID = "mmlmfjhmonkocbjadbfplnigmagldckm"
# Edge, Brave, Vivaldi, Opera and Arc install it from the Chrome Web Store as well.
STORE_URL = f"https://chromewebstore.google.com/detail/playwright-extension/{EXTENSION_ID}"
# Connecting waits for the user to pick a tab in the extension.
_TIMEOUT = 120.0
# Pinned to an exact version, so a cached copy is already the right one. Without --prefer-offline
# npx revalidates the manifest against the registry on every launch; on a slow or broken network
# that alone outlasts the start timeout and the browser never connects.
_PLAYWRIGHT_MCP = ("npx", "--prefer-offline", "-y", "@playwright/mcp@0.0.79")

# The longest a step may hold a button or rest the pointer.
MAX_HOLD_SECONDS = 10.0
_ROLE = re.compile(r"[a-z]+")

_lock = threading.Lock()
_clients: dict[tuple[str, str], Any] = {}

def label(browser: str) -> str:
    """What people call ``browser``: "Google Chrome", "Brave", "Safari"."""
    return catalog.get(browser).label


def engine(browser: str) -> str:
    """How ``browser`` is reached: ``chromium``, ``safari`` or ``firefox``."""
    return catalog.get(browser).engine


def bundles(browser: str) -> tuple[str, ...]:
    return catalog.get(browser).bundles


def programs(browser: str) -> tuple[str, ...]:
    return catalog.get(browser).programs


def _data_dir(browser: str) -> Path | None:
    return catalog.data_dir(browser)


def installed(browser: str) -> bool:
    """Whether ``browser`` can be reached from here: for a Chromium browser, the Playwright Extension is
    installed in one of its profiles (and its executable is found where no channel finds it); Safari on
    macOS and Firefox where their program is.

    A store install unpacks into ``<profile>/Extensions/<id>``, which is what
    Playwright MCP looks for too. Only files are read; the browser is not touched.
    """
    kind = engine(browser)
    if kind == "safari":
        from . import safari
        return safari.installed()
    if kind == "firefox":
        from . import firefox
        return firefox.installed()
    root = _data_dir(browser)
    if root is None:
        return False
    if not any((profile / "Extensions" / EXTENSION_ID).is_dir() for profile in catalog.profiles(root)):
        return False
    return bool(BROWSERS[browser]) or bool(catalog.executable(browser))


def installed_browsers() -> tuple[str, ...]:
    """The user's browsers this module can reach, in the catalog's order."""
    return tuple(browser for browser in catalog.NAMES if installed(browser))


def connect(session: str, browser: str):
    """The conversation's connection to ``browser``, whatever its engine: ``tabs``, ``select`` and ``run``.
    Reaching it sends nothing to a page."""
    kind = engine(browser)
    if kind == "safari":
        from . import safari
        return safari.SafariBrowser(session, browser)
    if kind == "firefox":
        from . import firefox
        return firefox.FirefoxBrowser(session, browser)
    return ExtensionBrowser(session, browser)


def token() -> str:
    try:
        from openprogram.state.config import read_config
        value = (read_config().get("computer_use") or {}).get("chrome_extension_token")
    except Exception:
        return ""
    return str(value or "").strip()


def _command(browser: str) -> list[str]:
    """Playwright MCP in extension mode for ``browser``: by its channel, or by its executable."""
    output = Path(tempfile.gettempdir()) / "openprogram-extension-browser"
    channel = BROWSERS[browser]
    where = (["--browser", channel] if channel else
             ["--browser", "chrome", "--executable-path", catalog.executable(browser)])
    return [*_PLAYWRIGHT_MCP, "--extension", *where, "--caps", "vision", "--output-dir", str(output)]


def _environment(browser: str) -> dict[str, str]:
    """What the server needs besides its command line.

    With an executable and no channel, Playwright MCP 0.0.79 looks for the
    profile that holds the extension only in the folder
    ``PWTEST_EXTENSION_USER_DATA_DIR`` names; it then opens the connect page
    with ``--user-data-dir`` and ``--profile-directory``, which a running
    browser hands to its own window of that profile. Without it the page
    opens in whichever profile the browser used last.
    """
    env = {"PLAYWRIGHT_MCP_EXTENSION_TOKEN": token()} if token() else {}
    root = _data_dir(browser)
    if not BROWSERS[browser] and root is not None:
        env["PWTEST_EXTENSION_USER_DATA_DIR"] = str(root)
    background_tabs = Path(__file__).parent / "playwright_background_tabs.cjs"
    env["NODE_OPTIONS"] = f'--require "{background_tabs}"'
    return env


def _client(session: str, browser: str):
    key = (session, browser)
    with _lock:
        client = _clients.get(key)
        if client is not None and client.is_alive():
            return client
        _clients.pop(key, None)
    client = _Client(_command(browser), timeout=_TIMEOUT, env=_environment(browser))
    with _lock:
        _clients[key] = client
    return client


def connected(session: str, browser: str) -> bool:
    """Whether this conversation already holds a connection to ``browser``."""
    if engine(browser) != "chromium":
        return _script_module(browser).connected(session, browser)
    with _lock:
        client = _clients.get((session, browser))
    return client is not None and client.is_alive()


def _script_module(browser: str):
    from . import firefox, safari
    return safari if engine(browser) == "safari" else firefox


def close_session(session: str, browser: str = "") -> None:
    """End a conversation's connection to ``browser``, or to every browser."""
    if not browser:
        from . import firefox, safari
        for module in (safari, firefox):
            module.close_session(session, "")
    elif engine(browser) != "chromium":
        _script_module(browser).close_session(session, browser)
        return
    with _lock:
        stale = [key for key in _clients if key[0] == session and browser in ("", key[1])]
        clients = [_clients.pop(key) for key in stale]
    for client in clients:
        try:
            client.close()
        except Exception:
            # Teardown: the client's socket or page may already be gone.
            _log.debug("browser extension client close failed session=%s", session, exc_info=True)


async def _cancel_pending_tasks(timeout: float = 6.0) -> None:
    current = asyncio.current_task()
    pending = [
        task for task in asyncio.all_tasks()
        if task is not current and not task.done()
    ]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.wait(pending, timeout=timeout)


class _Client:
    """The Playwright MCP server of one connection, its async supervisor kept alive on a private event loop."""

    def __init__(self, command: list[str], *, timeout: float = 30.0,
                 env: dict[str, str] | None = None) -> None:
        self._loop = asyncio.new_event_loop()
        self._client = None
        self._thread = threading.Thread(
            target=self._run, name="browser-extension-mcp", daemon=True,
        )
        self._thread.start()
        try:
            # Imported when a browser is first driven: the MCP SDK takes
            # 0.12 s to import, and every process that loads the Programs
            # (each command, each standalone run) paid it for this module.
            from openprogram.extensions.mcp.client import MCPClient
            from openprogram.extensions.mcp.config import MCPServerConfig

            self._client = MCPClient(MCPServerConfig(
                name="browser-extension",
                command=command,
                env=dict(env or {}),
                timeout_seconds=timeout,
            ))
            self._submit(self._client.start(), timeout + 5)
            if self._client.error:
                raise RuntimeError("Playwright MCP did not start")
        except BaseException:
            self.close()
            raise

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _submit(self, awaitable, timeout: float = 35.0):
        future = asyncio.run_coroutine_threadsafe(awaitable, self._loop)
        return future.result(timeout=timeout)

    def call(self, name: str, arguments: dict[str, Any]):
        return self._submit(self._client.call_tool(name, arguments))

    def is_alive(self) -> bool:
        """True while the private loop runs and the MCP session is usable.

        A supervisor that gave up (fatal or re-auth), a dropped session that is
        still reconnecting, or a stopped loop all count as dead: the caller
        replaces the client instead of sending a write into it.
        """
        client = self._client
        if client is None or not self._thread.is_alive() or not self._loop.is_running():
            return False
        if getattr(client, "error", None):
            return False
        ready = getattr(client, "is_ready", None)
        return True if ready is None else bool(ready)

    def close(self) -> None:
        client = getattr(self, "_client", None)
        if client is not None and self._loop.is_running():
            try:
                self._submit(client.stop(), 5)
            except Exception:
                # Teardown: the MCP client may already be dead; the loop is stopped below regardless.
                _log.debug("MCP client stop failed", exc_info=True)
        if self._loop.is_running():
            # A start that timed out leaves the supervisor (and its npx child)
            # pending. Cancel and await it on this loop so the stdio transport
            # terminates the child before the loop closes; otherwise the child
            # outlives the loop that owns its pipes.
            try:
                self._submit(_cancel_pending_tasks(), 8)
            except Exception:
                # Teardown: best-effort cancel; the loop is stopped below regardless.
                _log.debug("MCP pending task cancel failed", exc_info=True)
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=5)
        if not self._thread.is_alive():
            self._loop.close()


def _target(args: Mapping[str, Any], prefix: str = "") -> tuple[str, str]:
    """The element a call names, as Playwright MCP takes it, and how to call it; empty when it names none."""
    ref = str(args.get(f"{prefix}element") or "").strip().lstrip("@").removeprefix("ref=")
    if ref:
        return ref, ref
    role = str(args.get(f"{prefix}role") or "").strip()
    if not role:
        return "", ""
    if not _ROLE.fullmatch(role):
        raise pa.ActionError("invalid_arguments", f"role is an ARIA role such as button or textbox, not {role!r}")
    name = str(args.get(f"{prefix}name") or "")
    if not name:
        return f"getByRole({json.dumps(role)})", role
    return (f"getByRole({json.dumps(role)}, {{ name: {json.dumps(name, ensure_ascii=False)}, exact: true }})",
            f'{role} "{name}"')


def _explained(result: dict[str, Any], named: list[tuple[str, str]]) -> dict[str, Any]:
    """A role locator that matched no element, or several, fails in Playwright's words; say it as the built-in browser does."""
    located = [(locator, label) for locator, label in named if locator.startswith("getByRole(")]
    said = str(result.get("result") or "")
    if result.get("ok") is not False or not located:
        return result
    for locator, label in located:
        if f'"{locator}" does not match any elements' in said:
            return {**result, "reason_code": "element_gone",
                    "result": f"{label} is not on the page; read the page for the element's ref"}
    several = re.search(r"strict mode violation: (.*?) resolved to (\d+) elements", said, re.S)
    if several:
        # Playwright prints the locator with its own quoting: the name inside tells a drag's two elements apart.
        label = next((label for _, label in located if label.partition(" ")[2].strip('"') in several.group(1)),
                     located[0][1])
        return {**result, "reason_code": "ambiguous_element", "result": f"{several.group(2)} elements are {label}; use a ref"}
    return result


def _text(result: Any) -> str:
    return "\n".join(str(getattr(item, "text", "")) for item in getattr(result, "content", None) or []
                     if getattr(item, "type", "") == "text").strip()


def _images(result: Any) -> list[bytes]:
    return [base64.b64decode(item.data) for item in getattr(result, "content", None) or []
            if getattr(item, "type", "") == "image" and getattr(item, "data", None)]


def _failed(result: Any) -> bool:
    return bool(getattr(result, "isError", False))


class Unavailable(RuntimeError):
    """The browser could not be reached; nothing was sent. ``missing`` names what this computer lacks;
    ``permission`` says where the user turns on what the browser refused (a setting, an operating-system
    permission)."""

    def __init__(self, message: str, missing: str = "", permission: str = "") -> None:
        super().__init__(message)
        self.missing = missing
        self.permission = permission


class Lost(RuntimeError):
    """The connection broke during a call: what was sent may or may not have taken effect."""


class _ObservationRequired(Exception):
    """Interrupt all action chains when only a refreshed observation is safe."""

    def __init__(self, text: str, images: list[bytes]):
        self.text, self.images = text, images


# Lists the tabs the extension attached, in Playwright MCP's order, with the one its tools act on. A
# tab carries a key stamped on its page object, which lasts as long as the connection does.
_LIST_TABS = r"""async (page) => {
  const rows = [];
  for (const p of page.context().pages()) {
    if (!p.__openprogramTab) p.__openprogramTab = Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
    let title = "";
    try { title = await p.title(); } catch (e) {}
    rows.push({key: p.__openprogramTab, url: p.url(), title, current: p === page});
  }
  return rows;
}"""


class ExtensionBrowser:
    """One conversation's connection to one of the user's Chromium browsers.

    Actions take the browser family's names and arguments, already checked;
    each acts on the tab Playwright MCP has selected, which :meth:`select`
    sets. Keys arrive in xdotool's spelling.
    """

    def __init__(self, session: str, browser: str) -> None:
        if browser not in BROWSERS:
            raise ValueError(f"browser is one of {', '.join(BROWSERS)}, not {browser!r}")
        self.browser = browser
        if not BROWSERS[browser] and not catalog.executable(browser):
            raise Unavailable(f"{NAMES[browser]} is not installed where OpenProgram looks for it",
                              missing=f"Install {NAMES[browser]} in its usual place, then call again.")
        if shutil.which(_PLAYWRIGHT_MCP[0]) is None:
            raise Unavailable(f"this computer has no Node.js, which reaching your {NAMES[browser]} needs",
                              missing="Install Node.js (https://nodejs.org), which brings npx, then call again.")
        try:
            self.client = _client(session, browser)
        except Exception as exc:
            raise Unavailable(
                f"your {NAMES[browser]} did not connect ({exc}). The Playwright Extension in it connects when a "
                f"tab is shared with it; it is at {STORE_URL}") from exc

    def _call(self, name: str, arguments: dict[str, Any]) -> tuple[str, list[bytes], bool]:
        try:
            result = self.client.call(name, arguments)
        except Exception as exc:
            raise Lost(f"the connection to your {NAMES[self.browser]} broke ({exc}). If it did not connect again, "
                       "check that a tab is shared with the Playwright Extension") from exc
        text, images, failed = _text(result), _images(result), _failed(result)
        if not failed and "No open tabs. Navigate to a URL to create one." in text and (name != "browser_tabs" or arguments.get("action") in ("new", "select")):
            # Detach/reattach can leave response rendering with no tab. Retry
            # observation only; the original action may already have happened.
            expected = str(arguments.get("url") or getattr(self.client, "last_page_url", ""))
            for _ in range(3):
                time.sleep(0.15)
                try:
                    observed = self.client.call("browser_snapshot", {})
                except Exception:
                    break
                fresh = _text(observed)
                if not _failed(observed) and expected and self._page_url(fresh) == expected:
                    raise _ObservationRequired(
                        text + "\nPage connection refreshed. Check the fresh state before continuing; "
                        "the original action was not replayed.\n" + fresh, _images(observed))
            raise _ObservationRequired(
                text + "\nThe page is no longer attached. Reconnect and inspect it before continuing; do not replay the action.", images)
        return text, images, failed

    def _evaluate(self, function: str, ref: str = "") -> tuple[str, bool]:
        text, _, failed = self._call("browser_evaluate", {**({"target": ref} if ref else {}), "function": function})
        return text, failed

    def _hidden(self) -> bool:
        text, failed = self._evaluate("() => document.visibilityState")
        return not failed and "hidden" in _evaluated(text)

    # -- tabs -------------------------------------------------------------------

    def tabs(self) -> list[dict[str, Any]]:
        """The attached tabs in the order Playwright MCP numbers them: key, url, title and whether it is selected."""
        try:
            text, _, failed = self._call("browser_run_code_unsafe", {"code": _LIST_TABS})
        except _ObservationRequired as exc:
            # Listing changes no page; a connection left with no attached tab is a broken one to its callers.
            raise Lost(f"your {NAMES[self.browser]} lost its attached tab while listing tabs; check that a tab "
                       "is shared with the Playwright Extension") from exc
        if failed:
            if "No open tabs" in text:
                return []
            raise Lost(f"your {NAMES[self.browser]} did not list its tabs: {text}")
        try:
            rows = json.loads(_evaluated(text))
        except ValueError:
            raise Lost(f"your {NAMES[self.browser]} listed its tabs in a form this version does not read") from None
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def select(self, index: int, url: str) -> bool:
        """Make the tab at ``index`` the one actions go to; False when the tab there is not at ``url`` any more."""
        try:
            text, _, failed = self._call("browser_tabs", {"action": "select", "index": index})
        except _ObservationRequired as exc:
            raise Lost(f"your {NAMES[self.browser]} lost its attached tab while selecting tab {index}; check "
                       "that a tab is shared with the Playwright Extension") from exc
        if failed:
            return False
        current = re.search(r"^- \d+: \(current\) \[.*\]\((.*)\)", text, re.M)
        return current is None or current.group(1) == url

    # -- actions ----------------------------------------------------------------

    def run(self, action: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """One action on the selected tab; ``index`` names the tab for ``close_tab``."""
        try:
            return self._run(action, dict(arguments))
        except _ObservationRequired as observation:
            return self._result(observation.text, observation.images, False, status="observation_required",
                                observe_required=True, action_outcome="unknown")

    def _run(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        if action == "create_tab":
            url = str(args.get("url") or "").strip()
            if not url:
                return pa.ActionError("url_required", "create_tab needs url").result()
            # The extension's connection page is not an attached web page: open a working tab beside it.
            return self._result(*self._call("browser_tabs", {"action": "new", "url": url}))
        if action == "close_tab":
            return self._result(*self._call("browser_tabs", {"action": "close", "index": int(args["index"])}))
        if action == "get_state":
            return self._state(str(args.get("include") or "tree"))
        if action == "find":
            text, role = str(args.get("text") or ""), str(args.get("role") or "")
            if not text.strip() and not role.strip():
                return pa.ActionError("text_required", "find needs text, role or both").result()
            said, _, failed = self._call("browser_snapshot", {})
            if failed:
                return self._result(said, [], True)
            return {**self._result(said, [], False), "found": pa.find_lines(said, text, role)}
        return self._act(action, args)

    def _state(self, include: str) -> dict[str, Any]:
        text, images, failed = ("", [], False)
        if include in ("tree", "all"):
            text, images, failed = self._call("browser_snapshot", {})
        if include in ("screenshot", "all") and not failed:
            said, shot, failed = self._call("browser_take_screenshot", {"type": "png"})
            text, images = (text or said), [*images, *shot]
        return self._result(text, images, failed)

    def _result(self, text: str, images: list[bytes], failed: bool, **extra: Any) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": not failed, "browser": self.browser, "result": text, **extra}
        if failed:
            out["observe_required"] = True
        if not failed:
            url = self._page_url(text)
            if url:
                self.client.last_page_url = url
        if images:
            out["_images"] = images
        return out

    @staticmethod
    def _page_url(text: str) -> str:
        match = re.search(r"^[- ]*Page URL: (\S+)", text, re.M)
        return match.group(1) if match else ""

    @staticmethod
    def page_title(text: str) -> str:
        match = re.search(r"^[- ]*Page Title: (.*)$", text, re.M)
        return match.group(1).strip() if match else ""

    page_url = _page_url

    def _recover_action(self, result: dict[str, Any], expected_url: str) -> dict[str, Any]:
        """Observe after a stale ref or a lost connection, never replay input."""
        if result.get("ok") is not False:
            return result
        error = str(result.get("result") or "")
        stale = bool(re.search(r"Ref \S+ not found in the current page snapshot", error))
        closed = "Target page, context or browser has been closed" in error
        if not (stale or closed) or not expected_url:
            return result
        text, images, failed = self._call("browser_snapshot", {})
        # A reconnect may expose a different tab or only the connection page.
        # Do not call that recovery of the page this action was targeting.
        if failed or self._page_url(text) != expected_url:
            return result
        outcome = "not_dispatched" if stale else "unknown"
        note = ("The element is no longer on the page; this action was not dispatched." if stale else
                "The browser connection was interrupted; whether this action took effect is unknown.")
        return self._result(note + " Do not replay earlier steps. Inspect this fresh state before continuing.\n\n" + text,
                            images, False, status="observation_required", action_outcome=outcome)

    def _act(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            ref, label = _target(args)
            to_ref, to_label = _target(args, "to_")
        except pa.ActionError as exc:
            return exc.result()
        pointed = args.get("x") is not None and args.get("y") is not None
        if not ref and (action == "set_value" or action == "drag" and to_ref
                        or action in ("click", "hover") and not pointed):
            return {"ok": False, "reason_code": "target_required",
                    "message": f"{action} needs an element: name it by its ref (or role and name)"
                               + ("" if action in ("set_value", "drag") else ", or give x and y")}
        expected_url = getattr(self.client, "last_page_url", "")
        result = _explained(self._send(action, args, ref, to_ref), [(ref, label), (to_ref, to_label)])
        return self._recover_action(result, expected_url)

    def _send(self, action: str, args: dict[str, Any], ref: str, to_ref: str) -> dict[str, Any]:
        try:
            return self._translated(action, args, ref, to_ref)
        except pa.ActionError as exc:
            return exc.result()

    def _translated(self, action: str, args: dict[str, Any], ref: str, to_ref: str) -> dict[str, Any]:
        """One action as Playwright MCP calls; ``ActionError`` for arguments it cannot take."""
        call, on = self._call, ({"target": ref} if ref else {})
        if action == "wait":
            return self._wait(args)
        if action == "upload_file":
            path = str(args.get("path") or "")
            if not path:
                raise pa.ActionError("invalid_arguments", "upload_file needs path")
            if args.get("field") is not None:
                raise pa.ActionError("unsupported_option", f"upload_file(field=n) is not available in "
                                     f"{NAMES[self.browser]}; name the upload button as element")
            # Without an element it answers the file chooser the last click opened.
            if ref:
                text, images, failed = call("browser_click", on)
                if failed:
                    return self._result(text, images, failed)
            return self._result(*call("browser_file_upload", {"paths": [str(Path(path).expanduser())]}))
        if action == pa.EVALUATE_ACTION:
            return self._result(*call("browser_evaluate", {**on, "function": pa.evaluate_source(args)}))
        # Playwright MCP filters the console by level only and the requests by pattern only.
        if action == "read_console_messages":
            if args.get("pattern") or args.get("limit") is not None:
                raise pa.ActionError("unsupported_option", f"read_console_messages takes only only_errors in "
                                     f"{NAMES[self.browser]}")
            return self._result(*call("browser_console_messages", {"level": "error" if args.get("only_errors") else "info"}))
        if action == "read_network_requests":
            if args.get("only_errors") or args.get("limit") is not None:
                raise pa.ActionError("unsupported_option", f"read_network_requests takes only pattern in "
                                     f"{NAMES[self.browser]}")
            return self._result(*call("browser_network_requests",
                                      {"filter": str(args["pattern"])} if args.get("pattern") else {}))
        if action == "navigate":
            if not args.get("url"):
                raise pa.ActionError("url_required", "navigate needs url")
            return self._result(*call("browser_navigate", {"url": str(args["url"])}))
        if action == "back":
            return self._result(*call("browser_navigate_back", {}))
        if action in ("forward", "reload"):
            return self._result(*call("browser_evaluate", {
                "function": "() => history.forward()" if action == "forward" else "() => location.reload()"}))
        if action == "click":
            return self._click(args, ref)
        if action == "hover":
            return self._hover(args, ref)
        if action == "type_text":
            return self._type(args, ref)
        if action == "paste":
            script = pa.bound_call(pa.PASTE_SCRIPT, pa.paste_payload(args), with_element=bool(ref), require="ok")
            return self._result(*call("browser_evaluate", {**on, "function": script}))
        if action == "set_value":
            return self._set_value(args, ref)
        if action == "press_key":
            if _seconds(args.get("seconds")):
                raise pa.ActionError("unsupported_option", f"holding a key is not available in {NAMES[self.browser]}; "
                                     "press_key without seconds")
            written = str(args.get("key") or "").strip()
            if not written:
                raise pa.ActionError("key_required", "press_key needs key, such as Return or ctrl+a")
            if ref:
                text, images, failed = call("browser_evaluate", {**on, "function": "(element) => element.focus()"})
                if failed:
                    return self._result(text, images, failed)
            return self._result(*call("browser_press_key", {"key": _playwright_key(written)}))
        if action == "scroll":
            return self._scroll(args, ref)
        if action == "drag":
            if _seconds(args.get("seconds")):
                raise pa.ActionError("unsupported_option", f"a drag that holds is not available in {NAMES[self.browser]}")
            if ref and to_ref:
                return self._result(*call("browser_drag", {"startTarget": ref, "endTarget": to_ref}))
            if any(args.get(key) is None for key in ("x", "y", "to_x", "to_y")):
                raise pa.ActionError("unsupported_option", f"in {NAMES[self.browser]} a drag goes from element to "
                                     "to_element, or from x,y to to_x,to_y")
            return self._result(*call("browser_mouse_drag_xy", {"startX": args["x"], "startY": args["y"],
                                                                "endX": args["to_x"], "endY": args["to_y"]}))
        return {"ok": False, "reason_code": "unsupported_action",
                "message": f"{action} is not available in {NAMES[self.browser]}"}

    def _click(self, args: dict[str, Any], ref: str) -> dict[str, Any]:
        click = pa.click_options(args)
        hold = _seconds(args.get("seconds"))
        if hold:
            if ref or click["modifiers"] or click["count"] != 1:
                raise pa.ActionError("unsupported_option", f"a long press in {NAMES[self.browser]} is a single click "
                                     "by x,y with seconds")
            text, images, failed = self._call("browser_mouse_move_xy", {"x": args["x"], "y": args["y"]})
            if failed:
                return self._result(text, images, failed)
            self._call("browser_mouse_down", {"button": click["button"]})
            time.sleep(hold)
            return self._result(*self._call("browser_mouse_up", {"button": click["button"]}))
        if not ref:
            if click["modifiers"]:
                raise pa.ActionError("unsupported_option", f"a click by x,y with modifiers is not available in "
                                     f"{NAMES[self.browser]}; name the element")
            return self._result(*self._call("browser_mouse_click_xy", {
                "x": args.get("x"), "y": args.get("y"), "button": click["button"], "clickCount": click["count"]}))
        # Playwright MCP clicks once or twice. A triple click, and any click on a tab behind others (Chrome stops
        # drawing it, and a real click waits for frames that never come), is sent as events in the page.
        if click["count"] == 3 or self._hidden():
            script = pa.bound_call(pa.POINTER_SCRIPT, {
                "button": click["button"], "count": click["count"], "flags": click["flags"]})
            return self._result(*self._call("browser_evaluate", {"target": ref, "function": script}))
        # Only what differs from a plain click, so a plain one stays the minimal call.
        return self._result(*self._call("browser_click", {
            "target": ref,
            **({"doubleClick": True} if click["count"] == 2 else {}),
            **({"button": click["button"]} if click["button"] != "left" else {}),
            **({"modifiers": click["modifiers"]} if click["modifiers"] else {})}))

    def _hover(self, args: dict[str, Any], ref: str) -> dict[str, Any]:
        rest = _seconds(args.get("seconds"), 0.8)
        if not ref:
            result = self._result(*self._call("browser_mouse_move_xy", {"x": args["x"], "y": args["y"]}))
        elif self._hidden():
            # Chrome stops drawing a tab nobody is looking at, and a real hover waits for its frames.
            result = self._result(*self._call("browser_evaluate", {"target": ref, "function": pa.HOVER_SCRIPT}))
        else:
            result = self._result(*self._call("browser_hover", {"target": ref}))
        if result.get("ok") and rest:
            time.sleep(rest)
        return result

    def _scroll(self, args: dict[str, Any], ref: str) -> dict[str, Any]:
        size = (0.0, 0.0)
        if args.get("pixels") in (None, ""):
            measured, failed = self._evaluate("(e) => [e.clientWidth, e.clientHeight]" if ref else
                                              "() => [innerWidth, innerHeight]", ref)
            try:
                width, height = json.loads(_evaluated(measured)) if not failed else (0, 0)
                size = (float(width or 0), float(height or 0))
            except (TypeError, ValueError):
                size = (0.0, 0.0)
        dx, dy = pa.scroll_delta(args, size)
        if ref:
            script = pa.bound_call(pa.SCROLL_SCRIPT, {"dx": dx, "dy": dy}, require="moved")
            return self._result(*self._call("browser_evaluate", {"target": ref, "function": script}))
        if args.get("x") is not None and args.get("y") is not None:
            text, images, failed = self._call("browser_mouse_move_xy", {"x": args["x"], "y": args["y"]})
            if failed:
                return self._result(text, images, failed)
        return self._result(*self._call("browser_mouse_wheel", {"deltaX": dx, "deltaY": dy}))

    def _type(self, args: dict[str, Any], ref: str) -> dict[str, Any]:
        text = args.get("text")
        if not isinstance(text, str):
            raise pa.ActionError("text_required", "type_text needs text")
        if ref and args.get("clear"):
            result = self._call("browser_type", {"target": ref, "text": text})
        else:
            # Into the focused element, or at the caret of the named one: the text is inserted, not filled.
            if args.get("clear"):
                self._call("browser_press_key", {"key": "ControlOrMeta+a"})
            script = pa.bound_call(pa.INSERT_TEXT_SCRIPT, {"text": text}, with_element=bool(ref), require="ok")
            result = self._call("browser_evaluate", {**({"target": ref} if ref else {}), "function": script})
        if args.get("submit") and not result[2]:
            result = self._call("browser_press_key", {"key": "Enter"})
        return self._result(*result)

    def _set_value(self, args: dict[str, Any], ref: str) -> dict[str, Any]:
        said, failed = self._evaluate(pa.FIELD_KIND_SCRIPT, ref)
        if failed:
            return self._result(said, [], True)
        kind = _evaluated(said).strip('"')
        if kind == "select":
            return self._result(*self._call("browser_select_option", {"target": ref, "values": pa.option_values(args)}))
        if kind == "check":
            on = "true" if pa.checked(args) else "false"
            return self._result(*self._call("browser_fill_form", {"fields": [
                {"name": ref, "type": "checkbox", "target": ref, "value": on}]}))
        value = args.get("value")
        return self._result(*self._call("browser_type", {"target": ref, "text": "" if value is None else str(value)}))

    def _wait(self, args: dict[str, Any]) -> dict[str, Any]:
        """Until a text is on the page, or for some seconds; the same limits as the built-in browser."""
        text = str(args.get("text") or "")
        try:
            seconds = max(0.0, min(float(args.get("seconds") or (10 if text else 1)), 30.0))
        except (TypeError, ValueError) as exc:
            raise pa.ActionError("invalid_seconds", "seconds must be a number") from exc
        if not text:
            return self._result(*self._call("browser_wait_for", {"time": seconds}))
        shows = f"() => !!document.body && document.body.innerText.includes({json.dumps(text, ensure_ascii=False)})"
        deadline = time.monotonic() + seconds
        while True:
            found, failed = self._evaluate(shows)
            if failed:
                return self._result(found, [], True)
            if _evaluated(found) == "true":
                return self._result(f"{text!r} is on the page", [], False)
            if time.monotonic() >= deadline:
                return {**self._result(f"{text!r} did not appear within {seconds:g}s", [], True),
                        "reason_code": "text_not_found"}
            time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))


def _playwright_key(chord: str) -> str:
    from openprogram.programs.tools.computer_use.computer_use.actions.keynames import playwright
    return playwright(chord)


def _seconds(value: Any, default: float = 0.0) -> float:
    try:
        seconds = float(default if value in (None, "") else value)
    except (TypeError, ValueError):
        raise pa.ActionError("invalid_seconds", "seconds must be a number") from None
    if not 0 <= seconds <= MAX_HOLD_SECONDS:
        raise pa.ActionError("invalid_seconds", f"seconds must be between 0 and {MAX_HOLD_SECONDS:g}")
    return seconds


def _evaluated(text: str) -> str:
    """The value part of a browser_evaluate reply, without its markdown sections."""
    match = re.search(r"### Result\s*\n(.*?)(?:\n### |\Z)", text, re.S)
    return (match.group(1) if match else text).strip()


__all__ = ["BROWSERS", "ExtensionBrowser", "Lost", "NAMES", "STORE_URL", "Unavailable", "bundles", "close_session",
           "connect", "connected", "engine", "installed", "installed_browsers", "label", "programs", "token"]
