"""Argument parsing and page scripts the browser targets' two drivers share.

The built-in browser's tab driver and the user's own browsers (Chromium
through Playwright MCP, Safari and Firefox through ``script_page``) read the
same arguments here, so an action means the same in each. Clicks sent as
events, element scrolling, inserting text at the caret and pasting run as
small scripts inside the page, so a tab is driven without being focused.
Each script takes ``(element, options)``; ``element`` is ``null`` for the
page's focus.
"""
from __future__ import annotations

import json
from typing import Any

MOUSE_BUTTONS = ("left", "right", "middle")
DIRECTIONS = ("down", "up", "right", "left")
PASTE_FORMATS = ("text", "md", "html")
# Arbitrary JavaScript reads everything a signed-in page holds, so it is
# approved per call and never by a standing rule (see permissions.policy).
EVALUATE_ACTION = "evaluate"
MAX_SCRIPT_CHARS = 8000
MAX_RESULT_CHARS = 20_000

_MODIFIERS = {
    "cmd": "Meta", "command": "Meta", "meta": "Meta", "super": "Meta",
    "ctrl": "Control", "control": "Control",
    "alt": "Alt", "option": "Alt", "opt": "Alt",
    "shift": "Shift",
}
_JS_FLAGS = {"Meta": "metaKey", "Control": "ctrlKey", "Alt": "altKey", "Shift": "shiftKey"}


class ActionError(ValueError):
    """An argument the page action cannot use; ``reason_code`` names it."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code

    def result(self) -> dict[str, Any]:
        return {"ok": False, "reason_code": self.reason_code, "message": str(self)}


def modifiers(spec: Any) -> list[str]:
    """``"cmd+shift"`` or ``"ctrl, alt"`` -> Playwright modifier names, in order, without repeats."""
    out: list[str] = []
    for part in str(spec or "").replace(",", "+").split("+"):
        name = part.strip().lower()
        if not name:
            continue
        if name not in _MODIFIERS:
            raise ActionError("invalid_modifiers", f"unknown modifier {part.strip()!r}; use cmd, ctrl, alt or shift")
        if _MODIFIERS[name] not in out:
            out.append(_MODIFIERS[name])
    return out


def click_options(arguments: dict[str, Any]) -> dict[str, Any]:
    button = str(arguments.get("mouse_button") or "left").lower()
    if button not in MOUSE_BUTTONS:
        raise ActionError("invalid_mouse_button", "mouse_button must be left, right or middle")
    try:
        count = int(arguments.get("click_count") or 1)
    except (TypeError, ValueError) as exc:
        raise ActionError("invalid_click_count", "click_count must be 1, 2 or 3") from exc
    if not 1 <= count <= 3:
        raise ActionError("invalid_click_count", "click_count must be 1, 2 or 3")
    keys = modifiers(arguments.get("modifiers"))
    return {"button": button, "count": count, "modifiers": keys,
            "flags": {_JS_FLAGS[key]: True for key in keys}}


def mouse_options(options: dict[str, Any]) -> dict[str, Any]:
    """Playwright mouse arguments that differ from a single left click."""
    out: dict[str, Any] = {}
    if options["button"] != "left":
        out["button"] = options["button"]
    if options["count"] != 1:
        out["click_count"] = options["count"]
    return out


# One page of a scroll is this share of the visible width or height, so a line of what was seen stays in view.
PAGE_SHARE = 0.9


def scroll_delta(arguments: dict[str, Any], viewport: tuple[float, float] = (0, 0)) -> tuple[int, int]:
    """(dx, dy) in CSS pixels: ``pixels``, else ``pages`` (1 by default) of ``viewport`` (width, height),
    towards ``direction`` (down by default)."""
    direction = str(arguments.get("direction") or "down").lower()
    if direction not in DIRECTIONS:
        raise ActionError("invalid_direction", "direction must be down, up, right or left")
    across = direction in ("left", "right")
    try:
        if arguments.get("pixels") not in (None, ""):
            amount = abs(int(arguments["pixels"]))
        else:
            size = float(viewport[0] if across else viewport[1]) or 800.0
            amount = int(round(abs(float(arguments.get("pages") or 1)) * size * PAGE_SHARE))
    except (TypeError, ValueError) as exc:
        raise ActionError("invalid_amount", "pages and pixels are numbers") from exc
    return {"down": (0, amount), "up": (0, -amount), "right": (amount, 0), "left": (-amount, 0)}[direction]


# What set_value does to an element: choose options of a select box, check or uncheck a box, or replace text.
FIELD_KIND_SCRIPT = """e => e instanceof HTMLSelectElement ? "select"
  : (e instanceof HTMLInputElement && (e.type === "checkbox" || e.type === "radio")) ? "check" : "text\""""


def option_values(arguments: dict[str, Any]) -> list[str]:
    """set_value on a select box: one option, or several, by value or label."""
    value = arguments.get("value")
    values = [str(item) for item in value] if isinstance(value, (list, tuple)) else [str(value if value is not None else "")]
    if not any(values):
        raise ActionError("value_required", "set_value needs value: the option's value or label")
    return values


def checked(arguments: dict[str, Any]) -> bool:
    """set_value on a checkbox or radio button: whether it ends up checked."""
    value = arguments.get("value")
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else "").strip().lower()
    if text in ("true", "on", "yes", "1", "checked"):
        return True
    if text in ("false", "off", "no", "0", "unchecked", ""):
        return False
    raise ActionError("invalid_value", "a checkbox or radio button takes value true or false")


def find_lines(outline: str, text: str = "", role: str = "") -> list[str]:
    """The lines of a page outline that hold ``text`` (any case) and are elements of ``role``."""
    import re
    wanted, kind = text.strip().lower(), role.strip().lower()
    found = []
    for line in outline.splitlines():
        head = re.match(r"^\s*- (?:<changed> )?([\w-]+)", line)
        if kind and (head is None or head.group(1).lower() != kind):
            continue
        if wanted and wanted not in line.lower():
            continue
        if head is not None:
            found.append(line.strip())
    return found


def paste_payload(arguments: dict[str, Any]) -> dict[str, str]:
    text = arguments.get("text")
    if not isinstance(text, str) or not text:
        raise ActionError("text_required", "paste needs text")
    kind = str(arguments.get("format") or "text").lower()
    kind = "md" if kind == "markdown" else kind
    if kind not in PASTE_FORMATS:
        raise ActionError("invalid_format", "format must be text, md or html")
    if kind == "text":
        return {"text": text, "html": ""}
    if kind == "md":
        from markdown_it import MarkdownIt
        return {"text": text, "html": MarkdownIt("commonmark").enable("table").enable("strikethrough").render(text)}
    return {"text": _strip_tags(text), "html": text}


_FUNCTION_START = None

# How long a script's promise may take to settle; one that never settles would hold the tab.
SCRIPT_SECONDS = 30

# What the page runs for ``evaluate``: a function script is called (with the element the call
# names, when it names one), any other script runs
# as a console runs it (``_CONSOLE_RUN``), and a promise it returns is given SCRIPT_SECONDS.
_SCRIPT_RUNNER = r"""(element) => {
  const run = __RUN__;
  const value = run(element);
  if (!value || typeof value.then !== "function") return value;
  let timer;
  const limit = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error("the script did not finish within __SECONDS__ s")), __SECONDS__ * 1000);
  });
  return Promise.race([Promise.resolve(value), limit]).finally(() => clearTimeout(timer));
}"""

# A script that is not a function, run once, the way a console does: as an expression; else as
# statements, whose last value is the result; else, when it awaits, as the body of an async
# function, whose return is the result. Each form is parsed before anything runs, so the script
# runs once, and the error of a script that fits no form is the one for the form it is closest to.
_CONSOLE_RUN = r"""() => {
    const s = __SCRIPT__;
    const Async = Object.getPrototypeOf(async function () {}).constructor;
    const parses = (make, body) => { try { make(body); return null; } catch (e) { if (e instanceof SyntaxError) return e; throw e; } };
    const expression = "return (" + s + "\n)";
    if (!parses(Function, expression)) return (0, eval)("(" + s + "\n)");
    const statements = parses(Function, s);
    if (!statements) {
      try { return (0, eval)(s); } catch (e) { if (!(e instanceof SyntaxError) || !/^Illegal return/.test(e.message)) throw e; }
      return new Function(s)();
    }
    if (!parses(Async, expression)) return new Async(expression)();
    const asyncStatements = parses(Async, s);
    if (!asyncStatements) return new Async(s)();
    throw /\bawait\b/.test(s) ? asyncStatements : statements;
  }"""


def evaluate_source(arguments: dict[str, Any]) -> str:
    """``script`` as a function the page can call: a function is called, anything else runs as in a console."""
    import re
    global _FUNCTION_START
    script = arguments.get("script")
    if not isinstance(script, str) or not script.strip():
        raise ActionError("script_required", "evaluate needs script: an expression, statements or a function such as "
                          "() => document.title")
    script = script.strip()
    if len(script) > MAX_SCRIPT_CHARS:
        raise ActionError("script_too_long", f"script is limited to {MAX_SCRIPT_CHARS} characters")
    if _FUNCTION_START is None:
        _FUNCTION_START = re.compile(r"^(async\s+)?(function\b|\([^()]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)")
    run = f"({script}\n)" if _FUNCTION_START.match(script) else _CONSOLE_RUN.replace("__SCRIPT__", json.dumps(script))
    return _SCRIPT_RUNNER.replace("__RUN__", run).replace("__SECONDS__", str(SCRIPT_SECONDS))


def evaluation_text(result: Any) -> str:
    """The script's result as JSON text, cut at MAX_RESULT_CHARS."""
    if result is None:
        text = "undefined"
    elif isinstance(result, str):
        text = result
    else:
        text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + f"\n[truncated, {len(text) - MAX_RESULT_CHARS} more characters]"
    return text


def _strip_tags(html: str) -> str:
    import re
    from html import unescape
    return unescape(re.sub(r"<[^>]+>", "", html))


POINTER_SCRIPT = r"""
(element, o) => {
  const rect = element.getBoundingClientRect();
  const x = rect.left + rect.width / 2, y = rect.top + rect.height / 2;
  const cursor = globalThis.__openprogramAgentCursor;
  if (cursor?.armed && typeof cursor.show === "function") {
    cursor.armed = false;
    cursor.show(x, y);
  }
  const button = {left: 0, middle: 1, right: 2}[o.button] ?? 0;
  const flags = o.flags || {};
  if (button === 0 && o.count === 1 && !Object.keys(flags).length && typeof element.click === "function") {
    element.click();
    return true;
  }
  const base = {bubbles: true, cancelable: true, composed: true, view: window,
    clientX: x, clientY: y, button, ...flags};
  const pressed = {0: 1, 1: 4, 2: 2}[button];
  const fire = (type, detail, buttons) => element.dispatchEvent(type.startsWith("pointer")
    ? new PointerEvent(type, {...base, detail, buttons, pointerType: "mouse", isPrimary: true})
    : new MouseEvent(type, {...base, detail, buttons}));
  if (typeof element.focus === "function") element.focus({preventScroll: true});
  for (let i = 1; i <= o.count; i++) {
    fire("pointerdown", i, pressed); fire("mousedown", i, pressed);
    fire("pointerup", i, 0); fire("mouseup", i, 0);
    if (button === 0) fire("click", i, 0);
    else if (button === 1) fire("auxclick", i, 0);
  }
  if (button === 0 && o.count === 2) fire("dblclick", 2, 0);
  if (button === 2) fire("contextmenu", 1, 0);
  return true;
}
"""

HOVER_SCRIPT = r"""
(element) => {
  const rect = element.getBoundingClientRect();
  const at = {bubbles: true, composed: true, view: window,
    clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2};
  element.dispatchEvent(new PointerEvent("pointerover", {...at, pointerType: "mouse", isPrimary: true}));
  element.dispatchEvent(new MouseEvent("mouseover", at));
  element.dispatchEvent(new MouseEvent("mouseenter", {...at, bubbles: false}));
  element.dispatchEvent(new MouseEvent("mousemove", at));
  return true;
}
"""

SCROLL_SCRIPT = r"""
(element, o) => {
  const doc = element.ownerDocument;
  const root = doc.scrollingElement || doc.documentElement;
  const scrolls = (n) => {
    const s = getComputedStyle(n);
    return (o.dy && /(auto|scroll|overlay)/.test(s.overflowY) && n.scrollHeight > n.clientHeight)
      || (o.dx && /(auto|scroll|overlay)/.test(s.overflowX) && n.scrollWidth > n.clientWidth);
  };
  let node = element;
  while (node && node !== doc.body && node !== doc.documentElement && !scrolls(node)) node = node.parentElement;
  const target = node && node !== doc.body && node !== doc.documentElement ? node : root;
  const before = [target.scrollLeft, target.scrollTop];
  target.scrollBy({left: o.dx, top: o.dy, behavior: "instant"});
  return {moved: before[0] !== target.scrollLeft || before[1] !== target.scrollTop,
          container: target === root ? "page" : target.tagName.toLowerCase()};
}
"""

# For a tab behind others, where Chrome draws no frames and real pointer input waits for them: the element at
# a viewport point, standing in for the element a script is bound to.
AT_POINT = "document.elementFromPoint(o.x, o.y) || document.scrollingElement || document.documentElement"

# A drag as page events, from the element (or the point o.from) to the point o.to: pointer and mouse events
# along the way, and HTML5 drag events with one shared DataTransfer when the start is draggable.
DRAG_SCRIPT = r"""
async (element, o) => {
  const doc = element ? element.ownerDocument : document;
  const rect = element ? element.getBoundingClientRect() : null;
  const from = o.from || {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2};
  const start = element || doc.elementFromPoint(from.x, from.y);
  if (!start) return {ok: false, reason_code: "no_element_at_start"};
  const at = (x, y, buttons) => ({bubbles: true, cancelable: true, composed: true, view: window,
    clientX: x, clientY: y, button: 0, buttons});
  const pointer = (target, type, x, y, buttons) => {
    target.dispatchEvent(new PointerEvent("pointer" + type, {...at(x, y, buttons), pointerType: "mouse", isPrimary: true}));
    target.dispatchEvent(new MouseEvent("mouse" + type, at(x, y, buttons)));
  };
  // A task between steps lets the page's handlers run; a message, unlike a timer, is not held back to once a
  // second in a tab behind others.
  const pause = () => new Promise((resolve) => {
    const channel = new MessageChannel();
    channel.port1.onmessage = () => resolve();
    channel.port2.postMessage(0);
  });
  const draggable = start.closest?.("[draggable=true]") || (start.draggable ? start : null);
  pointer(start, "down", from.x, from.y, 1);
  const data = draggable ? new DataTransfer() : null;
  const drag = (target, type, x, y) => target.dispatchEvent(new DragEvent(type, {...at(x, y, 1), dataTransfer: data}));
  if (draggable) drag(draggable, "dragstart", from.x, from.y);
  let over = start;
  const steps = 8;
  for (let i = 1; i <= steps; i++) {
    const x = from.x + (o.to.x - from.x) * i / steps, y = from.y + (o.to.y - from.y) * i / steps;
    const target = doc.elementFromPoint(x, y) || doc.body;
    if (draggable) {
      if (target !== over) drag(target, "dragenter", x, y);
      drag(target, "dragover", x, y);
    } else {
      pointer(target, "move", x, y, 1);
    }
    over = target;
    await pause();
  }
  if (draggable) {
    drag(over, "drop", o.to.x, o.to.y);
    drag(draggable, "dragend", o.to.x, o.to.y);
  }
  pointer(over, "up", o.to.x, o.to.y, 0);
  return {ok: true};
}
"""

CENTER_SCRIPT = "(e) => { const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }"

# A checkbox or radio set by a click in the page, when it is not already as asked.
CHECK_SCRIPT = r"""
(element, o) => {
  if (element.checked !== o.on) element.click();
  return {ok: element.checked === o.on, reason_code: "not_toggled"};
}
"""

# A press held at an element for o.seconds, as page events.
HOLD_SCRIPT = r"""
async (element, o) => {
  const rect = element.getBoundingClientRect();
  const button = {left: 0, middle: 1, right: 2}[o.button] ?? 0;
  const at = {bubbles: true, cancelable: true, composed: true, view: window,
    clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2, button};
  const pressed = {0: 1, 1: 4, 2: 2}[button];
  element.dispatchEvent(new PointerEvent("pointerdown", {...at, buttons: pressed, pointerType: "mouse", isPrimary: true}));
  element.dispatchEvent(new MouseEvent("mousedown", {...at, buttons: pressed}));
  await new Promise((resolve) => setTimeout(resolve, o.seconds * 1000));
  element.dispatchEvent(new PointerEvent("pointerup", {...at, buttons: 0, pointerType: "mouse", isPrimary: true}));
  element.dispatchEvent(new MouseEvent("mouseup", {...at, buttons: 0}));
  if (button === 0) element.dispatchEvent(new MouseEvent("click", {...at, buttons: 0}));
  return {ok: true};
}
"""

INSERT_TEXT_SCRIPT = r"""
(element, o) => {
  const doc = element ? element.ownerDocument : document;
  if (element && typeof element.focus === "function") element.focus({preventScroll: true});
  const active = doc.activeElement;
  if (!active || (active === doc.body && doc.designMode !== "on")) return {ok: false, reason_code: "focus_required"};
  if (doc.execCommand("insertText", false, o.text)) return {ok: true};
  if (typeof active.setRangeText === "function") {
    const end = active.value.length;
    active.setRangeText(o.text, active.selectionStart ?? end, active.selectionEnd ?? end, "end");
    active.dispatchEvent(new InputEvent("input", {bubbles: true, inputType: "insertText", data: o.text}));
    return {ok: true};
  }
  return {ok: false, reason_code: "not_editable"};
}
"""

PASTE_SCRIPT = r"""
(element, o) => {
  const doc = element ? element.ownerDocument : document;
  if (element && typeof element.focus === "function") element.focus({preventScroll: true});
  const target = doc.activeElement || doc.body;
  const data = new DataTransfer();
  data.setData("text/plain", o.text);
  if (o.html) data.setData("text/html", o.html);
  const event = new ClipboardEvent("paste", {clipboardData: data, bubbles: true, cancelable: true, composed: true});
  if (!target.dispatchEvent(event)) return {ok: true, handled_by_page: true};
  const rich = target.isContentEditable || doc.designMode === "on";
  const asHtml = rich && !!o.html;
  const done = asHtml ? doc.execCommand("insertHTML", false, o.html)
    : doc.execCommand("insertText", false, o.text);
  return done ? {ok: true, handled_by_page: false, rich: asHtml} : {ok: false, reason_code: "not_editable"};
}
"""


def bound_call(script: str, options: dict[str, Any], *, with_element: bool = True, require: str = "",
               at_point: bool = False) -> str:
    """A function an MCP ``evaluate`` can run, with the options inlined as JSON.

    With ``require``, a result whose field of that name is falsy throws, so
    the backend reports the call as failed instead of as done. With
    ``at_point`` the script is bound to the element at ``options``' x, y
    (``AT_POINT``) instead of a target. A script that returns a promise is
    awaited.
    """
    payload = json.dumps(options, ensure_ascii=False)
    if at_point:
        head, element = "() => ", f"((o) => {AT_POINT})({payload})"
    else:
        head, element = ("(element) => ", "element") if with_element else ("() => ", "null")
    call = f"({script.strip()})({element}, {payload})"
    if not require:
        return f"{head}{call}"
    return (f"async {head}{{ const r = await {call}; if (!r || !r[{json.dumps(require)}]) "
            f"throw new Error((r && r.reason_code) || {json.dumps(require + '_failed')}); return r; }}")


__all__ = [
    "AT_POINT", "CENTER_SCRIPT", "CHECK_SCRIPT", "DRAG_SCRIPT", "HOLD_SCRIPT", "ActionError", "EVALUATE_ACTION", "FIELD_KIND_SCRIPT", "HOVER_SCRIPT", "INSERT_TEXT_SCRIPT", "MAX_RESULT_CHARS",
    "MAX_SCRIPT_CHARS", "PAGE_SHARE", "PASTE_SCRIPT", "POINTER_SCRIPT", "SCROLL_SCRIPT", "bound_call", "checked",
    "click_options", "evaluate_source", "evaluation_text", "find_lines", "modifiers", "mouse_options",
    "option_values", "paste_payload", "scroll_delta",
]
