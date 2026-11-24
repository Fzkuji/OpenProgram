"""Targets ``browser chrome``, ``browser safari``, ``browser firefox``, ...: the user's own browser, with their
tabs and sign-ins.

They are opened by the ``built_in`` browser kind of this computer
(``built_in_browser.py``), one for each of the user's browsers that can be
reached (``extension.installed_browsers``): a Chromium browser whose
Playwright Extension is installed, Safari on macOS, Firefox where it is
installed. Listing reads files and never asks whether a browser is
connected now. A call reaches the browser through the browser application's
adapter (``extension.connect``): one Playwright MCP connection per
conversation and Chromium browser, AppleScript for Safari, the WebDriver
BiDi agent for Firefox. All three answer the same ``tabs``, ``select`` and
``run``, so everything below is the same for each.

Tabs. Playwright MCP numbers the tabs it attached, and the numbers shift
when one closes, so every tab gets an id here (``tk3fa``) that stays the
same across calls: the connection stamps each page with a key, and the key
names the id. When the extension connects again, its pages carry new keys;
a new key takes over the id of a page no longer seen at the same address,
so the ids survive reconnecting. A number or an unknown id answers
``BAD_ARGUMENT:`` with the open tabs. Each agent has its own current tab
here: a step without ``tab`` acts on the tab that agent used last, at first
on the tab the extension has selected; once that tab is gone (closed, or its
id lost on reconnecting) such a step answers ``BAD_ARGUMENT:`` rather than
act on another of the user's tabs. Selecting a tab and acting on it are one
turn of the conversation's connection, so two agents never interleave.

Locks. A tab's id is drawn by each conversation's connection, so the same
tab has another id in another conversation, and a step without ``tab`` names
none: a call holds one key for the whole browser, which keeps a second call
off any tab of it, in this process or another.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import logging
import sys
import threading
import time
from typing import Any

from openprogram.programs._host.execution import ToolReturn

from .. import errors, locks, vocabulary
from ..contract import Approval, Call, Described, Provider, Row, Steps
from .built_in_browser import shortcut_line, with_include

_log = logging.getLogger(__name__)

_CAPABILITIES = frozenset({"pixels", "tree", "pointer", "hover", "keyboard", "tabs", "script", "upload"})
# What each engine has (``catalog.py``): Safari's page scripts cannot choose a file.
_ENGINE_CAPABILITIES = {"chromium": _CAPABILITIES, "firefox": _CAPABILITIES, "safari": _CAPABILITIES - {"upload"}}
# The actions only OpenProgram's own browser has.
_BUILT_IN_ONLY = ("list_panes", "start_video_recording", "stop_video_recording")
# The actions an engine lacks besides those: Safari keeps its console and requests to itself.
_ENGINE_LACKS = {"safari": ("upload_file", "read_console_messages", "read_network_requests")}

_BAD = {"ambiguous_element", "target_required", "invalid_arguments", "url_required", "text_required",
        "key_required", "value_required", "script_required", "script_too_long", "invalid_modifiers",
        "invalid_mouse_button", "invalid_click_count", "invalid_amount", "invalid_direction", "invalid_format",
        "invalid_seconds", "invalid_value"}

# Ids of pages no longer seen, kept to give back to a page that comes back at the same address.
_GONE_KEPT = 50


class _Unsent(Exception):
    """The connection broke while a step was choosing its tab: the step itself sent nothing."""


@dataclass
class _Table:
    """One conversation's tab ids in one browser."""
    ids: dict[str, str] = field(default_factory=dict)
    urls: dict[str, str] = field(default_factory=dict)
    # The ids in the latest listing: a gone tab keeps its id for a reconnect, but no window is claimed for it.
    present: set[str] = field(default_factory=set)


_guard = threading.Lock()
_tables: dict[tuple[str, str], _Table] = {}
# (conversation, browser, agent) -> the id of the tab that agent used last.
_current: dict[tuple[str, str, str], str] = {}
# A key's lock is never dropped: a call may be waiting on it, and a fresh one would let two calls hold the turn.
_turns: dict[tuple[str, str], threading.Lock] = {}
# (conversation, browser) -> the agents that have used the connection; it ends with the last of them.
_users: dict[tuple[str, str], set[str]] = {}


def _turn(conversation: str, browser: str) -> threading.Lock:
    with _guard:
        return _turns.setdefault((conversation, browser), threading.Lock())


def _table(conversation: str, browser: str) -> _Table:
    with _guard:
        return _tables.setdefault((conversation, browser), _Table())


def _name(browser: str, key: str, taken: set[str]) -> str:
    digest = base64.b32encode(hashlib.sha1(f"{browser}\0{key}".encode()).digest()).decode().lower()
    return next(f"t{digest[:size]}" for size in range(4, len(digest)) if f"t{digest[:size]}" not in taken)


def identify(table: _Table, browser: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The listed tabs with their ids and their numbers in Playwright MCP's list."""
    present = {str(row.get("key") or "") for row in rows}
    taken = set(table.ids.values())
    gone = {tab: table.urls.get(tab, "") for key, tab in table.ids.items() if key not in present}
    out = []
    for index, row in enumerate(rows):
        key, url = str(row.get("key") or ""), str(row.get("url") or "")
        tab = table.ids.get(key)
        if tab is None:
            same = [old for old, seen in gone.items() if seen and seen == url]
            if len(same) == 1:
                tab = same[0]
                gone.pop(tab)
                for old_key in [old_key for old_key, old in table.ids.items() if old == tab]:
                    table.ids.pop(old_key)
            else:
                tab = _name(browser, key, taken)
            table.ids[key] = tab
            taken.add(tab)
        table.urls[tab] = url
        out.append({**row, "id": tab, "index": index})
    stale = [key for key, tab in table.ids.items() if key not in present and tab in gone][:-_GONE_KEPT or None]
    for key in stale:
        table.urls.pop(table.ids.pop(key), None)
    table.present = {row["id"] for row in out}
    return out


def _in_background(target, *args) -> None:
    threading.Thread(target=target, args=args, name="user-browser-close", daemon=True).start()


class UserBrowser(Provider):
    """Target ``browser <name>``: ``name`` is the browser as ``app`` names it (``chrome``, ``safari``, ...)."""

    family = "browser"
    state_scope = "conversation"

    def __init__(self, browser, name: str) -> None:
        self.browser = browser
        self.name = name
        self.app = name
        self.label = self.extension.label(name)
        self.engine = self.extension.engine(name)
        self.capabilities = _ENGINE_CAPABILITIES[self.engine]

    @property
    def extension(self):
        return self.browser.extension

    # -- what the model reads -------------------------------------------------

    def describe(self) -> Described:
        return Described(f"the user's own {self.label} with their sign-ins; only when the user names it or the task "
                         "needs their sign-in.")

    def note(self) -> str:
        head = (f'Note: type="browser", app="{self.app}" is the user\'s own {self.label} on this computer, with their '
                "tabs and sign-ins, reached through {how}. Use it only when the user names it or the task needs their "
                "signed-in state; otherwise leave app out for OpenProgram's own browser. A tab's id from list_tabs "
                "(such as tk3fa) stays the same across calls; a step without tab acts on the tab you used last here, "
                "at first the tab {first}; once that tab is gone otherwise than by your close_tab, such a step "
                "answers BAD_ARGUMENT until you pass title. close_tab closes only a tab the call names. Here "
                "get_state always returns the whole page, never only what changed, and every other step returns the "
                "page after it. ")
        if self.engine == "safari":
            return head.format(how="AppleScript", first="in front in Safari's front window") + (
                "Safari runs these scripts only while Develop → \"Allow JavaScript from Apple Events\" is on; a call "
                "that answers PERMISSION_MISSING says where the user turns it on. Safari draws only the front tab of "
                "a window, so a tab behind others has no screenshot: create_tab opens a tab of your own in a Safari "
                "window of its own, behind the user's front application (visible=true opens it in front instead). "
                "Clicks, typing and keys are page events: typing, Return (submits a form), Tab, Backspace and select "
                "all do what they do in Safari; other shortcuts reach only the page, and nothing can be held down. It "
                "lacks list_panes and video recording, upload_file, read_console_messages and "
                "read_network_requests. " + shortcut_line())
        if self.engine == "firefox":
            return head.format(how="Firefox's remote agent (WebDriver BiDi)", first="in front in Firefox") + (
                "OpenProgram starts Firefox with the user's profile and the agent on when it is closed; when it is "
                "open without the agent, a call answers PERMISSION_MISSING with what the user does. The address bar "
                "shows stripes while the agent is on. Clicks, keys, hovers and drags are real input on the tab shown "
                "in its window, and page events on a tab behind others. upload_file names the file field as element "
                "(an input of type file); console messages and requests are those since OpenProgram connected. It "
                "lacks list_panes and video recording and upload_file(field). " + shortcut_line())
        return head.format(how="the Playwright Extension", first="the extension has selected") + (
            f"Without a token set for the extension, the first call may show the user a page in {self.label} that "
            "asks which tab to share. It lacks list_panes and video recording, upload_file(field), holding a key, "
            "long presses except a single click by x,y, drags that mix an element with a point, clicks by x,y with "
            "modifiers, pattern and limit for read_console_messages, and only_errors and limit for "
            "read_network_requests. " + shortcut_line())

    def extra_actions(self) -> tuple[str, ...]:
        lacks = (*_BUILT_IN_ONLY, *_ENGINE_LACKS.get(self.engine, ()))
        return tuple(name for name in vocabulary.FAMILIES["browser"].extras if name not in lacks)

    # -- operating ------------------------------------------------------------

    def opening(self, subject: dict) -> Steps:
        return [("list_tabs", {})]

    def list(self, kind: str, subject: dict, args: dict, call: Call) -> ToolReturn:
        if kind != "tabs":
            raise errors.NotSupported(f"{self.address()} has nothing to list as {kind}; it lists tabs")
        return self._run(subject, [("list_tabs", args)], call)

    def get_state(self, subject: dict, options: dict, call: Call) -> ToolReturn:
        return self._run(subject, [("get_state", with_include(options, call))], call)

    def act(self, subject: dict, steps: Steps, call: Call) -> ToolReturn:
        return self._run(subject, steps, call)

    def _run(self, subject: dict, steps: Steps, call: Call) -> ToolReturn:
        turn = _turn(call.conversation, self.name)
        waited = time.monotonic() + locks.WAIT_SECONDS
        while not turn.acquire(timeout=0.25):
            call.check()
            if time.monotonic() >= waited:
                raise errors.ScreenBusy(f"another agent has been operating {self.address()} for {locks.WAIT_SECONDS:g} s; "
                                        "nothing was sent. Try again when it is done.")
        try:
            with _guard:
                _users.setdefault((call.conversation, self.name), set()).add(call.agent)
            return self._steps(subject, steps, call)
        finally:
            turn.release()

    def _steps(self, subject: dict, steps: Steps, call: Call) -> ToolReturn:
        """The steps, in the conversation's turn at this browser."""
        from openprogram.host._platform import front

        with front.kept(self._operated(call)):
            session = self._session(call)
            texts, images, data, done = [], [], [], []
            batch = len(steps) > 1
            for number, (action, step) in enumerate(steps, 1):
                try:
                    call.check()
                except errors.Timeout:
                    error = errors.Timeout(f"the call ran out of time; steps that took effect: "
                                           f"{', '.join(done) or 'none'}.")
                    error.sent = bool(done)
                    raise error from None
                try:
                    text, shots, result, failed = self._step(session, action, {**subject, **step}, call)
                except _Unsent as exc:
                    raise self._lost(exc, "", done, number if batch else 0) from None
                except self.extension.Lost as exc:
                    raise self._lost(exc, action, done, number if batch else 0) from None
                except self.extension.Unavailable as exc:
                    # The browser refused before the step reached a page (Safari's scripting is off).
                    error = self._unavailable(exc)
                    if batch:
                        error = type(error)(f"step {number} ({action}): {error} Steps done: "
                                            f"{', '.join(done) or 'none'}.")
                    error.sent = bool(done)
                    raise error from None
                except errors.CallError as exc:
                    if not batch:
                        raise
                    error = type(exc)(f"step {number} ({action}): {exc} Steps done: {', '.join(done) or 'none'}.")
                    error.sent = exc.sent or bool(done)
                    raise error from None
                images.extend(shots)
                data.append(result)
                texts.append(f"[{number}] {action}: {text}" if batch else text)
                if failed:
                    if batch:
                        texts.append(f"step {number} failed; the steps after it did not run. {len(done)} of "
                                     f"{len(steps)} steps done.")
                    return ToolReturn(text="\n".join(texts), images=images if call.images else [],
                                      json_data=data if batch else result, is_error=True)
                done.append(f"{number}. {action}")
            if batch:
                texts.append(f"{len(done)} of {len(steps)} steps done.")
            return ToolReturn(text="\n".join(texts), images=images if call.images else [],
                              json_data=data if batch else data[0] if data else None)

    def _session(self, call: Call):
        """The conversation's connection to this browser; reaching it sends nothing."""
        try:
            return self.extension.connect(call.conversation, self.name)
        except self.extension.Unavailable as exc:
            raise self._unavailable(exc) from None

    @staticmethod
    def _unavailable(exc) -> errors.CallError:
        """A browser that could not be reached: what the user turns on, what this computer lacks, or neither."""
        if getattr(exc, "permission", ""):
            return errors.PermissionMissing(f"{exc}. {exc.permission} Nothing was sent.")
        if exc.missing:
            return errors.DependencyMissing(f"{exc}. {exc.missing}")
        return errors.MachineUnreachable(f"{exc}. Nothing was sent.")

    def _lost(self, exc: Exception, action: str, done: list[str], number: int) -> errors.CallError:
        """A broken connection: nothing sent when the step only reads or had not acted yet (``action`` empty)."""
        where = f"step {number} ({action or 'choosing its tab'}): " if number else ""
        progress = f" Steps done: {', '.join(done) or 'none'}." if number else ""
        if not action or action in vocabulary.READS:
            error: errors.CallError = errors.MachineUnreachable(f"{where}{exc}. Nothing was sent.{progress}")
            error.sent = bool(done)
            return error
        return errors.OutcomeUnknown(f"{where}{exc}. {action} may or may not have taken effect: read the state "
                                     f"before deciding, and do not repeat it.{progress}")

    # -- one step -------------------------------------------------------------

    def _step(self, session, action: str, args: dict, call: Call) -> tuple[str, list[bytes], Any, bool]:
        named = str(args.pop("tab", "") or "").strip()
        table = _table(call.conversation, self.name)
        row = None
        if action not in ("list_tabs", "create_tab"):
            # Choosing the tab sends nothing to a page; a connection that breaks here leaves the step unsent.
            try:
                rows = identify(table, self.name, session.tabs())
                if action != "close_tab":
                    row = self._select(session, table, named, call, rows)
                elif named:
                    row = self._find(rows, named)
            except self.extension.Lost as exc:
                raise _Unsent(str(exc)) from exc
        if action == "list_tabs":
            rows = identify(table, self.name, session.tabs())
            current = _current.get(self._agent(call))
            chosen, gone = self._chosen(rows, current)
            return self._listing(rows, chosen, gone), [], {"ok": True, "tabs": _public(rows, chosen)}, False
        if action == "create_tab":
            result = session.run("create_tab", args)
            self._raise(result, action)
            rows = identify(table, self.name, session.tabs())
            opened = next((row for row in rows if row.get("current")), None)
            if opened is not None:
                with _guard:
                    _current[self._agent(call)] = opened["id"]
            return self._answer(result, opened, "opened")
        if action == "close_tab":
            if row is None:
                raise errors.BadArgument("close_tab closes only a tab the call names: pass title, an id from list_tabs.")
            result = session.run("close_tab", {"index": row["index"]})
            self._raise(result, action)
            with _guard:
                for key in [key for key, tab in table.ids.items() if tab == row["id"]]:
                    table.ids.pop(key)
                table.urls.pop(row["id"], None)
                table.present.discard(row["id"])
                # Only the caller's own tab is forgotten: another agent that used it gets BAD_ARGUMENT naming it
                # rather than act on whichever tab the browser selects next.
                if _current.get(self._agent(call)) == row["id"]:
                    _current.pop(self._agent(call))
            return f"closed tab {row['id']} · {row.get('title') or '(untitled)'} · {row.get('url') or ''}", [], \
                {"ok": True, "tab": row["id"]}, False
        result = session.run(action, args)
        self._raise(result, action)
        return self._answer(result, row, "")

    def _agent(self, call: Call) -> tuple[str, str, str]:
        return call.conversation, self.name, call.agent

    def _select(self, session, table: _Table, named: str, call: Call, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """The tab a step acts on, made the one Playwright MCP's tools act on."""
        if named:
            row = self._find(rows, named)
        else:
            mine = _current.get(self._agent(call))
            if mine:
                # The agent's tab is gone: acting on whichever tab the extension has selected could reach
                # another of the user's pages, so the agent names one.
                row = next((item for item in rows if item["id"] == mine), None)
                if row is None:
                    raise errors.BadArgument(f"tab {mine}, the one you used last here, is no longer open or lost "
                                             f"its id when the extension reconnected; pass title. "
                                             f"{self._open_tabs(rows)}")
            else:
                row = next((item for item in rows if item.get("current")), None)
            if row is None:
                raise errors.BadArgument(f"{self._no_tab()}; create_tab(url) opens one.")
        if not row.get("current") and not session.select(row["index"], str(row.get("url") or "")):
            raise errors.BadArgument(f"tab {row['id']} could not be selected: the tabs changed while it was "
                                     f"chosen. {self._open_tabs(identify(table, self.name, session.tabs()))}")
        with _guard:
            _current[self._agent(call)] = row["id"]
        return row

    def _find(self, rows: list[dict[str, Any]], named: str) -> dict[str, Any]:
        row = next((item for item in rows if item["id"] == named), None)
        if row is None:
            # A call may name a tab by its title: the one tab whose title holds it.
            titled = [item for item in rows if named.casefold() in str(item.get("title") or "").casefold()]
            row = titled[0] if len(titled) == 1 else None
            if len(titled) > 1:
                raise errors.BadArgument(f"title {named!r} matches {len(titled)} tabs; name one by its id. "
                                         f"{self._open_tabs(rows)}")
        if row is None:
            what = "a number" if named.lstrip("-").isdigit() else "not an open tab"
            raise errors.BadArgument(f"tab {named!r} is {what}: title is an id from list_tabs or a tab's title. "
                                     f"{self._open_tabs(rows)}")
        return row

    @staticmethod
    def _open_tabs(rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "No tab is open."
        return "Open tabs: " + ", ".join(f"{row['id']} ({row.get('title') or row.get('url') or 'untitled'})"
                                         for row in rows) + "."

    @staticmethod
    def _chosen(rows: list[dict[str, Any]], current: str | None) -> tuple[str | None, str | None]:
        """The tab a step without ``tab`` acts on, as ``_select`` chooses it, and the agent's tab when it is gone."""
        if current is not None:
            if any(row["id"] == current for row in rows):
                return current, None
            return None, current
        return next((row["id"] for row in rows if row.get("current")), None), None

    def _listing(self, rows: list[dict[str, Any]], chosen: str | None, gone: str | None) -> str:
        if not rows:
            said = self._no_tab()
            return f"{said[0].upper()}{said[1:]}. create_tab(url) opens one."
        head = (f"tab {gone}, the one you used last, is gone; pass title on every call" if gone else
                "the current one is used when title is omitted")
        return f"Tabs of {self.label} ({head}):\n" + "\n".join(
            f"- {row['id']}{' (current)' if row['id'] == chosen else ''}: {row.get('title') or '(untitled)'} — "
            f"{row.get('url') or ''}" for row in rows)

    def _no_tab(self) -> str:
        if self.engine == "chromium":
            return f"no tab of {self.label} is shared with the extension"
        return f"no tab of {self.label} is open"

    def _answer(self, result: dict[str, Any], row: dict[str, Any] | None,
                verb: str) -> tuple[str, list[bytes], Any, bool]:
        images = result.pop("_images", [])
        said = str(result.get("result") or result.get("message") or "")
        failed = result.get("ok") is False
        lines = []
        if row is not None:
            url = self.extension.ExtensionBrowser.page_url(said) or str(row.get("url") or "")
            title = self.extension.ExtensionBrowser.page_title(said) or str(row.get("title") or "")
            lines.append(f"{verb + ' ' if verb else ''}tab {row['id']} · {title or '(untitled)'} · {url}")
            result["tab"] = row["id"]
        if "found" in result:
            found = result.pop("found")
            lines.append("found:\n" + ("\n".join(found) if found else "(nothing on the page matches)"))
        elif said:
            lines.append(said)
        return "\n".join(lines), images, result, failed

    def _raise(self, result: dict[str, Any], action: str) -> None:
        """The coded failure a result stands for; a failure without a code is answered in its own words."""
        said = str(result.get("result") or result.get("message") or result.get("error") or "")
        if result.get("status") == "observation_required":
            if result.get("action_outcome") == "not_dispatched":
                raise errors.ElementGone(said)
            raise errors.OutcomeUnknown(said)
        if result.get("ok") is not False:
            return
        code = str(result.get("reason_code") or "")
        if code == "element_gone":
            raise errors.ElementGone(said)
        if code in ("unsupported_action", "unsupported_option"):
            raise errors.NotSupported(said)
        if code == "permission_missing":
            raise errors.PermissionMissing(said)
        if code in _BAD:
            raise errors.BadArgument(said)

    # -- permission -----------------------------------------------------------

    def approval(self, subject: dict, steps: Steps) -> Approval | None:
        """The user's own browser holds their sign-ins: one grant covers what is done in it."""
        if any(action == "upload_file" for action, _ in steps):
            return Approval(f"computer_use uploads a local file to a website in your {self.label}", self.target)
        return Approval(f"computer_use controls your {self.label}", self.target)

    def authority(self, steps: Steps, code: bool) -> str:
        return "process.exec"

    def runs_page_script(self, steps: Steps) -> bool:
        return any(action == "evaluate" for action, _ in steps)

    def lock(self, subject: dict, steps: Steps) -> locks.Locks:
        return locks.Locks(exclusive=(locks.tab_key("local", self.target, "*"),))

    # -- showing --------------------------------------------------------------

    def resource(self, subject: dict) -> Row | None:
        return Row("desktop", self.label)

    def claims(self, window: dict[str, Any], conversation: str = "") -> str:
        """Only a page this conversation's connection has attached: any other tab is out of this target's
        reach and stays with ``app``. A window without its address (Linux, Windows) is not claimed."""
        bundle = str(window.get("bundle_id") or window.get("bundle") or "")
        name = str(window.get("app") or window.get("name") or window.get("process") or "").strip().lower()
        if bundle not in self.extension.bundles(self.name) and name not in self.extension.programs(self.name):
            return ""
        url = str(window.get("url") or "").strip()
        if not url:
            return ""
        with _guard:
            table = _tables.get((conversation, self.name))
            # A step's identify() changes the table outside the guard, so a listed tab's address may just have gone.
            attached = table is not None and url in {table.urls.get(tab) for tab in table.present}
        if attached:
            return (f"the web page in this window is the user's {self.label}; list_tabs names its tabs, and the tab "
                    "strip, toolbar and menus stay here")
        return ""

    def close(self, call: Call) -> None:
        """The agent ended: forget its current tab; the conversation's connection ends with the last agent
        that used it. The caller may be the worker's event loop, so ending it, which waits for a step another
        agent is running, happens on a thread of its own."""
        key = (call.conversation, self.name)
        with _guard:
            _current.pop(self._agent(call), None)
            users = _users.get(key)
            if users is None:
                return
            users.discard(call.agent)
            if users:
                return
        _in_background(self._end, key)

    def _end(self, key: tuple[str, str]) -> None:
        with _turn(*key):
            with _guard:
                if _users.get(key):
                    # Another agent started using the connection while this waited for the turn.
                    return
                _users.pop(key, None)
                _tables.pop(key, None)
            try:
                self.extension.close_session(*key)
            except Exception:
                _log.warning("closing %s for conversation %s failed", key[1], key[0], exc_info=True)

    def _operated(self, call: Call):
        """The processes of this browser, for keeping the user's front application while it is operated."""
        from openprogram.host._platform import front
        if self.engine == "chromium" and not self.extension.token() \
                and not self.extension.connected(call.conversation, self.name):
            # Without a token the user approves the connection on the page the browser opens, so it stays in front.
            return None
        if sys.platform != "darwin":
            return front.programs(*self.extension.programs(self.name)[:3])
        return front.bundles(*self.extension.bundles(self.name))


def _public(rows: list[dict[str, Any]], current: str | None) -> list[dict[str, Any]]:
    return [{"tab": row["id"], "title": row.get("title") or "", "url": row.get("url") or "",
             **({"current": True} if row["id"] == current else {})} for row in rows]


def providers(browser) -> list[Provider]:
    """One provider for each of the user's browsers that can be reached: a Chromium browser with the extension
    installed, Safari on macOS, Firefox where it is installed."""
    return [UserBrowser(browser, name) for name in browser.extension.installed_browsers()]


__all__ = ["UserBrowser", "identify", "providers"]
