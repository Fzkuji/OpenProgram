"""A window's state: its screenshot, its tree, OCR text for sparse windows, and when it has settled."""
from __future__ import annotations

import ctypes
import time

from ..common import audio
from ..common import ocr
from ..common.base import (INCLUDE, OCR_SPARSE_NODES, AudioClip, BadArgument, NotDispatched, claim_line, claimed,
    ComputerUseError, Observation, Report, check_cancelled, save_recording, window_node_count)
from ..common.tree import diff, leave_out, number, role_name, snapshot
from .formats import png_encode
from .win32 import (App, BUSY_MAX, MAX_IMAGE_SIDE, MAX_NODES, QUICK_NODES, RANGE_VALUE, SETTLE_MAX, SETTLE_MIN, Win,
    WindowState, _BUSY_ROLES, _CLAIMED_ROLES, _STATE)
from . import win32


def _read_text(image: bytes | None) -> list[ocr.TextBox]:
    if image is None:
        return []
    try:
        return ocr.recognize_tesseract(image)
    except Exception:  # noqa: BLE001 - OCR only adds to a read
        return []


def _text_section(boxes: list[ocr.TextBox]) -> list[str]:
    if not boxes:
        return []
    return ["", "Text on the screenshot (OCR; this app exposes little to UI Automation, so act on it with x,y at a "
                "line's centre):", ocr.render(boxes)]


class WindowReading:
    """Reading a window: capture, tree, zoom, find, settle and sound."""

    def _capture(self, win: Win, region=None) -> tuple[bytes | None, float]:
        """PNG of the window through PrintWindow (covered windows included), scaled to MAX_IMAGE_SIDE."""
        if win.minimized:
            return None, 1.0
        from ctypes import wintypes
        user32, gdi32 = win32._user32(), _STATE["gdi32"]
        outer = wintypes.RECT()
        user32.GetWindowRect(win.hwnd, ctypes.byref(outer))
        full_w, full_h = outer.right - outer.left, outer.bottom - outer.top
        if full_w <= 0 or full_h <= 0:
            return None, 1.0
        screen = user32.GetDC(0)
        whole_dc = gdi32.CreateCompatibleDC(screen)
        whole = gdi32.CreateCompatibleBitmap(screen, full_w, full_h)
        gdi32.SelectObject(whole_dc, whole)
        try:
            if not user32.PrintWindow(win.hwnd, whole_dc, 2):  # PW_RENDERFULLCONTENT
                return None, 1.0
            # The visible frame inside the outer rectangle (the rest is the invisible resize border).
            ox, oy = win.x - outer.left, win.y - outer.top
            rx, ry, rw, rh = region or (0, 0, win.width, win.height)
            rx, ry, rw, rh = int(ox + rx), int(oy + ry), int(rw), int(rh)
            scale = 1.0
            if region is None and max(rw, rh) > MAX_IMAGE_SIDE:
                scale = MAX_IMAGE_SIDE / max(rw, rh)
            out_w, out_h = max(1, int(rw * scale)), max(1, int(rh * scale))
            out_dc = gdi32.CreateCompatibleDC(screen)
            out = gdi32.CreateCompatibleBitmap(screen, out_w, out_h)
            previous = gdi32.SelectObject(out_dc, out)
            try:
                gdi32.SetStretchBltMode(out_dc, 4)  # HALFTONE
                gdi32.StretchBlt(out_dc, 0, 0, out_w, out_h, whole_dc, rx, ry, rw, rh, 0x00CC0020)  # SRCCOPY
                gdi32.SelectObject(out_dc, previous)  # GetDIBits reads a bitmap no DC holds

                class BITMAPINFOHEADER(ctypes.Structure):
                    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                                ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

                header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), out_w, -out_h, 1, 32, 0, 0, 0, 0, 0, 0)
                pixels = ctypes.create_string_buffer(out_w * out_h * 4)
                if not gdi32.GetDIBits(screen, out, 0, out_h, pixels, ctypes.byref(header), 0):
                    return None, 1.0
                return png_encode(out_w, out_h, pixels.raw), scale
            finally:
                gdi32.DeleteObject(out)
                gdi32.DeleteDC(out_dc)
        finally:
            gdi32.DeleteObject(whole)
            gdi32.DeleteDC(whole_dc)
            user32.ReleaseDC(0, screen)

    def get_state(self, session: str, query: str, *, window=None, window_id=None, full=False,
                  as_diff=True, include: str = "all", claim=None) -> Observation:
        """Read one window: its tree, its screenshot, or both (``include``). ``claim`` (see
        :func:`base.claimed`) names a target that operates the window's content itself; that content is left
        out of the tree."""
        include = str(include or "all").lower()
        if include not in INCLUDE:
            raise BadArgument("include must be all, tree or screenshot")
        with self._lock:
            automation, _ = win32._uia()
            app = self.resolve_app(query)
            key = (session, app.pid)
            previous = self._states.get(key)
            win = self._pick(app, window, window_id, previous.window_id if previous else 0)
            title, wid = (win.title, win.hwnd) if win else ("", 0)
            same_window = bool(previous) and previous.window_id == wid
            if include == "screenshot" and not same_window:
                raise BadArgument("Read this window with include=all first; a screenshot-only read keeps the "
                                  "tree of the window the model already has.")
            image, scale = (None, 1.0)
            if include != "tree" and win is not None:
                image, scale = self._capture(win)
            lines = [f"App: {app.name} ({app.ident}, pid {app.pid})"]
            if win is None:
                lines.append("Window: none open.")
            else:
                note = " (minimized)" if win.minimized else ""
                lines.append(f'Window: "{title}"{note}, window_id {wid}, {win.width}x{win.height} pixels')
                if image is not None:
                    w, h = int(round(win.width * scale)), int(round(win.height * scale))
                    lines.append(f"Screenshot: {w}x{h} pixels; x,y coordinates in actions are pixels of this screenshot.")
                elif include != "tree":
                    lines.append("Screenshot: unavailable (the window is minimized; show_window restores it). "
                                 "Use element indices.")
            owner = claimed(claim, {"app": app.name, "bundle": app.ident, "pid": app.pid, "title": title,
                                    "window_id": wid}) if wid else None
            if owner is not None:
                lines.append(claim_line(owner))
            others = [w for w in self._windows(app.pid) if w.hwnd != wid]
            if others:
                lines.append("Other windows: " + "; ".join(f'"{w.title}"' for w in others[:8])
                             + ". list_windows shows their ids; pass window=<title> or window_id to read one.")
            frame = (win.x, win.y, win.width, win.height) if win else None
            if include == "screenshot":
                previous.origin = (frame[0], frame[1]) if frame else previous.origin
                previous.size = (frame[2], frame[3]) if frame else previous.size
                previous.scale, previous.has_screenshot = scale, image is not None
                previous.text_boxes = _read_text(image) if previous.sparse else []
                lines += ["", "Tree not read (include=screenshot); element indices are those of the last tree."]
                lines.extend(_text_section(previous.text_boxes))
                return Observation(text="\n".join(lines), image=image, app=app.name, sparse=previous.sparse)
            root = self._safe(lambda: automation.ElementFromHandle(wid)) if wid else None
            nodes, elements, complete = self._read_tree(root)
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
                state.text_boxes = _read_text(image)
            self._states[key] = state
            if not complete:
                lines.append(f"The tree was cut at {MAX_NODES} elements.")
            lines.append("")
            lines.append("Accessibility tree changes since the last state:" if is_diff else "Accessibility tree:")
            lines.append(body or "(empty)")
            lines.extend(_text_section(state.text_boxes))
            return Observation(text="\n".join(lines), image=image, app=app.name, sparse=state.sparse)

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
            win = next((w for w in self._windows(app.pid) if w.hwnd == state.window_id), None)
            if win is None:
                raise ComputerUseError("The window is gone; call get_state again.")
            region = (px / state.scale, py / state.scale, pw / state.scale, ph / state.scale)
            image, _ = self._capture(win, region=region)
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

    def start_audio(self, session: str, query: str, max_duration=None) -> Report:
        try:
            limit = audio.seconds(max_duration)
        except ValueError as exc:
            raise ComputerUseError(str(exc)) from exc
        with self._lock:
            running = self._recordings.get(session)
            if running is not None and not running.stopped:
                raise ComputerUseError(f"Already recording {running.app}; call stop_audio_recording first.")
            app = self.resolve_app(query)
            from . import audio as windows_audio
            try:
                recording = windows_audio.start(app.pid, app.name, limit)
            except (OSError, RuntimeError) as exc:
                raise ComputerUseError(f"Could not record {app.name}: {exc}") from exc
            self._recordings[session] = recording
            return Report(f"Recording the sound {app.name} plays, for up to {limit:g} seconds; the user still hears "
                          "it and nothing else is recorded. Act as usual, then call stop_audio_recording.")

    def stop_audio(self, session: str, query: str = "") -> AudioClip:
        with self._lock:
            recording = self._recordings.get(session)
            if recording is None:
                raise ComputerUseError("Nothing is being recorded; call start_audio_recording first.")
            if query:
                app = self._running_match(query)
                if app is not None and app.pid != recording.pid:
                    raise ComputerUseError(f"The recording is of {recording.app}, not {query}.")
            del self._recordings[session]
        from . import audio as windows_audio
        data, peak = windows_audio.finish(recording)
        return AudioClip(app=recording.app, path=save_recording(recording.app, data), data=data,
                         seconds=round(recording.seconds, 2), peak=peak)

    def settle(self, session: str, query: str) -> float:
        app = self.resolve_app(query, launch=False)
        previous = self._states.get((session, app.pid))
        start = time.monotonic()
        time.sleep(SETTLE_MIN)
        win = self._pick(app, prefer_id=previous.window_id if previous else 0)
        automation, _ = win32._uia()
        root = self._safe(lambda: automation.ElementFromHandle(win.hwnd)) if win else None
        last, busy = self._quick_signature(root)
        while True:
            elapsed = time.monotonic() - start
            if elapsed >= (BUSY_MAX if busy else SETTLE_MAX):
                break
            check_cancelled()
            time.sleep(0.2)
            current, busy = self._quick_signature(root)
            if current == last and not busy:
                break
            last = current
        return time.monotonic() - start

    def _quick_signature(self, root) -> tuple[tuple, bool]:
        out, busy = [], False
        queue = [root] if root is not None else []
        while queue and len(out) < QUICK_NODES:
            el = queue.pop(0)
            role = self._role(el)
            if role in _BUSY_ROLES:
                ranged = self._pattern(el, RANGE_VALUE)
                current = self._safe(lambda: float(ranged.CurrentValue)) if ranged is not None else None
                maximum = self._safe(lambda: float(ranged.CurrentMaximum)) if ranged is not None else None
                busy = busy or current is None or (maximum is not None and current < maximum)
            out.append((role, self._name(el), self._value_text(el) or "",
                        self._safe(lambda el=el: bool(el.CurrentIsEnabled), True)))
            queue.extend(self._children(el))
        return tuple(out), busy
