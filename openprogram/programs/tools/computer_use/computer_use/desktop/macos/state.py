"""A window's state: its screenshot, its tree, OCR text for sparse windows, and when it has settled."""
from __future__ import annotations

import time
from typing import Any

from ..common import audio
from ..common import ocr
from ..common.base import (INCLUDE, OCR_SPARSE_NODES, AudioClip, BadArgument, ComputerUseError, NotDispatched,
    Observation, Report, claim_line, claimed, check_cancelled as _check_cancelled, save_recording as _save_recording,
    window_node_count as _window_node_count)
from ..common.tree import _BIDI_MARKS, diff, leave_out, number, role_name, snapshot
from .frameworks import (BUSY_MAX, FILE_PANEL_NOTE, FILE_PANEL_OPEN_NOTE, MAX_IMAGE_SIDE, MAX_NODES, QUICK_NODES,
    SETTLE_MAX, SETTLE_MIN, WindowState, _BUSY_ROLES, _CLAIMED_ROLES, _log, _unlocked)
from .geometry import live_origin
from .accessibility import _await
from . import frameworks


def _report_control(info: dict[str, Any], payload: dict[str, Any]) -> None:
    from openprogram.host.resources.native_window import note_control
    note_control(payload, owner=info.get("owner"))


class WindowReading:
    """Reading a window: capture, tree, text off the screenshot, zoom, find, settle and sound."""

    def _backing_scale(self) -> float:
        appkit, _, _, _ = frameworks._frameworks()
        return max([1.0] + [float(s.backingScaleFactor()) for s in appkit.NSScreen.screens() or []])

    def _capture(self, pid: int, frame, window_id: int = 0, *, region=None) -> tuple[bytes | None, float]:
        """PNG of the window (or of ``region``, window-relative points) and its pixels per point.

        The window is found by its window-server id, falling back to its
        frame; windows on another Space or covered by others are captured
        as they are, without being brought forward.
        """
        appkit, _, _, sc = frameworks._frameworks()
        x, y, width, height = frame
        content = _await(lambda cb: sc.SCShareableContent
                         .getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(True, False, cb))
        match = None
        for window in content.windows():
            owner = window.owningApplication()
            if owner is None or int(owner.processID()) != pid:
                continue
            if window_id and int(window.windowID()) == window_id:
                match = window
                break
            f = window.frame()
            if (match is None and window.windowLayer() == 0 and max(
                    abs(f.origin.x - x), abs(f.origin.y - y), abs(f.size.width - width), abs(f.size.height - height)) < 3):
                match = window
        if match is None:
            return None, 1.0
        rx, ry, rw, rh = region if region else (0.0, 0.0, width, height)
        scale = min(self._backing_scale(), MAX_IMAGE_SIDE / max(rw, rh, 1))
        config = sc.SCStreamConfiguration.alloc().init()
        if region:
            config.setSourceRect_(((rx, ry), (rw, rh)))
        config.setWidth_(max(1, int(round(rw * scale))))
        config.setHeight_(max(1, int(round(rh * scale))))
        config.setShowsCursor_(False)
        content_filter = sc.SCContentFilter.alloc().initWithDesktopIndependentWindow_(match)
        image = _await(lambda cb: sc.SCScreenshotManager
                       .captureImageWithFilter_configuration_completionHandler_(content_filter, config, cb))
        bitmap = appkit.NSBitmapImageRep.alloc().initWithCGImage_(image)
        data = bitmap.representationUsingType_properties_(appkit.NSBitmapImageFileTypePNG, {})
        return bytes(data), float(config.width()) / rw if rw else scale

    def capture_window(self, pid: int, frame, *, region=None) -> tuple[bytes | None, float]:
        """PNG of the window of ``pid`` at ``frame`` (x, y, width, height in points, origin top left), or of
        ``region`` of it, and its pixels per point; None when no such window is on the window server. For a
        browser target that captures its own window (Safari)."""
        return self._capture(int(pid), tuple(frame), region=region)

    def get_state(self, session: str, query: str, *, window=None, window_id=None, full=False,
                  as_diff=True, include: str = "all", claim=None) -> Observation:
        """Read one window: its tree, its screenshot, or both (``include``).

        Indices stay stable for the same window (see :func:`tree.number`);
        a tree read without ``full`` returns only what changed since the
        model last saw it. A screenshot-only read updates the coordinate
        frame and leaves the tree and its indices as they were. ``claim``
        (see :func:`base.claimed`) names a target that operates the window's
        content itself; that content is then left out of the tree.
        """
        include = str(include or "all").lower()
        if include not in INCLUDE:
            raise BadArgument("include must be all, tree or screenshot")
        _unlocked()
        _, ax, _, _ = frameworks._frameworks()
        app = self.resolve_app(query)
        self._await_answer(app)
        with self._lock:
            pid = int(app.processIdentifier())
            root = ax.AXUIElementCreateApplication(pid)
            ax.AXUIElementSetMessagingTimeout(root, 3.0)
            key = (session, pid)
            previous = self._states.get(key)
            target = self._pick_window(root, window, window_id=window_id,
                                       prefer_id=previous.window_id if previous else 0)
            followed = ""
            if previous is not None and window in (None, "") and window_id in (None, "", 0) and include != "screenshot":
                fresh = self._new_front_window(root, previous)
                if fresh is not None and fresh != target:
                    followed, target = previous.window_title, fresh
            frame = self._frame(target) if target is not None else None
            title = str(self._attr(target, "AXTitle") or "") if target is not None else ""
            minimized = bool(self._attr(target, "AXMinimized")) if target is not None else False
            wid = self._window_id(target, pid, frame) if target is not None else 0
            same_window = bool(previous) and (previous.window_id == wid if wid else previous.window_title == title)
            self._control(session, pid, str(app.localizedName() or ""), target, wid)
            if include == "screenshot" and not same_window:
                raise BadArgument("Read this window with include=all first; a screenshot-only read keeps the "
                                  "tree of the window the model already has.")
            image, scale = (None, 1.0)
            if include != "tree" and frame is not None and frame[2] > 1 and frame[3] > 1 and not minimized:
                image, scale = self._capture(pid, frame, wid)
            name = str(app.localizedName() or query)
            lines = [f"App: {name} ({app.bundleIdentifier() or 'no bundle id'}, pid {pid})"
                     + ("" if app.isActive() else ", in the background")]
            if target is None:
                lines.append("Window: none open. The tree lists the menu bar only.")
            else:
                notes = [n for n, on in (("minimized", minimized), ("app hidden", bool(app.isHidden()))) if on]
                note = f" ({', '.join(notes)})" if notes else ""
                where = f"window_id {wid}, " if wid else ""
                lines.append(f'Window: "{title}"{note}, {where}{int(frame[2])}x{int(frame[3])} points'
                             if frame else f'Window: "{title}"{note}')
                if image is not None:
                    w, h = int(round(frame[2] * scale)), int(round(frame[3] * scale))
                    lines.append(f"Screenshot: {w}x{h} pixels; x,y coordinates in actions are pixels of this screenshot.")
                elif include != "tree":
                    lines.append("Screenshot: unavailable (the window is minimized or its app is hidden; "
                                 "show_window brings it back). Use element indices.")
                if followed:
                    lines.append(f'It opened over "{followed}" after the last step, so this state and the next '
                                 "actions are of it; get_state(window=...) reads another window.")
            owner = claimed(claim, {"app": name, "bundle": str(app.bundleIdentifier() or ""), "pid": pid,
                                    "title": title, "window_id": wid,
                                    "url": str(self._attr(target, "AXDocument") or "") if target is not None else ""}
                            ) if target is not None else None
            if owner is not None:
                lines.append(claim_line(owner))
            windows = self._windows(root)
            others = [w for w in windows if w != target]
            file_panel = self._file_panel(target)
            if file_panel:
                lines.append(FILE_PANEL_NOTE)
            elif target is not None and (any(self._file_panel(w) for w in others)
                                         or self.__dict__.get("_live_panels", {}).get(pid)):
                lines.append(FILE_PANEL_OPEN_NOTE)
            if others:
                lines.append("Other windows: " + "; ".join(
                    f'"{t}"' if (t := str(self._attr(w, "AXTitle") or "")) else "(untitled)" for w in others[:8])
                    + ". list_windows shows their ids; pass window=<title> or window_id to read one.")
            if include == "screenshot":
                previous.origin = (frame[0], frame[1]) if frame else previous.origin
                previous.size = (frame[2], frame[3]) if frame else previous.size
                previous.scale, previous.has_screenshot = scale, image is not None
                previous.text_boxes = self._read_text(image, frame, scale) if previous.sparse else []
                previous.shapes = self._read_shapes(image, frame, scale, previous.text_boxes) if previous.sparse else []
                lines.append("")
                lines.append("Tree not read (include=screenshot); element indices are those of the last tree.")
                lines.extend(self._text_section(previous.text_boxes, previous.shapes))
                return Observation(text="\n".join(lines), image=image, app=name, sparse=previous.sparse)
            nodes, elements, complete = self._read_tree(root, target)
            left_out = _CLAIMED_ROLES if owner is not None else frozenset()
            if left_out:
                nodes, elements, _ = leave_out(nodes, elements, left_out)
            prior = previous.snapshot if same_window else None
            fresh = number(nodes, prior)
            body, is_diff = diff(prior if (as_diff and not full) else None, nodes)
            state = WindowState(
                pid=pid, window_title=title, window_id=wid,
                elements={node.index: element for node, element in zip(nodes, elements)},
                nodes=nodes, by_index={node.index: node for node in nodes}, snapshot=snapshot(nodes, fresh),
                origin=(frame[0], frame[1]) if frame else (0.0, 0.0),
                size=(frame[2], frame[3]) if frame else (0.0, 0.0), scale=scale, has_screenshot=image is not None,
                window_keys=frozenset(self._window_key(w) for w in windows), left_out=left_out)
            if include == "tree" and same_window and previous.has_screenshot:
                state.scale, state.has_screenshot = previous.scale, True
            # A file dialog's file list is not in the tree, so its names come from the screenshot.
            state.sparse = target is not None and (file_panel or _window_node_count(nodes) < OCR_SPARSE_NODES)
            if state.sparse:
                state.text_boxes = self._read_text(image, frame, scale)
                state.shapes = self._read_shapes(image, frame, scale, state.text_boxes)
            self._states[key] = state
            if not complete:
                lines.append(f"The tree was cut at {MAX_NODES} elements.")
            lines.append("")
            lines.append("Accessibility tree changes since the last state:" if is_diff else "Accessibility tree:")
            lines.append(body or "(empty)")
            lines.extend(self._text_section(state.text_boxes, state.shapes))
            return Observation(text="\n".join(lines), image=image, app=name, sparse=state.sparse)

    def _read_text(self, image: bytes | None, frame, scale: float) -> list[ocr.TextBox]:
        if image is None or frame is None:
            return []
        try:
            return ocr.recognize(image, frame[2] * scale, frame[3] * scale)
        except Exception:  # OCR only adds to a read; a failure must not fail it
            return []

    def _read_shapes(self, image: bytes | None, frame, scale: float, text: list[ocr.TextBox]) -> list[ocr.Shape]:
        if image is None or frame is None:
            return []
        try:
            return ocr.detect_shapes(image, frame[2] * scale, frame[3] * scale, text, scale)
        except Exception:  # like OCR, shapes only add to a read
            return []

    @staticmethod
    def _text_section(boxes: list[ocr.TextBox], shapes: list[ocr.Shape] = ()) -> list[str]:
        out = []
        if boxes:
            out += ["", "Text on the screenshot (OCR; this app exposes little to Accessibility, so act on it "
                        "with x,y at a line's centre):", ocr.render(boxes)]
        if shapes:
            out += ["", "Icons and controls without text on the screenshot (outlines; check the screenshot "
                        "for what each is, then act with x,y at its centre):", ocr.render_shapes(list(shapes))]
        return out

    def _state_for(self, session: str, query: str, app=None) -> tuple[Any, WindowState]:
        app = app if app is not None else self.resolve_app(query, launch=False)
        state = self._states.get((session, int(app.processIdentifier())))
        if state is None:
            raise NotDispatched(f"Call get_state for {query!r} first; actions use its element indices and screenshot.")
        return app, state

    def _follow_window(self, state: WindowState) -> None:
        """Move ``state.origin`` to where the window is now, if the window server knows it."""
        if not state.window_id:
            return
        try:
            _, _, cg, _ = frameworks._frameworks()
            rows = cg.CGWindowListCopyWindowInfo(cg.kCGWindowListOptionIncludingWindow, state.window_id) or []
        except Exception:
            return
        origin = live_origin(rows, state.window_id)
        if origin is not None:
            state.origin = origin

    def _control(self, session: str, pid: int, app_name: str, window, window_id: int) -> None:
        """Mark ``window_id`` as the window this session controls (see window_watch)."""
        if not window_id:
            return
        info: dict[str, Any] = {"pid": pid, "app_name": app_name}
        try:
            from openprogram.host.resources.native_window import control_owner
            info["owner"] = control_owner()
            if window is None:
                _, ax, _, _ = frameworks._frameworks()
                window = next((w for w in self._windows(ax.AXUIElementCreateApplication(pid))
                               if self._ax_window_id(w) == window_id), None)
            if window is not None:
                info["buttons"] = self._title_buttons(window)
        except Exception:
            # The overlay is a courtesy; a step never fails because of it.
            _log.debug("window overlay info unavailable pid=%s", pid, exc_info=True)
        self._watch.touch(session, window_id, info)

    def zoom(self, session: str, query: str, x, y, width, height) -> Observation:
        """A region of the last screenshot at the display's full pixel density."""
        _unlocked()
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
            region = (px / state.scale, py / state.scale, pw / state.scale, ph / state.scale)
            image, scale = self._capture(state.pid, (*state.origin, *state.size), state.window_id, region=region)
            if image is None:
                raise ComputerUseError("The window could not be captured; call get_state again.")
            text = (f"Zoomed region ({int(px)}, {int(py)}) {int(pw)}x{int(ph)} of the last screenshot, at "
                    f"{scale:g}x pixels per point. Coordinates for actions still refer to the last screenshot, "
                    "not to this image.")
            return Observation(text=text, image=image, app=str(app.localizedName() or query))

    def find(self, session: str, query: str, text: str = "", role: str = "") -> str:
        """Elements of the last full tree whose label, value or placeholder contains ``text``."""
        with self._lock:
            _, state = self._state_for(session, query)
            needle = " ".join(str(text or "").split()).lower()
            wanted_role = str(role or "").strip().lower()
            if not needle and not wanted_role:
                raise BadArgument("find needs text, role, or both")
            hits = []
            for node in state.nodes:
                if wanted_role and wanted_role not in (node.role.lower(), role_name(node.role).lower(),
                                                       role_name(node.role, node.subrole).lower()):
                    continue
                haystack = " ".join(_BIDI_MARKS.sub("", f"{node.label} {node.value} {node.placeholder}").split()).lower()
                if needle and needle not in haystack:
                    continue
                hits.append(f"{node.index} {node.body()}")
            if not hits and needle and not wanted_role and state.has_screenshot:
                if not state.text_boxes:
                    image, scale = self._capture(state.pid, (*state.origin, *state.size), state.window_id)
                    if image is not None and abs(scale - state.scale) < 1e-3:
                        state.text_boxes = self._read_text(image, (*state.origin, *state.size), scale)
                seen = ocr.matching(state.text_boxes, needle)
                if seen:
                    return (f"no element matches, but the screenshot shows {len(seen)} line(s) with that text "
                            "(OCR; act with x,y at the centre):\n" + ocr.render(seen[:60]))
            if not hits:
                return "no element of the last tree matches; call get_state for a fresh tree, or scroll"
            more = f"\n…and {len(hits) - 60} more; narrow the search." if len(hits) > 60 else ""
            return f"{len(hits)} match(es):\n" + "\n".join(hits[:60]) + more

    def start_audio(self, session: str, query: str, max_duration=None) -> Report:
        """Record what ``query`` plays until stop_audio or the limit."""
        try:
            limit = audio.seconds(max_duration)
        except ValueError as exc:
            raise ComputerUseError(str(exc)) from exc
        with self._lock:
            frameworks._frameworks()
            running = self._recordings.get(session)
            if running is not None and not running.stopped:
                raise ComputerUseError(f"Already recording {running.app}; call stop_audio_recording first.")
            app = self.resolve_app(query)
            name = str(app.localizedName() or query)
            try:
                recording = audio.start(int(app.processIdentifier()), name, limit)
            except RuntimeError as exc:
                raise ComputerUseError(f"Could not record {name}: {exc}") from exc
            self._recordings[session] = recording
            return Report(f"Recording the sound {name} plays, for up to {limit:g} seconds; the user still hears it "
                          "and nothing else is recorded. Act as usual, then call stop_audio_recording.")

    def stop_audio(self, session: str, query: str = "") -> AudioClip:
        """Stop this session's recording and save it as a WAV."""
        with self._lock:
            recording = self._recordings.get(session)
            if recording is None:
                raise ComputerUseError("Nothing is being recorded; call start_audio_recording first.")
            if query:
                app = self._running_match(query)
                if app is not None and int(app.processIdentifier()) != recording.pid:
                    raise ComputerUseError(f"The recording is of {recording.app}, not {query}.")
            del self._recordings[session]
        data, peak = audio.finish(recording)
        return AudioClip(app=recording.app, path=_save_recording(recording.app, data), data=data,
                         seconds=round(recording.seconds, 2), peak=peak)

    def settle(self, session: str, query: str) -> float:
        """Wait for the window to stop changing after an action; returns the seconds waited."""
        _, ax, _, _ = frameworks._frameworks()
        app = self.resolve_app(query, launch=False)
        began = time.monotonic()
        # A step the app is still carrying out leaves it silent; what it shows is read once it answers.
        self._await_answer(app)
        pid = int(app.processIdentifier())
        previous = self._states.get((session, pid))
        root = ax.AXUIElementCreateApplication(pid)
        window = self._pick_window(root, None, prefer_id=previous.window_id if previous else 0)
        start = time.monotonic()
        time.sleep(SETTLE_MIN)
        last, busy = self._quick_signature(root, window)
        while True:
            elapsed = time.monotonic() - start
            if elapsed >= (BUSY_MAX if busy else SETTLE_MAX):
                break
            _check_cancelled()
            time.sleep(0.2)
            current, busy = self._quick_signature(root, window)
            if current == last and not busy:
                break
            last = current
        return time.monotonic() - began

    def _quick_signature(self, root, window) -> tuple[tuple, bool]:
        """A cheap fingerprint of the first QUICK_NODES elements, and whether anything shows busy."""
        out, busy = [], False
        queue = [window] if window is not None else []
        for child in self._attr(root, "AXChildren") or []:
            if str(self._attr(child, "AXRole") or "") in ("AXMenu", "AXSheet", "AXPopover"):
                queue.append(child)
        seen: set = {root}
        while queue and len(out) < QUICK_NODES:
            element = queue.pop(0)
            try:
                if element in seen:
                    continue
                seen.add(element)
            except TypeError:
                pass
            values = self._attrs(element, ["AXRole", "AXTitle", "AXValue", "AXEnabled", "AXChildren"])
            role = str(values.get("AXRole") or "")
            value = values.get("AXValue")
            if role in _BUSY_ROLES and not isinstance(value, (int, float)) or (
                    role == "AXProgressIndicator" and isinstance(value, (int, float)) and value < 1):
                busy = True
            out.append((role, str(values.get("AXTitle") or ""), str(value) if value is not None else "",
                        values.get("AXEnabled")))
            queue.extend(values.get("AXChildren") or [])
        return tuple(out), busy
