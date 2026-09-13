from __future__ import annotations


import asyncio


import json


import threading


import time


from types import SimpleNamespace


import pytest


class _Adapter:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[tuple[str, dict]] = []

    def observe(self, session, arguments):
        self.calls.append(("observe", dict(arguments)))
        return {"frame_id": "frame-1", "backend": self.name}

    def act(self, session, arguments):
        self.calls.append(("act", dict(arguments)))
        return {"ok": True, "backend": self.name}

    def verify(self, session, arguments):
        self.calls.append(("verify", dict(arguments)))
        return {"passed": True, "backend": self.name}

    def close(self, session):
        self.calls.append(("close", {}))


def _allow_binding(_binding_id):
    return {"ok": True}


class _Page:
    def __init__(self) -> None:
        self.marker_name = ""
        self.marker_value = ""

    def evaluate(self, script, arg=None):
        if "Object.defineProperty" in script:
            self.marker_name, self.marker_value = arg
        return None


class _Controller:
    def __init__(self) -> None:
        self.binding_id = ""
        self.page = _Page()
        self.frame = {
            "frame_id": "frame-1", "url": "https://example.test/",
            "target": {"tab_id": "tab-1", "target_id": "target-1"},
        }
        self.invalidated = 0
        self.closed = 0

    def _page(self):
        return self.page

    def evaluate_bound_page(self, script, arg=None):
        return self.page.evaluate(script, arg)

    def execute(self, **params):
        if params["action"] == "observe":
            return dict(self.frame)
        return {"passed": True}

    def _require_fresh(self, frame_id):
        return None if frame_id == self.frame["frame_id"] else {
            "ok": False, "reason_code": "stale_observation",
        }

    def prepare_external_action(self, arguments):
        rejected = self._require_fresh(arguments.get("expected_frame_id"))
        return rejected if rejected is not None else self._write_allowed()

    def _invalidate_frame(self):
        self.invalidated += 1

    def invalidate_external_frame(self):
        return self._invalidate_frame()

    def _write_allowed(self):
        return None

    def _mutated(self, detail):
        self.invalidated += 1
        return {"ok": True, "detail": detail, "observe_required": True}

    def record_external_mutation(self, detail):
        return self._mutated(detail)

    def close(self):
        self.closed += 1


class _Result:
    def __init__(self, text="", structured=None, error=False) -> None:
        self.content = [SimpleNamespace(text=text)] if text else []
        self.structuredContent = structured
        self.isError = error


class _PlaywrightClient:
    def __init__(self, page: _Page) -> None:
        self.page = page
        self.selected = 0
        self.calls = []

    def call(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        if name == "browser_snapshot":
            return _Result('- button "Save" [ref=e7]')
        return _Result("done")

    def close(self):
        pass


class _NativeObserveAdapter:
    """BrowserPageController observe shape: frame_id, no ok."""

    supports_operation_guard = True

    def __init__(self, name):
        self.name = name
        self.calls = []

    def observe(self, session, arguments, *, before_dispatch=None):
        del session, arguments
        if before_dispatch is not None:
            before_dispatch()
        self.calls.append("observe")
        return {
            "frame_id": "frame_1_b6a848a6",
            "url": "http://127.0.0.1:62147/page/1",
            "origin": "http://127.0.0.1:62147",
            "title": "Resource test PAGE",
            "target": {
                "kind": "web_tab",
                "tab_id": "tab-opened",
                "target_id": "target-opened",
            },
            "viewport": {"width": 1280, "height": 800},
        }

    def act(self, session, arguments, *, before_dispatch=None):
        del session
        if before_dispatch is not None:
            before_dispatch()
        self.calls.append(("act", dict(arguments)))
        return {"ok": True, "detail": "clicked"}

    def verify(self, session, arguments, *, before_dispatch=None):
        del session, arguments, before_dispatch
        return {"ok": True, "passed": True}

    def close(self, session):
        del session
        self.calls.append("close")


def _public_open_transport(webtab):
    def request_on_ws(_ws, command, timeout=15.0):
        del timeout
        if command.get("op") == "open":
            return {
                "ok": True,
                "window_id": "win",
                "tab_id": "tab-opened",
                "target_id": "target-opened",
                "url": command.get("url") or "http://127.0.0.1:62147/page/1",
                "title": "Resource test PAGE",
                "geometry_revision": 7,
                "page_revision": 99,
                "access_revision": 99,
                "created": True,
                "reused": False,
            }
        window_id = command.get("window_id")
        tab_id = command.get("tab_id")
        for entry in webtab._bindings.values():
            if entry[1] == window_id and entry[2] == tab_id:
                return {
                    "ok": True,
                    "window_id": entry[1],
                    "tab_id": entry[2],
                    "target_id": entry[3],
                    "geometry_revision": entry[7],
                }
        return {"ok": False, "reason_code": "page_context_stale"}

    return request_on_ws

