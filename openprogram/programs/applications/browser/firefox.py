"""The user's Firefox, through its WebDriver BiDi remote agent.

Firefox has no Playwright Extension and no extension mode, and Playwright's
Firefox is a patched build, not the user's. Firefox itself carries a remote
agent that speaks WebDriver BiDi when it is started with
``--remote-debugging-port``: tabs (``browsingContext.getTree``, ``create``,
``navigate``, ``close``, ``captureScreenshot``), scripts in a page
(``script.evaluate``), real pointer and keys (``input.performActions``),
files for a file field (``input.setFiles``) and the console and requests as
events. So OpenProgram starts the user's own Firefox, with their default
profile and sign-ins, with the agent on, and talks BiDi over a websocket.

A running Firefox does not take the flag: when Firefox is open without the
agent, a call answers how to fix it (quit Firefox so OpenProgram starts it,
or start it with ``--remote-debugging-port``) and never quits it. One BiDi
session serves every conversation of this process; each conversation keeps
its own current tab. A tab's key is its browsing-context id, which lasts as
long as the tab.

The page itself is read and operated by the page layer (``script_page.py``);
clicks, hovers, drags and keys are real input while the tab is the one
shown in its window, and page events while it is behind others.
"""
from __future__ import annotations

import base64
from collections import deque
import itertools
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from typing import Any

from . import catalog
from . import page_actions as pa
from .extension import Lost, Unavailable
from .script_page import ScriptError, ScriptPage, Unsupported, serialized

_log = logging.getLogger(__name__)

ENGINE = "firefox"
# Firefox's own default for --remote-debugging-port without a value, so a Firefox the user started with the
# bare flag is found too.
PORT = 9222
# How long Firefox may take to start and open its agent.
START_SECONDS = 30.0
COMMAND_SECONDS = 30.0
# Console messages and requests kept per tab.
KEPT_EVENTS = 500
_EVENTS = ("log.entryAdded", "network.responseCompleted", "network.fetchError")

NOT_REMOTE = (f"Firefox is open without its remote agent, so OpenProgram cannot reach it. Quit Firefox and call "
              f"again: OpenProgram then starts it with your profile and the agent on. Or start Firefox yourself "
              f"with --remote-debugging-port {PORT}.")

# WebDriver's codes of the keys that are not a character (W3C WebDriver, "Keyboard actions").
KEYS = {"Enter": "", "Tab": "", "Backspace": "", "Escape": "", "Delete": "",
        "Insert": "", "Home": "", "End": "", "PageUp": "", "PageDown": "",
        "ArrowLeft": "", "ArrowUp": "", "ArrowRight": "", "ArrowDown": "", "Space": " ",
        "Shift": "", "Control": "", "Alt": "", "Meta": "", "Pause": "",
        **{f"F{number}": chr(0xE031 + number - 1) for number in range(1, 13)}}


def installed() -> bool:
    return bool(catalog.executable("firefox"))


# -- messages ---------------------------------------------------------------------------

def command(number: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"id": number, "method": method, "params": params}


def key_value(key: str) -> str:
    if key in KEYS:
        return KEYS[key]
    if len(key) == 1:
        return key
    raise pa.ActionError("key_required", f"{key} is not a key Firefox can press")


def pointer_actions(context: str, steps: list[dict[str, Any]], modifiers: list[str]) -> dict[str, Any]:
    """``input.performActions`` for pointer steps in CSS pixels of the viewport, with modifier keys held."""
    pointer = []
    for step in steps:
        item = dict(step)
        if item["type"] == "pointerMove":
            item["x"], item["y"] = int(round(item["x"])), int(round(item["y"]))
        pointer.append(item)
    sources: list[dict[str, Any]] = []
    if modifiers:
        held = [{"type": "keyDown", "value": key_value(name)} for name in modifiers]
        # The key source waits while the pointer moves and clicks, then lets go.
        pauses = [{"type": "pause"} for _ in pointer]
        released = [{"type": "keyUp", "value": key_value(name)} for name in reversed(modifiers)]
        sources.append({"type": "key", "id": "keys", "actions": [*held, *pauses, *released]})
        pointer = [*({"type": "pause"} for _ in held), *pointer, *({"type": "pause"} for _ in released)]
    sources.insert(0, {"type": "pointer", "id": "mouse", "parameters": {"pointerType": "mouse"}, "actions": pointer})
    return {"context": context, "actions": sources}


def key_actions(context: str, modifiers: list[str], key: str, hold: float = 0.0) -> dict[str, Any]:
    """``input.performActions`` for one key with its modifiers, held for ``hold`` seconds."""
    names = [*modifiers, key]
    actions: list[dict[str, Any]] = [{"type": "keyDown", "value": key_value(name)} for name in names]
    if hold:
        actions.append({"type": "pause", "duration": int(hold * 1000)})
    actions += [{"type": "keyUp", "value": key_value(name)} for name in reversed(names)]
    return {"context": context, "actions": [{"type": "key", "id": "keys", "actions": actions}]}


# -- the connection ---------------------------------------------------------------------

class BidiError(RuntimeError):
    def __init__(self, error: str, message: str) -> None:
        super().__init__(f"{error}: {message}" if message else error)
        self.error = error


class _Bidi:
    """One websocket to Firefox's remote agent, with the session made on it and the events it reports."""

    def __init__(self, url: str, connect=None) -> None:
        if connect is None:
            from websockets.sync.client import connect
        self._socket = connect(url, max_size=None, open_timeout=5, close_timeout=2)
        self._numbers = itertools.count(1)
        self._guard = threading.Lock()
        self._waiting: dict[int, tuple[threading.Event, list]] = {}
        self.closed = False
        # context -> recent console messages and requests.
        self.console: dict[str, deque] = {}
        self.network: dict[str, deque] = {}
        self._reader = threading.Thread(target=self._read, name="firefox-bidi", daemon=True)
        self._reader.start()

    def _read(self) -> None:
        try:
            for raw in self._socket:
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                if "id" in message:
                    with self._guard:
                        waiter = self._waiting.pop(message["id"], None)
                    if waiter is not None:
                        waiter[1].append(message)
                        waiter[0].set()
                elif message.get("type") == "event":
                    self._event(message.get("method", ""), message.get("params") or {})
        except Exception:
            _log.debug("the Firefox connection ended", exc_info=True)
        finally:
            self.closed = True
            with self._guard:
                waiting, self._waiting = self._waiting, {}
            for event, _ in waiting.values():
                event.set()

    def _event(self, method: str, params: dict[str, Any]) -> None:
        if method == "log.entryAdded":
            context = str((params.get("source") or {}).get("context") or "")
            self.console.setdefault(context, deque(maxlen=KEPT_EVENTS)).append(
                {"level": params.get("level") or "info", "text": params.get("text") or ""})
        elif method in ("network.responseCompleted", "network.fetchError"):
            request = params.get("request") or {}
            row = {"method": request.get("method") or "", "url": request.get("url") or ""}
            if method == "network.fetchError":
                row["error"] = params.get("errorText") or "failed"
            else:
                row["status"] = (params.get("response") or {}).get("status")
            self.network.setdefault(str(params.get("context") or ""), deque(maxlen=KEPT_EVENTS)).append(row)

    def send(self, method: str, params: dict[str, Any], timeout: float = COMMAND_SECONDS) -> dict[str, Any]:
        return self.many([(method, params)], timeout)[0]

    def many(self, calls: list[tuple[str, dict[str, Any]]], timeout: float = COMMAND_SECONDS) -> list[Any]:
        """Send several commands at once and wait for all; each answer is its result or a ``BidiError``."""
        if self.closed:
            raise Lost("the connection to Firefox closed")
        waiters = []
        for method, params in calls:
            number = next(self._numbers)
            waiter: tuple[threading.Event, list] = (threading.Event(), [])
            with self._guard:
                self._waiting[number] = waiter
            try:
                self._socket.send(json.dumps(command(number, method, params)))
            except Exception as exc:
                raise Lost(f"the connection to Firefox broke ({exc})") from None
            waiters.append(waiter)
        deadline = time.monotonic() + timeout
        out: list[Any] = []
        for event, answer in waiters:
            if not event.wait(max(0.0, deadline - time.monotonic())) or not answer:
                raise Lost("Firefox did not answer in time" if not self.closed else "the connection to Firefox closed")
            message = answer[0]
            if message.get("type") == "error" or "error" in message and "result" not in message:
                out.append(BidiError(str(message.get("error") or "error"), str(message.get("message") or "")))
            else:
                out.append(message.get("result") or {})
        return out

    def close(self) -> None:
        try:
            self.send("session.end", {}, timeout=3)
        except Exception:
            # Teardown: the agent may already be gone.
            pass
        try:
            self._socket.close()
        except Exception:
            pass


def _checked(answer: Any) -> dict[str, Any]:
    if isinstance(answer, BidiError):
        raise answer
    return answer


_lock = threading.Lock()
_connection: _Bidi | None = None
_selected: dict[str, str] = {}
_sessions: set[str] = set()


def listening(port: int = PORT) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def running() -> bool:
    """Whether a Firefox is open on this computer."""
    try:
        if sys.platform == "win32":
            done = subprocess.run(["tasklist", "/FI", "IMAGENAME eq firefox.exe", "/NH"], capture_output=True,
                                  text=True, timeout=10)
            return "firefox.exe" in done.stdout.lower()
        done = subprocess.run(["pgrep", "-x", "firefox"], capture_output=True, text=True, timeout=10)
        return done.returncode == 0 and bool(done.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def start_command(executable: str, port: int = PORT) -> list[str]:
    """How Firefox is started with its agent: in the background on macOS (``open -g``), so it does not take
    the front; elsewhere the program itself."""
    if sys.platform == "darwin" and ".app/" in executable:
        bundle = executable[:executable.index(".app/") + 4]
        return ["/usr/bin/open", "-g", "-a", bundle, "--args", "--remote-debugging-port", str(port)]
    return [executable, "--remote-debugging-port", str(port)]


def _start() -> None:
    executable = catalog.executable("firefox")
    if not executable:
        raise Unavailable("Firefox is not installed where OpenProgram looks for it",
                          missing="Install Firefox in its usual place, then call again.")
    options: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                               "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        options["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        options["start_new_session"] = True
    try:
        subprocess.Popen(start_command(executable), **options)
    except OSError as exc:
        raise Unavailable(f"Firefox did not start ({exc})") from None
    deadline = time.monotonic() + START_SECONDS
    while time.monotonic() < deadline:
        if listening():
            return
        time.sleep(0.25)
    raise Unavailable(f"Firefox started but its remote agent did not open port {PORT} within "
                      f"{START_SECONDS:g} s")


def _connect(connect=None) -> _Bidi:
    global _connection
    with _lock:
        if _connection is not None and not _connection.closed:
            return _connection
        if not listening():
            if running():
                raise Unavailable("Firefox is open without its remote agent", permission=NOT_REMOTE)
            _start()
        try:
            bidi = _Bidi(f"ws://127.0.0.1:{PORT}/session", connect)
        except Exception as exc:
            raise Unavailable(f"something listens on port {PORT} that is not Firefox's remote agent ({exc}). "
                              f"Close it, or quit Firefox and call again") from None
        try:
            made = bidi.send("session.new", {"capabilities": {}})
            if not isinstance(made, BidiError):
                bidi.send("session.subscribe", {"events": list(_EVENTS)})
        except Lost as exc:
            bidi.close()
            raise Unavailable(f"Firefox's remote agent did not answer ({exc})") from None
        if isinstance(made, BidiError):
            bidi.close()
            raise Unavailable(f"Firefox did not open a session ({made}). Another program may be driving it; "
                              "close that program and call again")
        _connection = bidi
        return bidi


def connected(session: str, browser: str = "") -> bool:
    with _lock:
        return session in _sessions and _connection is not None and not _connection.closed


def close_session(session: str, browser: str = "") -> None:
    """The conversation is done with Firefox; the session ends with the last conversation. Firefox stays open."""
    global _connection
    with _lock:
        _sessions.discard(session)
        _selected.pop(session, None)
        ending = _connection if not _sessions else None
        if ending is not None:
            _connection = None
    if ending is not None:
        ending.close()


class FirefoxBrowser(ScriptPage):
    """One conversation's connection to the user's Firefox."""

    lacks: dict[str, str] = {}

    def __init__(self, session: str, browser: str = "firefox", connect=None) -> None:
        super().__init__()
        if not installed():
            raise Unavailable("Firefox is not installed where OpenProgram looks for it",
                              missing="Install Firefox in its usual place, then call again.")
        self.session = session
        self.browser = browser
        self.label = "Firefox"
        self.bidi = _connect(connect)
        with _lock:
            _sessions.add(session)

    def _send(self, method: str, params: dict[str, Any], timeout: float = COMMAND_SECONDS) -> dict[str, Any]:
        answer = self.bidi.send(method, params, timeout)
        if isinstance(answer, BidiError):
            if answer.error == "no such frame":
                raise Lost("the Firefox tab is no longer open")
            raise ScriptError(str(answer))
        return answer

    # -- tabs ----------------------------------------------------------------------

    def tabs(self) -> list[dict[str, Any]]:
        tree = self._send("browsingContext.getTree", {"maxDepth": 0}).get("contexts") or []
        contexts = [item for item in tree if isinstance(item, dict) and item.get("context")]
        probe = "JSON.stringify([document.title, document.visibilityState, document.hasFocus()])"
        answers = self.bidi.many([("script.evaluate", {"expression": probe, "target": {"context": item["context"]},
                                                       "awaitPromise": False, "resultOwnership": "none"})
                                  for item in contexts])
        rows = []
        for item, answer in zip(contexts, answers):
            title, visible, focused = "", False, False
            if not isinstance(answer, BidiError) and answer.get("type") == "success":
                try:
                    title, shown, focused = json.loads((answer.get("result") or {}).get("value") or "[]")
                    visible = shown == "visible"
                except (TypeError, ValueError):
                    pass
            rows.append({"key": item["context"], "url": item.get("url") or "", "title": title or "",
                         "visible": visible, "focused": focused})
        with _lock:
            chosen = _selected.get(self.session)
            if chosen not in {row["key"] for row in rows}:
                first = (next((row for row in rows if row["focused"]), None)
                         or next((row for row in rows if row["visible"]), None) or (rows[0] if rows else None))
                chosen = first["key"] if first else ""
                _selected[self.session] = chosen
        return [{"key": row["key"], "url": row["url"], "title": row["title"], "current": row["key"] == chosen}
                for row in rows]

    def select(self, index: int, url: str) -> bool:
        rows = self.tabs()
        if not 0 <= index < len(rows) or rows[index]["url"] != url:
            return False
        with _lock:
            _selected[self.session] = rows[index]["key"]
        return True

    def _tab(self) -> str:
        with _lock:
            return _selected.get(self.session, "")

    # -- the backend ---------------------------------------------------------------

    def _evaluate(self, expression: str) -> Any:
        found = self._send("script.evaluate", {"expression": serialized(expression), "target": {"context": self._tab()},
                                               "awaitPromise": True, "resultOwnership": "none"}, timeout=40)
        if found.get("type") == "exception":
            details = found.get("exceptionDetails") or {}
            raise ScriptError(str(details.get("text") or "the script threw"))
        value = (found.get("result") or {}).get("value")
        try:
            return json.loads(value) if isinstance(value, str) else value
        except ValueError:
            return value

    def _screenshot(self) -> tuple[bytes, float]:
        ratio = float(self._viewport().get("ratio") or 1.0)
        shot = self._send("browsingContext.captureScreenshot", {"context": self._tab(), "origin": "viewport"})
        return base64.b64decode(shot.get("data") or ""), ratio

    def _navigate(self, url: str) -> None:
        self._send("browsingContext.navigate", {"context": self._tab(), "url": url, "wait": "complete"}, timeout=60)

    def _create(self, url: str, visible: bool) -> None:
        made = self._send("browsingContext.create", {"type": "tab", "background": not visible})
        with _lock:
            _selected[self.session] = str(made.get("context") or "")
        self._navigate(url)

    def _close(self, index: int) -> None:
        rows = self.tabs()
        if not 0 <= index < len(rows):
            raise Lost("the Firefox tab is no longer open")
        self._send("browsingContext.close", {"context": rows[index]["key"]})

    def _history(self, which: str) -> None:
        if which == "reload":
            self._send("browsingContext.reload", {"context": self._tab(), "wait": "complete"}, timeout=60)
        else:
            self._send("browsingContext.traverseHistory", {"context": self._tab(), "delta": -1 if which == "back" else 1})

    def _pointer(self, steps: list[dict[str, Any]], modifiers: list[str]) -> bool:
        self._send("input.performActions", pointer_actions(self._tab(), steps, modifiers), timeout=40)
        self._send("input.releaseActions", {"context": self._tab()})
        return True

    def _keys(self, modifiers: list[str], key: str, seconds: float) -> bool:
        self._send("input.performActions", key_actions(self._tab(), modifiers, key, seconds), timeout=40)
        self._send("input.releaseActions", {"context": self._tab()})
        return True

    def _upload(self, args: dict[str, Any]) -> dict[str, Any]:
        path = str(args.get("path") or "")
        if not path:
            raise pa.ActionError("invalid_arguments", "upload_file needs path")
        if args.get("field") is not None:
            raise Unsupported("upload_file(field=n) is not available in Firefox; name the file field as element")
        if not args.get("element") and not args.get("role"):
            raise Unsupported("in Firefox upload_file names the file field (an input of type file) as element")
        from .script_page import page_script, _named
        found = self._send("script.evaluate", {
            "expression": page_script({"action": "node", **_named(args, "")}), "target": {"context": self._tab()},
            "awaitPromise": False, "resultOwnership": "root"})
        node = found.get("result") or {}
        if node.get("type") != "node" or not node.get("sharedId"):
            value = node.get("value")
            if isinstance(value, list):
                said = {key: (item or {}).get("value") for key, item in value if isinstance(item, dict)}
                if said.get("reason_code"):
                    raise pa.ActionError(str(said["reason_code"]), str(said.get("message") or ""))
            raise pa.ActionError("target_required", "upload_file needs the file field as element")
        try:
            self._send("input.setFiles", {"context": self._tab(), "element": {"sharedId": node["sharedId"]},
                                          "files": [str(Path(path).expanduser())]})
        except ScriptError as exc:
            raise pa.ActionError("invalid_arguments", f"the element is not a file field ({exc}); name the input "
                                 "of type file") from None
        return self._after(f"uploaded {Path(path).name}")

    def _console(self, args: dict[str, Any]) -> dict[str, Any]:
        rows = list(self.bidi.console.get(self._tab(), ()))
        if args.get("only_errors"):
            rows = [row for row in rows if row["level"] == "error"]
        if args.get("pattern"):
            rows = [row for row in rows if str(args["pattern"]) in row["text"]]
        rows = rows[-int(args["limit"]):] if args.get("limit") else rows
        text = "\n".join(f"[{row['level']}] {row['text']}" for row in rows) or "(no console messages)"
        return self._result(text)

    def _network(self, args: dict[str, Any]) -> dict[str, Any]:
        rows = list(self.bidi.network.get(self._tab(), ()))
        if args.get("only_errors"):
            rows = [row for row in rows if row.get("error") or (row.get("status") or 0) >= 400]
        if args.get("pattern"):
            rows = [row for row in rows if str(args["pattern"]) in row["url"]]
        rows = rows[-int(args["limit"]):] if args.get("limit") else rows
        text = "\n".join(f"{row['method']} {row['url']} → {row.get('status') or row.get('error')}" for row in rows)
        return self._result(text or "(no requests)")


__all__ = ["BidiError", "ENGINE", "FirefoxBrowser", "KEYS", "NOT_REMOTE", "PORT", "close_session", "command",
           "connected", "installed", "key_actions", "key_value", "listening", "pointer_actions", "running",
           "start_command"]
