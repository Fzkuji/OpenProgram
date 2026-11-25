"""A window's state: its screenshot, its tree, OCR text for sparse windows, and when it has settled."""
from __future__ import annotations

import time

from ..common import ocr
from ..common.base import (INCLUDE, OCR_SPARSE_NODES, BadArgument, NotDispatched, claim_line, claimed,
    ComputerUseError, Observation, check_cancelled, window_node_count)
from ..common.tree import diff, leave_out, number, role_name, snapshot
from .x11 import (App, BUSY_MAX, MAX_IMAGE_SIDE, MAX_NODES, QUICK_NODES, SETTLE_MAX, SETTLE_MIN, WindowState,
    XWindow, _BUSY_ROLES, _CLAIMED_ROLES, _gdk)


def _text_section(boxes: list[ocr.TextBox]) -> list[str]:
    if not boxes:
        return []
    return ["", "Text on the screenshot (OCR; this app exposes little to accessibility, so act on it with x,y at a "
                "line's centre):", ocr.render(boxes)]


class WindowReading:
    """Reading a window: capture, tree, zoom, find and settle."""

    def _capture(self, xw: XWindow, region=None) -> tuple[bytes | None, float]:
        """PNG of the window (or of ``region`` in window pixels) and its pixels per screen pixel."""
        if not xw.visible:
            return None, 1.0
        try:
            Gdk, GdkX11 = _gdk()
            display = Gdk.Display.get_default()
            window = GdkX11.X11Window.foreign_new_for_display(display, xw.xid)
            x, y, w, h = region or (0, 0, xw.width, xw.height)
            pixbuf = Gdk.pixbuf_get_from_window(window, int(x), int(y), int(w), int(h))
        except Exception:  # noqa: BLE001 - no picture is not an error; the tree still reads
            return None, 1.0
        if pixbuf is None:
            return None, 1.0
        scale = 1.0
        longest = max(pixbuf.get_width(), pixbuf.get_height())
        if region is None and longest > MAX_IMAGE_SIDE:
            from gi.repository import GdkPixbuf
            scale = MAX_IMAGE_SIDE / longest
            pixbuf = pixbuf.scale_simple(max(1, int(pixbuf.get_width() * scale)),
                                         max(1, int(pixbuf.get_height() * scale)), GdkPixbuf.InterpType.BILINEAR)
        ok, data = pixbuf.save_to_bufferv("png", [], [])
        return (bytes(data) if ok else None), scale

    def get_state(self, session: str, query: str, *, window=None, window_id=None, full=False,
                  as_diff=True, include: str = "all", claim=None) -> Observation:
        """Read one window: its tree, its screenshot, or both (``include``). ``claim`` (see
        :func:`base.claimed`) names a target that operates the window's content itself; that content is left
        out of the tree."""
        include = str(include or "all").lower()
        if include not in INCLUDE:
            raise BadArgument("include must be all, tree or screenshot")
        with self._lock:
            app = self.resolve_app(query)
            key = (session, app.pid)
            previous = self._states.get(key)
            acc, xw = self._pair(app, window, window_id, previous.window_id if previous else 0)
            title = xw.title if xw else self._name(acc)
            wid = xw.xid if xw else 0
            same_window = bool(previous) and (previous.window_id == wid if wid else previous.window_title == title)
            if include == "screenshot" and not same_window:
                raise BadArgument("Read this window with include=all first; a screenshot-only read keeps the "
                                  "tree of the window the model already has.")
            image, scale = (None, 1.0)
            if include != "tree" and xw is not None:
                image, scale = self._capture(xw)
            lines = [f"App: {app.name} ({app.ident}, pid {app.pid})"]
            if xw is None and acc is None:
                lines.append("Window: none open.")
            else:
                note = " (minimized)" if xw is not None and not xw.visible else ""
                size = f", {xw.width}x{xw.height} pixels" if xw else ""
                lines.append(f'Window: "{title}"{note}' + (f", window_id {wid}" if wid else "") + size)
                if image is not None:
                    w, h = int(round(xw.width * scale)), int(round(xw.height * scale))
                    lines.append(f"Screenshot: {w}x{h} pixels; x,y coordinates in actions are pixels of this screenshot.")
                elif include != "tree":
                    lines.append("Screenshot: unavailable (the window is minimized; show_window brings it back). "
                                 "Use element indices.")
            if app.accessible is None:
                lines.append("This app does not expose an accessibility tree (for GTK and Qt apps, turn on "
                             "accessibility and restart the app); act with x,y on the screenshot.")
            owner = claimed(claim, {"app": app.name, "bundle": app.ident, "pid": app.pid, "title": title,
                                    "window_id": wid}) if wid else None
            if owner is not None:
                lines.append(claim_line(owner))
            others = [w for w in self._x_windows(app.pid) if w.xid != wid]
            if others:
                lines.append("Other windows: " + "; ".join(f'"{w.title}"' for w in others[:8])
                             + ". list_windows shows their ids; pass window=<title> or window_id to read one.")
            frame = (xw.x, xw.y, xw.width, xw.height) if xw else None
            if include == "screenshot":
                previous.origin = (frame[0], frame[1]) if frame else previous.origin
                previous.size = (frame[2], frame[3]) if frame else previous.size
                previous.scale, previous.has_screenshot = scale, image is not None
                previous.text_boxes = self._read_text(image) if previous.sparse else []
                lines += ["", "Tree not read (include=screenshot); element indices are those of the last tree."]
                lines.extend(_text_section(previous.text_boxes))
                return Observation(text="\n".join(lines), image=image, app=app.name, sparse=previous.sparse)
            nodes, elements, complete = self._read_tree(acc)
            left_out = _CLAIMED_ROLES if owner is not None else frozenset()
            if left_out:
                nodes, elements, _ = leave_out(nodes, elements, left_out)
            prior = previous.snapshot if same_window else None
            fresh = number(nodes, prior)
            body, is_diff = diff(prior if (as_diff and not full) else None, nodes)
            state = WindowState(
                pid=app.pid, window_title=title, window_id=wid,
                elements={node.index: element for node, element in zip(nodes, elements)},
                nodes=nodes, by_index={node.index: node for node in nodes}, snapshot=snapshot(nodes, fresh),
                origin=(frame[0], frame[1]) if frame else (0.0, 0.0),
                size=(frame[2], frame[3]) if frame else (0.0, 0.0), scale=scale, has_screenshot=image is not None)
            if include == "tree" and same_window and previous.has_screenshot:
                state.scale, state.has_screenshot = previous.scale, True
            state.left_out = left_out
            state.sparse = window_node_count(nodes) < OCR_SPARSE_NODES
            if state.sparse:
                state.text_boxes = self._read_text(image)
            self._states[key] = state
            if not complete:
                lines.append(f"The tree was cut at {MAX_NODES} elements.")
            lines.append("")
            lines.append("Accessibility tree changes since the last state:" if is_diff else "Accessibility tree:")
            lines.append(body or "(empty)")
            lines.extend(_text_section(state.text_boxes))
            return Observation(text="\n".join(lines), image=image, app=app.name, sparse=state.sparse)

    @staticmethod
    def _read_text(image: bytes | None) -> list[ocr.TextBox]:
        if image is None:
            return []
        try:
            return ocr.recognize_tesseract(image)
        except Exception:  # noqa: BLE001 - OCR only adds to a read
            return []

    def _state_for(self, session: str, query: str) -> tuple[App, WindowState]:
        app = self.resolve_app(query, launch=False)
        state = self._states.get((session, app.pid))
        if state is None:
            raise NotDispatched(f"call get_state for {app.name} first")
        return app, state

    def zoom(self, session: str, query: str, x, y, width, height) -> Observation:
        with self._lock:
            app, state = self._state_for(session, query)
            if not state.has_screenshot:
                raise ComputerUseError("This window has no screenshot to zoom into; call get_state first.")
            try:
                px, py, pw, ph = (float(v) for v in (x, y, width, height))
            except (TypeError, ValueError) as exc:
                raise BadArgument("zoom needs x, y, width and height in screenshot pixels") from exc
            full_w, full_h = state.size[0] * state.scale, state.size[1] * state.scale
            px, py = max(0.0, px), max(0.0, py)
            pw, ph = min(pw, full_w - px), min(ph, full_h - py)
            if pw < 4 or ph < 4:
                raise BadArgument(f"the region must lie inside the {int(full_w)}x{int(full_h)} screenshot")
            xw = next((w for w in self._x_windows(app.pid) if w.xid == state.window_id), None)
            if xw is None:
                raise ComputerUseError("The window is gone; call get_state again.")
            region = (px / state.scale, py / state.scale, pw / state.scale, ph / state.scale)
            image, _ = self._capture(xw, region=region)
            if image is None:
                raise ComputerUseError("The window could not be captured; call get_state again.")
            text = (f"Zoomed region ({int(px)}, {int(py)}) {int(pw)}x{int(ph)} of the last screenshot, at full "
                    "pixel density. Coordinates for actions still refer to the last screenshot, not to this image.")
            return Observation(text=text, image=image, app=app.name)

    def find(self, session: str, query: str, text: str = "", role: str = "") -> str:
        with self._lock:
            _, state = self._state_for(session, query)
            needle = " ".join(str(text or "").split()).lower()
            wanted_role = str(role or "").strip().lower()
            if not needle and not wanted_role:
                raise BadArgument("find needs text, role, or both")
            hits = []
            for node in state.nodes:
                if wanted_role and wanted_role not in (node.role.lower(), role_name(node.role).lower()):
                    continue
                haystack = " ".join(f"{node.label} {node.value} {node.placeholder}".split()).lower()
                if needle and needle not in haystack:
                    continue
                hits.append(f"{node.index} {node.body()}")
            if not hits and needle and not wanted_role and state.text_boxes:
                seen = ocr.matching(state.text_boxes, needle)
                if seen:
                    return (f"no element matches, but the screenshot shows {len(seen)} line(s) with that text "
                            "(OCR; act with x,y at the centre):\n" + ocr.render(seen[:60]))
            if not hits:
                return "no element of the last tree matches; call get_state for a fresh tree, or scroll"
            more = f"\n…and {len(hits) - 60} more; narrow the search." if len(hits) > 60 else ""
            return f"{len(hits)} match(es):\n" + "\n".join(hits[:60]) + more

    def settle(self, session: str, query: str) -> float:
        app = self.resolve_app(query, launch=False)
        previous = self._states.get((session, app.pid))
        start = time.monotonic()
        time.sleep(SETTLE_MIN)
        window, _ = self._pair(app, prefer_id=previous.window_id if previous else 0)
        last, busy = self._quick_signature(window)
        while True:
            elapsed = time.monotonic() - start
            if elapsed >= (BUSY_MAX if busy else SETTLE_MAX):
                break
            check_cancelled()
            time.sleep(0.2)
            current, busy = self._quick_signature(window)
            if current == last and not busy:
                break
            last = current
        return time.monotonic() - start

    def _quick_signature(self, window) -> tuple[tuple, bool]:
        out, busy = [], False
        queue = [window] if window is not None else []
        while queue and len(out) < QUICK_NODES:
            acc = queue.pop(0)
            if not (self._has(acc, "SHOWING") or self._has(acc, "VISIBLE")) and acc is not window:
                continue
            role = self._role(acc)
            if role in _BUSY_ROLES and (role == "animation" or (self._value(acc) or 1.0) < 1.0):
                busy = True
            out.append((role, self._name(acc), self._text(acc, 80) or "", self._has(acc, "ENABLED")))
            queue.extend(self._children(acc))
        return tuple(out), busy
