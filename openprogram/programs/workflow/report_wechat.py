"""Restricted WeChat accessibility reader. No general input or send operation."""

from __future__ import annotations

import importlib
import sys
import time


class AccessibilityUnavailable(RuntimeError):
    pass


def select_application(apps):
    """Select a unique WeChat main-window owner without activating any app."""
    apps = [a for a in apps if a.bundleIdentifier() == "com.tencent.xinWeChat"]
    if not apps:
        raise AccessibilityUnavailable("APP_NOT_RUNNING")
    if len(apps) == 1:
        return apps[0]
    try:
        cg = importlib.import_module("Quartz")
    except ImportError as exc:
        raise AccessibilityUnavailable("NATIVE_DEPENDENCIES_UNAVAILABLE") from exc
    owners = {
        w.get("kCGWindowOwnerPID")
        for w in cg.CGWindowListCopyWindowInfo(cg.kCGWindowListOptionAll, 0)
        if w.get("kCGWindowLayer") == 0
        and w.get("kCGWindowName") in ("WeChat", "微信")
        and w.get("kCGWindowBounds", {}).get("Width", 0) >= 600
        and w.get("kCGWindowBounds", {}).get("Height", 0) >= 400
    }
    candidates = [a for a in apps if a.processIdentifier() in owners]
    if len(candidates) != 1:
        raise AccessibilityUnavailable("APP_NOT_UNIQUE")
    return candidates[0]


class MacAccessibility:
    """Reuse the optional native dependencies used by system-access diagnostics."""

    def __init__(self):
        if sys.platform != "darwin":
            raise AccessibilityUnavailable("UNSUPPORTED_PLATFORM")
        try:
            self.ax = importlib.import_module("ApplicationServices")
            workspace = importlib.import_module("AppKit").NSWorkspace.sharedWorkspace()
        except ImportError as exc:
            raise AccessibilityUnavailable("NATIVE_DEPENDENCIES_UNAVAILABLE") from exc
        if not self.ax.AXIsProcessTrusted():
            raise AccessibilityUnavailable("ACCESS_REQUIRED")
        app = select_application(workspace.runningApplications())
        self.root = self.ax.AXUIElementCreateApplication(app.processIdentifier())
        self.ax.AXUIElementSetMessagingTimeout(self.root, 0.2)

    def attr(self, node, name):
        error, value = self.ax.AXUIElementCopyAttributeValue(node, name, None)
        return None if error else value

    def text(self, node, name):
        value = self.attr(node, name)
        return value if isinstance(value, str) else ""

    def nodes(self):
        pending, result = [(self.root, None)], []
        deadline = time.monotonic() + 8
        while pending and len(result) < 1200:
            if time.monotonic() >= deadline:
                raise AccessibilityUnavailable("READ_TIMEOUT")
            node, parent = pending.pop()
            number = len(result)
            result.append(
                {
                    "handle": node,
                    "parent": parent,
                    "index": number,
                    "role": self.text(node, "AXRole"),
                    "subrole": self.text(node, "AXSubrole"),
                    "title": self.text(node, "AXTitle"),
                    "value": self.text(node, "AXValue"),
                    "description": self.text(node, "AXDescription"),
                    "help": self.text(node, "AXHelp"),
                }
            )
            children = self.attr(node, "AXChildren") or []
            pending.extend(
                (child, number)
                for child in reversed(list(children)[: 1200 - len(result)])
            )
        return result

    def search(self, node, group):
        if self.text(node, "AXSubrole") != "AXSearchField":
            raise AccessibilityUnavailable("SEARCH_CONTROL_CHANGED")
        if self.ax.AXUIElementSetAttributeValue(node, "AXValue", group):
            raise AccessibilityUnavailable("SEARCH_UNAVAILABLE")

    def select(self, node, group):
        if self.text(node, "AXRole") != "AXRow" or self.text(node, "AXTitle") != group:
            raise AccessibilityUnavailable("GROUP_RESULT_CHANGED")
        if self.ax.AXUIElementPerformAction(node, "AXPress"):
            raise AccessibilityUnavailable("GROUP_SELECTION_UNAVAILABLE")

    def close(self):
        self.root = None


def read_group(group: str) -> dict:
    """Search an exact group, returning only scoped accessible rows or a blocker."""
    if not isinstance(group, str) or not group.strip() or len(group) > 200:
        return {"status": "SCOPE_REQUIRED"}
    bridge = None
    try:
        bridge = MacAccessibility()
        nodes = bridge.nodes()
        if any(
            any(
                word in (n["title"] + n["value"] + n["description"])
                for word in ("重新登录", "请登录", "扫码登录")
            )
            for n in nodes
        ):
            return {"status": "LOGIN_REQUIRED"}
        # A group header is required separately from a sidebar/search result.
        header = lambda ns: any(
            n["role"] == "AXHeading" and n["title"] == group for n in ns
        )
        if not header(nodes):
            fields = [n for n in nodes if n["subrole"] == "AXSearchField"]
            if len(fields) != 1:
                return {"status": "SEARCH_UNAVAILABLE"}
            bridge.search(fields[0]["handle"], group)
            nodes = bridge.nodes()
            rows = [n for n in nodes if n["role"] == "AXRow" and n["title"] == group]
            if len(rows) != 1:
                return {"status": "GROUP_NOT_UNIQUE"}
            bridge.select(rows[0]["handle"], group)
            nodes = bridge.nodes()
        if not header(nodes):
            return {"status": "GROUP_NOT_VERIFIED"}
        # Only message rows explicitly identified by accessibility are accepted.
        headers = [n for n in nodes if n["role"] == "AXHeading" and n["title"] == group]
        if len(headers) != 1 or headers[0].get("parent") is None:
            return {"status": "GROUP_NOT_VERIFIED"}
        container = headers[0]["parent"]
        if container == 0 or nodes[container]["role"] != "AXGroup":
            return {"status": "GROUP_NOT_VERIFIED"}
        scoped = {n["index"] for n in nodes if n.get("parent") == container}
        for n in nodes:
            if n.get("parent") in scoped:
                scoped.add(n["index"])
        blocks = [
            {
                "id": "ax-" + str(i),
                "text": n["value"],
                "author": n["title"],
                "date": n.get("help", ""),
            }
            for i, n in enumerate(nodes)
            if n.get("index") in scoped
            and n["role"] == "AXRow"
            and n["description"] == "message"
            and n["value"]
        ]
        if not blocks:
            return {"status": "HISTORY_UNAVAILABLE"}
        return {
            "status": "READY",
            "group": group,
            "blocks": blocks[:100],
            "complete": False,
            "coverage": "accessible message rows only; older history not verified",
        }
    except (AccessibilityUnavailable, OSError) as exc:
        return {"status": str(exc)[:200] or "ACCESS_UNAVAILABLE"}
    finally:
        if bridge is not None:
            bridge.close()
