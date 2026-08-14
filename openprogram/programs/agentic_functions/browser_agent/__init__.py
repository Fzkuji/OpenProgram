"""DOM-first agent for the visible browser tab in OpenProgram Desktop."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import re
import uuid
from typing import Any
from urllib.parse import urlparse

from openprogram.agentic_programming.function import CancelledError, agentic_function
from openprogram.programs import ToolReturn
from openprogram.programs._runtime import function
from openprogram.providers.utils.errors import ExecInterrupt


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
        "url": {"type": "string"},
        "text": {"type": "string"},
        "key": {"type": "string"},
        "value": {"type": "string"},
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
    "additionalProperties": False,
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


class BrowserPageController:
    """One call-scoped browser session and its latest observation refs."""

    def __init__(self, browser_api=None, *, url: str = "", max_steps: int = 20):
        if browser_api is None:
            from openprogram.programs.functions.browser import browser as browser_api
        self.browser_api = browser_api
        self.initial_url = url
        self.max_steps = max(1, int(max_steps))
        self.session_id = ""
        self._frame: dict[str, Any] | None = None
        self._refs: dict[str, Any] = {}
        self._frame_seq = 0
        self._mutations = 0
        self._verified_mutation = -1
        self._evidence: list[dict[str, Any]] = []
        self._screenshot_frame = ""
        self._dom_signature = ""
        self._terminal_reason = ""
        self._owner = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="browser-agent-page",
        )
        self.tool = function(
            name="browser_page",
            description=(
                "Control only the visible OpenProgram browser tab. Start with "
                "observe. Use screenshot only for visual verification, canvas, "
                "or when DOM/ARIA refs cannot identify the target. Every write "
                "needs the latest frame_id and refs become stale after it."
            ),
            parameters=_TOOL_PARAMETERS,
            requires_approval=self._requires_approval,
            register_globally=False,
            max_result_chars=40_000,
        )(self.execute)

    def _requires_approval(self, action: str = "", url: str = "", **_kw):
        if action not in {
            "navigate", "click", "type", "press", "scroll", "hover", "select",
        }:
            return False
        target_url = url or str((self._frame or {}).get("url") or self.initial_url)
        if _is_local(target_url):
            return False
        return f"browser action '{action}' changes external origin {_origin(target_url)}"

    def _ensure_open(self) -> None:
        if self.session_id:
            return
        result = self.browser_api.execute(
            action="open", engine="app", url=self.initial_url or None,
        )
        match = re.search(r"`(br_[^`]+)`", str(result))
        if not match:
            self._terminal_reason = "target_lost"
            raise RuntimeError(str(result))
        self.session_id = match.group(1)

    def _session(self) -> dict[str, Any]:
        self._ensure_open()
        session = self.browser_api._sessions.get(self.session_id)
        if not isinstance(session, dict):
            self._terminal_reason = "target_lost"
            raise RuntimeError("visible browser session was lost")
        return session

    def _page(self):
        session = self._session()
        current = getattr(self.browser_api, "_current_page", None)
        return current(session) if callable(current) else session["page"]

    def _viewport(self, page, snapshot: dict[str, Any]) -> dict[str, Any]:
        size = page.viewport_size or {}
        return {
            "width": int(size.get("width") or 0),
            "height": int(size.get("height") or 0),
            "device_scale_factor": snapshot.get("device_scale_factor", 1),
            "scroll_x": snapshot.get("scroll_x", 0),
            "scroll_y": snapshot.get("scroll_y", 0),
        }

    def _observe(self) -> dict[str, Any]:
        page = self._page()
        snapshot = page.evaluate(_OBSERVE_SCRIPT)
        if not isinstance(snapshot, dict):
            raise RuntimeError("browser observation did not return an object")
        self._frame_seq += 1
        frame_id = f"frame_{self._frame_seq}_{uuid.uuid4().hex[:8]}"
        locator = page.locator(_INTERACTIVE_SELECTOR)
        elements = []
        refs = {}
        for item in snapshot.get("elements") or []:
            if not isinstance(item, dict):
                continue
            try:
                dom_index = int(item["dom_index"])
            except (KeyError, TypeError, ValueError):
                continue
            ref = f"e{len(elements) + 1}"
            refs[ref] = locator.nth(dom_index)
            elements.append({
                "ref": ref,
                "role": str(item.get("role") or item.get("tag") or "element"),
                "name": str(item.get("name") or ""),
                "disabled": bool(item.get("disabled")),
            })
        try:
            aria = page.locator("body").aria_snapshot() or ""
        except Exception:
            aria = ""
        if len(aria) > 12000:
            aria = aria[:12000] + "\n[truncated]"
        session = self._session()
        frame = {
            "frame_id": frame_id,
            "url": page.url,
            "origin": _origin(page.url),
            "title": page.title(),
            "target": {
                "kind": "web_tab",
                "tab_id": session.get("app_tab_id"),
                "target_id": session.get("app_target_id"),
            },
            "viewport": self._viewport(page, snapshot),
            "text": str(snapshot.get("text") or "")[:12000],
            "aria_snapshot": aria,
            "elements": elements,
        }
        self._frame = frame
        self._dom_signature = self._signature(snapshot)
        self._refs = refs
        self._screenshot_frame = ""
        return frame

    @staticmethod
    def _signature(snapshot: dict[str, Any]) -> str:
        value = {
            "text": snapshot.get("text") or "",
            "elements": snapshot.get("elements") or [],
        }
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
        return hashlib.sha256(encoded).hexdigest()

    def _fresh(self, expected_frame_id: str) -> bool:
        if not self._frame or expected_frame_id != self._frame["frame_id"]:
            return False
        page = self._page()
        session = self._session()
        if (
            page.url != self._frame["url"]
            or page.title() != self._frame["title"]
            or session.get("app_tab_id") != self._frame["target"]["tab_id"]
            or session.get("app_target_id") != self._frame["target"]["target_id"]
        ):
            return False
        snapshot = page.evaluate(_OBSERVE_SCRIPT)
        return (
            self._viewport(page, snapshot) == self._frame["viewport"]
            and self._signature(snapshot) == self._dom_signature
        )

    def _require_fresh(self, expected_frame_id: str) -> dict[str, Any] | None:
        if self._fresh(expected_frame_id):
            return None
        self._frame = None
        self._refs = {}
        self._dom_signature = ""
        return {"ok": False, "reason_code": "stale_observation"}

    def _ref(self, ref: str):
        return self._refs.get((ref or "").lstrip("@"))

    def _mutated(self, detail: str) -> dict[str, Any]:
        self._mutations += 1
        self._frame = None
        self._refs = {}
        self._screenshot_frame = ""
        self._dom_signature = ""
        return {"ok": True, "detail": detail, "observe_required": True}

    def _write_allowed(self) -> dict[str, Any] | None:
        if self._mutations < self.max_steps:
            return None
        self._terminal_reason = "step_limit"
        return {"ok": False, "reason_code": "step_limit"}

    def execute(
        self,
        action: str,
        expected_frame_id: str = "",
        ref: str = "",
        url: str = "",
        text: str = "",
        key: str = "",
        value: str = "",
        amount: int = 500,
        assertion: str = "",
    ) -> Any:
        return self._owner.submit(
            self._execute,
            action,
            expected_frame_id,
            ref,
            url,
            text,
            key,
            value,
            amount,
            assertion,
        ).result()

    def _execute(
        self,
        action: str,
        expected_frame_id: str = "",
        ref: str = "",
        url: str = "",
        text: str = "",
        key: str = "",
        value: str = "",
        amount: int = 500,
        assertion: str = "",
    ) -> Any:
        if action == "observe":
            return self._observe()
        if action in {"screenshot", "verify"} and not expected_frame_id and self._frame:
            expected_frame_id = self._frame["frame_id"]
        stale = self._require_fresh(expected_frame_id)
        if stale:
            return stale
        page = self._page()
        if action == "screenshot":
            if self._screenshot_frame == expected_frame_id:
                return {"ok": False, "reason_code": "screenshot_already_captured"}
            image = page.screenshot(full_page=False)
            self._screenshot_frame = expected_frame_id
            return ToolReturn(
                text=f"Current viewport screenshot for {expected_frame_id}.",
                images=[image],
                json_data={"frame_id": expected_frame_id},
            )
        if action == "wait":
            page.wait_for_timeout(max(0, min(int(amount), 5000)))
            return {"ok": True, "frame_id": expected_frame_id}
        if action == "verify":
            return self._verify(page, expected_frame_id, assertion, value)
        capped = self._write_allowed()
        if capped:
            return capped
        if action == "navigate":
            if not _is_http_url(url):
                return {"ok": False, "reason_code": "unsupported_url"}
            page.goto(url)
            return self._mutated(f"navigated to {url}")
        if action == "scroll":
            page.mouse.wheel(0, int(amount))
            return self._mutated(f"scrolled {int(amount)}px")
        target = self._ref(ref)
        if target is None:
            return {"ok": False, "reason_code": "ref_not_found"}
        if action == "click":
            target.click()
            return self._mutated(f"clicked {ref}")
        if action == "type":
            target.fill(text)
            return self._mutated(f"typed {len(text)} character(s) into {ref}")
        if action == "press":
            target.press(key)
            return self._mutated(f"pressed {key} on {ref}")
        if action == "hover":
            target.hover()
            return self._mutated(f"hovered {ref}")
        if action == "select":
            target.select_option(value)
            return self._mutated(f"selected an option in {ref}")
        return {"ok": False, "reason_code": "unsupported_action"}

    def _verify(self, page, frame_id: str, assertion: str, value: str) -> dict:
        text = page.inner_text("body")
        checks = {
            "text_contains": value in text,
            "text_not_contains": value not in text,
            "url_contains": value in page.url,
            "title_contains": value in page.title(),
            "element_present": any(
                value.casefold() in str(item.get("name") or "").casefold()
                for item in (self._frame or {}).get("elements", [])
            ),
        }
        if assertion not in checks:
            return {"ok": False, "reason_code": "unsupported_assertion"}
        passed = bool(checks[assertion])
        evidence = {
            "kind": "assertion",
            "assertion": assertion,
            "value": value,
            "frame_id": frame_id,
            "passed": passed,
        }
        if passed:
            self._verified_mutation = self._mutations
            self._evidence = [evidence]
        return {"ok": True, "passed": passed, "evidence": evidence}

    def final_result(self, *, summary: str, reason_code: str | None = None) -> dict:
        return self._owner.submit(
            self._final_result,
            summary=summary,
            reason_code=reason_code,
        ).result()

    def _final_result(self, *, summary: str, reason_code: str | None = None) -> dict:
        verified = bool(self._evidence) and self._verified_mutation == self._mutations
        reason = reason_code or self._terminal_reason or (
            "verified" if verified else "verification_missing"
        )
        status = "cancelled" if reason == "cancelled" else (
            "succeeded" if verified and reason == "verified" else "failed"
        )
        target = {"kind": "web_tab", "tab_id": None, "url": ""}
        if self.session_id:
            try:
                session = self._session()
                target.update({
                    "tab_id": session.get("app_tab_id"),
                    "url": self._page().url,
                })
            except Exception:
                reason = "target_lost"
                status = "failed"
        return {
            "status": status,
            "reason_code": reason,
            "summary": summary,
            "target": target,
            "steps_taken": self._mutations,
            "completion_evidence": list(self._evidence) if verified else [],
            "artifacts": [],
        }

    def close(self) -> str | None:
        try:
            return self._owner.submit(self._close).result()
        finally:
            self._owner.shutdown(wait=True)

    def _close(self) -> str | None:
        session_id, self.session_id = self.session_id, ""
        self._frame = None
        self._refs = {}
        self._dom_signature = ""
        if not session_id:
            return None
        try:
            result = str(self.browser_api.execute(
                action="close", session_id=session_id,
            ))
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"
        if result.startswith("Error:") or " with warnings:" in result:
            return result
        return None


def _new_controller() -> BrowserPageController:
    return BrowserPageController()


def _prompt(task: str, url: str) -> str:
    return f"""Complete this browser task in the visible OpenProgram web tab.

Task: {task}
Initial URL: {url or "use the currently visible tab"}

Rules:
- Call browser_page observe first. It returns DOM text, ARIA and element refs without an image.
- Prefer refs and structured page state. Do not request a screenshot for ordinary text, links, buttons or forms.
- Call screenshot only for a visual acceptance question, canvas, or when observe cannot identify the target. It is one current viewport image.
- Every state-changing action needs the latest frame_id. Observe again after every write.
- Finish only after browser_page verify returns passed=true for the completed task. A verbal claim is not completion evidence.
- Never ask for OCR, object detection, zoom, visual memory, workflow replay, JavaScript, cookies, uploads or downloads; none are available.
"""


@agentic_function(
    name="browser_agent",
    toolset=("browser",),
    unsafe_in=("wechat", "telegram"),
    requires_approval=_browser_agent_requires_approval,
    defer=True,
    input={
        "task": {"description": "Browser task", "multiline": True},
        "url": {"description": "Optional initial http(s) URL"},
        "max_steps": {"description": "Maximum state-changing actions"},
        "max_seconds": {"description": "Wall-clock limit in seconds"},
        "runtime": {"hidden": True},
    },
)
def browser_agent(
    task: str,
    url: str = "",
    max_steps: int = 20,
    max_seconds: int = 300,
    runtime=None,
) -> dict:
    """Complete a task in OpenProgram's visible built-in browser tab."""
    if runtime is None:
        raise ValueError("browser_agent() requires a runtime argument")
    if not (task or "").strip():
        raise ValueError("task must not be empty")
    controller = _new_controller()
    controller.initial_url = url or ""
    controller.max_steps = max(1, min(int(max_steps), 100))
    result: dict
    try:
        if url and not _is_http_url(url):
            result = controller.final_result(
                summary="Initial URL must use http or https.",
                reason_code="unsupported_url",
            )
        else:
            reply = runtime.exec(
                content=[{"type": "text", "text": _prompt(task.strip(), url)}],
                tools=[controller.tool],
                parallel_tool_calls=False,
                max_iterations=min(controller.max_steps * 3 + 3, 303),
                timeout_s=max(1, min(int(max_seconds), 1800)),
                execution_kind="browser_agent",
            )
            summary = (
                reply if isinstance(reply, str)
                else reply.get("summary") if isinstance(reply, dict)
                else None
            )
            if not isinstance(summary, str):
                result = controller.final_result(
                    summary="Browser agent returned an invalid final response.",
                    reason_code="planner_invalid",
                )
            else:
                result = controller.final_result(summary=summary.strip())
    except (CancelledError, ExecInterrupt, asyncio.CancelledError) as exc:
        result = controller.final_result(
            summary=str(exc) or "Browser task cancelled.",
            reason_code="cancelled",
        )
    except Exception as exc:
        name = type(exc).__name__.lower()
        message = str(exc).lower()
        reason = (
            "timeout" if "timeout" in name or "timed out" in message
            else controller._terminal_reason or "tool_error"
        )
        result = controller.final_result(summary=str(exc), reason_code=reason)
    finally:
        cleanup_error = controller.close()
    if cleanup_error:
        result["cleanup_error"] = cleanup_error
        result["summary"] = (
            result.get("summary", "") + f" Cleanup warning: {cleanup_error}"
        ).strip()
        if result.get("status") == "succeeded":
            result["status"] = "failed"
            result["reason_code"] = "cleanup_failed"
    return result


__all__ = ["browser_agent", "BrowserPageController"]
