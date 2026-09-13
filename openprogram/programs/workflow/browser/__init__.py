"""DOM-first agent for an exact OpenProgram built-in browser Page."""
from __future__ import annotations

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from contextvars import copy_context
import itertools
import json
import math
import re
import time
import uuid
from typing import Any, Mapping
from urllib.parse import urlparse

from openprogram.agentic_programming import agent
from openprogram.agentic_programming.function import CancelledError, agentic_function
from openprogram.programs import ToolReturn
from openprogram.programs._runtime import function
from openprogram.providers.utils.errors import ExecInterrupt
from openprogram.web_use_contract import (
    normalize_web_use_arguments,
    web_use_parameters,
)


_INTERACTIVE_SELECTOR = (
    "a[href],button,input,textarea,select,summary,[role=button],"
    "[role=link],[role=checkbox],[role=radio],[role=tab],[role=menuitem],"
    "[contenteditable=true],[tabindex]:not([tabindex='-1'])"
)
_OBSERVE_SCRIPT = r"""
() => {
  const selector = %r;
  const nodes = Array.from(document.querySelectorAll(selector));
  const visible = (el) => {
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== "hidden" && style.display !== "none"
      && rect.width > 0 && rect.height > 0;
  };
  const nameOf = (el) => (
    el.getAttribute("aria-label") || el.getAttribute("title")
    || el.getAttribute("placeholder") || el.innerText || el.value || ""
  ).replace(/\s+/g, " ").trim().slice(0, 240);
  return {
    text: (document.body?.innerText || "").slice(0, 12000),
    viewport_width: window.innerWidth,
    viewport_height: window.innerHeight,
    navigation_time_origin: performance.timeOrigin,
    scroll_x: window.scrollX,
    scroll_y: window.scrollY,
    device_scale_factor: window.devicePixelRatio || 1,
    elements: nodes.map((el, dom_index) => ({
      dom_index,
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute("role") || (
        el.tagName === "A" ? "link" :
        el.tagName === "BUTTON" ? "button" :
        ["INPUT", "TEXTAREA"].includes(el.tagName) ? "textbox" :
        el.tagName.toLowerCase()
      ),
      name: nameOf(el),
      disabled: Boolean(el.disabled || el.getAttribute("aria-disabled") === "true"),
    })).filter((item) => visible(nodes[item.dom_index])).slice(0, 120),
  };
}
""" % _INTERACTIVE_SELECTOR

_REF_SNAPSHOT_SCRIPT = r"""
(el) => {
  const style = getComputedStyle(el);
  const rect = el.getBoundingClientRect();
  const name = (
    el.getAttribute("aria-label") || el.getAttribute("title")
    || el.getAttribute("placeholder") || el.innerText || el.value || ""
  ).replace(/\s+/g, " ").trim().slice(0, 240);
  return {
    connected: el.isConnected,
    visible: style.visibility !== "hidden" && style.display !== "none"
      && rect.width > 0 && rect.height > 0,
    tag: el.tagName.toLowerCase(),
    role: el.getAttribute("role") || (
      el.tagName === "A" ? "link" :
      el.tagName === "BUTTON" ? "button" :
      ["INPUT", "TEXTAREA"].includes(el.tagName) ? "textbox" :
      el.tagName.toLowerCase()
    ),
    name,
    disabled: Boolean(el.disabled || el.getAttribute("aria-disabled") === "true"),
    x: rect.x,
    y: rect.y,
    width: rect.width,
    height: rect.height,
  };
}
"""

_VIEWPORT_SCRIPT = """
() => ({
  viewport_width: window.innerWidth,
  viewport_height: window.innerHeight,
  navigation_time_origin: performance.timeOrigin,
  device_scale_factor: window.devicePixelRatio || 1,
  scroll_x: window.scrollX,
  scroll_y: window.scrollY,
})
"""

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

_BACKGROUND_REF_CLICK_SCRIPT = r"""
element => {
  const cursor = globalThis.__openprogramAgentCursor;
  if (cursor?.armed && typeof cursor.show === "function") {
    const rect = element.getBoundingClientRect();
    cursor.armed = false;
    cursor.show(rect.left + rect.width / 2, rect.top + rect.height / 2);
  }
  if (typeof element.click === "function") {
    element.click();
    return;
  }
  element.dispatchEvent(new MouseEvent("click", {
    bubbles: true,
    cancelable: true,
    view: window,
  }));
}
"""

_CAPTURE_HANDLES_SCRIPT = r"""
() => {
  const nodes = Array.from(document.querySelectorAll(%r));
  const visible = (el) => {
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== "hidden" && style.display !== "none"
      && rect.width > 0 && rect.height > 0;
  };
  return nodes.filter(visible).slice(0, 120);
}
""" % _INTERACTIVE_SELECTOR


_TOOL_PARAMETERS = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                "observe", "screenshot", "navigate", "click", "type",
                "press", "scroll", "hover", "select", "wait", "verify",
            ],
        },
        "expected_frame_id": {
            "type": "string",
            "description": "Latest frame_id returned by observe.",
        },
        "ref": {
            "type": "string",
            "description": "Element ref returned by observe, for example e3.",
        },
        "x": {
            "type": "number",
            "description": "Viewport CSS x coordinate; click only after screenshot.",
        },
        "y": {
            "type": "number",
            "description": "Viewport CSS y coordinate; click only after screenshot.",
        },
        "url": {"type": "string"},
        "text": {"type": "string"},
        "key": {"type": "string"},
        "value": {"type": "string", "minLength": 1},
        "amount": {"type": "integer"},
        "assertion": {
            "type": "string",
            "enum": [
                "text_contains", "text_not_contains", "url_contains",
                "title_contains", "element_present",
            ],
        },
    },
    "required": ["action"],
    "allOf": [{
        "if": {
            "properties": {"action": {"const": "verify"}},
            "required": ["action"],
        },
        "then": {"required": ["expected_frame_id", "assertion", "value"]},
    }],
    "additionalProperties": False,
}

_GUI_TOOL_PARAMETERS = {
    **_TOOL_PARAMETERS,
    "properties": {
        **_TOOL_PARAMETERS["properties"],
        "action": {
            **_TOOL_PARAMETERS["properties"]["action"],
            "enum": [
                *_TOOL_PARAMETERS["properties"]["action"]["enum"],
                "switch_page",
            ],
        },
        "page_context_token": {
            "type": "string",
            "maxLength": 128,
            "description": "Exact Page token returned in the current Page inventory.",
        },
    },
}

def _origin(url: str) -> str:
    parsed = urlparse(url or "")
    if not parsed.scheme or not parsed.hostname:
        return ""
    default = 443 if parsed.scheme == "https" else 80
    port = parsed.port or default
    return f"{parsed.scheme}://{parsed.hostname}:{port}"


def _is_local(url: str) -> bool:
    host = (urlparse(url or "").hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def _is_http_url(url: str) -> bool:
    parsed = urlparse(url or "")
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)


def _browser_agent_requires_approval(url: str = "", **_kw):
    if url and not _is_local(url):
        return f"browser_agent will open external origin {_origin(url)}"
    return False


from ._runtime.controller import (
    BrowserPageController,
)
from ._runtime.prompts import (
    _step_prompt,
    _screenshot_image_block,
    _result_for_prompt,
    _release_screenshot_payload,
)
from ._runtime.commands import (
    _run_browser_task_commands,
)
from ._runtime.page_recovery import (
    _requested_url,
    _has_usable_page,
    _open_page_error,
    _opened_observation_is_live,
    _start_session_on_opened_page,
    _recover_web_use_page,
)
from ._runtime.web_use import (
    _execute_web_use,
    execute_direct_web_use,
)
from ._runtime.tasks import (
    _run_browser_task,
)


def _new_controller() -> BrowserPageController:
    return BrowserPageController()


@agentic_function(
    name="browser_agent",
    toolset=("browser",),
    unsafe_in=("wechat", "telegram", "plan"),
    requires_approval=_browser_agent_requires_approval,
    defer=False,
    input={
        "task": {"description": "Browser task", "multiline": True},
        "url": {"description": "Optional initial http(s) URL"},
        "max_steps": {"description": "Maximum state-changing actions", "hidden": True, "advanced": True},
        "max_seconds": {"description": "Wall-clock limit in seconds", "hidden": True, "advanced": True},
        "backend": {
            "description": "Optional web_use backend for GUI Agent Harness",
            "hidden": True,
            "advanced": True,
            "options": [
                "playwright_mcp", "chrome_devtools_mcp", "open_claude_chrome",
            ],
        },
        "runtime": {"hidden": True},
    },
)
def browser_agent(
    task: str,
    url: str = "",
    max_steps: int = 20,
    max_seconds: int = 300,
    backend: str = "",
    runtime=None,
) -> dict:
    """Complete a task in one exact OpenProgram built-in browser Page."""
    if backend:
        return _run_browser_task_commands(
            task=task,
            backend=backend,
            max_steps=max_steps,
            max_seconds=max_seconds,
            runtime=runtime,
        )
    return _run_browser_task(
        task=task, url=url, max_steps=max_steps, max_seconds=max_seconds,
        runtime=runtime,
    )


DEFAULT_GUI_BROWSER_START_URL = "https://www.google.com/"
_CANCELLATION_ERRORS = (CancelledError, ExecInterrupt, asyncio.CancelledError)
_GUI_TASK_ERRORS = (*_CANCELLATION_ERRORS, Exception)


@agentic_function(
    name="web_use",
    toolset=("browser",),
    unsafe_in=("wechat", "telegram", "plan"),
    requires_approval=_browser_agent_requires_approval,
    defer=True,
    timeout=120,
    parameters=web_use_parameters(),
    input={
        "command": {
            "description": (
                "Call list_pages first; then observe, act, verify, or close. "
                "observe or act with url opens a desktop web tab when no Page exists."
            ),
        },
        "backend": {"description": "Backend used when observe creates a session", "hidden": True, "advanced": True},
        "page": {"description": "Turn Page alias used by observe; never a URL"},
        "web_session_id": {"description": "Session returned by observe", "hidden": True, "advanced": True},
        "page_context_token": {"hidden": True, "advanced": True},
        "arguments": {
            "description": (
                "Command-specific arguments. act needs action; expected_frame_id "
                "is filled from the last observe when omitted. action, url, text, "
                "and ref may also be passed at the top level."
            ),
        },
        "runtime": {"hidden": True},
    },
)
def web_use(
    command: str,
    backend: str = "",
    page: str = "",
    page_context_token: str = "",
    web_session_id: str = "",
    arguments: dict | None = None,
    runtime=None,
) -> dict | ToolReturn:
    """List, observe, or control exact Pages in OpenProgram's built-in browser.

    Start with ``list_pages``. Select a returned ``page_context_token`` for
    ``observe``; do not pass a URL as ``page``. ``observe`` or ``act`` with
    ``url`` opens a desktop web tab when no Page is available.
    """
    result = _execute_web_use(
        command, backend, page, page_context_token, web_session_id, arguments,
    )
    if isinstance(result, dict) and result.get("ok") is False:
        # Reopen the exact lost target, but never replay a possibly completed write.
        url = result.get("recovery_url")
        if result.get("reason_code") in {"target_lost", "page_context_stale", "page_closed", "binding_not_found", "page_context_not_found"} and isinstance(url, str) and url.startswith(("http://", "https://")):
            result = _recover_web_use_page(result, backend=backend)
            if isinstance(result, dict) and result.get("ok") is not False:
                result.update(recovered_page=True, observe_required=True,
                              message="The page was observed again. Continue using this fresh observation; the previous action was not replayed.")
                return result
        return ToolReturn(json_data=result, is_error=True)
    return result


# The Page inventory, WebSession registry, and renderer WebSocket registry are
# worker-owned. Keep the agentic runtime-card UI, but execute this bounded tool
# in the worker instead of copying those registries into an isolated process.
if getattr(web_use, "_agent_tool", None) is not None:
    setattr(web_use._agent_tool, "_run_in_worker", True)


__all__ = [
    "browser_agent",
    "web_use",
    "execute_direct_web_use",
    "BrowserPageController",
]
