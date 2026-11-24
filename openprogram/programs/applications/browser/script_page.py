"""The browser family's actions as scripts inside the page, for browsers without the Playwright Extension.

Safari (``safari.py``) and Firefox (``firefox.py``) reach their pages through
a channel that runs JavaScript in a tab: AppleScript's ``do JavaScript`` and
WebDriver BiDi's ``script.evaluate``. Everything else is the same for both
and lives here:

- ``get_state`` walks the DOM (open shadow roots and same-origin frames too)
  and prints it as the outline the other browsers print: one line per
  element with its role, name, state and ref (``- button "Next" [ref=e12]``).
  A ref is kept for its element in a ``WeakMap`` of the page, so the page's
  own DOM is never changed and a ref lasts while its element does.
- The actions (``click``, ``type_text``, ``set_value``, ``scroll``, ``hover``,
  ``drag``, ``press_key``, ``paste``, ``wait``, ``find``, ``evaluate``) find
  their element by ref, or by role and name, and run as page scripts with the
  arguments ``page_actions`` reads for every browser, so an action means
  the same everywhere. A backend that has real input (Firefox) sends pointer
  and keys itself and uses the page only to find where an element is.

A backend supplies the tabs (``tabs``, ``select``), ``_evaluate`` in the
selected tab, ``_screenshot``, ``_navigate``, ``_create`` and ``_close``; an
action it cannot do answers ``unsupported_action`` with the reason.
"""
from __future__ import annotations

import json
import time
from typing import Any, Mapping

from . import page_actions as pa

# How long an action's effect is given before the page is read again.
SETTLE_SECONDS = 0.15
# The outline is cut after this many characters.
MAX_OUTLINE_CHARS = 60_000

# The page side: one library, run as ``(() => { LIB; return run(options, fn); })()``.
LIBRARY = r"""
const S = (() => {
  if (window.__openprogram) return window.__openprogram;
  const made = {next: 1, refs: new Map(), ids: new WeakMap()};
  try { Object.defineProperty(window, "__openprogram", {value: made, configurable: true}); }
  catch (e) { window.__openprogram = made; }
  return made;
})();
const POINTER = __POINTER__;
const HOVER = __HOVER__;
const SCROLL = __SCROLL__;
const INSERT = __INSERT__;
const PASTE = __PASTE__;
const FIELD_KIND = __FIELD_KIND__;
const coded = (code, message) => ({ok: false, reason_code: code, message});
const clean = (t, n) => { t = String(t || "").replace(/\s+/g, " ").trim(); n = n || 100; return t.length > n ? t.slice(0, n - 1) + "…" : t; };
const refOf = (el) => {
  let r = S.ids.get(el);
  if (!r) { r = "e" + (S.next++); S.ids.set(el, r); S.refs.set(r, new WeakRef(el)); }
  return r;
};
const byRef = (r) => { const w = S.refs.get(r); const el = w && w.deref(); return el && el.isConnected ? el : null; };
const INPUTS = {button: "button", submit: "button", reset: "button", image: "button", file: "button", color: "button",
  checkbox: "checkbox", radio: "radio", range: "slider", number: "spinbutton", search: "searchbox"};
const HEADINGS = {h1: 1, h2: 2, h3: 3, h4: 4, h5: 5, h6: 6};
const TAGS = {button: "button", textarea: "textbox", option: "option", nav: "navigation", main: "main",
  header: "banner", footer: "contentinfo", aside: "complementary", form: "form", dialog: "dialog", ul: "list",
  ol: "list", li: "listitem", table: "table", tr: "row", td: "cell", th: "columnheader", summary: "button",
  details: "group", fieldset: "group", progress: "progressbar", iframe: "iframe", meter: "meter", hr: "separator"};
const FROM_CONTENT = new Set(["button", "link", "heading", "option", "tab", "menuitem", "menuitemcheckbox",
  "menuitemradio", "treeitem", "switch", "cell", "columnheader", "rowheader", "checkbox", "radio", "generic"]);
const FIELDS = new Set(["textbox", "searchbox", "combobox", "listbox", "checkbox", "radio", "slider", "spinbutton", "switch"]);
const roleOf = (el) => {
  const own = (el.getAttribute("role") || "").trim().split(/\s+/)[0];
  if (own && own !== "presentation" && own !== "none") return own;
  const tag = el.localName;
  if (tag === "a" || tag === "area") return el.hasAttribute("href") ? "link" : null;
  if (tag === "input") {
    const type = (el.getAttribute("type") || "text").toLowerCase();
    return type === "hidden" ? null : INPUTS[type] || "textbox";
  }
  if (tag === "select") return el.multiple || el.size > 1 ? "listbox" : "combobox";
  if (tag === "img") return el.getAttribute("alt") === "" ? null : "img";
  if (HEADINGS[tag]) return "heading";
  if (TAGS[tag]) return TAGS[tag];
  if (el.isContentEditable && !(el.parentElement && el.parentElement.isContentEditable)) return "textbox";
  if (el.hasAttribute("onclick") || (el.hasAttribute("tabindex") && el.tabIndex >= 0)) return "generic";
  return null;
};
const textOf = (el) => el.innerText !== undefined ? el.innerText : el.textContent;
const labelText = (label) => {
  const copy = label.cloneNode(true);
  for (const control of copy.querySelectorAll("select,input,textarea,button")) control.remove();
  return copy.textContent;
};
const nameOf = (el, role) => {
  const label = el.getAttribute("aria-label");
  if (label && label.trim()) return clean(label);
  const by = el.getAttribute("aria-labelledby");
  if (by) {
    const t = by.split(/\s+/).map(id => el.ownerDocument.getElementById(id)).filter(Boolean).map(textOf).join(" ");
    if (t.trim()) return clean(t);
  }
  const tag = el.localName;
  if (tag === "input" && ["button", "submit", "reset"].includes(el.type))
    return clean(el.value || (el.type === "submit" ? "Submit" : el.type === "reset" ? "Reset" : ""));
  if (FIELDS.has(role) || tag === "input" || tag === "select" || tag === "textarea") {
    if (el.labels && el.labels.length) {
      const t = Array.from(el.labels).map(labelText).join(" ");
      if (t.trim()) return clean(t);
    }
    if (FROM_CONTENT.has(role) && tag !== "input") return clean(textOf(el));
    return clean(el.getAttribute("placeholder") || el.getAttribute("title") || "");
  }
  if (tag === "img" || tag === "area" || (tag === "input" && el.type === "image"))
    return clean(el.getAttribute("alt") || el.getAttribute("title") || "");
  if (FROM_CONTENT.has(role)) return clean(textOf(el));
  return clean(el.getAttribute("title") || "");
};
const styleOf = (el) => { try { return el.ownerDocument.defaultView.getComputedStyle(el); } catch (e) { return null; } };
const gone = (el) => {
  if (el.hidden || el.getAttribute("aria-hidden") === "true" || el.localName === "script" || el.localName === "style"
      || el.localName === "noscript" || el.localName === "template" || el.localName === "head") return true;
  const style = styleOf(el);
  return !!style && style.display === "none";
};
const shown = (el) => { const style = styleOf(el); return !style || (style.visibility !== "hidden" && style.visibility !== "collapse"); };
const childrenOf = (el) => {
  if (el.shadowRoot) return Array.from(el.shadowRoot.childNodes);
  if (el.localName === "slot") { const assigned = el.assignedNodes({flatten: true}); if (assigned.length) return assigned; }
  if (el.localName === "iframe") {
    try { const doc = el.contentDocument; if (doc && doc.body) return [doc.body]; } catch (e) {}
    return [];
  }
  return Array.from(el.childNodes);
};
const states = (el, role) => {
  const out = [];
  const aria = (name) => el.getAttribute("aria-" + name);
  if (role === "heading") out.push("level=" + (HEADINGS[el.localName] || aria("level") || 2));
  if (role === "checkbox" || role === "radio" || role === "switch" || role === "menuitemcheckbox") {
    const mixed = el.indeterminate || aria("checked") === "mixed";
    if (mixed) out.push("checked=mixed"); else if (el.checked || aria("checked") === "true") out.push("checked");
  }
  if (el.disabled || aria("disabled") === "true") out.push("disabled");
  if (aria("expanded")) out.push(aria("expanded") === "true" ? "expanded" : "collapsed");
  if (aria("pressed") === "true") out.push("pressed");
  if (el.selected || aria("selected") === "true") out.push("selected");
  if (el === el.ownerDocument.activeElement && el !== el.ownerDocument.body) out.push("active");
  return out.map(s => " [" + s + "]").join("");
};
const valueOf = (el, role) => {
  if (el.localName === "select") return clean(Array.from(el.selectedOptions).map(o => o.label).join(", "), 200);
  if (role === "textbox" || role === "searchbox" || role === "spinbutton" || role === "slider" || role === "combobox") {
    if (el.localName === "input" && el.type === "password") return el.value ? "••••" : "";
    if ("value" in el && typeof el.value === "string") return clean(el.value, 200);
    if (el.isContentEditable) return clean(textOf(el), 200);
  }
  return "";
};
const quote = (t) => JSON.stringify(t);
const INLINE = new Set(["b", "i", "em", "strong", "span", "code", "small", "abbr", "sub", "sup", "mark", "u", "s",
  "time", "kbd", "q", "cite", "var", "label", "font"]);
const snapshot = () => {
  const lines = [];
  let size = 0, cut = false;
  // Text runs on until an element line or the end of a block, so a sentence with links in it stays one line.
  let words = [], wordsDepth = 0;
  const emit = (depth, text) => {
    if (cut) return;
    const line = "  ".repeat(depth) + "- " + text;
    size += line.length + 1;
    if (size > __MAX__) { cut = true; return; }
    lines.push(line);
  };
  const flush = () => {
    const t = clean(words.join(" "), 300);
    words = [];
    if (t) emit(wordsDepth, "text: " + quote(t));
  };
  const push = (depth, text) => { flush(); emit(depth, text); };
  const walk = (node, depth, quiet) => {
    if (cut) return;
    if (node.nodeType === 3) {
      if (quiet) return;
      const t = node.textContent.trim();
      if (!t) return;
      if (words.length && wordsDepth !== depth) flush();
      wordsDepth = depth;
      words.push(t);
      return;
    }
    if (node.nodeType !== 1) return;
    const el = node;
    if (gone(el)) return;
    const role = shown(el) ? roleOf(el) : null;
    if (!role) {
      for (const child of childrenOf(el)) walk(child, depth, quiet);
      const style = styleOf(el);
      const inline = INLINE.has(el.localName) || (!!style && String(style.display).startsWith("inline"));
      if (!inline || el.localName === "br") flush();
      return;
    }
    const name = nameOf(el, role);
    const value = valueOf(el, role);
    let line = role + (name ? " " + quote(name) : "") + states(el, role) + " [ref=" + refOf(el) + "]";
    if (value) line += ": " + quote(value);
    push(depth, line);
    if (role === "link" && el.getAttribute("href")) push(depth + 1, "/url: " + clean(el.href, 300));
    if (el.localName === "select" || el.localName === "textarea" || (el.localName === "input")) return;
    const named = FROM_CONTENT.has(role) && !!name;
    for (const child of childrenOf(el)) walk(child, depth + 1, quiet || named);
    flush();
  };
  walk(document.body || document.documentElement, 0, false);
  flush();
  return {url: location.href, title: document.title, outline: lines.join("\n"), truncated: cut};
};
const everything = (visit) => {
  const walk = (node) => {
    if (node.nodeType !== 1) return;
    visit(node);
    for (const child of childrenOf(node)) walk(child);
  };
  walk(document.documentElement);
};
const find = (o, prefix) => {
  const written = o[prefix + "element"];
  if (written) {
    const ref = String(written).trim().replace(/^@/, "").replace(/^ref=/, "");
    const el = byRef(ref);
    if (!el) throw coded("element_gone", "element " + ref + " is no longer on the page; read the page for current refs");
    return el;
  }
  const role = o[prefix + "role"];
  if (!role) return null;
  const name = o[prefix + "name"];
  const found = [];
  everything(el => { if (!gone(el) && roleOf(el) === role && (name == null || name === "" || nameOf(el, role) === clean(name))) found.push(el); });
  const label = role + (name ? " " + quote(name) : "");
  if (!found.length) throw coded("element_gone", label + " is not on the page; read the page for the element's ref");
  if (found.length > 1) throw coded("ambiguous_element", found.length + " elements are " + label + "; use a ref");
  return found[0];
};
const at = (o, prefix) => {
  const x = o[prefix + "x"], y = o[prefix + "y"];
  if (x == null || y == null) return null;
  const el = document.elementFromPoint(x, y);
  if (!el) throw coded("invalid_point", "nothing on the page is at " + x + "," + y);
  return el;
};
const point = (el) => {
  el.scrollIntoView({block: "nearest", inline: "nearest", behavior: "instant"});
  const r = el.getBoundingClientRect();
  let x = r.left + r.width / 2, y = r.top + r.height / 2;
  for (let w = el.ownerDocument.defaultView; w && w !== window && w.frameElement; w = w.parent) {
    const f = w.frameElement.getBoundingClientRect();
    x += f.left; y += f.top;
  }
  return {x, y, hidden: document.visibilityState !== "visible"};
};
const fire = (el, type, x, y, extra) => {
  const base = {bubbles: true, cancelable: true, composed: true, view: window, clientX: x, clientY: y, ...extra};
  return el.dispatchEvent(type.startsWith("pointer") ? new PointerEvent(type, {...base, pointerType: "mouse", isPrimary: true})
    : type.startsWith("drag") || type === "drop" ? new DragEvent(type, base) : new MouseEvent(type, base));
};
const MODS = {Control: "ctrlKey", Meta: "metaKey", Alt: "altKey", Shift: "shiftKey"};
const TABBABLE = "a[href],button,input,select,textarea,[tabindex],[contenteditable=''],[contenteditable=true],summary";
const editable = (el) => el && (el.isContentEditable || el.localName === "textarea"
  || (el.localName === "input" && !["button", "submit", "reset", "checkbox", "radio", "file", "image", "range", "color"].includes(el.type)));
const keys = (el, o) => {
  const doc = document;
  if (el && typeof el.focus === "function") el.focus({preventScroll: true});
  const target = doc.activeElement || doc.body;
  const flags = {};
  for (const m of o.modifiers) flags[MODS[m]] = true;
  const key = o.key;
  const init = {key, code: o.code, bubbles: true, cancelable: true, composed: true, ...flags};
  const go = target.dispatchEvent(new KeyboardEvent("keydown", init));
  let did = "events";
  const command = flags.ctrlKey || flags.metaKey;
  if (go) {
    if (command && key.toLowerCase() === "a") {
      if (typeof target.select === "function") target.select(); else doc.execCommand("selectAll");
      did = "select all";
    } else if (key.length === 1 && !command && !flags.altKey) {
      target.dispatchEvent(new KeyboardEvent("keypress", init));
      if (editable(target)) { doc.execCommand("insertText", false, key); did = "typed"; }
      else if (key === " " && typeof target.click === "function" && target !== doc.body) { target.click(); did = "clicked"; }
    } else if (key === "Enter") {
      target.dispatchEvent(new KeyboardEvent("keypress", init));
      if (target.isContentEditable) { doc.execCommand("insertParagraph"); did = "new line"; }
      else if (target.localName === "textarea") { doc.execCommand("insertText", false, "\n"); did = "new line"; }
      else if (target.form && target.localName === "input") {
        if (typeof target.form.requestSubmit === "function") target.form.requestSubmit(); else target.form.submit();
        did = "submitted the form";
      } else if (typeof target.click === "function" && target !== doc.body && target.matches("a[href],button,summary,[role=button],[role=link]")) {
        target.click(); did = "clicked";
      }
    } else if (key === "Tab") {
      const all = Array.from(doc.querySelectorAll(TABBABLE)).filter(e => e.tabIndex >= 0 && !e.disabled && !gone(e) && e.getClientRects().length);
      const here = all.indexOf(target);
      const next = all[(here + (flags.shiftKey ? -1 : 1) + all.length) % all.length];
      if (next) { next.focus(); did = "moved focus"; }
    } else if (key === "Backspace" && editable(target)) { doc.execCommand("delete"); did = "deleted"; }
    else if (key === "Delete" && editable(target)) { doc.execCommand("forwardDelete"); did = "deleted"; }
  }
  target.dispatchEvent(new KeyboardEvent("keyup", init));
  return {ok: true, default: did};
};
const setValue = (el, o) => {
  const kind = FIELD_KIND(el);
  if (kind === "select") {
    const wanted = o.values;
    let chosen = 0;
    for (const option of el.options) {
      const on = wanted.includes(option.value) || wanted.includes(option.label) || wanted.includes(clean(option.text, 1000));
      if (el.multiple) { option.selected = on; if (on) chosen++; }
      else if (on && !chosen) { option.selected = true; chosen = 1; }
    }
    if (!chosen) return coded("invalid_value", "no option of the select box has value or label " + wanted.map(quote).join(", "));
  } else if (kind === "check") {
    if (o.checked === null || o.checked === undefined) return coded("invalid_value", "a checkbox or radio button takes value true or false");
    if (el.checked !== o.checked) el.click();
    return {ok: true, checked: el.checked};
  } else if (el.isContentEditable) {
    el.focus({preventScroll: true});
    document.execCommand("selectAll");
    document.execCommand("insertText", false, o.text);
    return {ok: true};
  } else {
    el.focus({preventScroll: true});
    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), "value");
    if (setter && setter.set) setter.set.call(el, o.text); else el.value = o.text;
  }
  el.dispatchEvent(new Event("input", {bubbles: true, composed: true}));
  el.dispatchEvent(new Event("change", {bubbles: true}));
  return {ok: true};
};
const scrollBy = (el, o) => {
  const box = el || document.documentElement;
  const across = o.direction === "left" || o.direction === "right";
  const size = el && el !== document.documentElement ? (across ? el.clientWidth : el.clientHeight)
    : (across ? window.innerWidth : window.innerHeight);
  const amount = o.pixels != null ? Math.abs(o.pixels) : Math.round(Math.abs(o.pages) * (size || 800) * o.share);
  const d = {down: [0, amount], up: [0, -amount], right: [amount, 0], left: [-amount, 0]}[o.direction];
  return SCROLL(box, {dx: d[0], dy: d[1]});
};
const drag = (from, to, o) => {
  const a = point(from), b = point(to);
  const sx = o.x != null ? o.x : a.x, sy = o.y != null ? o.y : a.y;
  const tx = o.to_x != null ? o.to_x : b.x, ty = o.to_y != null ? o.to_y : b.y;
  fire(from, "pointerdown", sx, sy, {button: 0, buttons: 1}); fire(from, "mousedown", sx, sy, {button: 0, buttons: 1});
  const source = from.closest("[draggable=true]") || (from.draggable ? from : null);
  if (source) {
    const data = new DataTransfer();
    fire(source, "dragstart", sx, sy, {dataTransfer: data});
    fire(to, "dragenter", tx, ty, {dataTransfer: data});
    fire(to, "dragover", tx, ty, {dataTransfer: data});
    fire(to, "drop", tx, ty, {dataTransfer: data});
    fire(source, "dragend", tx, ty, {dataTransfer: data});
  } else {
    for (let i = 1; i <= 5; i++) {
      const x = sx + (tx - sx) * i / 5, y = sy + (ty - sy) * i / 5;
      const over = document.elementFromPoint(x, y) || to;
      fire(over, "pointermove", x, y, {buttons: 1}); fire(over, "mousemove", x, y, {buttons: 1});
    }
  }
  fire(to, "pointerup", tx, ty, {button: 0, buttons: 0}); fire(to, "mouseup", tx, ty, {button: 0, buttons: 0});
  return {ok: true};
};
const run = (o, fn) => {
  try {
    const el = (o.action === "snapshot" || o.action === "viewport" || o.action === "shows") ? null : (find(o, "") || at(o, ""));
    switch (o.action) {
      case "snapshot": return snapshot();
      case "viewport": return {width: innerWidth, height: innerHeight, top: Math.max(0, outerHeight - innerHeight),
        ratio: devicePixelRatio || 1, url: location.href, title: document.title, visible: document.visibilityState === "visible",
        focused: document.hasFocus(), ready: document.readyState};
      case "shows": return {ok: true, shows: !!document.body && document.body.innerText.includes(o.text)};
      case "point": if (!el) return coded("target_required", "name the element"); return {ok: true, ...point(el)};
      case "node": if (!el) return coded("target_required", "name the element"); return el;
      case "click": if (!el) return coded("target_required", "click needs an element or x and y");
        POINTER(el, {button: o.button, count: o.count, flags: o.flags}); return {ok: true};
      case "hover": if (!el) return coded("target_required", "hover needs an element or x and y"); return {ok: HOVER(el)};
      case "scroll": return {ok: true, ...scrollBy(el, o)};
      case "type": {
        if (el && typeof el.focus === "function") el.focus({preventScroll: true});
        const active = el || document.activeElement;
        if (o.clear && active) {
          if (typeof active.select === "function") active.select();
          else if (active.isContentEditable) document.execCommand("selectAll");
        }
        const done = INSERT(el, {text: o.text});
        return done.ok ? done : coded(done.reason_code === "focus_required" ? "target_required" : "invalid_arguments",
          done.reason_code === "focus_required" ? "no field has focus: name the element to type into" : "the element does not take text");
      }
      case "paste": { const done = PASTE(el, {text: o.text, html: o.html});
        return done.ok ? done : coded("invalid_arguments", "the element does not take a paste"); }
      case "key": return keys(el, o);
      case "focus": if (el && typeof el.focus === "function") el.focus({preventScroll: true}); return {ok: true};
      case "set_value": if (!el) return coded("target_required", "set_value needs an element"); return setValue(el, o);
      case "drag": { const to = find(o, "to_") || at(o, "to_");
        if (!el || !to) return coded("target_required", "drag needs element or x,y, and to_element or to_x,to_y");
        return drag(el, to, o); }
      case "history": if (o.which === "back") history.back(); else if (o.which === "forward") history.forward(); else location.reload();
        return {ok: true};
      case "evaluate": return fn(el);
    }
    return coded("unsupported_action", o.action + " is not a page action");
  } catch (e) {
    if (e && e.reason_code) return e;
    throw e;
  }
};
"""


def _library() -> str:
    return (LIBRARY.replace("__POINTER__", pa.POINTER_SCRIPT.strip()).replace("__HOVER__", pa.HOVER_SCRIPT.strip())
            .replace("__SCROLL__", pa.SCROLL_SCRIPT.strip()).replace("__INSERT__", pa.INSERT_TEXT_SCRIPT.strip())
            .replace("__PASTE__", pa.PASTE_SCRIPT.strip()).replace("__FIELD_KIND__", pa.FIELD_KIND_SCRIPT.strip())
            .replace("__MAX__", str(MAX_OUTLINE_CHARS)))


_LIBRARY: str | None = None


def page_script(options: Mapping[str, Any], function: str = "") -> str:
    """The page-side expression for one action: the library, then ``run(options, function)``. Its value is
    the action's result, or a promise of it (``evaluate`` with an async script)."""
    global _LIBRARY
    if _LIBRARY is None:
        _LIBRARY = _library()
    payload = json.dumps(dict(options), ensure_ascii=False)
    return f"(() => {{{_LIBRARY}\nreturn run({payload}, {function or 'null'});\n}})()"


def serialized(expression: str) -> str:
    """An expression whose value is a promise of the JSON text of ``expression``'s settled value."""
    return ("(async () => { const v = await (" + expression + "); "
            "try { return JSON.stringify(v === undefined ? null : v); } catch (e) { return JSON.stringify(String(v)); } })()")


class ScriptError(RuntimeError):
    """The page threw: the script ran and failed, in the page's words."""


class Unsupported(Exception):
    """The backend cannot do what was asked; the message says why."""


def chord(written: str) -> tuple[list[str], str]:
    """A key or chord in xdotool's names -> (modifiers, key) in DOM ``KeyboardEvent.key`` names."""
    from openprogram.programs.tools.computer_use.computer_use.keynames import playwright
    spelled = playwright(written)
    if spelled.endswith("++"):
        parts, key = spelled[:-2].split("+"), "+"
    else:
        *parts, key = spelled.split("+")
    modifiers = []
    for part in parts:
        if part == "ControlOrMeta":
            import sys
            part = "Meta" if sys.platform == "darwin" else "Control"
        if part and part not in modifiers:
            modifiers.append(part)
    if len(key) == 1 and key.isalpha():
        # A letter is lower case unless Shift is held, as KeyboardEvent.key has it.
        key = key.upper() if "Shift" in modifiers else key.lower()
    return modifiers, ("Space" if key == " " else key)


# KeyboardEvent.code of the keys whose code is not "Key" + the letter.
_CODES = {" ": "Space", "Enter": "Enter", "Tab": "Tab", "Escape": "Escape", "Backspace": "Backspace",
          "Delete": "Delete", "ArrowUp": "ArrowUp", "ArrowDown": "ArrowDown", "ArrowLeft": "ArrowLeft",
          "ArrowRight": "ArrowRight", "Home": "Home", "End": "End", "PageUp": "PageUp", "PageDown": "PageDown"}


def key_code(key: str) -> str:
    if len(key) == 1 and key.isalpha():
        return "Key" + key.upper()
    if len(key) == 1 and key.isdigit():
        return "Digit" + key
    return _CODES.get(key, key)


class ScriptPage:
    """One conversation's connection to a browser driven by page scripts: the actions, over a backend.

    ``run`` answers as ``extension.ExtensionBrowser.run`` does: ``ok``,
    ``result`` (the page's address and title as ``- Page URL:`` and
    ``- Page Title:`` lines, then what the action says), ``_images``, and on a
    failure ``reason_code`` and ``message``.
    """

    #: The browser's name and label, set by the backend.
    browser = ""
    label = ""
    #: Actions this backend cannot do, with the reason.
    lacks: dict[str, str] = {}

    def __init__(self) -> None:
        # Pixels of the last screenshot per CSS pixel, by tab: x,y of a step are pixels of that screenshot.
        self._scales: dict[str, float] = {}

    # -- what a backend supplies -------------------------------------------------

    def tabs(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def select(self, index: int, url: str) -> bool:
        raise NotImplementedError

    def _tab(self) -> str:
        """The key of the selected tab."""
        raise NotImplementedError

    def _evaluate(self, expression: str) -> Any:
        """The JSON value of ``expression`` (a value or a promise) in the selected tab; ``ScriptError`` when it
        throws."""
        raise NotImplementedError

    def _screenshot(self) -> tuple[bytes, float]:
        """A PNG of the selected tab's page and its pixels per CSS pixel; ``Unsupported`` with the reason."""
        raise Unsupported("this browser takes no screenshot")

    def _navigate(self, url: str) -> None:
        raise NotImplementedError

    def _create(self, url: str, visible: bool) -> None:
        """Open ``url`` in a new tab and select it."""
        raise NotImplementedError

    def _close(self, index: int) -> None:
        raise NotImplementedError

    def _history(self, which: str) -> None:
        self._page({"action": "history", "which": which})

    def _pointer(self, steps: list[dict[str, Any]], modifiers: list[str]) -> bool:
        """Real pointer input at CSS pixels of the viewport; False where the backend has none (the page's
        events are sent instead)."""
        return False

    def _keys(self, modifiers: list[str], key: str, seconds: float) -> bool:
        """A real key press; False where the backend has none."""
        return False

    def _upload(self, args: dict[str, Any]) -> dict[str, Any]:
        raise Unsupported(self.lacks.get("upload_file", f"upload_file is not available in {self.label}"))

    def _console(self, args: dict[str, Any]) -> dict[str, Any]:
        raise Unsupported(self.lacks.get("read_console_messages", f"read_console_messages is not available in {self.label}"))

    def _network(self, args: dict[str, Any]) -> dict[str, Any]:
        raise Unsupported(self.lacks.get("read_network_requests", f"read_network_requests is not available in {self.label}"))

    # -- helpers ---------------------------------------------------------------

    def _page(self, options: Mapping[str, Any], function: str = "") -> Any:
        return self._evaluate(page_script(options, function))

    def _checked(self, value: Any) -> dict[str, Any]:
        """A page result, raising its coded failure."""
        if isinstance(value, dict) and value.get("ok") is False and value.get("reason_code"):
            raise pa.ActionError(str(value["reason_code"]), str(value.get("message") or ""))
        return value if isinstance(value, dict) else {"ok": True, "value": value}

    def _point(self, args: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
        """Where a step points: the element's centre, or x,y of the last screenshot, in CSS pixels."""
        if args.get(f"{prefix}element") or args.get(f"{prefix}role"):
            found = self._checked(self._page({"action": "point", **_named(args, prefix)}))
            return {"x": float(found["x"]), "y": float(found["y"]), "hidden": bool(found.get("hidden"))}
        if args.get(f"{prefix}x") is None or args.get(f"{prefix}y") is None:
            raise pa.ActionError("target_required", "name the element, or give x and y")
        scale = self._scales.get(self._tab(), 1.0) or 1.0
        try:
            return {"x": float(args[f"{prefix}x"]) / scale, "y": float(args[f"{prefix}y"]) / scale, "hidden": False}
        except (TypeError, ValueError):
            raise pa.ActionError("invalid_point", "x and y are numbers") from None

    def _css(self, args: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
        """x,y of a step in CSS pixels, for the page scripts."""
        if args.get(f"{prefix}x") is None or args.get(f"{prefix}y") is None or args.get(f"{prefix}element") \
                or args.get(f"{prefix}role"):
            return {}
        point = self._point(args, prefix)
        return {f"{prefix}x": point["x"], f"{prefix}y": point["y"]}

    def _header(self, url: str, title: str) -> str:
        return f"- Page URL: {url}\n- Page Title: {title}"

    def _outline(self) -> tuple[str, str, str]:
        found = self._page({"action": "snapshot"})
        if not isinstance(found, dict):
            raise ScriptError("the page did not describe itself")
        outline = str(found.get("outline") or "")
        if found.get("truncated"):
            outline += "\n- text: \"[the outline was cut here; scroll, or find(text) for what comes after]\""
        return str(found.get("url") or ""), str(found.get("title") or ""), outline

    def _state_text(self) -> str:
        url, title, outline = self._outline()
        return f"{self._header(url, title)}\n- Page Snapshot:\n```yaml\n{outline}\n```"

    def _result(self, text: str, images: list[bytes] | None = None, failed: bool = False,
                **extra: Any) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": not failed, "browser": self.browser, "result": text, **extra}
        if images:
            out["_images"] = images
        return out

    def _after(self, said: str) -> dict[str, Any]:
        """An action's answer: what it did, then the page as it is now."""
        time.sleep(SETTLE_SECONDS)
        try:
            state = self._state_text()
        except ScriptError as exc:
            # The page was replaced (a navigation): its address is enough.
            state = f"(the page is loading: {exc})"
        return self._result(f"### Ran\n{said}\n{state}")

    # -- the actions -------------------------------------------------------------

    def run(self, action: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """One action on the selected tab; ``index`` names the tab for ``close_tab``."""
        args = dict(arguments)
        try:
            if action in self.lacks:
                raise Unsupported(self.lacks[action])
            return self._run(action, args)
        except pa.ActionError as exc:
            return exc.result()
        except Unsupported as exc:
            return {"ok": False, "reason_code": "unsupported_action", "message": str(exc)}
        except ScriptError as exc:
            return {"ok": False, "browser": self.browser, "result": f"the page answered with an error: {exc}",
                    "message": str(exc)}

    def _run(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        if action == "create_tab":
            url = str(args.get("url") or "").strip()
            if not url:
                raise pa.ActionError("url_required", "create_tab needs url")
            self._create(url, bool(args.get("visible")))
            return self._after(f"opened {url}")
        if action == "close_tab":
            self._close(int(args["index"]))
            return self._result("closed")
        if action == "get_state":
            return self._state(str(args.get("include") or "tree"))
        if action == "find":
            text, role = str(args.get("text") or ""), str(args.get("role") or "")
            if not text.strip() and not role.strip():
                raise pa.ActionError("text_required", "find needs text, role or both")
            url, title, outline = self._outline()
            return {**self._result(self._header(url, title)), "found": pa.find_lines(outline, text, role)}
        if action == "navigate":
            url = str(args.get("url") or "").strip()
            if not url:
                raise pa.ActionError("url_required", "navigate needs url")
            self._navigate(url)
            return self._after(f"went to {url}")
        if action in ("back", "forward", "reload"):
            before = str(self._viewport().get("url") or "")
            self._history(action)
            self._settle(before)
            return self._after(action)
        if action == "wait":
            return self._wait(args)
        if action == pa.EVALUATE_ACTION:
            value = self._page({"action": "evaluate", **_named(args, "")}, pa.evaluate_source(args))
            if isinstance(value, dict) and value.get("ok") is False and value.get("reason_code") in (
                    "element_gone", "ambiguous_element"):
                self._checked(value)
            url = self._viewport().get("url", "")
            return self._result(f"### Result\n{pa.evaluation_text(value)}\n- Page URL: {url}")
        if action == "upload_file":
            return self._upload(args)
        if action == "read_console_messages":
            return self._console(args)
        if action == "read_network_requests":
            return self._network(args)
        if action == "click":
            return self._click(args)
        if action == "hover":
            return self._hover(args)
        if action == "type_text":
            return self._type(args)
        if action == "press_key":
            return self._press(args)
        if action == "set_value":
            return self._set_value(args)
        if action == "scroll":
            pa.scroll_delta(args)
            pixels = args.get("pixels")
            options = {"action": "scroll", **_named(args, ""), **self._css(args),
                       "direction": str(args.get("direction") or "down").lower(),
                       "pixels": None if pixels in (None, "") else int(pixels),
                       "pages": float(args.get("pages") or 1), "share": pa.PAGE_SHARE}
            moved = self._checked(self._page(options))
            return self._after("scrolled" if moved.get("moved") else "scrolled; nothing moved: the page or box is at its end")
        if action == "drag":
            return self._drag(args)
        if action == "paste":
            payload = pa.paste_payload(args)
            self._checked(self._page({"action": "paste", **_named(args, ""), **payload}))
            return self._after("pasted")
        raise Unsupported(f"{action} is not available in {self.label}")

    def _settle(self, before: str = "", seconds: float = 15.0) -> None:
        """Until the page that is loading is ready, for up to ``seconds``: one at another address than
        ``before``, or, after a second, the same one loaded again."""
        start = time.monotonic()
        while time.monotonic() - start < seconds:
            time.sleep(0.2)
            try:
                viewport = self._viewport()
            except ScriptError:
                continue
            moved = not before or viewport.get("url") != before or time.monotonic() - start > 1.0
            if viewport.get("ready") == "complete" and moved:
                return

    def _viewport(self) -> dict[str, Any]:
        found = self._page({"action": "viewport"})
        return found if isinstance(found, dict) else {}

    def _state(self, include: str) -> dict[str, Any]:
        text, images = "", []
        if include in ("tree", "all"):
            text = self._state_text()
        if include in ("screenshot", "all"):
            try:
                image, scale = self._screenshot()
                self._scales[self._tab()] = scale
                images.append(image)
            except Unsupported as exc:
                if include == "screenshot":
                    raise
                text += f"\n(no screenshot: {exc})"
            if not text:
                viewport = self._viewport()
                text = self._header(str(viewport.get("url") or ""), str(viewport.get("title") or ""))
        return self._result(text, images)

    def _click(self, args: dict[str, Any]) -> dict[str, Any]:
        click = pa.click_options(args)
        hold = _seconds(args.get("seconds"))
        point = self._point(args)
        button = {"left": 0, "middle": 1, "right": 2}[click["button"]]
        presses: list[dict[str, Any]] = [{"type": "pointerMove", "x": point["x"], "y": point["y"]}]
        for number in range(click["count"]):
            presses.append({"type": "pointerDown", "button": button})
            if hold and number == click["count"] - 1:
                presses.append({"type": "pause", "duration": int(hold * 1000)})
            presses.append({"type": "pointerUp", "button": button})
        if not point["hidden"] and self._pointer(presses, click["modifiers"]):
            return self._after(f"clicked at {point['x']:.0f},{point['y']:.0f}")
        if hold:
            raise Unsupported(f"holding the button is not available in {self.label}: its clicks are page events")
        options = {"action": "click", **_named(args, ""), **self._css(args), "button": click["button"],
                   "count": click["count"], "flags": click["flags"]}
        self._checked(self._page(options))
        return self._after("clicked")

    def _hover(self, args: dict[str, Any]) -> dict[str, Any]:
        rest = _seconds(args.get("seconds"), 0.8)
        point = self._point(args)
        if point["hidden"] or not self._pointer([{"type": "pointerMove", "x": point["x"], "y": point["y"]}], []):
            self._checked(self._page({"action": "hover", **_named(args, ""), **self._css(args)}))
        if rest:
            time.sleep(rest)
        return self._after("hovered")

    def _drag(self, args: dict[str, Any]) -> dict[str, Any]:
        hold = _seconds(args.get("seconds"))
        start, end = self._point(args), self._point(args, "to_")
        moves = [{"type": "pointerMove", "x": start["x"], "y": start["y"]}, {"type": "pointerDown", "button": 0},
                 {"type": "pointerMove", "x": end["x"], "y": end["y"], "duration": 300}]
        if hold:
            moves.append({"type": "pause", "duration": int(hold * 1000)})
        moves.append({"type": "pointerUp", "button": 0})
        if not start["hidden"] and self._pointer(moves, []):
            return self._after("dragged")
        if hold:
            raise Unsupported(f"a drag that holds is not available in {self.label}")
        options = {"action": "drag", **_named(args, ""), **_named(args, "to_"), **self._css(args),
                   **self._css(args, "to_")}
        self._checked(self._page(options))
        return self._after("dragged")

    def _type(self, args: dict[str, Any]) -> dict[str, Any]:
        text = args.get("text")
        if not isinstance(text, str):
            raise pa.ActionError("text_required", "type_text needs text")
        self._checked(self._page({"action": "type", **_named(args, ""), "text": text, "clear": bool(args.get("clear"))}))
        if args.get("submit"):
            self._key([], "Enter", 0.0, {})
        return self._after(f"typed {len(text)} characters" + (" and pressed Return" if args.get("submit") else ""))

    def _press(self, args: dict[str, Any]) -> dict[str, Any]:
        written = str(args.get("key") or "").strip()
        if not written:
            raise pa.ActionError("key_required", "press_key needs key, such as Return or ctrl+a")
        hold = _seconds(args.get("seconds"))
        modifiers, key = chord(written)
        said = self._key(modifiers, key, hold, _named(args, ""))
        return self._after(f"pressed {written}{said}")

    def _key(self, modifiers: list[str], key: str, hold: float, element: dict[str, Any]) -> str:
        """Press one key: real where the backend has keys, else as page events with the browser's usual
        result for the keys a page cannot get otherwise (typing, Return, Tab, Backspace, select all)."""
        visible = True
        if element:
            self._checked(self._page({"action": "focus", **element}))
        if self._viewport().get("visible") is False:
            visible = False
        if visible and self._keys(modifiers, key, hold):
            return ""
        if hold:
            raise Unsupported(f"holding a key is not available in {self.label}: its keys are page events")
        dom_key = " " if key == "Space" else key
        found = self._checked(self._page({"action": "key", "key": dom_key, "code": key_code(dom_key),
                                          "modifiers": modifiers}))
        return f" (as page events: {found.get('default') or 'events'})"

    def _set_value(self, args: dict[str, Any]) -> dict[str, Any]:
        if not args.get("element") and not args.get("role"):
            raise pa.ActionError("target_required", "set_value needs an element: name it by its ref (or role and name)")
        options: dict[str, Any] = {"action": "set_value", **_named(args, "")}
        value = args.get("value")
        options["values"] = [str(item) for item in value] if isinstance(value, (list, tuple)) else [
            "" if value is None else str(value)]
        try:
            options["checked"] = pa.checked(args)
        except pa.ActionError:
            options["checked"] = None
        options["text"] = "" if value is None else str(value)
        self._checked(self._page(options))
        return self._after("set")

    def _wait(self, args: dict[str, Any]) -> dict[str, Any]:
        text = str(args.get("text") or "")
        try:
            seconds = max(0.0, min(float(args.get("seconds") or (10 if text else 1)), 30.0))
        except (TypeError, ValueError) as exc:
            raise pa.ActionError("invalid_seconds", "seconds must be a number") from exc
        if not text:
            time.sleep(seconds)
            return self._after(f"waited {seconds:g}s")
        deadline = time.monotonic() + seconds
        while True:
            try:
                found = self._page({"action": "shows", "text": text})
            except ScriptError:
                found = {}
            if isinstance(found, dict) and found.get("shows"):
                return self._after(f"{text!r} is on the page")
            if time.monotonic() >= deadline:
                return {**self._result(f"{text!r} did not appear within {seconds:g}s", failed=True),
                        "reason_code": "text_not_found"}
            time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))


def _named(args: Mapping[str, Any], prefix: str) -> dict[str, Any]:
    """The element a step names, as the page library finds it: by ref, or by role and name."""
    out = {}
    for key in ("element", "role", "name"):
        value = args.get(f"{prefix}{key}")
        if value not in (None, ""):
            out[f"{prefix}{key}"] = str(value)
    return out


def _seconds(value: Any, default: float = 0.0) -> float:
    try:
        seconds = float(default if value in (None, "") else value)
    except (TypeError, ValueError):
        raise pa.ActionError("invalid_seconds", "seconds must be a number") from None
    if not 0 <= seconds <= 10:
        raise pa.ActionError("invalid_seconds", "seconds must be between 0 and 10")
    return seconds


__all__ = ["LIBRARY", "ScriptError", "ScriptPage", "Unsupported", "chord", "key_code", "page_script", "serialized"]
