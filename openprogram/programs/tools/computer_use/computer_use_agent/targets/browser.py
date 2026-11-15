"""Target ``browser``: a web browser, chosen first, then operated with one set of actions.

As in Codex, the browser is chosen by ``args.browser``: omitted, it is
OpenProgram's built-in browser, whose tabs the browser application's tab
driver drives; ``chrome`` or ``edge`` is the user's own browser, reached
through the Playwright Extension. The actions are the same in all of them.

Both come from the ``browser`` service; without it the target is
unavailable and computer_use_agent operates the desktop only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from openprogram.programs._host.execution import ToolReturn

NAME = "browser"

# Actions that only read, so a failed one changed nothing.
READS = frozenset({"tabs", "read", "wait", "screenshot", "console", "network", "panes"})
# Choosing the browser; it is not an argument of any action.
_BROWSER_FIELD = "browser"

_GUIDE = (Path(__file__).parent.parent / "guides" / "browser.md").read_text(encoding="utf-8")

# The user's browser speaks the Playwright MCP backend's names.
_EXTENSION_NAMES = {"tabs": "list_pages", "read": "observe", "open": "observe", "console": "console_messages",
                    "network": "network_requests"}


_BUILT_IN = "OpenProgram's built-in browser: the tabs in the App's windows, and tabs you open in the background."


def own_browsers() -> tuple[str, ...]:
    """The user's browsers reachable now: ``chrome`` and ``edge`` where the extension is installed."""
    browser = _browser()
    return browser.extension.installed_browsers() if browser is not None else ()


def _browsers_text() -> str:
    """Which browsers the actions reach, as found now: the user's own only where the extension is installed."""
    browser = _browser()
    reachable = own_browsers()
    if not reachable:
        text = (f"The pages are in {_BUILT_IN} The user's own Chrome and Edge are not reachable as web pages here "
                "(no Playwright Extension is installed in them)")
        from . import app as app_target
        if app_target.available():
            text += ("; operate a page open in one of them as an application window: target=\"app\" with "
                     "args.app=\"Google Chrome\" or \"Microsoft Edge\"")
        return text + "."
    names = browser.user_browser_names
    own = " or ".join(f'"{name}"' for name in reachable)
    lines = ["Choose the browser first with args.browser; every action below is the same in each:",
             f"- omitted: {_BUILT_IN}",
             f"- {own}: the user's own {' or '.join(names[name] for name in reachable)}, with their tabs and "
             "sign-ins, through the Playwright Extension. Use it only when the user names it or the task needs "
             "their signed-in state, and keep to the browser the user named. On the first call the extension may "
             "ask the user which tab to share; there, tab is an index from tabs."]
    missing = [names[name] for name in browser.extension.BROWSERS if name not in reachable]
    if missing:
        lines.append(f"The user's {missing[0]} has no Playwright Extension and is not reachable here.")
    return "\n".join(lines)


def guide() -> str:
    return _GUIDE.replace("{browsers}", _browsers_text())


def reachable(chosen: str) -> bool:
    """Whether the user's ``chrome`` or ``edge`` can be operated: its extension is installed."""
    return _browser().extension.installed(chosen)


def missing(chosen: str) -> str:
    """What to use instead of a user's browser that has no extension."""
    from . import app as app_target
    name = _browser().user_browser_names[chosen]
    text = f"the user's {name} cannot be operated as web pages here: it has no Playwright Extension."
    if app_target.available():
        app = {"chrome": "Google Chrome", "edge": "Microsoft Edge"}[chosen]
        text += f" Operate a page open in it as an application window: target=\"app\" with args.app=\"{app}\"."
    return text + (" When the site does not need the user's own sign-in, use the built-in browser "
                   "(omit args.browser).")


def _browser():
    from openprogram.programs._host import services as _services
    return _services.lookup("browser")


def available() -> bool:
    """Whether the browser application is loaded."""
    return _browser() is not None


def actions() -> tuple[str, ...]:
    """The tab driver's actions; none without the browser."""
    browser = _browser()
    return tuple(browser.tabs.ACTIONS) if browser is not None else ()


def user_browser(args: dict) -> str:
    """``chrome`` or ``edge`` when the call operates the user's own browser, else ``""``."""
    browser = _browser()
    return browser.browser_of(args) if browser is not None else ""


def approval(steps: list[tuple[str, dict]], args: dict) -> str | bool:
    """What a call needs granted: the user's own browser, or an upload from the built-in one.

    The built-in browser's tabs are the conversation's to use: opening a
    site, acting on it and running a script in it ask nothing. The user's
    own Chrome or Edge holds their sign-ins: one grant covers everything
    done in it."""
    chosen = user_browser(args)
    if chosen and not reachable(chosen):
        # Nothing runs: the call is turned back with what to use instead.
        return False
    if chosen:
        names = _browser().user_browser_names
        if any(action == "upload" for action, _ in steps):
            return f"computer_use_agent uploads a local file to a website in your {names[chosen]}"
        return f"computer_use_agent controls your {names[chosen]}"
    if any(action == "upload" for action, _ in steps):
        return "Upload the selected local file to the website; this can transmit file contents immediately."
    return False


def evaluates(steps: list[tuple[str, dict]]) -> bool:
    """Whether a call runs page JavaScript, which reads all a signed-in page holds."""
    return any(action == "evaluate" for action, _ in steps)


def open_target(args: dict, session: str) -> ToolReturn:
    return run([("tabs", {})], args, session)


def render(result: dict[str, Any]) -> str:
    """A result as the model reads it: a header line, then the page or its changes as YAML."""
    if result.get("ok") is False:
        lines = [f"Error ({result.get('reason_code')}): {result.get('error') or result.get('message') or ''}".rstrip()]
    elif "tabs" in result:
        rows = result["tabs"]
        others = (f"\n{result['other_tabs']} other tabs are open (the user's or other conversations'); "
                  "tabs(all=true) lists them." if result.get("other_tabs") else "")
        if not rows:
            return "This conversation has no tabs open. open(url) opens one." + others
        return "Tabs (the current one is used when tab is omitted):\n" + "\n".join(
            f"- {row['tab']}{' (current)' if row.get('current') else ''}: {row.get('title') or '(untitled)'} — "
            f"{row.get('url') or ''} [{row.get('shown')}{', focused' if row.get('focused') else ''}]" for row in rows
        ) + others
    else:
        lines = [result["done"]] if result.get("done") else []
    if result.get("tab") and result.get("url") is not None:
        lines.append(f"tab {result['tab']} · {result.get('title') or '(untitled)'} · {result['url']}")
    elif result.get("tab") and not lines:
        lines.append(f"tab {result['tab']}")
    if result.get("note"):
        lines.append(f"note: {result['note']}")
    if result.get("file_chooser"):
        chooser = result["file_chooser"]
        kinds = f" for {chooser['accept']}" if chooser.get("accept") else ""
        lines.append(f"the page opened a file chooser{kinds}{' (several files)' if chooser.get('multiple') else ''}; "
                     "upload(path) without ref answers it. Nothing opened on the user's screen")
    for row in result.get("opened") or []:
        lines.append(f"it opened a new tab {row['tab']} · {row.get('title') or '(untitled)'} · {row.get('url') or ''}; "
                     f"pass tab=\"{row['tab']}\" to use it")
    for key, label in (("result", "result"), ("console", "console"), ("network", "network")):
        if key in result:
            value = result[key]
            body = value if isinstance(value, str) else "\n".join(
                item if isinstance(item, str) else
                f"{item['method']} {item['url']} → {item.get('error') or item['status']}" for item in value)
            lines.append(f"{label}:\n{body or '(none)'}")
    if "page" in result:
        lines.append("page:\n" + result["page"])
    elif "changes" in result:
        lines.append("changes:\n" + (result["changes"] or "(nothing on the page changed)"))
    if result.get("tabs") is not None and result.get("ok") is False:
        lines.append("tabs: " + ", ".join(f"{row['tab']} ({row.get('title') or row.get('url')})"
                                          for row in result["tabs"]))
    return "\n".join(lines)


def _one(action: str, step: dict, args: dict, session: str = "") -> ToolReturn:
    merged = {**args, **step}
    chosen = user_browser(merged)
    merged.pop(_BROWSER_FIELD, None)
    if chosen and action in _browser().tabs.video.ACTIONS:
        return ToolReturn(text=f"Error: {action} records panes of the OpenProgram App; call it without browser.",
                          json_data={"ok": False, "action_dispatched": False}, is_error=True)
    if chosen:
        return _in_user_browser(chosen, action, merged, session)
    result = _browser().tabs.driver().run(action, merged, session)
    images = result.pop("_images", [])
    return ToolReturn(text=render(result), json_data=result, images=images, is_error=result.get("ok") is False)


def _in_user_browser(chosen: str, action: str, merged: dict, session: str) -> ToolReturn:
    extension_browser = _browser().extension
    if not reachable(chosen):
        return ToolReturn(text=f"Error: {missing(chosen)}", json_data={"ok": False, "action_dispatched": False},
                          is_error=True)
    try:
        browser = extension_browser.ExtensionBrowser(session, chosen)
    except extension_browser.Unavailable as exc:
        return ToolReturn(text=f"Error: {exc}", is_error=True)
    if "tab" in merged:
        merged["page"] = merged.pop("tab")
    name = _EXTENSION_NAMES.get(action, action)
    if name in ("list_pages", "observe", "close"):
        result = browser.run(name, merged)
    else:
        result = browser.run("act", {"action": name, **merged})
    images = result.pop("_images", [])
    return ToolReturn(text=str(result.get("result") or result.get("message") or json.dumps(result, ensure_ascii=False)),
                      json_data=result, images=images, is_error=result.get("ok") is False)


# The user's browsers by the name a call gives them, as bundle identifiers.
# The built-in browser is drawn by the App itself.
_BROWSER_BUNDLES = {"chrome": ("com.google.Chrome",), "edge": ("com.microsoft.edgemac",)}
# The same browsers by executable, where applications are told apart by process.
_BROWSER_PROGRAMS = {"chrome": ("chrome", "google-chrome", "google-chrome-stable"),
                     "edge": ("msedge", "microsoft-edge", "microsoft-edge-stable")}
_APP_PROGRAMS = ("openprogram", "openprogram dev")


def _operated(steps: list[tuple[str, dict]], args: dict, session: str):
    """Which processes belong to the browser this call operates, for keeping the user's front application."""
    from openprogram.host._platform import front

    browser = _browser()
    if browser is None:
        return None
    chosen = next((name for name in (user_browser({**args, **step}) for _, step in steps) if name),
                  user_browser(args))
    if chosen and not browser.extension.token() and not browser.extension.connected(session, chosen):
        # Without a token the user approves the connection on the page the browser opens, so it stays in front.
        return None
    if sys.platform != "darwin":
        return front.programs(*_BROWSER_PROGRAMS.get(chosen, _APP_PROGRAMS))
    return front.bundles(*_BROWSER_BUNDLES.get(chosen, front.APP_BUNDLES))


def run(steps: list[tuple[str, dict]], args: dict, session: str) -> ToolReturn:
    """Run one action, or several in order, stopping at the first failure; the user's front application is kept."""
    from openprogram.host._platform import front

    with front.kept(_operated(steps, args, session)):
        return _run(steps, args, session)


def _run(steps: list[tuple[str, dict]], args: dict, session: str) -> ToolReturn:
    browser = _browser()
    if browser is None:
        return ToolReturn(text="Error: the browser is not enabled; computer_use_agent operates the desktop only.",
                          is_error=True)
    known = browser.tabs.ACTIONS
    for number, (action, _) in enumerate(steps, 1):
        if action not in known:
            where = f"step {number}: " if len(steps) > 1 else ""
            return ToolReturn(text=f"Error: {where}unknown browser action {action!r}; actions: {', '.join(known)}",
                              is_error=True)
    if len(steps) == 1:
        return _one(steps[0][0], steps[0][1], args, session)
    from openprogram.runtime.conversation.run_control import check_cancelled
    with browser.tabs.batch():
        return _steps(steps, args, session, check_cancelled)


def _steps(steps: list[tuple[str, dict]], args: dict, session: str, check_cancelled) -> ToolReturn:
    """Several actions in order; the tabs they touch stay held by this list until it ends."""
    texts, results, images = [], [], []
    for number, (action, step) in enumerate(steps, 1):
        check_cancelled()
        result = _one(action, step, args, session)
        images.extend(result.images)
        results.append(result.json_data)
        texts.append(f"[{number}] {action}: {result.text or ''}")
        if isinstance(result.json_data, dict) and result.json_data.get("status") == "observation_required":
            texts.append(f"step {number} requires checking the fresh page; the steps after it did not run")
            return ToolReturn(text="\n".join(texts), json_data=results, images=images)
        if result.is_error:
            texts.append(f"step {number} failed; the steps after it did not run")
            return ToolReturn(text="\n".join(texts), json_data=results, images=images, is_error=True)
    return ToolReturn(text="\n".join(texts), json_data=results, images=images)


__all__ = ["NAME", "READS", "actions", "approval", "available", "evaluates", "guide", "missing", "open_target",
           "own_browsers", "reachable", "render", "run", "user_browser"]
