"""Bounded WeChat window reader for versions without message Accessibility nodes.

Only search, exact-result selection and history scrolling are available. No
message composer, send action, clipboard, arbitrary key or agent tool is exposed.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import uuid
from zoneinfo import ZoneInfo

from openprogram.programs.workflow.report_io import encode, preflight, write_file


class VisualUnavailable(RuntimeError):
    pass


def _normal(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def _lines(frame):
    return [line for line in frame["lines"] if line.get("confidence", 0) >= 0.8]


def group_header(frame, group):
    """Require a full title in the conversation header, never a sidebar match."""
    return [
        line
        for line in _lines(frame)
        if line["x"] >= frame["width"] * 0.32
        and line["y"] < frame["height"] * 0.09
        and _normal(re.sub(r"\s*[（(]\d+[）)]$", "", line["label"])) == _normal(group)
    ]


def _date_label(label, captured):
    label = _normal(label)
    today = (
        datetime.fromisoformat(captured).astimezone(ZoneInfo("Asia/Shanghai")).date()
    )
    match = re.fullmatch(
        r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日(?:\d{1,2}:\d{2})?", label
    )
    try:
        if match:
            year = int(match[1] or today.year)
            result = today.replace(year=year, month=int(match[2]), day=int(match[3]))
            if not match[1] and result > today:
                result = result.replace(year=year - 1)
            return result.isoformat()
        match = re.fullmatch(
            r"(今天|昨天|星期[一二三四五六日天])(?:\d{1,2}:\d{2})?", label
        )
        if match:
            name = match[1]
            delta = (
                0
                if name == "今天"
                else 1
                if name == "昨天"
                else (
                    today.weekday()
                    - "一二三四五六日".index(name[-1].replace("天", "日"))
                )
                % 7
            )
            return (today - timedelta(days=delta)).isoformat()
    except ValueError:
        return None
    return None


def extract_messages(frame, group, members):
    """Keep complete OCR rows only with a preceding date and roster author.

    Dates never carry across screenshots: the first visible partial message is
    excluded. Every accepted message retains its precise date/author/body rows.
    This conservative adapter covers text bubbles, not image or attachment text.
    """
    if len(group_header(frame, group)) != 1:
        return []
    width, height = frame["width"], frame["height"]
    rows = sorted(
        [
            line
            for line in frame["lines"]
            if width * 0.33 < line["x"] < width * 0.93
            and height * 0.09 < line["y"] < height * 0.73
        ],
        key=lambda row: (row["y"], row["x"]),
    )
    result, date, date_row, author, author_row, body = [], None, None, None, None, []

    def finish(complete):
        if complete and date and author and body:
            text = "\n".join(row["label"] for row in body)
            result.append(
                {
                    "author": author,
                    "date": date,
                    "text": text,
                    "evidence": {
                        "date_row": date_row,
                        "author_row": author_row,
                        "body_rows": body,
                        "capture": frame.get("evidence"),
                    },
                }
            )

    for row in rows:
        if row.get("confidence", 0) < 0.8:
            # An unreadable sender/date is a boundary, not a line to omit.
            # Do not transfer attribution across an uncertain fragment.
            date, date_row, author, author_row, body = None, None, None, None, []
            continue
        stamp = _date_label(row["label"], frame["captured_at"])
        # Date separators are central; sender labels are left-aligned. A date
        # quoted inside a report cannot relabel another member's message.
        if stamp and width * 0.5 < row["x"] < width * 0.8:
            finish(True)
            date, date_row, author, body = stamp, row, None, []
        elif row["label"] in members and row["x"] < width * 0.46:
            finish(True)
            author, author_row, body = row["label"], row, []
        elif (
            re.fullmatch(r"\d{1,2}:\d{2}", _normal(row["label"]))
            and width * 0.5 < row["x"] < width * 0.8
        ):
            finish(True)
            author, body = None, []
        elif (
            author
            and abs(row["x"] - author_row["x"]) < 8
            and row["h"] <= author_row["h"] * 1.15
        ):
            # A new small sender label not in the roster ends the prior
            # message, but its content must not be assigned to that member.
            finish(True)
            author, body = None, []
        elif author:
            body.append(row)
    # The last bubble may extend below the viewport; do not claim it complete.
    finish(False)
    return result


def _cancel():
    from openprogram.agent.run_control import check_cancelled

    check_cancelled()


def _capture_allowed(window):
    if window.get("kCGWindowSharingState") == 0:
        raise VisualUnavailable("WINDOW_CAPTURE_UNAVAILABLE")


class WeChatWindow:
    """Exact PID/window identity with fixed, evidence-bound navigation methods."""

    def __init__(self):
        if sys.platform != "darwin":
            raise VisualUnavailable("UNSUPPORTED_PLATFORM")
        try:
            import AppKit
            import Quartz
            import ApplicationServices
            import ScreenCaptureKit
        except ImportError as exc:
            raise VisualUnavailable("NATIVE_DEPENDENCIES_UNAVAILABLE") from exc
        self.appkit, self.cg, self.sc, self.ax = (
            AppKit,
            Quartz,
            ScreenCaptureKit,
            ApplicationServices,
        )
        AppKit.NSApplication.sharedApplication()
        if (
            not Quartz.CGPreflightScreenCaptureAccess()
            or not ApplicationServices.AXIsProcessTrusted()
        ):
            raise VisualUnavailable("ACCESS_REQUIRED")
        apps = [
            a
            for a in AppKit.NSWorkspace.sharedWorkspace().runningApplications()
            if a.bundleIdentifier() == "com.tencent.xinWeChat"
        ]
        if len(apps) != 1:
            raise VisualUnavailable("APP_NOT_RUNNING")
        self.app = apps[0]
        self.pid = self.app.processIdentifier()
        self.launch = str(self.app.launchDate())
        # Ask this already-running application to reopen its main window. The
        # fixed bundle identifier cannot open another application or document.
        subprocess.run(
            ["/usr/bin/open", "-b", "com.tencent.xinWeChat"],
            check=True,
            capture_output=True,
            timeout=5,
        )
        time.sleep(0.5)
        windows = self._windows()
        if len(windows) != 1:
            raise VisualUnavailable("WINDOW_NOT_UNIQUE")
        _capture_allowed(windows[0])
        self.window_id = windows[0]["kCGWindowNumber"]
        self.bounds = dict(windows[0]["kCGWindowBounds"])
        self.deadline = time.monotonic() + 120
        self.scratch = tempfile.TemporaryDirectory(prefix="openprogram-wechat-")

    def _windows(self):
        return [
            w
            for w in self.cg.CGWindowListCopyWindowInfo(
                self.cg.kCGWindowListOptionAll, 0
            )
            if w.get("kCGWindowOwnerPID") == self.pid
            and w.get("kCGWindowLayer") == 0
            and w.get("kCGWindowName") in ("WeChat", "微信")
            and w["kCGWindowBounds"]["Width"] >= 600
            and w["kCGWindowBounds"]["Height"] >= 400
        ]

    def check(self):
        _cancel()
        if time.monotonic() >= self.deadline:
            raise VisualUnavailable("READ_TIMEOUT")
        if self.app.isTerminated() or str(self.app.launchDate()) != self.launch:
            raise VisualUnavailable("WINDOW_CHANGED")
        windows = self._windows()
        if (
            len(windows) != 1
            or windows[0]["kCGWindowNumber"] != self.window_id
            or dict(windows[0]["kCGWindowBounds"]) != self.bounds
        ):
            raise VisualUnavailable("WINDOW_CHANGED")
        _capture_allowed(windows[0])

    def _run(self, args, timeout):
        """Cancel the entire OCR subprocess group, including its Swift child."""
        self.check()
        proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
        )
        end = min(self.deadline, time.monotonic() + timeout)
        try:
            while True:
                self.check()
                if time.monotonic() >= end:
                    raise VisualUnavailable("OCR_TIMEOUT")
                try:
                    out, err = proc.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    pass
            if proc.returncode:
                raise VisualUnavailable("CAPTURE_OR_OCR_FAILED")
            return out
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate()

    def observe(self):
        self.check()
        path = Path(self.scratch.name) / (uuid.uuid4().hex + ".png")
        content = self._capture_call(
            lambda cb: (
                self.sc.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
                    True, False, cb
                )
            )
        )
        matches = [
            w
            for w in content.windows()
            if w.windowID() == self.window_id
            and w.owningApplication()
            and w.owningApplication().processID() == self.pid
        ]
        if len(matches) != 1:
            raise VisualUnavailable("WINDOW_CHANGED")
        config = self.sc.SCStreamConfiguration.alloc().init()
        config.setWidth_(int(self.bounds["Width"] * 2))
        config.setHeight_(int(self.bounds["Height"] * 2))
        config.setShowsCursor_(False)
        content_filter = (
            self.sc.SCContentFilter.alloc().initWithDesktopIndependentWindow_(
                matches[0]
            )
        )
        image = self._capture_call(
            lambda cb: (
                self.sc.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_(
                    content_filter, config, cb
                )
            )
        )
        bitmap = self.appkit.NSBitmapImageRep.alloc().initWithCGImage_(image)
        data = bitmap.representationUsingType_properties_(
            self.appkit.NSBitmapImageFileTypePNG, {}
        )
        path.write_bytes(bytes(data))
        # Reuse GUI Harness's native OCR implementation in a bounded process.
        raw = self._run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                "import json,sys; from gui_harness.perception.ocr import detect_text; "
                "print(json.dumps(detect_text(sys.argv[1]),ensure_ascii=False))",
                str(path),
            ],
            25,
        )
        try:
            lines = json.loads(raw)
            if not isinstance(lines, list) or len(lines) > 1000:
                raise ValueError()
            if not lines:
                raise VisualUnavailable("CAPTURE_CONTENT_UNAVAILABLE")
            for line in lines:
                if not isinstance(line.get("label"), str) or any(
                    type(line.get(k)) not in (int, float)
                    for k in ("x", "y", "w", "h", "confidence")
                ):
                    raise ValueError()
            bitmap = self.appkit.NSBitmapImageRep.imageRepWithContentsOfFile_(str(path))
            width, height = int(bitmap.pixelsWide()), int(bitmap.pixelsHigh())
        except (ValueError, TypeError, AttributeError) as exc:
            raise VisualUnavailable("OCR_UNAVAILABLE") from exc
        self.check()
        return {
            "lines": lines,
            "width": width,
            "height": height,
            "image": str(path),
            "captured_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "window_id": self.window_id,
            "pid": self.pid,
            "bounds": self.bounds,
        }

    def _capture_call(self, start):
        completed, result = threading.Event(), []

        def done(value, error):
            result.extend((value, error))
            completed.set()

        start(done)
        end = min(self.deadline, time.monotonic() + 10)
        while not completed.wait(0.02):
            self.check()
            if time.monotonic() >= end:
                raise VisualUnavailable("CAPTURE_TIMEOUT")
        if result[1] or result[0] is None:
            raise VisualUnavailable("CAPTURE_UNAVAILABLE")
        return result[0]

    def _click_line(self, frame, line):
        self.check()
        if (
            line not in frame["lines"]
            or line["x"] < 0
            or line["y"] < 0
            or line["x"] + line["w"] > frame["width"]
            or line["y"] + line["h"] > frame["height"]
        ):
            raise VisualUnavailable("CONTROL_NOT_VERIFIED")
        x = (
            self.bounds["X"]
            + (line["x"] + line["w"] / 2) * self.bounds["Width"] / frame["width"]
        )
        y = (
            self.bounds["Y"]
            + (line["y"] + line["h"] / 2) * self.bounds["Height"] / frame["height"]
        )
        self.app.activateWithOptions_(
            self.appkit.NSApplicationActivateIgnoringOtherApps
        )
        self.check()
        for kind in (self.cg.kCGEventLeftMouseDown, self.cg.kCGEventLeftMouseUp):
            event = self.cg.CGEventCreateMouseEvent(
                None, kind, (x, y), self.cg.kCGMouseButtonLeft
            )
            self.cg.CGEventPostToPid(self.pid, event)

    def search(self, frame, group):
        targets = [
            line
            for line in _lines(frame)
            if line["label"] in ("搜索", "Search")
            and line["x"] < frame["width"] * 0.27
            and line["y"] < frame["height"] * 0.09
        ]
        if len(targets) != 1:
            raise VisualUnavailable("SEARCH_UNAVAILABLE")
        self._click_line(frame, targets[0])
        self.check()
        if not self._search_has_focus():
            raise VisualUnavailable("SEARCH_FOCUS_UNVERIFIABLE")
        # Unicode insertion is fixed to a validated single-line group name.
        # No Return, clipboard paste, hotkey or arbitrary text action exists.
        event = self.cg.CGEventCreateKeyboardEvent(None, 0, True)
        self.cg.CGEventKeyboardSetUnicodeString(
            event, len(group.encode("utf-16-le")) // 2, group
        )
        self.cg.CGEventPostToPid(self.pid, event)
        time.sleep(0.25)

    def _search_has_focus(self):
        # OCR identifies where to click; it cannot prove keyboard focus. A
        # custom-rendered app without this native focus contract must wait for
        # the user to open the exact group, never type into an unknown field.
        root = self.ax.AXUIElementCreateApplication(self.pid)
        error, element = self.ax.AXUIElementCopyAttributeValue(
            root, "AXFocusedUIElement", None
        )
        if error or element is None:
            return False
        error, subrole = self.ax.AXUIElementCopyAttributeValue(
            element, "AXSubrole", None
        )
        if error or subrole != "AXSearchField":
            return False
        error, focused = self.ax.AXUIElementCopyAttributeValue(
            element, "AXFocused", None
        )
        return not error and bool(focused)

    def select_group(self, frame, group):
        targets = [
            line
            for line in _lines(frame)
            if _normal(line["label"]) == _normal(group)
            and line["y"] > frame["height"] * 0.1
            and line["x"] < frame["width"] * 0.35
        ]
        if len(targets) != 1:
            raise VisualUnavailable("GROUP_NOT_UNIQUE")
        self._click_line(frame, targets[0])
        time.sleep(0.25)

    def older(self, frame, group):
        headers = group_header(frame, group)
        if len(headers) != 1:
            raise VisualUnavailable("GROUP_NOT_VERIFIED")
        self.check()
        # Scroll only the conversation area below its verified title; never
        # touch the editor or sidebar. Event is delivered to the exact PID.
        x = self.bounds["X"] + self.bounds["Width"] * 0.7
        y = self.bounds["Y"] + self.bounds["Height"] * 0.4
        event = self.cg.CGEventCreateScrollWheelEvent(
            None, self.cg.kCGScrollEventUnitPixel, 1, int(self.bounds["Height"] * 0.45)
        )
        self.cg.CGEventSetLocation(event, (x, y))
        self.cg.CGEventPostToPid(self.pid, event)
        time.sleep(0.25)

    def close(self):
        self.scratch.cleanup()


def read_visual_group(group, members, output_dir):
    """Read at most twelve verified pages; never assert complete history."""
    if (
        not isinstance(group, str)
        or not group.strip()
        or len(group) > 200
        or any(ord(c) < 32 for c in group)
        or not isinstance(members, list)
        or not members
        or any(not isinstance(m, str) or not m.strip() for m in members)
    ):
        return {"status": "SCOPE_REQUIRED"}
    window = None
    try:
        target = Path(preflight(output_dir)) / "wechat-sources" / uuid.uuid4().hex
        window = WeChatWindow()
        frame = window.observe()
        if len(group_header(frame, group)) != 1:
            window.search(frame, group)
            frame = window.observe()
            window.select_group(frame, group)
            frame = window.observe()
        blocks, seen, pages = [], set(), []
        for index in range(12):
            if len(group_header(frame, group)) != 1:
                raise VisualUnavailable("GROUP_NOT_VERIFIED")
            digest = hashlib.sha256(encode(frame["lines"]).encode()).hexdigest()
            if digest in seen:
                break
            seen.add(digest)
            evidence = str(target / f"{index:02d}.json")
            # Persist only pages after exact group verification. Evidence is
            # local JSON, including the original PNG and all original OCR rows.
            payload = {k: v for k, v in frame.items() if k != "image"}
            payload["png_base64"] = base64.b64encode(
                Path(frame["image"]).read_bytes()
            ).decode()
            outcome = write_file(evidence, encode(payload))
            if not isinstance(outcome, str) or not outcome.splitlines()[-1].startswith(
                "Wrote "
            ):
                raise OSError(str(outcome))
            frame["evidence"] = evidence
            pages.append(evidence)
            blocks.extend(extract_messages(frame, group, members))
            if index < 11:
                window.older(frame, group)
                frame = window.observe()
        unique = {(b["author"], b["date"], b["text"]): b for b in blocks}
        return {
            "status": "READY" if unique else "SOURCE_METADATA_UNAVAILABLE",
            "group": group,
            "blocks": list(unique.values())[:100],
            "complete": False,
            "evidence": pages,
            "coverage": "At most 12 visible pages; text with page-local date and roster author only; images, clipped bubbles and older history unverified",
        }
    except VisualUnavailable as exc:
        return {"status": str(exc)[:200] or "VISUAL_READ_FAILED"}
    finally:
        if window is not None:
            window.close()
