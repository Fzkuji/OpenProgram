"""OpenProgram's own browser on this computer, and the user's browsers beside it.

It is offered for the connection method ``local``. It yields target ``browser``, this conversation's Pages in
the App, read and operated through the browser application's tab driver;
then ``browser chrome``, ``browser brave``, ``browser safari``,
``browser firefox`` ... (``user.py``) for each of the user's browsers
that can be reached: a Chromium browser with the Playwright Extension
installed, Safari on macOS, Firefox where it is installed. Listing follows
the set-up: a browser is listed whether or not it is connected now, and a
call to a browser that does not connect answers with its code.

The browser application reaches this module only through its ``browser``
service; without the service (the application is not enabled) it
yields nothing.

The tab driver keeps each agent's current tab, its baseline of changes and
its last screenshot, and holds a tab while an action runs on it. A call
that names its tab also holds that tab's lock key across processes.
"""
from __future__ import annotations

import sys
from typing import Any

from openprogram.host.machines import Machine
from openprogram.runtime.agent.tool_execution import ToolReturn

from ... import errors
from ...routing import locks, registry
from ...actions import vocabulary
from ...contract import Approval, Call, Described, Provider, Row, Steps

_CAPABILITIES = frozenset({"pixels", "tree", "pointer", "hover", "keyboard", "tabs", "script", "upload", "video"})
_UPLOAD = "Upload the selected local file to the website; this can transmit file contents immediately."
_APP_PROGRAMS = ("openprogram", "openprogram dev")

# The tab driver's failures, by reason code, as the codes a model reads. Any other is a failure in its own words.
_BAD = {"tab_not_found", "ambiguous_element", "target_required", "unsupported_url", "screenshot_required",
        "no_file_field", "invalid_modifiers", "invalid_mouse_button", "invalid_click_count", "invalid_amount",
        "invalid_direction", "invalid_point", "invalid_seconds", "invalid_value", "text_required",
        "invalid_format", "script_required", "script_too_long", "key_required", "value_required",
        "invalid_argument", "unknown_action"}
_CODES: dict[str, type[errors.CallError]] = {
    **{code: errors.BadArgument for code in _BAD},
    "tab_closed": errors.BadArgument,
    "element_gone": errors.ElementGone,
    "tab_busy": errors.ScreenBusy,
    # Raised only once input may have reached the page; a failure before that is element_gone.
    "action_failed": errors.OutcomeUnknown,
    "close_uncertain": errors.OutcomeUnknown,
    "page_access_disabled": errors.PermissionMissing,
    "app_unavailable": errors.MachineUnreachable,
    "desktop_app_unavailable": errors.MachineUnreachable,
    "browser_unavailable": errors.DependencyMissing,
}


def service():
    """The browser application's service, or None while it is not enabled."""
    from openprogram.programs._host import services
    return services.lookup("browser")


def tabs_named(subject: dict, steps: Steps) -> list[str]:
    """The tabs a call names, in its subject and in its steps."""
    named = [subject.get("tab"), *(step.get("tab") for _, step in steps)]
    return sorted({str(tab).strip() for tab in named if tab not in (None, "") and str(tab).strip()})


def with_include(options: dict, call: Call) -> dict:
    """get_state's options with the vocabulary's default, both the outline and the screenshot; a call that takes
    no images (code mode) gets the outline."""
    include = str(options.get("include") or "all").lower()
    if include == "all" and not call.images:
        include = "tree"
    return {**options, "include": include}


def failure(result: dict[str, Any], render) -> errors.CallError | None:
    """A failed driver result as a coded error, or None when it has no code."""
    cls = _CODES.get(str(result.get("reason_code") or ""))
    if cls is None:
        return None
    return cls(render(result))


class BuiltInBrowser(Provider):
    """Target ``browser``: this conversation's Pages in OpenProgram's browser."""

    family = "browser"
    capabilities = _CAPABILITIES
    state_scope = "conversation"

    def __init__(self, browser) -> None:
        self.browser = browser

    # -- what the model reads -------------------------------------------------

    def describe(self) -> Described:
        return Described("OpenProgram's built-in browser, this conversation's pages.")

    def note(self) -> str:
        return (
            'Note: type="browser" without app is OpenProgram\'s own browser on this computer: the tabs in the App\'s windows, '
            "and tabs it opens in the background. list_tabs lists the ones this conversation opened; list_tabs(all="
            "true) adds the user's and other conversations', which you use only when the user asks. A conversation "
            "keeps 8 tabs of its own open; one more closes the one used least recently. With no App window to open "
            "a tab in (the App is closed), create_tab uses a background browser with no window: the same actions "
            "and results, but none of the App's sign-ins, and its result says so; a page that needs the user's "
            "sign-in has to wait for the App. Only here: list_panes, start_video_recording(pane, max_duration=60) "
            "and stop_video_recording(discard) record one pane of the OpenProgram window as an MP4 while you act "
            '(pane is "conversation", "window", or an id from list_panes; at most 600 seconds, one recording at a '
            "time). " + shortcut_line()
        )

    def extra_actions(self) -> tuple[str, ...]:
        return tuple(vocabulary.FAMILIES["browser"].extras)

    # -- operating ------------------------------------------------------------

    def opening(self, subject: dict) -> Steps:
        return [("list_tabs", {})]

    def list(self, kind: str, subject: dict, args: dict, call: Call) -> ToolReturn:
        action = {"tabs": "list_tabs", "panes": "list_panes"}.get(kind)
        if action is None:
            raise errors.NotSupported(f"{self.address()} has nothing to list as {kind}")
        return self._run(subject, [(action, args)], call)

    def get_state(self, subject: dict, options: dict, call: Call) -> ToolReturn:
        return self._run(subject, [("get_state", with_include(options, call))], call)

    def act(self, subject: dict, steps: Steps, call: Call) -> ToolReturn:
        return self._run(subject, steps, call)

    def _run(self, subject: dict, steps: Steps, call: Call) -> ToolReturn:
        from openprogram.host._platform import front

        with front.kept(_operated()):
            if len(steps) == 1:
                action, step = steps[0]
                try:
                    call.check()
                except errors.Timeout:
                    raise _ran_out([]) from None
                text, images, data, failed = self._one(action, {**subject, **step}, call)
                return ToolReturn(text=text, images=images if call.images else [], json_data=data, is_error=failed)
            with self.browser.tabs.batch():
                return self._steps(subject, steps, call)

    def _steps(self, subject: dict, steps: Steps, call: Call) -> ToolReturn:
        """Several steps in order; the tabs they touch stay held by this list until it ends."""
        texts, results, images, done = [], [], [], []
        for number, (action, step) in enumerate(steps, 1):
            try:
                call.check()
            except errors.Timeout:
                raise _ran_out(done) from None
            try:
                text, shots, data, failed = self._one(action, {**subject, **step}, call)
            except errors.CallError as exc:
                error = type(exc)(f"step {number} ({action}): {exc} Steps done: {_listed(done)}.")
                error.sent = exc.sent or bool(done)
                raise error from None
            images.extend(shots)
            results.append(data)
            texts.append(f"[{number}] {action}: {text}")
            if failed:
                texts.append(f"step {number} failed; the steps after it did not run. {len(done)} of {len(steps)} "
                             "steps done.")
                return ToolReturn(text="\n".join(texts), json_data=results, images=images if call.images else [],
                                  is_error=True)
            done.append(f"{number}. {action}")
        texts.append(f"{len(done)} of {len(steps)} steps done.")
        return ToolReturn(text="\n".join(texts), json_data=results, images=images if call.images else [])

    def _one(self, action: str, args: dict, call: Call) -> tuple[str, list[bytes], Any, bool]:
        """One step through the tab driver: its text, images and data, and whether it failed without a code."""
        if action == "close_tab" and not str(args.get("tab") or "").strip():
            raise errors.BadArgument("close_tab closes only a tab the call names: pass title, an id from list_tabs.")
        result = self.browser.tabs.driver().run(action, args, call.conversation, agent=call.agent)
        images = result.pop("_images", [])
        if result.get("ok") is False:
            coded = failure(result, render)
            if coded is not None:
                raise coded
            return render(result), images, result, True
        return render(result), images, result, False

    # -- permission -----------------------------------------------------------

    def approval(self, subject: dict, steps: Steps) -> Approval | None:
        """The conversation's own pages are its to use: opening a site, acting on it and running a script in it
        ask nothing. An upload sends a local file away, so it asks each time."""
        if any(action == "upload_file" for action, _ in steps):
            return Approval(_UPLOAD)
        return None

    def authority(self, steps: Steps, code: bool) -> str:
        return "process.exec" if code else "browser.control"

    def runs_page_script(self, steps: Steps) -> bool:
        return any(action == "evaluate" for action, _ in steps)

    def lock(self, subject: dict, steps: Steps) -> locks.Locks:
        """The tabs the call names; pages are driven in the background, so the machine is not held and a
        ``screen`` call does not wait for them, nor they for it."""
        return locks.Locks(exclusive=tuple(locks.tab_key("local", self.target, tab)
                                           for tab in tabs_named(subject, steps)))

    # -- showing --------------------------------------------------------------

    def resource(self, subject: dict) -> Row | None:
        named = str(subject.get("tab") or "").strip()
        if not named:
            return None
        tab = self.browser.tabs.driver().known(named)
        if tab is None:
            return None
        return Row("web", tab.title or "(untitled)", tab.origin)

    def created(self, conversation: str) -> list[dict[str, Any]]:
        if not conversation:
            return []
        rows, _, _ = self.browser.pages.project_conversation_resources(conversation)
        return [row for row in rows if row.get("status") != "closed"]

    def claims(self, window: dict[str, Any], conversation: str = "") -> str:
        from openprogram.host._platform import front
        bundle = str(window.get("bundle_id") or window.get("bundle") or "")
        name = str(window.get("app") or window.get("name") or window.get("process") or "").strip().lower()
        if bundle in front.APP_BUNDLES or name in _APP_PROGRAMS:
            return "the web page in this window is a Page of OpenProgram's browser; list_tabs names its tab"
        return ""


    def close(self, call: Call) -> None:
        """The agent ended: the tab driver forgets its current tab and the screenshots kept for it."""
        if call.agent:
            self.browser.tabs.forget(call.agent)

    def environment_note(self, machine: str, targets: set[str]) -> str:
        """In a conversation on another machine, this browser's localhost is still this computer's."""
        own = f"{machine} browser"
        return " Its http://localhost is this computer's" + (
            f"; for {machine}'s use device=\"{machine}\", type=\"browser\"." if own in targets else ".")


def _operated():
    """The App's own windows, which the tab driver's input reaches without bringing them forward."""
    from openprogram.host._platform import front
    if sys.platform != "darwin":
        return front.programs(*_APP_PROGRAMS)
    return front.bundles(*front.APP_BUNDLES)


def shortcut_line() -> str:
    """What press_key's modifiers mean in a page of this computer's browsers."""
    if sys.platform == "darwin":
        return ("press_key goes to the page, not the browser's menus: a page's shortcuts use super (Command), "
                "e.g. super+a selects all and super+c copies.")
    return ("press_key goes to the page, not the browser's menus: a page's shortcuts use ctrl, e.g. ctrl+a selects "
            "all and ctrl+c copies.")


def _listed(done: list[str]) -> str:
    return ", ".join(done) or "none"


def _ran_out(done: list[str]) -> errors.Timeout:
    """TIMEOUT between steps: the next step was not sent, so the call sent something only when a step was done."""
    error = errors.Timeout(f"the call ran out of time; steps that took effect: {_listed(done)}.")
    error.sent = bool(done)
    return error


def render(result: dict[str, Any]) -> str:
    """A tab driver result as the model reads it: a header line, then the page or its changes as an outline."""
    if result.get("ok") is False:
        lines = [str(result.get("error") or result.get("message") or result.get("reason_code") or "failed")]
    elif "tabs" in result:
        rows = result["tabs"]
        others = (f"\n{result['other_tabs']} other tabs are open (the user's or other conversations'); "
                  "list_tabs(all=true) lists them." if result.get("other_tabs") else "")
        if not rows:
            return "This conversation has no tabs open. create_tab(url) opens one." + others
        return "Tabs (the current one is used when tab is omitted):\n" + "\n".join(
            f"- {row['tab']}{' (current)' if row.get('current') else ''}: {row.get('title') or '(untitled)'} — "
            f"{row.get('url') or ''} [{row.get('shown')}{', focused' if row.get('focused') else ''}]" for row in rows
        ) + others
    else:
        lines = [result["done"]] if result.get("done") else []
    if result.get("tab") and result.get("url") is not None:
        lines.append(f"tab {result['tab']} · {result.get('title') or '(untitled)'} · {result['url']}")
    elif result.get("tab"):
        lines.append(f"tab {result['tab']}")
    if result.get("note"):
        lines.append(f"note: {result['note']}")
    if result.get("file_chooser"):
        chooser = result["file_chooser"]
        accepted = f" for {chooser['accept']}" if chooser.get("accept") else ""
        lines.append(f"the page opened a file chooser{accepted}{' (several files)' if chooser.get('multiple') else ''}; "
                     "upload_file(path) without element answers it. Nothing opened on the user's screen")
    for row in result.get("opened") or []:
        lines.append(f"it opened a new tab {row['tab']} · {row.get('title') or '(untitled)'} · {row.get('url') or ''}; "
                     f"pass title=\"{row['tab']}\" to use it")
    if "found" in result:
        found = result["found"]
        lines.append("found:\n" + ("\n".join(found) if found else "(nothing on the page matches)"))
    for key, label in (("result", "result"), ("console", "console"), ("network", "network")):
        if key in result:
            value = result[key]
            body = value if isinstance(value, str) else "\n".join(
                item if isinstance(item, str) else
                f"{item['method']} {item['url']} → {item.get('error') or item['status']}" for item in value)
            lines.append(f"{label}:\n{body or '(none)'}")
    if result.get("panes") is not None and not result.get("done"):
        lines.append("panes: " + ", ".join(str(pane.get("id")) for pane in result["panes"]))
    if "page" in result:
        lines.append("page:\n" + result["page"])
    elif "changes" in result:
        lines.append("changes:\n" + (result["changes"] or "(nothing on the page changed)"))
    if result.get("tabs") is not None and result.get("ok") is False:
        lines.append("open tabs: " + (", ".join(f"{row['tab']} ({row.get('title') or row.get('url')})"
                                                for row in result["tabs"]) or "none"))
    return "\n".join(lines)


def _open(machine: Machine, conversation: str) -> list[Provider]:
    if not machine.is_local:
        return []
    browser = service()
    if browser is None:
        return []
    from . import user as user_browser
    return [BuiltInBrowser(browser), *user_browser.providers(browser)]


registry.offer("local", _open)

__all__ = ["BuiltInBrowser", "failure", "render", "service", "tabs_named", "with_include"]
