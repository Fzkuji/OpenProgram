"""The built-in browser's tabs, operated directly through Playwright.

A tab keeps one short id (``tk3fa``) derived from the App's window and tab,
the same in every turn, sub-agent and process of every conversation. Reading a tab
returns Playwright's AI snapshot: the page's ARIA tree, each element with a
ref (``e12``; ``f1e3`` inside a frame). A ref stays valid while its element
exists, through any number of actions, and every action returns what it
changed as an incremental snapshot, so there is no separate observe step and
nothing that expires between calls. A ref whose element is gone is found
again by its role and name; failing that, the action fails with the page as
it is now.

Several agents may use the browser at once, also on one tab. An action
holds its tab from the first page call until its result is read, and a list
of actions holds every tab it touches until the list ends, so another
agent's input never lands in the middle; a tab held for a minute fails with
``tab_busy``. Each agent (a conversation's execution) has its own current
tab and its own baseline for changes, so a result shows everything that
changed since that agent last looked, whoever changed it, and says when
another agent acted on the tab in between. No write is ever sent twice: a
failure after the input was sent says the input may have taken effect and
carries the page as it is. Coordinates belong to one screenshot and stop
working once the page moved or was acted on. Playwright's sync API
belongs to one thread, so one owner thread holds the CDP connection to the
App, reconnects when the App restarts, and runs each page call; waiting
(``wait``, and settling after an action) happens on the caller's thread in
short owner calls, so one agent's wait does not hold up the others. Tabs are
listed, opened, captured and closed through the App's renderer
(``surface_context`` / ``webtab``). Input is Playwright's own, trusted
input; it reaches a tab in the background without bringing it forward.

Every action is reported to the conversation's page preview
(``pages.report_browser_operation``): a write as ``dispatched`` before its
input and ``acknowledged`` or ``failed`` after, a click with where it
landed; a read, and a tab just opened, as a read the preview follows
without marking the page operated.

A page needs somewhere to open. With an App window there, that is the App:
the user can see the tab and it has the App's sign-ins. With no App window to
open it in (the App is closed, or this process cannot reach it), the driver
starts a browser of its own with no window and a fresh profile
(``host/resources/background_browser.py``) and the tab opens there, with the
same id rules, actions and results; ``open`` says so, since such a page has
none of the App's sign-ins. Those tabs live in this process: they close with
it, or once none has been used for ``OWN_IDLE_SECONDS``.

In a workflow subprocess the same code runs in the child: the renderer
calls go through the parent's bridge, which confines them to the
originating window and closes the tabs the child opened when it ends, and
the operation reports go to the parent, which holds the App connections.
"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import contextvars
import hashlib
import inspect
from dataclasses import dataclass, field
import itertools
import logging
import os
import re
import struct
import threading
import time
from typing import Any
from urllib.parse import urlparse

from . import page_actions as pa
from . import video

_log = logging.getLogger(__name__)

SNAPSHOT_LIMIT = 40_000
ACTION_TIMEOUT_MS = 10_000
_TRACK = "openprogram"
# The agent a call acts for: "<session>/<execution>", or the session outside an execution.
_VIEWER: contextvars.ContextVar[str] = contextvars.ContextVar("browser_tab_viewer", default="")
# The tab locks a list of actions holds until it ends; None outside a list.
_HELD: contextvars.ContextVar[list | None] = contextvars.ContextVar("browser_tab_held", default=None)
TAB_WAIT_SECONDS = 60.0
# The window id of tabs in the driver's own browser, which no App window holds.
OWN_WINDOW = "background"
# The driver's own browser closes once none of its tabs was used for this long.
OWN_IDLE_SECONDS = 30 * 60.0
# Tabs one conversation's agents hold at once; one more closes the least recently used.
TABS_PER_CONVERSATION = 8
_ROOM_NOTE = ("closed {count} tab(s) this conversation opened earlier and used least recently: "
              f"a conversation keeps {TABS_PER_CONVERSATION} of its own open")
_OWN_NOTE = ("no OpenProgram App window is available to this turn, so this page is in a background browser with "
             "none of the App's sign-ins; a page that needs the user's sign-in has to wait for the App")
_LOG_LIMIT = 300
# "- button "Next" [ref=e12]" -> role, name, ref.
_REF_LINE = re.compile(r'^\s*- (?:<changed> )?([\w-]+)(?: "((?:[^"\\]|\\.)*)")?[^\n]*?\[ref=(\w+)\]', re.M)

_CURSOR_AT_SCRIPT = r"""
element => {
  const cursor = globalThis.__openprogramAgentCursor;
  if (!cursor?.armed || typeof cursor.show !== "function") return;
  const rect = element.getBoundingClientRect();
  cursor.armed = false;
  cursor.show(rect.left + rect.width / 2, rect.top + rect.height / 2);
}
"""

_VIEWPORT = "() => [window.innerWidth, Math.round(window.scrollX), Math.round(window.scrollY)]"
_VIEWPORT_SIZE = "() => ({width: window.innerWidth, height: window.innerHeight})"


# Actions that only look at the page.
_READS = {"read", "console", "network"}
# What the conversation's preview follows as a read: it never marks the page as operated.
_LOOKS = _READS | {"wait", "screenshot"}

# The file input an element is, or labels. Anything else (an upload button, a drop zone) is
# clicked, and answered through the file chooser it opens: guessing a nearby input could put
# the file into another field.
FILE_INPUT_SCRIPT = """e => {
  const isFile = n => n instanceof HTMLInputElement && n.type === "file";
  return isFile(e) ? e : isFile(e.control) ? e.control : null;
}"""
# The agent's cursor drawn in the page, where it points and clicks.
_AGENT_CURSOR_SCRIPT = r"""
armed => {
  const key = "__openprogramAgentCursor";
  let state = globalThis[key];
  if (!state || state.version !== 2) {
    state = {version: 2, armed: false, show: null};
    try {
      Object.defineProperty(globalThis, key, {
        value: state, configurable: true,
      });
    } catch (_) {
      return false;
    }
    state.show = (clientX, clientY) => {
      document.querySelectorAll("[data-openprogram-agent-cursor]")
        .forEach(node => node.remove());

      const host = document.createElement("div");
      host.setAttribute("data-openprogram-agent-cursor", "");
      host.setAttribute("aria-hidden", "true");
      Object.assign(host.style, {
        position: "fixed",
        left: `${clientX}px`,
        top: `${clientY}px`,
        width: "0",
        height: "0",
        zIndex: "2147483647",
        pointerEvents: "none",
      });

      const ring = document.createElement("span");
      Object.assign(ring.style, {
        position: "absolute",
        left: "-15px",
        top: "-15px",
        width: "30px",
        height: "30px",
        border: "2px solid rgba(112, 92, 255, .9)",
        borderRadius: "50%",
        boxSizing: "border-box",
      });

      const cursor = document.createElement("span");
      Object.assign(cursor.style, {
        position: "absolute",
        left: "-2px",
        top: "-2px",
        width: "24px",
        height: "30px",
        filter: "drop-shadow(0 2px 3px rgba(0, 0, 0, .35))",
      });
      cursor.innerHTML = '<svg viewBox="0 0 24 30" width="24" height="30" '
        + 'xmlns="http://www.w3.org/2000/svg"><path d="M2 2v21l5.6-5.2 '
        + '3.8 9.1 4.2-1.8-3.8-9.1H20L2 2Z" fill="#705cff" '
        + 'stroke="white" stroke-width="2" stroke-linejoin="round"/></svg>';
      host.append(ring, cursor);
      (document.documentElement || document.body)?.append(host);

      const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
      if (!reduced) {
        ring.animate([
          {transform: "scale(.35)", opacity: 1},
          {transform: "scale(1.35)", opacity: 0},
        ], {duration: 650, easing: "cubic-bezier(.2,.8,.2,1)"});
        cursor.animate([
          {transform: "translate(-2px,-2px)", opacity: 1},
          {transform: "translate(0,0)", opacity: 1, offset: .65},
          {transform: "translate(0,0)", opacity: 0},
        ], {duration: 900, easing: "ease-out"});
      }
      setTimeout(() => host.remove(), reduced ? 240 : 900);
    };
    addEventListener("pointerdown", event => {
      if (!state.armed || !event.isTrusted) return;
      state.armed = false;
      state.show(event.clientX, event.clientY);
    }, true);
  }
  state.armed = Boolean(armed);
  return true;
}
"""

# The page's file fields, for upload(field=n) when a button's click opens no chooser (the page
# lost track of its own input, or opens it only after an await that spent the click). They are
# listed with what tells them apart and never picked for the model: a wrong guess would put the
# file into another field and still report success.
FILE_FIELDS_SCRIPT = """(e) => {
  const words = n => (n && (n.innerText || n.textContent) || "").replace(/\\s+/g, " ").trim().slice(0, 60);
  const fields = [...document.querySelectorAll('input[type="file"]')];
  let section = null;
  for (let node = e; node && !section; node = node.parentElement) {
    if (node.querySelector('input[type="file"]')) section = node;
  }
  return fields.map((field, index) => {
    const label = field.labels && field.labels[0];
    let near = words(label);
    for (let node = field.parentElement; !near && node && node !== document.body; node = node.parentElement) {
      near = words(node);
    }
    return {
      index, id: field.id || "", name: field.name || "", accept: field.getAttribute("accept") || "",
      multiple: field.multiple, disabled: field.disabled, near,
      beside: !!section && section.contains(field) && section.querySelectorAll('input[type="file"]').length === 1,
    };
  });
}"""
# What the element shows now, so a failed upload says the page is stuck ("Uploading… 100%").
ELEMENT_STATE_SCRIPT = """e => ({
  text: (e.innerText || e.value || "").replace(/\\s+/g, " ").trim().slice(0, 80),
  disabled: !!(e.disabled || e.getAttribute("aria-disabled") === "true"),
})"""
CHOOSER_WAIT_MS = 3000


class BrowserError(Exception):
    """An action that did not happen, with what the model needs to go on."""

    def __init__(self, reason_code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.extra = extra

    def result(self) -> dict[str, Any]:
        return {"ok": False, "reason_code": self.reason_code, "error": str(self), **self.extra}


@dataclass
class Tab:
    id: str
    window_id: str
    tab_id: str
    binding_id: str = ""
    page_key: str = ""
    # The desktop connection that owned the Page when it was listed; the store
    # keeps a display only from the connection that owns the Page now.
    connection_generation: int = 0
    target_id: str = ""
    title: str = ""
    origin: str = ""
    visible: bool = False
    focused: bool = False
    # ref -> (role, name) from the latest snapshot, to find an element again.
    roles: dict[str, tuple[str, str]] = field(default_factory=dict)
    # Per agent, its last screenshot: CSS pixels per image pixel, the last write before it, url, scroll
    # and the image itself, which a click by x,y is checked against once the tab was acted on.
    shots: dict[str, tuple[float, int, str, int, int, bytes]] = field(default_factory=dict)
    # Per agent: the document (navigation count) of its last snapshot; another one has no baseline.
    documents: dict[str, int] = field(default_factory=dict)
    # Write actions as (sequence, agent), and per agent the sequence it last saw.
    writes: list[tuple[int, str]] = field(default_factory=list)
    seen: dict[str, int] = field(default_factory=dict)
    opener_tab_id: str = ""
    # The file chooser the page opened during an agent action, waiting for upload(path).
    chooser: Any = field(default=None, repr=False, compare=False)
    # The page's file fields, numbered, listed when an upload button opened no chooser: upload(field=n).
    file_fields: dict[int, Any] = field(default_factory=dict, repr=False, compare=False)
    # Where the current action's click lands, in viewport CSS pixels: {"point": {x, y}, "viewport": {width, height}}.
    aim: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def last_write(self) -> int:
        return self.writes[-1][0] if self.writes else 0

    def forget_page(self) -> None:
        """Another page behind the same tab (the App restarted): nothing seen of the old one holds."""
        self.roles, self.documents, self.shots, self.chooser, self.file_fields = {}, {}, {}, None, {}

    def row(self, url: str = "") -> dict[str, Any]:
        return {"tab": self.id, "title": self.title, "url": url or self.origin,
                "shown": "visible" if self.visible else "background", **({"focused": True} if self.focused else {})}


@dataclass
class _Watch:
    """A page's logs and in-flight requests, recorded from the moment it is first used."""
    console: list[str] = field(default_factory=list)
    network: list[dict[str, Any]] = field(default_factory=list)
    pending: set = field(default_factory=set)
    documents: int = 0
    focus: Any = None


@dataclass
class _Acted:
    """An action done on the owner thread, before its effects have settled."""
    done: str
    before_url: str
    documents: int
    pending: set
    created: int


_writes = itertools.count(1)


def _tab_name(window_id: str, tab_id: str, taken: set[str]) -> str:
    """A short id that every process derives alike from the App's window and tab."""
    digest = base64.b32encode(hashlib.sha1(f"{window_id}\0{tab_id}".encode()).digest()).decode().lower()
    return next(f"t{digest[:size]}" for size in range(4, len(digest)) if f"t{digest[:size]}" not in taken)


@contextmanager
def batch():
    """A list of actions: each tab it touches stays held until the list ends."""
    held: list[threading.Lock] = []
    token = _HELD.set(held)
    try:
        yield
    finally:
        _HELD.reset(token)
        for lock in reversed(held):
            lock.release()


def _check_cancelled() -> None:
    from openprogram.runtime.conversation.run_control import check_cancelled
    check_cancelled()


def _access_disabled() -> bool:
    """Whether the user turned off agent access to the page of this conversation's turn."""
    from openprogram.runtime.conversation import surface_context
    context = surface_context.current() or {}
    return any(isinstance(surface, dict) and surface.get("preview_status") == "disabled"
               for surface in context.get("surfaces") or [])



def _response_status(request) -> int:
    """The HTTP status of a finished request, read from what Playwright already holds.

    ``Request.response()`` is a protocol round trip. Called from a
    ``requestfinished`` listener it races the page, context or connection
    closing (a closed tab, ``_disconnect`` reconnecting): the call is
    cancelled and its future fails with ``TargetClosedError`` that nobody
    retrieves. The response arrived before the request finished, so it is
    on the request already.
    """
    response = getattr(request, "existing_response", None)
    if response is None:  # Playwright before Request.existing_response
        response = getattr(getattr(request, "_impl_obj", None), "_response", None)
    try:
        return int(getattr(response, "status", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _target_id(page) -> str:
    session = None
    try:
        session = page.context.new_cdp_session(page)
        info = session.send("Target.getTargetInfo").get("targetInfo") or {}
        return str(info.get("targetId") or "")
    except Exception:
        return ""
    finally:
        if session is not None:
            try:
                session.detach()
            except Exception:
                # Teardown: the page or CDP session may already be gone.
                _log.debug("CDP session detach failed", exc_info=True)


def _cut(text: str) -> str:
    if len(text) <= SNAPSHOT_LIMIT:
        return text
    return (text[:SNAPSHOT_LIMIT] + f"\n[{len(text) - SNAPSHOT_LIMIT} more characters not shown; scroll, "
            "or act on what is shown and the change comes back]")


def _png_width(image: bytes) -> int:
    if image[:8] != b"\x89PNG\r\n\x1a\n":
        return 0
    return struct.unpack(">I", image[16:20])[0]


# The square around a click's point that must still look as it did in the screenshot, in CSS pixels.
POINT_RADIUS_PX = 32
# A channel difference up to this is rendering noise (anti-aliasing, a caret); when more than this
# share of the square's pixels differ, the point shows something else now.
_PIXEL_TOLERANCE = 32
_CHANGED_SHARE = 0.06


def _same_around(old: bytes, new: bytes, point: tuple[float, float], radius: int) -> bool:
    """Whether two PNG screenshots of a tab show the same thing around ``point``, in image pixels."""
    first, second = _pixels_around(old, point, radius), _pixels_around(new, point, radius)
    if not first or len(first) != len(second):
        return False
    changed = sum(1 for i in range(0, len(first), 4)
                  if abs(first[i] - second[i]) > _PIXEL_TOLERANCE or abs(first[i + 1] - second[i + 1]) > _PIXEL_TOLERANCE
                  or abs(first[i + 2] - second[i + 2]) > _PIXEL_TOLERANCE)
    return changed <= len(first) // 4 * _CHANGED_SHARE


def _pixels_around(png: bytes, point: tuple[float, float], radius: int) -> bytes:
    """RGBA bytes of the square of ``radius`` around ``point`` of a PNG; empty where they cannot be read.

    Core Graphics decodes it on macOS; everywhere else the decoder below does.
    """
    pixels = _quartz_pixels_around(png, point, radius)
    return _png_pixels_around(png, point, radius) if pixels is None else pixels


def _quartz_pixels_around(png: bytes, point: tuple[float, float], radius: int) -> bytes | None:
    """The square read through Core Graphics; ``None`` where PyObjC is absent (Windows, Linux)."""
    try:
        import Foundation
        import Quartz
    except ImportError:
        return None
    try:
        data = Foundation.NSData.dataWithBytes_length_(png, len(png))
        source = Quartz.CGImageSourceCreateWithData(data, None)
        image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None) if source is not None else None
        if image is None:
            return b""
        width, height = Quartz.CGImageGetWidth(image), Quartz.CGImageGetHeight(image)
        left, top = max(0, int(point[0]) - radius), max(0, int(point[1]) - radius)
        right, bottom = min(width, int(point[0]) + radius), min(height, int(point[1]) + radius)
        if right <= left or bottom <= top:
            return b""
        part = Quartz.CGImageCreateWithImageInRect(image, Quartz.CGRectMake(left, top, right - left, bottom - top))
        pixels = bytearray((right - left) * (bottom - top) * 4)
        context = Quartz.CGBitmapContextCreate(pixels, right - left, bottom - top, 8, (right - left) * 4,
                                               Quartz.CGColorSpaceCreateDeviceRGB(),
                                               Quartz.kCGImageAlphaPremultipliedLast)
        Quartz.CGContextDrawImage(context, Quartz.CGRectMake(0, 0, right - left, bottom - top), part)
        return bytes(pixels)
    except Exception:
        _log.debug("screenshot pixels unavailable", exc_info=True)
        return b""


def _png_pixels_around(png: bytes, point: tuple[float, float], radius: int) -> bytes:
    """The same square decoded here: an 8-bit RGB or RGBA PNG without interlacing, which is what
    Chromium and the App write. Empty for any other PNG.

    A filter reads only the pixels to the left and above, so each row is undone up to the square's
    right edge and the rows below the square are left alone.
    """
    import zlib
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        return b""
    try:
        position, header, packed = 8, None, []
        while position + 8 <= len(png):
            length, kind = struct.unpack(">I4s", png[position:position + 8])
            body = png[position + 8:position + 8 + length]
            if kind == b"IHDR":
                header = struct.unpack(">IIBBBBB", body)
            elif kind == b"IDAT":
                packed.append(body)
            elif kind == b"IEND":
                break
            position += 12 + length
        if header is None:
            return b""
        width, height, depth, colour, _, _, interlace = header
        size = {2: 3, 6: 4}.get(colour)
        if depth != 8 or size is None or interlace:
            return b""
        left, top = max(0, int(point[0]) - radius), max(0, int(point[1]) - radius)
        right, bottom = min(width, int(point[0]) + radius), min(height, int(point[1]) + radius)
        if right <= left or bottom <= top:
            return b""
        stride = width * size
        data = zlib.decompressobj().decompress(b"".join(packed), bottom * (stride + 1))
        if len(data) < bottom * (stride + 1):
            return b""
        needed = right * size
        out, above = bytearray(), bytes(needed)
        for row in range(bottom):
            start = row * (stride + 1)
            line = _unfilter(data[start], data[start + 1:start + 1 + needed], above, size)
            if row >= top:
                part = line[left * size:]
                if size == 4:
                    out += part
                else:
                    rgba = bytearray(b"\xff") * ((right - left) * 4)
                    rgba[0::4], rgba[1::4], rgba[2::4] = part[0::3], part[1::3], part[2::3]
                    out += rgba
            above = line
        return bytes(out)
    except Exception:
        _log.debug("screenshot pixels unavailable", exc_info=True)
        return b""


def _unfilter(kind: int, line: bytes, above: bytes, size: int) -> bytes:
    """One PNG scanline with its filter undone; ``above`` is the previous line, ``size`` bytes per pixel.

    This runs over every byte above a click's point with no native decoder, so Up, the filter Chromium
    writes most, is one integer addition, and the others walk one colour channel at a time.
    """
    if kind == 0:
        return line
    if kind == 2:
        # Bytewise addition modulo 256 of two whole rows: add the low seven bits of every byte, then
        # put each byte's top bit back without letting a carry cross into its neighbour.
        count = len(line)
        low, high = int.from_bytes(b"\x7f" * count, "big"), int.from_bytes(b"\x80" * count, "big")
        first, second = int.from_bytes(line, "big"), int.from_bytes(above, "big")
        return (((first & low) + (second & low)) ^ ((first ^ second) & high)).to_bytes(count, "big")
    if kind not in (1, 3, 4):
        raise ValueError(f"unknown PNG filter {kind}")
    out = bytearray(len(line))
    for channel in range(size):
        values = line[channel::size]
        if kind == 1:
            out[channel::size] = bytes(itertools.accumulate(values, _add_byte))
            continue
        done, before = [], 0
        if kind == 3:
            for value, up in zip(values, above[channel::size]):
                before = (value + ((before + up) >> 1)) & 255
                done.append(before)
        else:
            corner = 0
            for value, up in zip(values, above[channel::size]):
                # Paeth: whichever of left, up and upper-left is nearest to left + up - corner.
                near_before, near_up = abs(up - corner), abs(before - corner)
                if near_before <= near_up and near_before <= abs(before + up - corner - corner):
                    before = (value + before) & 255
                elif near_up <= abs(before + up - corner - corner):
                    before = (value + up) & 255
                else:
                    before = (value + corner) & 255
                done.append(before)
                corner = up
        out[channel::size] = bytes(done)
    return bytes(out)


def _add_byte(total: int, value: int) -> int:
    return (total + value) & 255


def _script_failure(exc: Exception) -> str:
    """The page's own error from Playwright's report of a failed script, in one line."""
    first = str(exc).split("\n", 1)[0].strip()
    for prefix in ("Page.evaluate: ", "Frame.evaluate: "):
        first = first.removeprefix(prefix)
    return first or type(exc).__name__


def _http_url(url: Any) -> str:
    text = str(url or "").strip()
    if text and "://" not in text and not text.startswith(("about:", "data:")):
        text = "https://" + text
    if urlparse(text).scheme not in {"http", "https", "file"}:
        raise BrowserError("unsupported_url", f"cannot open {text or 'an empty url'}; give an http(s) address")
    return text


class Driver:
    def __init__(self) -> None:
        self._owner = ThreadPoolExecutor(max_workers=1, thread_name_prefix="browser-tabs")
        self._lock = threading.Lock()
        self._tabs: dict[tuple[str, str], Tab] = {}
        self._last: dict[str, str] = {}
        self._used_at: dict[tuple[str, str], float] = {}
        self._playwright = None
        self._browser = None
        self._pages: dict[str, Any] = {}
        self._known: dict[int, str] = {}
        self._watches: dict[str, _Watch] = {}
        # Page targets the App created since connecting, to notice tabs an action opened.
        self._created = 0
        # The driver's own browser: its pages by tab id, and when one was last used.
        self._own = None
        self._own_context = None
        self._own_pages: dict[str, Any] = {}
        self._own_used = 0.0
        self._own_timer: threading.Timer | None = None

    # -- the conversation's side: listing, choosing, opening, closing ------

    def run(self, action: str, args: dict[str, Any], session: str) -> dict[str, Any]:
        """One action; a failure is a result with ``ok: False``, never an exception."""
        from openprogram.runtime.execution.run_control import get_current_execution_id
        execution = get_current_execution_id() or ""
        token = _VIEWER.set(f"{session}/{execution}" if execution else session)
        try:
            if _access_disabled():
                from openprogram.runtime.permissions import refusal
                return BrowserError("page_access_disabled", refusal.page_access().text).result()
            return self._run(action, args, session)
        finally:
            _VIEWER.reset(token)

    def _hold(self, tab: Tab) -> bool:
        """Hold the tab for this action; True when the caller releases it (outside a list)."""
        held = _HELD.get()
        if held is not None and tab.lock in held:
            return False
        deadline = time.monotonic() + TAB_WAIT_SECONDS
        while not tab.lock.acquire(timeout=0.25):
            _check_cancelled()
            if time.monotonic() >= deadline:
                raise BrowserError("tab_busy", f"another agent has been acting on tab {tab.id} for "
                                   f"{TAB_WAIT_SECONDS:g}s; try again, or open a tab of your own")
        if held is not None:
            held.append(tab.lock)
            return False
        return True

    def _run(self, action: str, args: dict[str, Any], session: str) -> dict[str, Any]:
        try:
            if action in video.ACTIONS:
                return video.run(action, args, session)
            if action == "tabs":
                return self._list(session, bool(args.get("all")))
            if action == "open":
                return self._open(args, session)
            tab = self._tab(args.get("tab"), session)
            for attempt in (1, 2):
                release = self._hold(tab)
                try:
                    return self._dispatch(tab, action, args)
                except BrowserError as exc:
                    # tab_gone is raised only before any input was sent, so trying again sends nothing twice.
                    if exc.reason_code != "tab_gone" or attempt == 2:
                        raise
                finally:
                    if release:
                        tab.lock.release()
                # The tab may have moved to another window, or the App restarted.
                self._refresh()
                tab = self._find(tab.id)
                if tab is None:
                    raise BrowserError("tab_closed", "that tab is closed",
                                       tabs=[item.row() for item in self._refresh()])
        except BrowserError as exc:
            return exc.result()
        except pa.ActionError as exc:
            return {"ok": False, "reason_code": exc.reason_code, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "reason_code": "browser_error",
                    "error": f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"}
        raise AssertionError("unreachable")

    def _dispatch(self, tab: Tab, action: str, args: dict[str, Any]) -> dict[str, Any]:
        if action == "close":
            return self._close(tab)
        if action in _LOOKS:
            if action == "screenshot":
                result = self._screenshot(tab)
            elif action == "wait":
                result = self._wait(tab, args)
            else:
                result = self._on_owner(self._operate, tab, action, args)
            self._publish(tab, result)
            self._report_look(tab, action, result)
            return result
        # The preview follows the write, and shows the page as operated, before its input is sent.
        tab.aim = {}
        self._publish(tab, {"ok": True})
        operation = self._report_operation(tab, action, args, "dispatched")
        outcome: dict[str, Any] = {"ok": False, "reason_code": "browser_error"}
        try:
            # A file chooser the page opens while the agent acts is held for upload(path) instead of
            # opening a window on the user's screen; the user's own clicks still open it.
            caught = self._on_owner(self._catch_choosers, tab)
            try:
                result = self._on_owner(self._operate, tab, action, args)
                if isinstance(result, _Acted):
                    result = self._after(tab, result)
            finally:
                self._on_owner(self._release_choosers, tab, caught)
            # An upload answers the chooser its own click opened.
            if caught.get("chooser") is not None and action != "upload" and result.get("ok", True):
                tab.chooser = caught["chooser"]
                result["file_chooser"] = caught["detail"]
            self._publish(tab, result)
            outcome = result
            return result
        except BrowserError as exc:
            outcome = exc.result()
            raise
        except pa.ActionError as exc:
            outcome = {"ok": False, "reason_code": exc.reason_code}
            raise
        finally:
            failed = outcome.get("ok") is False
            self._report_operation(tab, action, args, "failed" if failed else "acknowledged",
                                   operation_id=operation, result=outcome)

    def _catch_choosers(self, tab: Tab) -> dict[str, Any]:
        caught: dict[str, Any] = {}
        try:
            page = self._page(tab)
        except BrowserError:
            # The action itself reports a tab that is gone.
            return caught

        def hold(chooser) -> None:
            try:
                accept = chooser.element.get_attribute("accept") or ""
            except Exception:
                accept = ""
            caught.update(chooser=chooser, detail={
                "multiple": chooser.is_multiple(), **({"accept": accept} if accept else {})})
        caught["listener"] = hold
        page.on("filechooser", hold)
        return caught

    def _release_choosers(self, tab: Tab, caught: dict[str, Any]) -> None:
        try:
            if "listener" in caught:
                self._page(tab).remove_listener("filechooser", caught["listener"])
        except Exception:
            # Teardown: the page may already be closed, taking its listener with it.
            _log.debug("filechooser listener not removed tab=%s", tab.id, exc_info=True)

    def _after(self, tab: Tab, acted: "_Acted") -> dict[str, Any]:
        """The result of an action already sent: never an error that would send it again."""
        try:
            self._settle(tab, acted)
            result = {**self._on_owner(self._report, tab, acted.before_url), "done": acted.done}
        except BrowserError as exc:
            if exc.reason_code != "tab_gone":
                return {"ok": True, "tab": tab.id, "done": acted.done, "note": f"reading the page after it failed: {exc}"}
            return {"ok": True, "tab": tab.id, "done": acted.done,
                    "note": "the tab closed or left the App right after the action; tabs lists the tabs now"}
        except Exception as exc:
            return {"ok": True, "tab": tab.id, "done": acted.done,
                    "note": f"reading the page after it failed ({type(exc).__name__}); read shows it"}
        if self._created != acted.created:
            opened = self._opened(tab)
            if opened:
                result["opened"] = opened
        return result

    def _opened(self, tab: Tab) -> list[dict[str, Any]]:
        """Tabs the action opened (a link to a new window): listed, and left for the agent to choose."""
        with self._lock:
            before = set(self._tabs)
        try:
            listed = self._refresh()
        except BrowserError:
            return []
        # Only what this tab opened: a tab with no opener may be one of the user's the driver had not listed yet.
        new = [item for item in listed if (item.window_id, item.tab_id) not in before
               and item.opener_tab_id == tab.tab_id]
        for item in new:
            _note_opened(item)
        return [item.row() for item in new]

    def _wait(self, tab: Tab, args: dict[str, Any]) -> dict[str, Any]:
        """Until a text is on the page, or for some seconds, without holding the browser for others."""
        seconds = max(0.0, min(float(args.get("seconds") or (10 if args.get("text") else 1)), 30.0))
        text = str(args.get("text") or "")
        deadline = time.monotonic() + seconds
        found = False
        while True:
            found = bool(text) and self._on_owner(self._shows, tab, text)
            if found or time.monotonic() >= deadline:
                break
            _check_cancelled()
            time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
        state = self._on_owner(self._report, tab, None)
        if text and not found:
            raise BrowserError("text_not_found", f"{text!r} did not appear within {seconds:g}s", **state)
        return {**state, "done": f"{text!r} is on the page" if text else f"waited {seconds:g}s"}

    def _settle(self, tab: Tab, acted: "_Acted") -> None:
        """Wait for what an action started: a new document, then its requests (2 s at most)."""
        deadline = time.monotonic() + 0.3
        documents, busy = self._on_owner(self._activity, tab, acted.pending)
        while documents == acted.documents and time.monotonic() < deadline:
            time.sleep(0.05)
            documents, busy = self._on_owner(self._activity, tab, acted.pending)
        if documents != acted.documents:
            deadline = time.monotonic() + ACTION_TIMEOUT_MS / 1000
            while not self._on_owner(self._loaded, tab) and time.monotonic() < deadline:
                _check_cancelled()
                time.sleep(0.1)
        deadline = time.monotonic() + 2
        while busy and time.monotonic() < deadline:
            _check_cancelled()
            time.sleep(0.05)
            _, busy = self._on_owner(self._activity, tab, acted.pending)

    def _refresh(self, *, every_window: bool = False) -> list[Tab]:
        """The tabs there are now: the App's, and the driver's own. No App is no tabs of its, not a failure."""
        from openprogram.host.resources import background_browser
        from openprogram.runtime.conversation import surface_context
        own = self._on_owner(self._own_surfaces) if self._own_pages else []
        try:
            wait = self._app_may_come_back()
            captured = (surface_context.capture_pages_for_listing(wait=wait) if every_window
                        else surface_context.capture_pages_for_listing(surface_context.current(), wait=wait))
        except surface_context.DesktopUnavailableError as exc:
            # An App that is running and does not answer is a failure to report; one that is
            # not running has no tabs, and the driver's own browser is where the next opens.
            if not own and (wait or not background_browser.available()):
                raise BrowserError("app_unavailable", str(exc) or "the OpenProgram App is not connected") from None
            captured = {}
        alive = {str(surface["tab_id"]) for surface in own}
        with self._lock:
            for key in [key for key in self._tabs if key[0] == OWN_WINDOW and key[1] not in alive]:
                self._tabs.pop(key, None)
        return [self._remember(surface) for surface in [*(captured.get("surfaces") or []), *own]
                if isinstance(surface, dict) and surface.get("window_id") and surface.get("tab_id")]

    def _app_may_come_back(self) -> bool:
        """Whether a listing waits for the App: it is running, so a missing window is a restart or a reconnect."""
        if os.environ.get("OPENPROGRAM_IN_AGENTIC_SUBPROCESS") == "1":
            return True
        from openprogram.programs.applications.browser.app_endpoint import desktop_app_ws_url
        return bool(desktop_app_ws_url())

    def _remember(self, surface: dict[str, Any]) -> Tab:
        key = (str(surface["window_id"]), str(surface["tab_id"]))
        target = str(surface.get("target_id") or "")
        with self._lock:
            tab = self._tabs.get(key)
            if tab is None:
                # A tab moved to another window keeps its page, and its id with it.
                moved = next((item for item in self._tabs.values() if target and item.target_id == target), None)
                if moved is not None:
                    _move_opened((moved.window_id, moved.tab_id), key)
                    self._tabs.pop((moved.window_id, moved.tab_id), None)
                    moved.window_id, moved.tab_id = key
                    tab = self._tabs[key] = moved
                else:
                    taken = {item.id for item in self._tabs.values()}
                    tab = self._tabs[key] = Tab(id=_tab_name(*key, taken), window_id=key[0], tab_id=key[1])
        binding = str(surface.get("binding_id") or "")
        if binding and tab.binding_id and binding != tab.binding_id:
            # Each listing binds the page again; the binding it replaces is not used any more.
            from openprogram.host.resources.desktop_link import release_binding
            release_binding(tab.binding_id)
        tab.binding_id = binding or tab.binding_id
        tab.page_key = str(surface.get("page_key") or tab.page_key)
        tab.connection_generation = int(surface.get("connection_generation") or tab.connection_generation or 0)
        if target and tab.target_id and target != tab.target_id:
            tab.forget_page()
        tab.target_id = target or tab.target_id
        tab.opener_tab_id = str(surface.get("opener_tab_id") or tab.opener_tab_id)
        tab.title = str(surface.get("title") or "")
        tab.origin = str(surface.get("origin") or "")
        tab.visible = bool(surface.get("visible"))
        tab.focused = bool(surface.get("focused"))
        if not tab.target_id and tab.binding_id:
            from openprogram.host.resources.desktop_link import request_bound_tab
            tab.target_id = str(request_bound_tab(tab.binding_id).get("target_id") or "")
        return tab

    def _find(self, name: str) -> Tab | None:
        with self._lock:
            return next((tab for tab in self._tabs.values() if tab.id == name), None)

    def _current(self, session: str) -> str:
        """The tab this agent used last, else the one its conversation used last."""
        return self._last.get(_VIEWER.get(), "") or self._last.get(session, "")

    def _use(self, tab: Tab, session: str) -> None:
        self._used_at[(tab.window_id, tab.tab_id)] = time.time()
        self._last[session] = tab.id
        if _VIEWER.get():
            self._last[_VIEWER.get()] = tab.id

    def tab(self, name: Any, session: str) -> Tab:
        """The tab a conversation names, by the rules its actions follow; ``BrowserError`` when there is none."""
        return self._tab(name, session)

    def _tab(self, name: Any, session: str) -> Tab:
        """The named tab, else the one this agent used last, else its conversation's, else the focused one.

        A turn no window is attached to stops before the focused one: that tab is whatever the user is doing."""
        from openprogram.runtime.agent.turn_request import get_turn_request
        from openprogram.runtime.permissions.identity import detached_from_window

        detached = detached_from_window(get_turn_request())
        wanted = str(name or "").strip()
        tab = self._find(wanted or self._current(session))
        if tab is None:
            listed = self._refresh()
            tab = self._find(wanted) if wanted else (
                self._find(self._current(session))
                or (None if detached else (
                    next((item for item in listed if item.focused), None)
                    or next((item for item in listed if item.visible), None)
                    or (listed[0] if listed else None))))
            if tab is None:
                message = (f"there is no tab {wanted}" if wanted else
                           "the tab in front is the user's and this turn has none of its own; open(url) opens one "
                           "behind their work, or name one from tabs(all=true)" if detached else
                           "no tab is open; open(url) opens one")
                raise BrowserError("tab_not_found", message, tabs=[item.row() for item in listed])
        self._use(tab, session)
        return tab

    def _list(self, session: str, everything: bool = False) -> dict[str, Any]:
        """The tabs this conversation opened and the one it is on; ``everything`` adds the user's and others'."""
        every = self._refresh()
        current = self._current(session)
        opened = _opened_by(_conversation(session))
        listed = every if everything else [
            tab for tab in every if (tab.window_id, tab.tab_id) in opened or tab.id == current]
        urls = self._on_owner(self._urls, listed)
        return {"ok": True, "other_tabs": len(every) - len(listed),
                "tabs": [{**tab.row(urls.get(tab.id, "")), **({"current": True} if tab.id == current else {})}
                         for tab in listed]}

    def _keep_within_limit(self, new: Tab, session: str) -> int:
        """After one more tab opened, close the conversation's least recently used ones over its limit.

        Whatever opens pages over and over (a monitor that never closes one, a
        Goal that runs for days) ends up holding the limit and no more. Only
        tabs this turn can list are counted and closed; the new tab and a tab
        in front of the user stay. Failing to tidy never fails the open."""
        from .pages import BrowserResourceStore
        conversation = _conversation(session) or str(_VIEWER.get() or "").split("/", 1)[0]
        if not conversation:
            return 0
        try:
            opened = BrowserResourceStore().opened_tab_times(conversation)
            if len(opened) <= TABS_PER_CONVERSATION:
                return 0
            live = {(tab.window_id, tab.tab_id): tab for tab in self._refresh()}
        except Exception:  # noqa: BLE001 - the page is open; tidying waits for the next one
            _log.warning("could not count the tabs of conversation %s", conversation, exc_info=True)
            return 0
        held = [key for key in opened if key in live]
        spare = sorted((key for key in held if key != (new.window_id, new.tab_id)
                        and not live[key].focused and not live[key].visible),
                       key=lambda key: self._used_at.get(key, opened[key]))
        closed = 0
        for key in spare[:max(0, len(held) - TABS_PER_CONVERSATION)]:
            try:
                self._close(live[key])
                closed += 1
            except BrowserError:
                _log.warning("could not close tab %s to keep within the limit", live[key].id)
        return closed

    def _open(self, args: dict[str, Any], session: str) -> dict[str, Any]:
        from openprogram.runtime.conversation import surface_context
        url = _http_url(args.get("url"))
        opened = surface_context.open_page(url, background=not args.get("visible"))
        surfaces = opened.get("surfaces") or []
        own = opened.get("reason_code") == "desktop_unavailable"
        if own:
            # No App window to open it in: the driver's own browser, which never fails for want of a window.
            surfaces = [self._on_owner(self._own_open, url)]
        elif opened.get("ok") is False or not surfaces:
            raise BrowserError(str(opened.get("reason_code") or "open_failed"),
                               str(opened.get("error") or f"the App did not open {url}"))
        tab = self._remember(surfaces[0])
        made_room = 0
        self._use(tab, session)
        if own or surfaces[0].get("agent_owned") is True:
            _note_opened(tab, session)
            made_room = self._keep_within_limit(tab, session)
        result = self._on_owner(self._operate, tab, "read", {"wait_for_load": True})
        if own and result.get("ok") is not False:
            result["note"] = _OWN_NOTE
        if made_room and result.get("ok") is not False:
            result["note"] = " ".join(filter(None, [result.get("note"), _ROOM_NOTE.format(count=made_room)]))
        self._publish(tab, result)
        # The tab just opened is the one the conversation's preview follows now.
        self._report_look(tab, "open", result)
        return result

    def _close(self, tab: Tab) -> dict[str, Any]:
        if tab.window_id == OWN_WINDOW:
            self._on_owner(self._own_close, tab.tab_id)
        else:
            self._close_in_app(tab)
        from .pages import BrowserResourceStore
        BrowserResourceStore().forget_opened_tab(tab.window_id, tab.tab_id)
        with self._lock:
            self._tabs.pop((tab.window_id, tab.tab_id), None)
        self._used_at.pop((tab.window_id, tab.tab_id), None)
        for agent, name in list(self._last.items()):
            if name == tab.id:
                self._last.pop(agent, None)
        return {"ok": True, "tab": tab.id, "done": "closed the tab"}

    def _close_in_app(self, tab: Tab) -> None:
        from openprogram.host.resources.desktop_link import request_close_tab
        from openprogram.host.resources.desktop_link import RESPONSE_TIMEOUT_REASON_CODE
        closed = request_close_tab(tab.binding_id) if tab.binding_id else {"reason_code": "page_context_stale"}
        if closed.get("reason_code") == RESPONSE_TIMEOUT_REASON_CODE:
            raise BrowserError("close_uncertain", "the App did not answer whether it closed the tab; tabs shows "
                               "whether it is still open")
        if closed.get("reason_code") == "page_context_stale":
            raise BrowserError("tab_gone", "the tab's binding expired")
        if not closed.get("ok"):
            raise BrowserError("close_failed", str(closed.get("error") or "the App did not close the tab"))

    def _screenshot(self, tab: Tab) -> dict[str, Any]:
        """The tab as the user would see it, captured by the App without bringing it forward."""
        written = tab.last_write()
        image, url, viewport = self._on_owner(self._capture, tab)
        self._record_shot(tab, image, url, viewport, written)
        return {"ok": True, "tab": tab.id, "done": "took a screenshot; x,y of pointer actions are its pixels",
                "_images": [image]}

    def _capture(self, tab: Tab) -> tuple[bytes, str, list[int]]:
        """The tab's image, by the App or else by Playwright, with its url and viewport; on the owner thread."""
        from openprogram.host.resources.desktop_link import request_bound_screenshot
        if tab.window_id == OWN_WINDOW:
            image = self._page(tab).screenshot(scale="css", timeout=5000)
            url, viewport = self._viewport(tab)
            return image, url, viewport
        captured = request_bound_screenshot(tab.binding_id) if tab.binding_id else {}
        data = str(captured.get("image_data_url") or "")
        if captured.get("ok") and data.startswith("data:image/png;base64,"):
            image = base64.b64decode(data.split(",", 1)[1])
        elif captured.get("reason_code") == "page_context_stale" or not tab.binding_id:
            raise BrowserError("tab_gone", "the tab's binding expired")
        else:
            image = self._page(tab).screenshot(scale="css", timeout=5000)
        url, viewport = self._viewport(tab)
        return image, url, viewport

    def _record_shot(self, tab: Tab, image: bytes, url: str, viewport: list[int], written: int) -> None:
        """Keep the screenshot the agent's next x,y refer to."""
        width, scroll_x, scroll_y = viewport
        pixels = _png_width(image)
        tab.shots[_VIEWER.get()] = ((float(width) / pixels) if pixels and width else 1.0, written, url,
                                    int(scroll_x), int(scroll_y), image)

    def _viewport(self, tab: Tab) -> tuple[str, list[int]]:
        page = self._page(tab)
        return page.url, page.evaluate(_VIEWPORT)

    def _publish(self, tab: Tab, result: dict[str, Any]) -> None:
        """Show the conversation which tab it is operating."""
        if not tab.page_key or not result.get("ok"):
            return
        try:
            from openprogram.programs.applications.browser.pages import publish_bound_page
            from openprogram.host.resources.desktop_link import binding_page_descriptor
            # The store keeps a display only from the connection that owns the Page now; a
            # binding that is gone, or now names another Page, publishes no display. A
            # spawned program has no binding table and keeps the generation it was listed with.
            descriptor = binding_page_descriptor(tab.binding_id) if tab.binding_id else {}
            if descriptor:
                generation = (int(descriptor.get("connection_generation") or 0)
                              if descriptor.get("page_key") == tab.page_key else 0)
            else:
                generation = tab.connection_generation
            publish_bound_page(tab.page_key, window_id=tab.window_id, tab_id=tab.tab_id,
                               connection_generation=generation,
                               title=str(result.get("title") or tab.title), target=str(result.get("url") or ""))
        except Exception:
            _log.debug("browser tab publish failed tab=%s", tab.id, exc_info=True)

    def _report_operation(self, tab: Tab, action: str, args: dict[str, Any], phase: str, *,
                          operation_id: str = "", result: dict[str, Any] | None = None,
                          follow: bool = True) -> str:
        """Tell the conversation's preview what the agent does on the tab; never fails the action.

        A write reports ``dispatched`` before its input is sent, then ``acknowledged`` or ``failed``
        under the same operation id; a click's outcome carries where it landed. Returns the id.
        """
        operation_id = operation_id or f"op_{os.urandom(6).hex()}"
        if not tab.page_key:
            return operation_id
        try:
            from openprogram.runtime.execution.run_control import (
                get_current_execution_id,
            )
            from openprogram.programs.applications.browser.pages import (
                report_browser_operation, sanitize_operation,
            )
            receipt = {key: value for key, value in (result or {"ok": True}).items() if key in ("ok", "reason_code")}
            if tab.aim.get("point"):
                receipt["point"] = tab.aim["point"]
            # A click's x,y are screenshot pixels, not the viewport's: only the aim places the marker.
            arguments = {key: value for key, value in args.items() if key not in ("x", "y")}
            operation = sanitize_operation(
                action=action, arguments=arguments, result=receipt, phase=phase,
                operation_id=operation_id, viewport=tab.aim.get("viewport"),
            )
            report_browser_operation(tab.page_key, operation, follow=follow and phase != "failed",
                                     execution_id=get_current_execution_id())
        except Exception:
            _log.debug("browser operation report failed tab=%s action=%s", tab.id, action, exc_info=True)
        return operation_id

    def _report_look(self, tab: Tab, action: str, result: dict[str, Any]) -> None:
        """A read the agent made: the preview follows it, and the page is not marked operated."""
        if result.get("ok") is False:
            return
        tab.aim = {}
        self._report_operation(tab, action, {}, "acknowledged", follow=False)

    # -- the owner thread: Playwright ---------------------------------------

    def _on_owner(self, function, *args):
        return self._owner.submit(contextvars.copy_context().run, function, *args).result()

    def _connect(self) -> None:
        if self._browser is not None and self._browser.is_connected():
            return
        self._disconnect()
        from openprogram.programs.applications.browser.app_endpoint import attach_to_app, desktop_app_ws_url
        endpoint = desktop_app_ws_url()
        if not endpoint:
            raise BrowserError("app_unavailable", "the OpenProgram App's browser is not reachable")
        try:
            self._browser = attach_to_app(self._started_playwright(), endpoint)
        except Exception as exc:
            self._disconnect()
            raise BrowserError("app_unavailable", f"cannot reach the App's browser ({type(exc).__name__})") from None
        try:
            targets = self._browser.new_browser_cdp_session()

            def created(event: dict) -> None:
                if (event.get("targetInfo") or {}).get("type") in {"page", "webview"}:
                    self._created += 1
            targets.on("Target.targetCreated", created)
            targets.send("Target.setDiscoverTargets", {"discover": True})
        except Exception:
            # Target discovery only feeds the new-tab counter; tabs work without it.
            _log.debug("browser target discovery unavailable", exc_info=True)

    def _started_playwright(self):
        """One Playwright for the thread: it serves the App's connection and the driver's own browser."""
        if self._playwright is None:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
        return self._playwright

    def _disconnect(self) -> None:
        """Let go of the App. Playwright stays while the driver's own browser runs on it."""
        steps = [getattr(self._browser, "close", None)]
        self._browser = None
        if self._own is None:
            steps.append(getattr(self._playwright, "stop", None))
            self._playwright = None
        for close in steps:
            try:
                if close:
                    close()
            except Exception:
                # Teardown: the browser or Playwright driver may already be gone.
                _log.debug("browser disconnect step failed", exc_info=True)
        self._pages.clear()
        self._known.clear()
        for target in [target for target in self._watches if not target.startswith("own:")]:
            self._watches.pop(target, None)

    # -- the owner thread: the driver's own browser -------------------------

    def _own_start(self) -> None:
        if self._own is not None and self._own.is_connected():
            return
        from openprogram.host.resources import background_browser
        self._own_forget()
        try:
            self._own = background_browser.launch(self._started_playwright())
            self._own_context = self._own.new_context()
        except Exception as exc:
            self._own_forget()
            raise BrowserError(
                "browser_unavailable",
                "no OpenProgram App window is available and no browser could be started in the background "
                f"({type(exc).__name__}); open the App, or install Chrome") from None
        self._own_context.on("page", self._own_adopt)

    def _own_adopt(self, page) -> str:
        """Give a page of the driver's own browser its tab id; a page an action opened counts as new."""
        for tab_id, known in self._own_pages.items():
            if known is page:
                return tab_id
        tab_id = os.urandom(6).hex()
        self._own_pages[tab_id] = page
        self._created += 1
        return tab_id

    def _own_open(self, url: str) -> dict[str, Any]:
        self._own_start()
        page = self._own_context.new_page()
        tab_id = self._own_adopt(page)
        try:
            page.goto(url, wait_until="commit", timeout=3 * ACTION_TIMEOUT_MS)
        except Exception as exc:
            self._own_close(tab_id)
            raise BrowserError("open_failed", f"could not open {url}: {_script_failure(exc)}") from None
        self._own_touch()
        return self._own_surface(tab_id, page)

    def _own_surface(self, tab_id: str, page) -> dict[str, Any]:
        opener = ""
        try:
            parent = page.opener()
            opener = next((key for key, known in self._own_pages.items() if known is parent), "")
            title = page.title()
        except Exception:
            title = ""
        parsed = urlparse(page.url)
        return {"window_id": OWN_WINDOW, "tab_id": tab_id, "target_id": f"own:{tab_id}", "title": title,
                "origin": f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else page.url,
                "opener_tab_id": opener, "visible": False, "focused": False}

    def _own_surfaces(self) -> list[dict[str, Any]]:
        for tab_id in [tab_id for tab_id, page in self._own_pages.items() if page.is_closed()]:
            self._own_pages.pop(tab_id, None)
            self._watches.pop(f"own:{tab_id}", None)
        return [self._own_surface(tab_id, page) for tab_id, page in list(self._own_pages.items())]

    def _own_close(self, tab_id: str) -> None:
        page = self._own_pages.pop(tab_id, None)
        self._watches.pop(f"own:{tab_id}", None)
        try:
            if page is not None and not page.is_closed():
                page.close()
        except Exception:
            _log.debug("own browser tab did not close tab=%s", tab_id, exc_info=True)
        if not self._own_pages:
            self._own_stop()

    def _own_forget(self) -> None:
        self._own = self._own_context = None
        self._own_pages.clear()
        for target in [target for target in self._watches if target.startswith("own:")]:
            self._watches.pop(target, None)

    def _own_stop(self) -> None:
        """Close the driver's own browser; its tabs are gone with it, and their records."""
        browser = self._own
        gone = list(self._own_pages)
        self._own_forget()
        if self._own_timer is not None:
            self._own_timer.cancel()
            self._own_timer = None
        try:
            if browser is not None:
                browser.close()
        except Exception:
            _log.debug("own browser did not close", exc_info=True)
        if self._browser is None and self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                _log.debug("playwright did not stop", exc_info=True)
            self._playwright = None
        if gone:
            try:
                from .pages import BrowserResourceStore
                store = BrowserResourceStore()
                for tab_id in gone:
                    store.forget_opened_tab(OWN_WINDOW, tab_id)
            except Exception:
                _log.debug("own browser tab records not forgotten", exc_info=True)

    def _own_touch(self) -> None:
        """A tab of the driver's own browser was used: it stays for ``OWN_IDLE_SECONDS`` more."""
        self._own_used = time.monotonic()
        if self._own_timer is None:
            self._own_wake(OWN_IDLE_SECONDS)

    def _own_wake(self, seconds: float) -> None:
        self._own_timer = threading.Timer(seconds, lambda: self._owner.submit(self._own_sweep))
        self._own_timer.daemon = True
        self._own_timer.start()

    def _own_sweep(self) -> None:
        self._own_timer = None
        if self._own is None:
            return
        left = OWN_IDLE_SECONDS - (time.monotonic() - self._own_used)
        if left > 0:
            self._own_wake(left)
        else:
            self._own_stop()

    def _own_page(self, tab: Tab):
        page = self._own_pages.get(tab.tab_id)
        if page is None or page.is_closed():
            raise BrowserError("tab_gone", "the tab is closed")
        if tab.target_id not in self._watches:
            self._watch(page, tab.target_id)
        self._own_touch()
        return page

    def _page(self, tab: Tab):
        if tab.window_id == OWN_WINDOW:
            return self._own_page(tab)
        self._connect()
        page = self._pages.get(tab.target_id)
        if page is None or page.is_closed():
            page = self._scan(tab.target_id)
        if page is None:
            # A connection does not always learn of tabs the App opens after it: connect again.
            self._disconnect()
            self._connect()
            page = self._scan(tab.target_id)
        if page is None:
            raise BrowserError("tab_gone", "the tab is no longer in the App")
        if tab.target_id not in self._watches:
            self._watch(page, tab.target_id)
        return page

    def _scan(self, target_id: str):
        for context in self._browser.contexts:
            for candidate in context.pages:
                if id(candidate) not in self._known:
                    target = _target_id(candidate)
                    if target:
                        self._known[id(candidate)] = target
                        self._pages[target] = candidate
        page = self._pages.get(target_id)
        return page if page is not None and not page.is_closed() else None

    def _watch(self, page, target: str) -> None:
        watch = self._watches[target] = _Watch()
        page.set_default_timeout(ACTION_TIMEOUT_MS)
        try:
            # A background tab fires no focus events, so inputs that open on focus stay shut.
            # The emulation lasts as long as this session and covers this tab only.
            watch.focus = page.context.new_cdp_session(page)
            watch.focus.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
        except Exception:
            _log.debug("focus emulation unavailable for a tab", exc_info=True)

        def keep(items: list, item) -> None:
            items.append(item)
            del items[:-_LOG_LIMIT]

        def request(item) -> None:
            if item.is_navigation_request() and item.frame == page.main_frame:
                watch.documents += 1
            elif item.resource_type in {"fetch", "xhr", "document"}:
                watch.pending.add(item)

        def settled(item, error: str = "") -> None:
            watch.pending.discard(item)
            status = 0 if error else _response_status(item)
            keep(watch.network, {"method": item.method, "url": item.url, "type": item.resource_type,
                                 "status": status, **({"error": error} if error else {})})

        page.on("console", lambda message: keep(watch.console, f"[{message.type}] {message.text}"))
        page.on("pageerror", lambda error: keep(watch.console, f"[error] {error}"))
        page.on("request", request)
        page.on("requestfinished", settled)
        page.on("requestfailed", lambda item: settled(item, item.failure or "failed"))

    def _urls(self, tabs: list[Tab]) -> dict[str, str]:
        urls = {}
        for tab in tabs:
            try:
                urls[tab.id] = self._page(tab).url
            except BrowserError:
                pass
        return urls

    def _snapshot(self, page, tab: Tab, *, full: bool) -> tuple[str | None, str | None]:
        """The whole page (when asked for, or when its document is new) and the changes since the last one.

        Playwright 1.60 serves the AI snapshot as ``Frame.ariaSnapshot(mode="ai")``, one text that is
        incremental when ``track`` has an earlier snapshot of the same document; earlier versions
        serve ``Page.snapshotForAI``, which returns both."""
        from openprogram.programs.applications.browser.privacy import password_values, redact_password_values
        impl = page._impl_obj
        viewer = _VIEWER.get()
        track = f"{_TRACK}:{viewer}" if viewer else _TRACK
        documents = self._watches[tab.target_id].documents if tab.target_id in self._watches else 0
        new_document = tab.documents.get(viewer) != documents
        if "mode" in inspect.signature(impl.aria_snapshot).parameters:
            frame = impl._main_frame

            def aria(params: dict[str, Any]) -> str:
                return str(page._sync(frame._channel.send("ariaSnapshot", frame._timeout, {"mode": "ai", **params}))
                           or "")
            tracked = aria({"track": track})
            whole, changes = (tracked, None) if new_document else ((aria({}) if full else None), tracked)
        else:
            raw = page._sync(impl._channel.send_return_as_dict(
                "snapshotForAI", impl._timeout_settings.timeout, {"track": track}))
            whole, changes = str(raw.get("full") or ""), raw.get("incremental")
            changes = None if new_document or changes is None else str(changes)
        tab.documents[viewer] = documents
        if whole is not None:
            tab.roles = {}
        tab.roles.update({ref: (role, name.replace('\\"', '"'))
                          for role, name, ref in _REF_LINE.findall(whole if whole is not None else changes or "")})
        secrets = password_values(page)
        return (None if whole is None else redact_password_values(whole, secrets),
                None if changes is None else redact_password_values(changes, secrets))

    def _state(self, page, tab: Tab, *, full: bool, before_url: str | None = None) -> dict[str, Any]:
        whole, changes = self._snapshot(page, tab, full=full)
        url = page.url
        if whole is None and (changes is None or (before_url is not None and url != before_url)):
            whole, _ = self._snapshot(page, tab, full=True)
        try:
            title = page.title()
        except Exception:
            title = tab.title
        out: dict[str, Any] = {"ok": True, "tab": tab.id, "url": url, "title": title}
        tab.title = out["title"] or tab.title
        viewer = _VIEWER.get()
        others = sum(1 for sequence, agent in tab.writes if agent != viewer and sequence > tab.seen.get(viewer, 0))
        if others:
            out["note"] = (f"another agent acted on this tab {others} time(s) since you last looked; "
                           "what it changed is in this result")
        tab.seen[viewer] = tab.writes[-1][0] if tab.writes else 0
        if whole is not None and (full or changes is None or (before_url is not None and url != before_url)):
            out["page"] = _cut(whole)
        else:
            out["changes"] = _cut(changes or "")
        return out

    def _locate(self, page, tab: Tab, args: dict[str, Any], prefix: str = ""):
        """The element named by ref, or by role and name; its locator and how to call it."""
        ref = str(args.get(f"{prefix}ref") or "").strip().lstrip("@").removeprefix("ref=")
        role = str(args.get(f"{prefix}role") or "").strip()
        name = str(args.get(f"{prefix}name") or "")
        if ref:
            if not tab.roles:
                self._snapshot(page, tab, full=True)
            locator = page.locator(f"aria-ref={ref}")
            if locator.count() == 1:
                return locator, ref
            role, name = tab.roles.get(ref, (role, name))
        if role:
            locator = page.get_by_role(role, name=name, exact=True) if name else page.get_by_role(role)
            count = locator.count()
            if count == 1:
                return locator, f'{role} "{name}"' if name else role
            if count > 1 and not ref:
                raise BrowserError("ambiguous_element", f'{count} elements are {role} "{name}"; use a ref')
        if not ref and not role:
            raise BrowserError("target_required", "name the element with ref (or role and name)")
        raise BrowserError("element_gone", f"{ref or role} is not on the page any more; the page as it is now follows",
                           **self._state(page, tab, full=True))

    def _activity(self, tab: Tab, pending: set) -> tuple[int, bool]:
        """The tab's document count, and whether requests started after ``pending`` are still running."""
        page = self._page(tab)
        page.wait_for_timeout(1)  # lets Playwright deliver the page's events
        watch = self._watches[tab.target_id]
        return watch.documents, bool(watch.pending - pending)

    def _loaded(self, tab: Tab) -> bool:
        try:
            self._page(tab).wait_for_load_state("domcontentloaded", timeout=100)
            return True
        except Exception:
            return False

    def _shows(self, tab: Tab, text: str) -> bool:
        try:
            return self._page(tab).get_by_text(text).count() > 0
        except Exception:
            return False

    def _report(self, tab: Tab, before_url: str | None) -> dict[str, Any]:
        return self._state(self._page(tab), tab, full=False, before_url=before_url)

    def _operate(self, tab: Tab, action: str, args: dict[str, Any]) -> dict[str, Any] | _Acted:
        """A read, or an action done; the caller waits for an action to settle, then reports."""
        page = self._page(tab)
        watch = self._watches[tab.target_id]
        if action == "read":
            if args.get("wait_for_load"):
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=ACTION_TIMEOUT_MS)
                except Exception:
                    # A slow load is still read; the state shows what has rendered.
                    _log.debug("load wait ended before read tab=%s", tab.id, exc_info=True)
            return self._state(page, tab, full=not args.get("changes_only"))
        if action in ("console", "network"):
            return self._logs(tab, watch, action, args)
        if action == "evaluate":
            from openprogram.programs.applications.browser.privacy import password_values, redact_password_values
            source = pa.evaluate_source(args)
            # Naming an element hands it to a function script as its argument.
            on = self._locate(page, tab, args)[0] if args.get("ref") or args.get("role") else page
            try:
                value = on.evaluate(source)
            except Exception as exc:
                # The script may have changed the page before it failed.
                tab.writes.append((next(_writes), _VIEWER.get()))
                raise BrowserError("script_error", f"the script failed: {_script_failure(exc)}") from None
            # A script may change the page like any action.
            tab.writes.append((next(_writes), _VIEWER.get()))
            return {"ok": True, "tab": tab.id,
                    "result": redact_password_values(pa.evaluation_text(value), password_values(page))}
        if action != "upload":
            # The page has moved on from a file chooser the agent did not answer, and from the
            # file fields listed when a button opened none.
            tab.chooser, tab.file_fields = None, {}
        before_url, documents, pending, created = page.url, watch.documents, set(watch.pending), self._created
        try:
            done = self._act(page, tab, action, args)
        except (BrowserError, pa.ActionError):
            raise
        except Exception as exc:
            # Playwright may have sent the input before failing: count it as a write, and never send it again.
            tab.writes.append((next(_writes), _VIEWER.get()))
            if page.is_closed():
                raise BrowserError("tab_closed", f"the tab closed during {action}; it may have taken effect") from None
            message = str(exc).split("\n", 1)[0]
            if "Timeout" in type(exc).__name__ or "Timeout" in message:
                message += "; it may have taken effect, so check the page below before trying again"
            raise BrowserError("action_failed", f"{action} failed: {message}",
                               **self._state(page, tab, full=False, before_url=before_url)) from None
        tab.writes.append((next(_writes), _VIEWER.get()))
        del tab.writes[:-50]
        return _Acted(done=done, before_url=before_url, documents=documents, pending=pending, created=created)

    def _act(self, page, tab: Tab, action: str, args: dict[str, Any]) -> str:
        if action == "navigate":
            url = _http_url(args.get("url"))
            page.goto(url, wait_until="domcontentloaded")
            return f"opened {url}"
        if action in ("back", "forward", "reload"):
            go = {"back": page.go_back, "forward": page.go_forward, "reload": page.reload}[action]
            response = go(wait_until="domcontentloaded")
            if response is None and action != "reload":
                return f"there is no page to go {action} to"
            return {"back": "went back", "forward": "went forward", "reload": "reloaded"}[action]
        if action == "press":
            key = str(args.get("key") or "").strip()
            if not key:
                raise pa.ActionError("key_required", "press needs key, such as Enter or Control+A")
            if args.get("ref") or args.get("role"):
                locator, label = self._locate(page, tab, args)
                locator.press(key)
                return f"pressed {key} on {label}"
            page.keyboard.press(key)
            return f"pressed {key}"
        if action == "scroll":
            dx, dy = pa.scroll_delta(args)
            if args.get("ref") or args.get("role"):
                locator, label = self._locate(page, tab, args)
                moved = locator.evaluate(pa.SCROLL_SCRIPT, {"dx": dx, "dy": dy})
            else:
                moved = page.evaluate(f"(o) => ({pa.SCROLL_SCRIPT.strip()})(document.documentElement, o)",
                                      {"dx": dx, "dy": dy})
                label = "the page"
            return f"scrolled {label} by ({dx}, {dy})px" + ("" if (moved or {}).get("moved") else "; it did not move")
        if action == "click" and args.get("x") is not None and not args.get("ref") and not args.get("role"):
            return self._click_point(page, tab, args)
        if action == "drag":
            source, label = self._locate(page, tab, args)
            target, to_label = self._locate(page, tab, args, prefix="to_")
            source.drag_to(target)
            return f"dragged {label} to {to_label}"
        if action == "upload" and args.get("field") is not None:
            return self._upload_to_field(tab, args)
        if action == "upload" and not args.get("ref") and not args.get("role"):
            return self._answer_chooser(tab, args)
        if action in ("type", "paste") and not args.get("ref") and not args.get("role"):
            return self._type_into(page, None, "the focused element", action, args)
        locator, label = self._locate(page, tab, args)
        if action == "click":
            click = pa.click_options(args)
            self._aim(page, tab, locator)
            self._show_cursor(page, locator)
            locator.click(**pa.mouse_options(click), **({"modifiers": click["modifiers"]} if click["modifiers"] else {}))
            words = {1: "clicked", 2: "double-clicked", 3: "triple-clicked"}[click["count"]]
            return f"{words} {label}" + (f" with the {click['button']} button" if click["button"] != "left" else "")
        if action == "hover":
            locator.hover()
            return f"hovered {label}"
        if action == "select":
            values = args.get("value")
            values = [str(item) for item in values] if isinstance(values, list) else [str(values or "")]
            if not any(values):
                raise pa.ActionError("value_required", "select needs value, the option's value or label")
            chosen = locator.select_option(values)
            return f"selected {', '.join(chosen) or 'nothing'} in {label}"
        if action in ("type", "paste"):
            return self._type_into(page, locator, label, action, args)
        if action == "upload":
            from .file_inputs import file_payload
            payload, problem = file_payload(args.get("path"))
            if problem:
                raise BrowserError(problem["reason_code"], problem["message"])
            field = locator.evaluate_handle(FILE_INPUT_SCRIPT).as_element()
            if field is not None:
                field.set_input_files(payload)
                return f"chose {payload['name']} in {label}"
            # Pages hide the real input behind a styled button: click it as a person would.
            try:
                with page.expect_file_chooser(timeout=CHOOSER_WAIT_MS) as chosen:
                    self._show_cursor(page, locator)
                    locator.click()
            except Exception as exc:
                if "Timeout" not in type(exc).__name__ and "Timeout" not in str(exc):
                    raise
                raise BrowserError("no_file_chooser", self._no_chooser_message(page, tab, locator, label)) from None
            chosen.value.set_files(payload)
            return f"clicked {label} and chose {payload['name']} in the file chooser it opened"
        raise BrowserError("unknown_action", f"unknown browser action {action!r}")

    @staticmethod
    def _no_chooser_message(page, tab: Tab, locator, label: str) -> str:
        """Why nothing opened, and the page's file fields for upload(field=n)."""
        try:
            state = locator.evaluate(ELEMENT_STATE_SCRIPT)
        except Exception:
            state = {}
        try:
            fields = locator.evaluate(FILE_FIELDS_SCRIPT)
        except Exception:
            fields = []
        shown = [f"it reads {state['text']!r}"] if state.get("text") else []
        if state.get("disabled"):
            shown.append("it is disabled")
        message = f"clicked {label}{' (' + '; '.join(shown) + ')' if shown else ''}, but it opened no file chooser"
        usable = [field for field in fields if not field.get("disabled")]
        tab.file_fields = {n: page.locator('input[type="file"]').nth(field["index"])
                           for n, field in enumerate(usable, 1)}
        if not usable:
            return message + (" and the page has no file field it could take. If the page is stuck in an earlier "
                              "upload, reload it or remove that file first")
        rows = []
        for n, fld in enumerate(usable, 1):
            facts = [f"#{fld['id']}" if fld.get("id") else "", f"name={fld['name']}" if fld.get("name") else "",
                     f"accept={fld['accept']}" if fld.get("accept") else "",
                     "multiple" if fld.get("multiple") else "",
                     f"near {fld['near']!r}" if fld.get("near") else "",
                     "the only one in the button's section" if fld.get("beside") else ""]
            rows.append(f"  field {n}: " + ", ".join(fact for fact in facts if fact))
        return (message + ". If the page is stuck in an earlier upload, reload it or remove that file first. "
                "Otherwise put the file straight into the field it belongs to with upload(field=n, path):\n"
                + "\n".join(rows))

    def _upload_to_field(self, tab: Tab, args: dict[str, Any]) -> str:
        from .file_inputs import file_payload
        try:
            number = int(args.get("field"))
        except (TypeError, ValueError):
            number = 0
        field = (tab.file_fields or {}).get(number)
        if field is None:
            raise BrowserError("no_file_field", "no such file field: field=n names one listed by the last upload "
                                                "whose button opened no file chooser")
        payload, problem = file_payload(args.get("path"))
        if problem:
            raise BrowserError(problem["reason_code"], problem["message"])
        field.set_input_files(payload)
        return f"chose {payload['name']} in file field {number}"

    def _answer_chooser(self, tab: Tab, args: dict[str, Any]) -> str:
        from .file_inputs import file_payload
        chooser, tab.chooser = tab.chooser, None
        if chooser is None:
            raise BrowserError("no_file_chooser", "no file chooser is open: click the page's upload button first, "
                                                  "or pass the ref of the button or the file field")
        payload, problem = file_payload(args.get("path"))
        if problem:
            tab.chooser = chooser
            raise BrowserError(problem["reason_code"], problem["message"])
        chooser.set_files(payload)
        return f"chose {payload['name']} in the file chooser"

    def _type_into(self, page, locator, label: str, action: str, args: dict[str, Any]) -> str:
        text = args.get("text")
        if not isinstance(text, str):
            raise pa.ActionError("text_required", f"{action} needs text")
        if action == "paste":
            payload = pa.paste_payload(args)
            target = locator.element_handle() if locator is not None else None
            result = page.evaluate(f"([e, o]) => ({pa.PASTE_SCRIPT.strip()})(e, o)", [target, payload])
            if not (result or {}).get("ok"):
                raise BrowserError("not_editable", f"{label} does not take text")
            return f"pasted {len(text)} character(s) into {label}"
        if locator is None:
            page.keyboard.insert_text(text)
        elif args.get("clear") is False:
            locator.focus()
            page.keyboard.insert_text(text)
        else:
            locator.fill(text)
        done = f"typed {len(text)} character(s) into {label}"
        if args.get("submit"):
            (locator.press if locator is not None else page.keyboard.press)("Enter")
            done += " and pressed Enter"
        return done

    def _click_point(self, page, tab: Tab, args: dict[str, Any]) -> str:
        shot = tab.shots.get(_VIEWER.get())
        if shot is None:
            raise BrowserError("screenshot_required", "x,y are pixels of a screenshot; take one first, or use a ref")
        scale, written, url, scroll_x, scroll_y, image = shot
        point = (float(args["x"]), float(args.get("y") or 0))
        _, now_x, now_y = page.evaluate(_VIEWPORT)
        if page.url != url or (now_x, now_y) != (scroll_x, scroll_y):
            raise self._stale(tab, "the page moved or scrolled since your screenshot, so its x,y point elsewhere now")
        if tab.last_write() != written:
            # Acted on since the screenshot: the point may still show the same thing (typing elsewhere, a
            # key that did nothing) or something else (a menu opened, a list scrolled). Its pixels decide.
            fresh = self._capture(tab)
            if not _same_around(image, fresh[0], point, max(1, int(POINT_RADIUS_PX / scale))):
                raise self._stale(tab, "what your screenshot showed at that point has changed since", fresh)
        click = pa.click_options(args)
        x, y = point[0] * scale, point[1] * scale
        self._aim(page, tab, None, (x, y))
        for key in click["modifiers"]:
            page.keyboard.down(key)
        try:
            page.mouse.click(x, y, **pa.mouse_options(click))
        finally:
            for key in reversed(click["modifiers"]):
                page.keyboard.up(key)
        return f"clicked ({args['x']}, {args.get('y')}) of the screenshot"

    def _stale(self, tab: Tab, why: str, fresh: tuple[bytes, str, list[int]] | None = None) -> BrowserError:
        """The refusal of a click by x,y, with a new screenshot so that the next one needs no extra call."""
        written = tab.last_write()
        try:
            image, url, viewport = fresh or self._capture(tab)
        except Exception:
            _log.debug("no new screenshot for a stale click tab=%s", tab.id, exc_info=True)
            return BrowserError("screenshot_stale", f"{why}; take a new screenshot, or use a ref")
        self._record_shot(tab, image, url, viewport, written)
        return BrowserError("screenshot_stale", f"{why}. A new screenshot is attached, and x,y of a click are its "
                            "pixels now; or use a ref", _images=[image])

    def _aim(self, page, tab: Tab, locator, point: tuple[float, float] | None = None) -> None:
        """Note where a click lands (the element's centre once in view) for the preview's marker."""
        try:
            if locator is not None:
                locator.scroll_into_view_if_needed(timeout=2000)
                box = locator.bounding_box(timeout=2000)
                if not box:
                    return
                point = (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            if point is not None:
                tab.aim = {"point": {"x": float(point[0]), "y": float(point[1])},
                           "viewport": page.evaluate(_VIEWPORT_SIZE)}
        except Exception:
            _log.debug("browser click aim unavailable tab=%s", tab.id, exc_info=True)

    def _show_cursor(self, page, locator) -> None:
        """The App's agent cursor, drawn where the click lands."""
        try:
            page.evaluate(_AGENT_CURSOR_SCRIPT, True)
            locator.evaluate(_CURSOR_AT_SCRIPT)
        except Exception:
            # The cursor overlay is cosmetic; the click proceeds without it.
            _log.debug("agent cursor not drawn", exc_info=True)

    def _logs(self, tab: Tab, watch: _Watch, action: str, args: dict[str, Any]) -> dict[str, Any]:
        pattern = str(args.get("pattern") or "")
        limit = max(1, min(int(args.get("limit") or 50), _LOG_LIMIT))
        if action == "console":
            lines = [line for line in watch.console
                     if (not args.get("only_errors") or line.startswith("[error]")) and pattern in line]
            return {"ok": True, "tab": tab.id, "console": lines[-limit:]}
        rows = [row for row in watch.network if pattern in row["url"]
                and (not args.get("only_errors") or row.get("error") or row["status"] >= 400)]
        return {"ok": True, "tab": tab.id, "network": rows[-limit:]}


def _conversation(session: str = "") -> str:
    """The conversation a tab belongs to: the one running now, else the caller's."""
    from openprogram.runtime.execution.run_control import get_current_session_id
    return session or get_current_session_id() or ""


def _note_opened(tab: Tab, session: str = "") -> None:
    from .pages import BrowserResourceStore
    conversation = _conversation(session) or str(_VIEWER.get() or "").split("/", 1)[0]
    if conversation:
        from openprogram.runtime.agent.turn_request import get_turn_request
        from openprogram.runtime.execution.run_control import get_current_execution_id
        from openprogram.runtime.permissions.identity import ends_with_its_run
        # Only a run that starts clean and ends with its answer is named: its pages close with it.
        run = (get_current_execution_id() or "") if ends_with_its_run(get_turn_request()) else ""
        BrowserResourceStore().note_tab_opened(tab.window_id, tab.tab_id, conversation, run)


def _move_opened(old: tuple[str, str], new: tuple[str, str]) -> None:
    from .pages import BrowserResourceStore
    BrowserResourceStore().move_opened_tab(old, new)


def _opened_by(conversation: str) -> set[tuple[str, str]]:
    from .pages import BrowserResourceStore
    return BrowserResourceStore().tabs_opened_by(conversation) if conversation else set()


def close_conversation_tabs(conversation: str) -> int:
    """Close every tab this conversation's agents opened; the user's own tabs stay. Returns how many closed."""
    from .pages import BrowserResourceStore
    opened = _opened_by(conversation)
    if not opened:
        return 0
    worker = driver()
    listed = worker._refresh(every_window=True)
    live = {(tab.window_id, tab.tab_id): tab for tab in listed}
    closed = 0
    for key in opened & set(live):
        try:
            worker._close(live[key])
            closed += 1
        except BrowserError:
            _log.warning("could not close tab %s of conversation %s", live[key].id, conversation)
    # A tab the user already closed needs no record; one in a window that is offline now keeps it.
    windows = {window for window, _ in live}
    store = BrowserResourceStore()
    for key in opened - set(live):
        # A tab of a browser the driver started is gone with the process that started it.
        if key[0] in windows or key[0] == OWN_WINDOW:
            store.forget_opened_tab(*key)
    return closed


def close_run_tabs(execution: str) -> int:
    """Close the tabs one run opened, once the run is over. Returns how many closed.

    A scheduled run and a check-in start clean and end with their answer, so
    nothing they opened is of use to the next one. Only those runs are named
    on a tab's record (``_note_opened``), so calling this for any execution
    that ended is safe. A tab of a browser the driver started in the run's own
    process went with that process; its record goes here."""
    from .pages import BrowserResourceStore
    store = BrowserResourceStore()
    opened = store.tabs_opened_in(execution)
    if not opened:
        return 0
    worker = driver()
    live = {(tab.window_id, tab.tab_id): tab for tab in worker._refresh(every_window=True)}
    closed = 0
    for key in opened & set(live):
        try:
            worker._close(live[key])
            closed += 1
        except BrowserError:
            _log.warning("could not close tab %s of run %s", live[key].id, execution)
    windows = {window for window, _ in live}
    for key in opened - set(live):
        if key[0] in windows or key[0] == OWN_WINDOW:
            store.forget_opened_tab(*key)
    return closed


def close_run_tabs_soon(execution: str) -> None:
    """Ending a run never waits for the App to close its tabs."""
    def run() -> None:
        try:
            close_run_tabs(execution)
        except Exception:
            _log.warning("closing the tabs of run %s failed", execution, exc_info=True)
    if execution:
        threading.Thread(target=run, name="close-run-tabs", daemon=True).start()


def close_conversation_tabs_soon(conversation: str) -> None:
    """Archiving or deleting a conversation never waits for the App to close its tabs."""
    def run() -> None:
        try:
            close_conversation_tabs(conversation)
        except Exception:
            _log.warning("closing the tabs of conversation %s failed", conversation, exc_info=True)
    if conversation:
        threading.Thread(target=run, name="close-conversation-tabs", daemon=True).start()


_driver: Driver | None = None
_driver_lock = threading.Lock()


def _forget_in_child() -> None:
    """A forked workflow subprocess starts its own driver: the owner thread and the connection stay the parent's."""
    global _driver, _driver_lock
    _driver, _driver_lock = None, threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_forget_in_child)


def acting_on(page_key: str) -> bool:
    """Whether an agent's action is running on the Page now (its tab is held), without starting a driver."""
    current = _driver
    if current is None or not page_key:
        return False
    with current._lock:
        held = [tab for tab in current._tabs.values() if tab.page_key == page_key]
    return any(tab.lock.locked() for tab in held)


def driver() -> Driver:
    global _driver
    with _driver_lock:
        if _driver is None:
            _driver = Driver()
        return _driver


ACTIONS = ("tabs", "open", "read", "click", "type", "paste", "select", "press", "hover", "scroll", "drag",
           "upload", "navigate", "back", "forward", "reload", "wait", "screenshot", "console", "network",
           "evaluate", "close", *video.ACTIONS)

__all__ = ["ACTIONS", "BrowserError", "Driver", "Tab", "acting_on", "batch", "close_conversation_tabs",
           "close_conversation_tabs_soon", "close_run_tabs", "close_run_tabs_soon", "driver"]
