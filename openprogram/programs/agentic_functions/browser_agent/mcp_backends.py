"""Private official-MCP adapters restricted to one exact Electron Page."""
from __future__ import annotations

import asyncio
import re
import threading
import uuid
from typing import Any, Callable, Mapping

from openprogram.mcp.client import MCPClient
from openprogram.mcp.config import MCPServerConfig


_PLAYWRIGHT_MCP_VERSION = "0.0.79"
_CHROME_DEVTOOLS_MCP_VERSION = "1.7.0"


def _result_text(result: Any) -> str:
    lines = []
    for item in getattr(result, "content", ()) or ():
        text = getattr(item, "text", None)
        if isinstance(text, str):
            lines.append(text)
    return "\n".join(lines)


def _structured(result: Any) -> dict[str, Any]:
    value = getattr(result, "structuredContent", None)
    if value is None:
        value = getattr(result, "structured_content", None)
    return value if isinstance(value, dict) else {}


class _SyncMCPClient:
    """Keep the async MCP supervisor alive on one private event loop."""

    def __init__(self, command: list[str], *, timeout: float = 30.0) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run, name="computer-use-mcp", daemon=True,
        )
        self._thread.start()
        self._client = MCPClient(MCPServerConfig(
            name="computer-use-private",
            command=command,
            timeout_seconds=timeout,
        ))
        self._submit(self._client.start(), timeout + 5)
        if self._client.error:
            self.close()
            raise RuntimeError("computer_use_backend_unavailable")

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _submit(self, awaitable, timeout: float = 35.0):
        future = asyncio.run_coroutine_threadsafe(awaitable, self._loop)
        return future.result(timeout=timeout)

    def call(self, name: str, arguments: dict[str, Any]):
        return self._submit(self._client.call_tool(name, arguments))

    def close(self) -> None:
        client = getattr(self, "_client", None)
        if client is not None and self._loop.is_running():
            try:
                self._submit(client.stop(), 5)
            except Exception:
                pass
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=5)


class OfficialMCPPageBackend:
    def __init__(
        self,
        name: str,
        controller_factory: Callable[[], Any],
        *,
        client_factory: Callable[[list[str]], Any] = _SyncMCPClient,
    ) -> None:
        self.name = name
        self._controller_factory = controller_factory
        self._client_factory = client_factory

    def _controller(self, session):
        if session.controller is None:
            controller = self._controller_factory()
            controller.binding_id = session.binding_id
            session.controller = controller
        return session.controller

    def _command(self, endpoint: str) -> list[str]:
        if self.name == "playwright_mcp":
            return [
                "npx", "-y", f"@playwright/mcp@{_PLAYWRIGHT_MCP_VERSION}",
                "--cdp-endpoint", endpoint,
            ]
        return [
            "npx", "-y",
            f"chrome-devtools-mcp@{_CHROME_DEVTOOLS_MCP_VERSION}",
            "--wsEndpoint", endpoint,
            "--experimentalPageIdRouting",
            "--experimentalStructuredContent",
        ]

    def _ensure_bound(self, session) -> Any:
        existing = session.state.get("mcp_client")
        if existing is not None:
            return existing
        controller = self._controller(session)
        page = controller._page()
        marker_name = "__openprogram_mcp_" + uuid.uuid4().hex
        marker_value = uuid.uuid4().hex
        page.evaluate(
            "([name, value]) => Object.defineProperty(globalThis, name, "
            "{value, configurable: true})",
            [marker_name, marker_value],
        )
        from openprogram.programs.functions.browser._chrome_bootstrap import (
            desktop_app_ws_url,
        )
        endpoint = desktop_app_ws_url()
        if not endpoint:
            raise RuntimeError("computer_use_backend_unavailable")
        client = self._client_factory(self._command(endpoint))
        try:
            selected = (
                self._bind_playwright(client, marker_name, marker_value)
                if self.name == "playwright_mcp"
                else self._bind_chrome(client, marker_name, marker_value)
            )
            if selected is None:
                raise RuntimeError("exact_page_not_found")
            session.state["mcp_client"] = client
            session.state["upstream_page"] = selected
            return client
        except Exception:
            client.close()
            raise
        finally:
            try:
                page.evaluate("name => delete globalThis[name]", marker_name)
            except Exception:
                pass

    def _bind_playwright(self, client, marker_name: str, marker_value: str):
        listed = client.call("browser_tabs", {"action": "list"})
        indices = [
            int(value) for value in re.findall(
                r"(?m)^-\s+(\d+):", _result_text(listed),
            )
        ]
        expression = f"() => globalThis[{marker_name!r}] || null"
        for index in indices:
            client.call("browser_tabs", {"action": "select", "index": index})
            result = client.call("browser_evaluate", {"function": expression})
            if marker_value in _result_text(result):
                return index
        return None

    def _bind_chrome(self, client, marker_name: str, marker_value: str):
        listed = client.call("list_pages", {})
        pages = _structured(listed).get("pages") or []
        expression = f"() => globalThis[{marker_name!r}] || null"
        for page in pages:
            page_id = page.get("id") if isinstance(page, dict) else None
            if not isinstance(page_id, int):
                continue
            result = client.call(
                "evaluate_script", {"function": expression, "pageId": page_id},
            )
            if marker_value in _result_text(result):
                return page_id
        return None

    def observe(self, session, arguments: Mapping[str, Any]):
        controller = self._controller(session)
        identity = controller.execute(action="observe")
        if not isinstance(identity, dict) or "frame_id" not in identity:
            return identity
        client = self._ensure_bound(session)
        if self.name == "playwright_mcp":
            upstream = client.call("browser_snapshot", {})
        else:
            upstream = client.call(
                "take_snapshot",
                {"pageId": session.state["upstream_page"]},
            )
        session.state["frame_id"] = identity["frame_id"]
        return {
            **identity,
            "elements": [],
            "aria_snapshot": _result_text(upstream),
            "backend_observation": self.name,
        }

    def _action_call(self, session, arguments: Mapping[str, Any]):
        action = str(arguments.get("action") or "")
        ref = str(arguments.get("ref") or "").lstrip("@")
        if self.name == "playwright_mcp":
            mapping = {
                "click": ("browser_click", {"target": ref}),
                "type": ("browser_type", {
                    "target": ref, "text": str(arguments.get("text") or ""),
                }),
                "press": ("browser_press_key", {"key": arguments.get("key")}),
                "hover": ("browser_hover", {"target": ref}),
                "select": ("browser_select_option", {
                    "target": ref, "values": [str(arguments.get("value") or "")],
                }),
                "navigate": ("browser_navigate", {"url": arguments.get("url")}),
            }
        else:
            page_id = session.state["upstream_page"]
            mapping = {
                "click": ("click", {"uid": ref, "pageId": page_id}),
                "type": ("fill", {
                    "uid": ref, "value": str(arguments.get("text") or ""),
                    "pageId": page_id,
                }),
                "press": ("press_key", {
                    "key": arguments.get("key"), "pageId": page_id,
                }),
                "hover": ("hover", {"uid": ref, "pageId": page_id}),
                "navigate": ("navigate_page", {
                    "type": "url", "url": arguments.get("url"),
                    "pageId": page_id,
                }),
            }
        if action not in mapping:
            return None
        return mapping[action]

    def act(self, session, arguments: Mapping[str, Any]):
        controller = self._controller(session)
        frame_id = str(arguments.get("expected_frame_id") or "")
        stale = controller._require_fresh(frame_id)
        if stale is not None:
            return stale
        call = self._action_call(session, arguments)
        if call is None:
            return {"ok": False, "reason_code": "unsupported_action"}
        client = self._ensure_bound(session)
        result = client.call(call[0], call[1])
        controller._invalidate_frame()
        return {
            "ok": not bool(getattr(result, "isError", False)),
            "result": _result_text(result),
            "observe_required": True,
        }

    def verify(self, session, arguments: Mapping[str, Any]):
        params = dict(arguments)
        params["action"] = "verify"
        return self._controller(session).execute(**params)

    def close(self, session) -> None:
        client = session.state.pop("mcp_client", None)
        if client is not None:
            client.close()
        if session.controller is not None:
            session.controller.close()


__all__ = ["OfficialMCPPageBackend"]
