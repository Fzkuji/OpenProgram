"""The user's Safari on macOS, through AppleScript.

Playwright's WebKit is not Safari, and a ``safaridriver`` session is a
separate automation window without the user's sign-ins, so neither reaches
the user's own tabs. Safari's scripting dictionary does: ``osascript`` lists
the windows and tabs, opens, closes and points tabs at an address, and
``do JavaScript`` runs the page layer (``script_page.py``) inside any tab,
also one behind others. ``do JavaScript`` runs only while Safari's
"Allow JavaScript from Apple Events" is on, and the first script asks the
user to let OpenProgram control Safari (Privacy & Security, Automation);
either refusal answers with where to turn it on.

Safari's tabs have no id in its dictionary, only a window id and a place in
the window, so each tab gets a key here that follows it while tabs open,
close and navigate (:func:`track`). A screenshot captures Safari's window
with the desktop driver's window capture, cropped to the page; Safari draws
only the front tab of a window, so a tab behind others has no screenshot.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import itertools
import json
import os
import subprocess
import sys
import threading
import time
from typing import Any

from . import catalog
from .extension import Lost, Unavailable
from .script_page import ScriptError, ScriptPage, Unsupported

ENGINE = "safari"
BUNDLE = "com.apple.Safari"
# How long one script may take inside Safari, beside osascript's own start.
SCRIPT_SECONDS = 35.0

JS_OFF = ("Safari does not run scripts sent by other apps. In Safari open Settings → Advanced and turn on \"Show "
          "features for web developers\", then turn on Develop → \"Allow JavaScript from Apple Events\" "
          "(Settings → Developer in Safari 17 and later), then call again.")
NOT_ALLOWED = ("OpenProgram is not allowed to control Safari. Turn it on in System Settings → Privacy & Security → "
               "Automation → OpenProgram → Safari, then call again.")

_lock = threading.Lock()
# Every Safari tab seen, with its key; one table for every conversation, since the tabs are the user's.
_seen: list["Seen"] = []
_keys = itertools.count(1)
# session -> the key of the tab that conversation's steps act on.
_selected: dict[str, str] = {}
_sessions: set[str] = set()


def installed() -> bool:
    return sys.platform == "darwin" and (bool(catalog.executable("safari")) or os.path.isdir("/Applications/Safari.app"))


def connected(session: str, browser: str = "") -> bool:
    with _lock:
        return session in _sessions


def close_session(session: str, browser: str = "") -> None:
    with _lock:
        _sessions.discard(session)
        _selected.pop(session, None)


# -- AppleScript --------------------------------------------------------------------

def applescript_string(text: str) -> str:
    """``text`` as an AppleScript string literal."""
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def failure(said: str) -> Exception:
    """What osascript's error text means: a permission the user turns on, Safari gone, or a script error."""
    if "-1743" in said or "Not authorized to send Apple events" in said or "not allowed to send" in said.lower():
        return Unavailable("OpenProgram may not control Safari", permission=NOT_ALLOWED)
    if "Allow JavaScript from Apple Events" in said or "JavaScript from Apple Events" in said:
        return Unavailable("Safari's \"Allow JavaScript from Apple Events\" is off", permission=JS_OFF)
    if "-600" in said or "isn’t running" in said or "isn't running" in said:
        return Lost("Safari quit while it was operated")
    if "-1728" in said or "Can’t get" in said or "Can't get" in said or "Invalid index" in said:
        return Lost("the Safari tab or window is no longer open")
    return ScriptError(said.strip() or "osascript failed")


def osascript(script: str, *, language: str = "JavaScript", timeout: float = SCRIPT_SECONDS + 10) -> str:
    """Run a script with osascript; its printed result. Nothing is shown on the screen."""
    try:
        done = subprocess.run(["/usr/bin/osascript", "-l", language, "-"], input=script, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise Lost("Safari did not answer in time") from None
    except OSError as exc:
        raise Unavailable(f"osascript could not run ({exc})") from None
    if done.returncode != 0:
        raise failure(done.stderr or done.stdout)
    return done.stdout.strip()


_LIST = r"""
(() => {
  const s = Application("Safari");
  if (!s.running()) return "[]";
  const out = [];
  const ids = s.windows.id();
  for (let w = 0; w < ids.length; w++) {
    const win = s.windows.byId(ids[w]);
    let urls, names;
    try { urls = win.tabs.url(); names = win.tabs.name(); } catch (e) { continue; }
    if (!urls || !urls.length) continue;
    let current = 0, bounds = null;
    try { current = win.currentTab.index(); } catch (e) {}
    try { bounds = win.bounds(); } catch (e) {}
    for (let i = 0; i < urls.length; i++)
      out.push({window: ids[w], index: i + 1, url: urls[i] || "", title: names[i] || "", visible: i + 1 === current,
                front: w === 0, bounds});
  }
  return JSON.stringify(out);
})()
"""


def list_script() -> str:
    return _LIST


def _tab(window: int, index: int) -> str:
    return f"Application(\"Safari\").windows.byId({int(window)}).tabs[{int(index) - 1}]"


def run_script(window: int, index: int, expression: str, token: str, seconds: float = SCRIPT_SECONDS) -> str:
    """The JXA that runs ``expression`` in a tab and waits for its value, in one osascript.

    ``do JavaScript`` returns at once and never waits for a promise, so the
    page keeps a promise's outcome under ``token`` and the script asks again
    until it is there. A page that was replaced meanwhile answers ``lost``.
    """
    kick = ("(() => { const done = (x) => { try { return JSON.stringify({value: x === undefined ? null : x}); } "
            "catch (e) { return JSON.stringify({value: String(x)}); } }; let v; "
            "try { v = (" + expression + "); } catch (e) { return JSON.stringify({error: String(e && e.message || e)}); } "
            "if (!v || typeof v.then !== \"function\") return done(v); "
            "const store = window.__openprogramAsync || (window.__openprogramAsync = {}); "
            "v.then(x => { store[" + json.dumps(token) + "] = done(x); }, "
            "e => { store[" + json.dumps(token) + "] = JSON.stringify({error: String(e && e.message || e)}); }); "
            "return \"pending\"; })()")
    poll = ("(() => { const s = window.__openprogramAsync; if (!s) return JSON.stringify({lost: true}); "
            "const r = s[" + json.dumps(token) + "]; if (r === undefined) return \"pending\"; "
            "delete s[" + json.dumps(token) + "]; return r; })()")
    return ("(() => { const s = Application(\"Safari\"); const t = " + _tab(window, index) + "; "
            "let r = s.doJavaScript(" + json.dumps(kick) + ", {in: t}); "
            "const end = Date.now() + " + str(int(seconds * 1000)) + "; "
            "while (r === \"pending\" && Date.now() < end) { delay(0.05); r = s.doJavaScript(" + json.dumps(poll) + ", {in: t}); } "
            "return r === \"pending\" ? JSON.stringify({error: \"the script did not finish in time\"}) : r; })()")


def navigate_script(window: int, index: int, url: str) -> str:
    return f"(() => {{ {_tab(window, index)}.url = {json.dumps(url)}; return \"ok\"; }})()"


def close_script(window: int, index: int) -> str:
    return f"(() => {{ {_tab(window, index)}.close(); return \"ok\"; }})()"


def create_script(url: str, visible: bool) -> str:
    """AppleScript that opens ``url``: in a new tab of the front window, made its front tab (``visible``), or
    in a window of its own, which Safari opens behind the application in front. Prints "<window id> <tab>"."""
    address = applescript_string(url)
    if visible:
        return ("tell application \"Safari\"\n"
                "  if (count of windows) is 0 then make new document\n"
                "  tell front window\n"
                f"    set current tab to (make new tab at end of tabs with properties {{URL:{address}}})\n"
                "    return ((id as text) & \" \" & ((count of tabs) as text))\n"
                "  end tell\n"
                "end tell")
    return ("tell application \"Safari\"\n"
            f"  make new document with properties {{URL:{address}}}\n"
            "  return ((id of front window as text) & \" 1\")\n"
            "end tell")


# -- tab keys -------------------------------------------------------------------------

@dataclass
class Seen:
    key: str
    window: int
    index: int
    url: str


def track(seen: list[Seen], rows: list[dict[str, Any]], make=lambda: f"s{next(_keys)}") -> list[Seen]:
    """The keys of the listed tabs, following each tab from the last listing.

    A tab keeps its key when it is in the same window at the same place and
    address; else at the same address in the same window (tabs before it
    opened or closed); else, when its window holds as many tabs as before,
    at the same place (it navigated). Any other tab is new.
    """
    left = list(seen)
    keys: list[str | None] = [None] * len(rows)

    def take(number: int, match) -> None:
        if match is not None:
            keys[number] = match.key
            left.remove(match)

    for number, row in enumerate(rows):
        take(number, next((item for item in left if item.window == row["window"] and item.index == row["index"]
                           and item.url == row["url"]), None))
    for number, row in enumerate(rows):
        if keys[number] is None:
            same = [item for item in left if item.window == row["window"] and item.url == row["url"]]
            take(number, min(same, key=lambda item: abs(item.index - row["index"])) if same else None)
    before, now = Counter(item.window for item in seen), Counter(row["window"] for row in rows)
    for number, row in enumerate(rows):
        if keys[number] is None and before[row["window"]] == now[row["window"]]:
            take(number, next((item for item in left if item.window == row["window"]
                               and item.index == row["index"]), None))
    return [Seen(key or make(), row["window"], row["index"], row["url"]) for key, row in zip(keys, rows)]


# -- the connection ---------------------------------------------------------------------

class SafariBrowser(ScriptPage):
    """One conversation's connection to the user's Safari."""

    lacks = {
        "upload_file": "upload_file is not available in Safari: a page script cannot choose a file. Ask the user "
                       "to choose it, or use another browser",
        "read_console_messages": "read_console_messages is not available in Safari: its console is not open to "
                                 "other apps",
        "read_network_requests": "read_network_requests is not available in Safari: its requests are not open to "
                                 "other apps",
    }

    def __init__(self, session: str, browser: str = "safari") -> None:
        super().__init__()
        if not installed():
            raise Unavailable("Safari is only on macOS", missing="Safari runs only on a Mac.")
        self.session = session
        self.browser = browser
        self.label = "Safari"
        self._rows: list[dict[str, Any]] = []
        with _lock:
            _sessions.add(session)

    # -- tabs ----------------------------------------------------------------------

    def _listing(self) -> list[dict[str, Any]]:
        try:
            rows = json.loads(osascript(list_script()) or "[]")
        except ValueError:
            raise Lost("Safari listed its tabs in a form this version does not read") from None
        rows = [row for row in rows if isinstance(row, dict)]
        global _seen
        with _lock:
            _seen = track(_seen, rows)
            for row, item in zip(rows, _seen):
                row["key"] = item.key
        self._rows = rows
        return rows

    def tabs(self) -> list[dict[str, Any]]:
        rows = self._listing()
        with _lock:
            chosen = _selected.get(self.session)
            if chosen not in {row["key"] for row in rows}:
                front = next((row for row in rows if row.get("front") and row.get("visible")), None)
                chosen = front["key"] if front else ""
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

    def _where(self) -> dict[str, Any]:
        key = self._tab()
        row = next((row for row in self._rows if row["key"] == key), None)
        if row is None:
            row = next((row for row in self._listing() if row["key"] == key), None)
        if row is None:
            raise Lost("the Safari tab is no longer open")
        return row

    # -- the backend ---------------------------------------------------------------

    def _evaluate(self, expression: str) -> Any:
        row = self._where()
        said = osascript(run_script(row["window"], row["index"], expression, f"t{time.monotonic_ns()}"))
        try:
            answer = json.loads(said)
        except ValueError:
            raise ScriptError(f"Safari answered {said[:200]!r}") from None
        if not isinstance(answer, dict):
            return answer
        if "error" in answer:
            raise ScriptError(str(answer["error"]))
        if answer.get("lost"):
            # The page was replaced before it answered: whatever was asked of the old one is done.
            return None
        return answer.get("value")

    def _navigate(self, url: str) -> None:
        row = self._where()
        before = row["url"]
        osascript(navigate_script(row["window"], row["index"], url))
        row["url"] = url
        self._settle(before)

    def _create(self, url: str, visible: bool) -> None:
        said = osascript(create_script(url, visible), language="AppleScript")
        try:
            window, index = (int(part) for part in said.split())
        except ValueError:
            raise Lost(f"Safari did not say where it opened the tab ({said!r})") from None
        rows = self._listing()
        opened = next((row for row in rows if row["window"] == window and row["index"] == index), None)
        if opened is not None:
            with _lock:
                _selected[self.session] = opened["key"]
        self._settle("about:blank")

    def _close(self, index: int) -> None:
        rows = self._rows or self._listing()
        if not 0 <= index < len(rows):
            raise Lost("the Safari tab is no longer open")
        osascript(close_script(rows[index]["window"], rows[index]["index"]))

    def _screenshot(self) -> tuple[bytes, float]:
        row = self._where()
        if not row.get("visible"):
            raise Unsupported("this tab is behind another tab in its Safari window, and Safari draws only the front "
                              "tab; create_tab opens a tab of your own in a window of its own")
        bounds = row.get("bounds") or {}
        try:
            frame = (float(bounds["x"]), float(bounds["y"]), float(bounds["width"]), float(bounds["height"]))
        except (KeyError, TypeError, ValueError):
            raise Unsupported("Safari did not say where its window is") from None
        viewport = self._viewport()
        width = float(viewport.get("width") or frame[2])
        height = float(viewport.get("height") or frame[3])
        region = (0.0, max(0.0, frame[3] - height), min(width, frame[2]), min(height, frame[3]))
        image, scale = capture(frame, region)
        return image, scale


def _pid() -> int:
    from AppKit import NSRunningApplication
    running = NSRunningApplication.runningApplicationsWithBundleIdentifier_(BUNDLE)
    if not running:
        raise Unsupported("Safari is not running")
    return int(running[0].processIdentifier())


def capture(frame: tuple[float, float, float, float], region: tuple[float, float, float, float]) -> tuple[bytes, float]:
    """A PNG of the part ``region`` of Safari's window at ``frame``, with the window capture of the desktop
    driver (Screen Recording), and its pixels per point."""
    try:
        from openprogram.programs.tools.computer_use.computer_use.desktop.macos import computer as macos
        image, scale = macos.backend().capture_window(_pid(), frame, region=region)
    except Unsupported:
        raise
    except Exception as exc:
        raise Unsupported(f"Safari's window could not be captured ({exc}); OpenProgram needs Screen Recording in "
                          "System Settings → Privacy & Security") from None
    if not image:
        raise Unsupported("Safari's window is not on screen")
    return image, scale


__all__ = ["BUNDLE", "ENGINE", "JS_OFF", "NOT_ALLOWED", "SafariBrowser", "Seen", "applescript_string", "capture",
           "close_session", "close_script", "connected", "create_script", "failure", "installed", "list_script",
           "navigate_script", "osascript", "run_script", "track"]
