"""The user's own Chrome or Edge, with the built-in browser's actions.

As in Codex, a browser is chosen first and every action afterwards is the
same whichever browser it is: here the ``browser`` target's actions
(``tabs``, ``read``, ``click``, ``type`` …) reach the user's Chrome or Edge
through Playwright MCP in extension mode. The server opens a relay and the
Playwright Extension in that browser connects to it, so the user's real
tabs are driven with their sign-ins.

Each action is translated here into Playwright MCP's tools, from the same
arguments the built-in browser's tab driver reads. An element is named as
there: by ``ref``, or by ``role`` and ``name``, which reach Playwright MCP
as the locator ``getByRole(role, { name, exact: true })``; its ``target``
takes a ref or a locator.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from pathlib import Path
import re
import tempfile
import threading
import time
from typing import Any, Mapping

from openprogram.extensions.mcp.client import MCPClient
from openprogram.extensions.mcp.config import MCPServerConfig

from . import page_actions as pa

_log = logging.getLogger(__name__)

BROWSERS = {"chrome": "chrome", "edge": "msedge"}
NAMES = {"chrome": "Chrome", "edge": "Edge"}
EXTENSION_ID = "mmlmfjhmonkocbjadbfplnigmagldckm"
# Edge installs it from the Chrome Web Store as well.
STORE_URL = f"https://chromewebstore.google.com/detail/playwright-extension/{EXTENSION_ID}"
# Connecting waits for the user to pick a tab in the extension.
_TIMEOUT = 120.0
# Pinned to an exact version, so a cached copy is already the right one. Without --prefer-offline
# npx revalidates the manifest against the registry on every launch; on a slow or broken network
# that alone outlasts the start timeout and the browser never connects.
_PLAYWRIGHT_MCP = ("npx", "--prefer-offline", "-y", "@playwright/mcp@0.0.79")

# Actions that are done to one element and have no meaning without it.
_ELEMENT_ACTIONS = ("hover", "select")
_ROLE = re.compile(r"[a-z]+")

_lock = threading.Lock()
_clients: dict[tuple[str, str], Any] = {}

# Where each browser keeps its profiles (macOS, Windows, Linux).
_DATA_DIRS = {
    "darwin": {"chrome": "Library/Application Support/Google/Chrome",
               "edge": "Library/Application Support/Microsoft Edge"},
    "win32": {"chrome": "Google/Chrome/User Data", "edge": "Microsoft/Edge/User Data"},
    "linux": {"chrome": ".config/google-chrome", "edge": ".config/microsoft-edge"},
}


def _data_dir(browser: str) -> Path:
    import os
    import sys
    platform = "win32" if sys.platform.startswith("win") else "darwin" if sys.platform == "darwin" else "linux"
    relative = _DATA_DIRS[platform][browser]
    if platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / relative
    return Path.home() / relative


def installed(browser: str) -> bool:
    """Whether the Playwright Extension is installed in one of ``browser``'s profiles.

    A store install unpacks into ``<profile>/Extensions/<id>``, which is what
    Playwright MCP looks for too. Only files are read; the browser is not touched.
    """
    root = _data_dir(browser)
    try:
        profiles = [entry for entry in root.iterdir() if entry.is_dir()]
    except OSError:
        return False
    return any((profile / "Extensions" / EXTENSION_ID).is_dir() for profile in profiles)


def installed_browsers() -> tuple[str, ...]:
    """The user's browsers this module can reach: those with the extension installed."""
    return tuple(browser for browser in BROWSERS if installed(browser))


def browser_of(args: Mapping[str, Any]) -> str:
    """``chrome`` or ``edge`` when the call names the user's browser, else ``""`` (built-in)."""
    value = str(args.get("browser") or "").strip().lower()
    return {"msedge": "edge", "google chrome": "chrome", "microsoft edge": "edge"}.get(value, value) \
        if value not in {"", "app", "builtin", "built-in", "iab", "openprogram"} else ""


def token() -> str:
    try:
        from openprogram.state.config import read_config
        value = (read_config().get("computer_use") or {}).get("chrome_extension_token")
    except Exception:
        return ""
    return str(value or "").strip()


def _command(browser: str) -> list[str]:
    output = Path(tempfile.gettempdir()) / "openprogram-extension-browser"
    return [*_PLAYWRIGHT_MCP, "--extension", "--browser", BROWSERS[browser], "--caps", "vision",
            "--output-dir", str(output)]


def _client(session: str, browser: str):
    key = (session, browser)
    with _lock:
        client = _clients.get(key)
        if client is not None and client.is_alive():
            return client
        _clients.pop(key, None)
    env = {"PLAYWRIGHT_MCP_EXTENSION_TOKEN": token()} if token() else {}
    background_tabs = Path(__file__).parent / "playwright_background_tabs.cjs"
    env["NODE_OPTIONS"] = f'--require "{background_tabs}"'
    client = _Client(_command(browser), timeout=_TIMEOUT, env=env)
    with _lock:
        _clients[key] = client
    return client


def connected(session: str, browser: str) -> bool:
    """Whether this conversation already holds a connection to ``browser``."""
    with _lock:
        client = _clients.get((session, browser))
    return client is not None and client.is_alive()


def close_session(session: str) -> None:
    with _lock:
        stale = [key for key in _clients if key[0] == session]
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
    ref = str(args.get(f"{prefix}ref") or "").strip().lstrip("@").removeprefix("ref=")
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
    pass


class _ObservationRequired(Exception):
    """Interrupt all action chains when only a refreshed observation is safe."""

    def __init__(self, text: str, images: list[bytes]):
        self.text, self.images = text, images


class ExtensionBrowser:
    """One conversation's connection to the user's Chrome or Edge."""

    def __init__(self, session: str, browser: str) -> None:
        if browser not in BROWSERS:
            raise ValueError(f"browser is the built-in browser (omit it), chrome or edge; not {browser!r}")
        self.browser = browser
        try:
            self.client = _client(session, browser)
        except Exception as exc:
            raise Unavailable(
                f"could not reach your {NAMES[browser]} ({exc}). It needs Node.js (npx) and the Playwright "
                f"Extension installed in {NAMES[browser]}: {STORE_URL}") from exc

    def _call(self, name: str, arguments: dict[str, Any]) -> tuple[str, list[bytes], bool]:
        try:
            result = self.client.call(name, arguments)
        except Exception as exc:
            return (f"{exc}. If {NAMES[self.browser]} did not connect, check that the Playwright Extension is "
                    f"installed ({STORE_URL}) and a tab was shared.", [], True)
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

    def run(self, command: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """One command (``list_pages``, ``observe``, ``close``) or one action on the page."""
        try:
            return self._run(command, arguments)
        except _ObservationRequired as observation:
            return self._result(observation.text, observation.images, False, status="observation_required",
                                observe_required=True, action_outcome="unknown")

    def _run(self, command: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        args = dict(arguments)
        if command == "list_pages":
            return self._result(*self._call("browser_tabs", {"action": "list"}))
        if command == "observe":
            return self._observe(args)
        if command == "close":
            return self._result(*self._call("browser_tabs", {"action": "close"}))
        return self._act(str(args.pop("action", command) or ""), args)

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
        note = ("The page changed; this action was not dispatched." if stale else
                "The browser connection was interrupted; whether this action took effect is unknown.")
        return self._result(note + " Do not replay earlier steps. Inspect this fresh state before continuing.\n\n" + text,
                            images, False, status="observation_required", action_outcome=outcome)

    def _observe(self, args: dict[str, Any]) -> dict[str, Any]:
        if args.get("url"):
            # The extension's connection page is not an attached web page.
            # Open a working tab rather than navigating that privileged page.
            text, images, failed = self._call("browser_tabs", {"action": "new", "url": str(args["url"])})
            if failed:
                return self._result(text, images, failed)
        elif args.get("page") not in (None, ""):
            try:
                index = int(args["page"])
            except (TypeError, ValueError):
                return {"ok": False, "reason_code": "invalid_arguments",
                        "message": "page is a tab index from list_pages"}
            text, images, failed = self._call("browser_tabs", {"action": "select", "index": index})
            if failed:
                listed, _, unlisted = self._call("browser_tabs", {"action": "list"})
                return self._result(text if unlisted else f"{text}\nThe tabs open now:\n{listed}", images, failed)
        return self._result(*self._call("browser_snapshot", {}))

    def _act(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            ref, label = _target(args)
            to_ref, to_label = _target(args, "to_")
        except pa.ActionError as exc:
            return exc.result()
        if not ref and (action in _ELEMENT_ACTIONS or action == "drag" and to_ref
                        or action == "click" and args.get("x") is None):
            return {"ok": False, "reason_code": "target_required",
                    "message": f"{action} needs an element: name it with ref (or role and name)"}
        # From here the element is args["ref"], whichever way the call named it.
        args = {**args, **({"ref": ref} if ref else {}), **({"to_ref": to_ref} if to_ref else {})}
        expected_url = getattr(self.client, "last_page_url", "")
        result = _explained(self._send(action, args, ref), [(ref, label), (to_ref, to_label)])
        return self._recover_action(result, expected_url)

    def _send(self, action: str, args: dict[str, Any], ref: str) -> dict[str, Any]:
        try:
            return self._translated(action, args, ref)
        except pa.ActionError as exc:
            return exc.result()

    def _translated(self, action: str, args: dict[str, Any], ref: str) -> dict[str, Any]:
        """One action as Playwright MCP calls; ``ActionError`` for arguments it cannot take."""
        call, on = self._call, ({"target": ref} if ref else {})
        if action == "screenshot":
            return self._result(*call("browser_take_screenshot", {"type": "png"}))
        if action == "wait":
            return self._wait(args)
        if action == "upload":
            path = str(args.get("path") or "")
            if not path:
                raise pa.ActionError("invalid_arguments", "upload needs path")
            if args.get("field") is not None:
                raise pa.ActionError("invalid_arguments", f"upload(field=n) is not available in {NAMES[self.browser]}; "
                                     "pass the upload button's ref")
            # Without an element it answers the file chooser the last click opened.
            if ref:
                text, images, failed = call("browser_click", on)
                if failed:
                    return self._result(text, images, failed)
            return self._result(*call("browser_file_upload", {"paths": [str(Path(path).expanduser())]}))
        if action == pa.EVALUATE_ACTION:
            return self._result(*call("browser_evaluate", {**on, "function": pa.evaluate_source(args)}))
        if action == "console_messages":
            return self._result(*call("browser_console_messages", {"level": "error" if args.get("only_errors") else "info"}))
        if action == "network_requests":
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
            # Chrome stops drawing a tab nobody is looking at, and a real hover waits for its frames.
            if self._hidden():
                return self._result(*call("browser_evaluate", {**on, "function": pa.HOVER_SCRIPT}))
            return self._result(*call("browser_hover", on))
        if action == "type":
            return self._type(args, ref)
        if action == "paste":
            script = pa.bound_call(pa.PASTE_SCRIPT, pa.paste_payload(args), with_element=bool(ref), require="ok")
            return self._result(*call("browser_evaluate", {**on, "function": script}))
        if action == "select":
            values = args.get("value")
            values = [str(item) for item in values] if isinstance(values, list) else [str(values or "")]
            if not any(values):
                raise pa.ActionError("value_required", "select needs value, the option's value or label")
            return self._result(*call("browser_select_option", {**on, "values": values}))
        if action == "press":
            key = str(args.get("key") or "").strip()
            if not key:
                raise pa.ActionError("key_required", "press needs key, such as Enter or Control+A")
            if ref:
                text, images, failed = call("browser_evaluate", {**on, "function": "(element) => element.focus()"})
                if failed:
                    return self._result(text, images, failed)
            return self._result(*call("browser_press_key", {"key": key}))
        if action == "scroll":
            dx, dy = pa.scroll_delta(args)
            if ref:
                script = pa.bound_call(pa.SCROLL_SCRIPT, {"dx": dx, "dy": dy}, require="moved")
                return self._result(*call("browser_evaluate", {**on, "function": script}))
            return self._result(*call("browser_mouse_wheel", {"deltaX": dx, "deltaY": dy}))
        if action == "drag":
            to_ref = str(args.get("to_ref") or "")
            if ref and to_ref:
                return self._result(*call("browser_drag", {"startTarget": ref, "endTarget": to_ref}))
            if any(args.get(key) is None for key in ("x", "y", "to_x", "to_y")):
                raise pa.ActionError("invalid_arguments", "drag needs ref and to_ref, or x, y, to_x and to_y")
            return self._result(*call("browser_mouse_drag_xy", {"startX": args["x"], "startY": args["y"],
                                                                "endX": args["to_x"], "endY": args["to_y"]}))
        return {"ok": False, "reason_code": "unsupported_action",
                "message": f"{action} is not available in {NAMES[self.browser]}"}

    def _click(self, args: dict[str, Any], ref: str) -> dict[str, Any]:
        click = pa.click_options(args)
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

    def _type(self, args: dict[str, Any], ref: str) -> dict[str, Any]:
        text = args.get("text")
        if not isinstance(text, str):
            raise pa.ActionError("text_required", "type needs text")
        if ref and args.get("clear") is not False:
            result = self._call("browser_type", {"target": ref, "text": text})
        else:
            # Into the focused element, or at the caret of the named one: the text is inserted, not filled.
            script = pa.bound_call(pa.INSERT_TEXT_SCRIPT, {"text": text}, with_element=bool(ref), require="ok")
            result = self._call("browser_evaluate", {**({"target": ref} if ref else {}), "function": script})
        if args.get("submit") and not result[2]:
            result = self._call("browser_press_key", {"key": "Enter"})
        return self._result(*result)

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


def _evaluated(text: str) -> str:
    """The value part of a browser_evaluate reply, without its markdown sections."""
    match = re.search(r"### Result\s*\n(.*?)(?:\n### |\Z)", text, re.S)
    return (match.group(1) if match else text).strip()


__all__ = ["BROWSERS", "ExtensionBrowser", "NAMES", "STORE_URL", "Unavailable", "browser_of", "close_session", "connected",
           "token"]
