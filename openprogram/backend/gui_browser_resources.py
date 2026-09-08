"""Invocation-owned browser capabilities for an isolated GUI script."""
from __future__ import annotations

import copy
import json
import logging
import threading
import uuid

from openprogram.agent import surface_context
from openprogram.programs import ToolReturn
from .gui_browser import register_browser_page

_log = logging.getLogger(__name__)


class GuiBrowserResources:
    """Take ownership of a freshly captured Page context and its bindings.

    The caller captures authorized Pages through surface_context.capture_pages.
    No current-Page fallback or new tab is created here. Close revokes broker
    handles first; registry cleanup can wait for an in-flight browser operation.
    This scope releases Page bindings/leases, never closes the user's tab.
    """

    def __init__(self, broker, registry, context: dict, *, backend="open_claude_chrome"):
        self._broker, self._registry = broker, registry
        self._context, self._backend = context, backend
        self._owner = "gui_pages_" + uuid.uuid4().hex
        self._lock = threading.RLock()
        self._closed = False
        self._handles = []
        self._pages = {}
        self._acquired = []
        self._consumed = set()
        try:
            inventory = registry.list_pages(context=context, owner_id=self._owner)
            if not inventory.get("ok"):
                raise RuntimeError(inventory.get("reason_code", "Page inventory unavailable"))
            self._pages = {page["page_context_token"]: page for page in inventory["pages"]}
            self.handle = broker.register(target="browser-catalog:" + self._owner,
                                          methods={"list": self._list, "acquire": self._acquire},
                                          validate=self._validate)
            self._handles.append(self.handle)
        except BaseException:
            try:
                self.close()
            except BaseException:
                _log.debug("browser resource scope cleanup failed during initialization", exc_info=True)
            raise

    def __enter__(self):
        with self._lock:
            if self._closed:
                raise PermissionError("browser resource scope is closed")
        return self

    def _validate(self, method, arguments, observation):
        with self._lock:
            if self._closed:
                raise PermissionError("browser resource scope is closed")
            expected = {"page_context_token"} if method == "acquire" else set()
            if set(arguments) != expected or observation is not None:
                raise ValueError("unsupported browser catalog arguments")
            if method == "acquire" and (not isinstance(arguments.get("page_context_token"), str) or arguments["page_context_token"] not in self._pages):
                raise PermissionError("Page token was not issued by this resource scope")

    def _list(self, arguments, observation, operation):
        operation.check()
        with self._lock:
            return {"pages": copy.deepcopy([page for token, page in self._pages.items() if token not in self._consumed]), "acquired": copy.deepcopy(self._acquired)}

    def _acquire(self, arguments, observation, operation):
        operation.check()
        token = arguments["page_context_token"]
        session_id = None
        with self._lock:
            if token in self._consumed:
                raise PermissionError("Page token has already been acquired")
            self._consumed.add(token)
        try:
            result = self._registry.execute(command="observe", owner_id=self._owner,
                                            backend=self._backend, page_context_token=token,
                                            before_dispatch=operation.check)
            data = result.json_data if isinstance(result, ToolReturn) else result
            session_id = data.get("web_session_id") if isinstance(data, dict) else None
            operation.check()
            if not isinstance(data, dict):
                raise RuntimeError("Page observation returned no structured metadata")
            if data.get("ok") is False or isinstance(result, ToolReturn) and result.is_error:
                if session_id:
                    try:
                        self._registry.execute(command="close", owner_id=self._owner, web_session_id=session_id)
                    except BaseException as exc:
                        data = {**data, "resource_cleanup_error": f"{type(exc).__name__}: {exc}"}
                    session_id = None
                return ToolReturn(text=result.text if isinstance(result, ToolReturn) else str(data.get("reason_code", "Page observation failed")),
                                  json_data=data, images=result.images if isinstance(result, ToolReturn) else [], is_error=True)
            if not session_id or not data.get("frame_id"):
                raise RuntimeError("Page observation missing exact session or frame")
            with self._lock:
                if self._closed:
                    raise PermissionError("browser resource scope is closed")
                handle = register_browser_page(self._broker, self._registry, owner_id=self._owner,
                                               web_session_id=session_id)
                self._handles.append(handle)
                self._acquired.append({"handle": handle, "frame_id": data["frame_id"]})
            payload = {**data, "handle": handle}
            return ToolReturn(text=result.text if isinstance(result, ToolReturn) else json.dumps(payload),
                              json_data=payload, images=result.images if isinstance(result, ToolReturn) else [])
        except BaseException:
            if session_id:
                try:
                    self._registry.execute(command="close", owner_id=self._owner, web_session_id=session_id)
                except BaseException:
                    _log.debug("browser page cleanup failed after observation error owner=%s session_id=%s",
                                self._owner, session_id, exc_info=True)
            raise

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            handles = tuple(self._handles)
        for handle in handles:
            self._broker.revoke_resource(handle)
        try:
            self._registry.release_owner(self._owner, strict=True)
        finally:
            surface_context.release_bindings(self._context)

    def __exit__(self, exc_type, exc, tb):
        try:
            self.close()
        except BaseException:
            if exc_type is None:
                raise
