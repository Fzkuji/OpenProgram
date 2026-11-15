"""Text and icons read off a screenshot, for windows whose Accessibility tree shows little.

Games, canvas editors, remote desktops and some Electron apps draw their
content without exposing it to Accessibility. Their screenshot still has
the words on it: Vision's text recognizer reads them with a box each, and
the box centre is a coordinate an action can click (on Linux, tesseract
reads them when it is installed). Icons and controls
without words are found as shapes: Vision's contour detector outlines
them, outlines that touch are merged, and whatever covers text or is too
small, too large or too thin to be a control is dropped.
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import shutil
import subprocess
import threading
from typing import Any

from .tree import _BIDI_MARKS

MAX_LINES = 150
MAX_LINE_CHARS = 200
MAX_SHAPES = 60


@dataclass(frozen=True)
class TextBox:
    """One recognized line, in pixels of the screenshot it was read from."""
    text: str
    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> tuple[int, int]:
        return int(round(self.x + self.width / 2)), int(round(self.y + self.height / 2))

    def line(self) -> str:
        cx, cy = self.center
        text = self.text if len(self.text) <= MAX_LINE_CHARS else self.text[:MAX_LINE_CHARS - 1] + "…"
        return f'"{text}" at ({cx}, {cy}), {int(round(self.width))}x{int(round(self.height))}'


def available() -> bool:
    return importlib.util.find_spec("Vision") is not None


def boxes_from_normalized(found: list[tuple[str, tuple[float, float, float, float]]],
                          width: float, height: float) -> list[TextBox]:
    """Vision boxes (0..1, origin bottom-left) as screenshot pixels, top to bottom, left to right."""
    out = []
    for text, (nx, ny, nw, nh) in found:
        text = " ".join(_BIDI_MARKS.sub("", str(text)).split())
        if not text:
            continue
        out.append(TextBox(text, *(round(v, 1) for v in (
            nx * width, (1.0 - ny - nh) * height, nw * width, nh * height))))
    # Rows first (a line is "the same row" within half its height), then left to right.
    out.sort(key=lambda b: (round((b.y + b.height / 2) / max(b.height, 1.0) * 2), b.x))
    return out


def render(boxes: list[TextBox]) -> str:
    lines = [b.line() for b in boxes[:MAX_LINES]]
    if len(boxes) > MAX_LINES:
        lines.append(f"…and {len(boxes) - MAX_LINES} more lines; zoom into a region to read them.")
    return "\n".join(lines)


def matching(boxes: list[TextBox], text: str) -> list[TextBox]:
    needle = " ".join(str(text or "").split()).lower()
    return [b for b in boxes if needle and needle in b.text.lower()]


_lock = threading.Lock()
_languages: list[str] | None = None


# Screens mix scripts whatever the system language is. The first language picks
# the model, so the list is only a hint: language detection chooses per image.
_DEFAULT_LANGUAGES = ("zh-Hans", "zh-Hant", "ja-JP", "ko-KR", "en-US")


def _recognition_languages(request) -> list[str]:
    """The user's preferred languages Vision can read, then Chinese, Japanese, Korean and English."""
    global _languages
    if _languages is None:
        import Foundation
        supported, _ = request.supportedRecognitionLanguagesAndReturnError_(None)
        _languages = pick_languages([str(p) for p in Foundation.NSLocale.preferredLanguages() or []],
                                    [str(s) for s in supported or []])
    return _languages


def pick_languages(preferred: list[str], supported: list[str]) -> list[str]:
    wanted: list[str] = []
    for tag in [*preferred, *_DEFAULT_LANGUAGES]:
        base = tag.split("-")[0]
        if base == "zh" and tag not in _DEFAULT_LANGUAGES:
            tag = "zh-Hant" if any(mark in tag for mark in ("Hant", "TW", "HK", "MO")) else "zh-Hans"
        match = tag if tag in supported else next((s for s in supported if s.split("-")[0] == base), "")
        if match and match not in wanted:
            wanted.append(match)
    return wanted or ["en-US"]


def recognize(png: bytes, width: float, height: float) -> list[TextBox]:
    """Lines of text in a PNG ``width`` x ``height`` pixels; empty when Vision is missing."""
    if not png or not available():
        return []
    import Foundation
    import Vision

    with _lock:
        data = Foundation.NSData.dataWithBytes_length_(png, len(png))
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, {})
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(True)
        request.setRecognitionLanguages_(_recognition_languages(request))
        if request.respondsToSelector_(b"setAutomaticallyDetectsLanguage:"):
            request.setAutomaticallyDetectsLanguage_(True)
        ok, _error = handler.performRequests_error_([request], None)
        if not ok:
            return []
        found: list[tuple[str, Any]] = []
        for observation in request.results() or []:
            candidates = observation.topCandidates_(1)
            if not candidates:
                continue
            box = observation.boundingBox()
            found.append((str(candidates[0].string()),
                          (box.origin.x, box.origin.y, box.size.width, box.size.height)))
    return boxes_from_normalized(found, width, height)



# Tesseract language packs tried in this order; the installed ones are used together.
_TESSERACT_LANGUAGES = ("eng", "chi_sim", "chi_tra", "jpn", "kor")
_tesseract_languages: str | None = None


def boxes_from_tesseract(tsv: str) -> list[TextBox]:
    """Lines of tesseract's TSV output (one row per word) as boxes, top to bottom, left to right."""
    lines: dict[tuple[str, str, str], list[tuple[str, int, int, int, int]]] = {}
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5" or not cols[11].strip():
            continue
        try:
            confidence = float(cols[10])
            left, top, width, height = (int(c) for c in cols[6:10])
        except ValueError:
            continue
        if confidence < 30:
            continue
        lines.setdefault((cols[2], cols[3], cols[4]), []).append((cols[11].strip(), left, top, width, height))
    out = []
    for words in lines.values():
        x0 = min(w[1] for w in words)
        y0 = min(w[2] for w in words)
        x1 = max(w[1] + w[3] for w in words)
        y1 = max(w[2] + w[4] for w in words)
        text = " ".join(_BIDI_MARKS.sub("", w[0]) for w in words).strip()
        if text:
            out.append(TextBox(text, float(x0), float(y0), float(x1 - x0), float(y1 - y0)))
    out.sort(key=lambda b: (round((b.y + b.height / 2) / max(b.height, 1.0) * 2), b.x))
    return out


def recognize_tesseract(png: bytes) -> list[TextBox]:
    """Lines of text in a PNG read by tesseract; empty when it is not installed."""
    global _tesseract_languages
    if not png or shutil.which("tesseract") is None:
        return []
    if _tesseract_languages is None:
        listed = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, timeout=10).stdout
        have = set(listed.split())
        _tesseract_languages = "+".join(lang for lang in _TESSERACT_LANGUAGES if lang in have) or "eng"
    done = subprocess.run(["tesseract", "stdin", "stdout", "-l", _tesseract_languages, "--psm", "11", "tsv"],
                          input=png, capture_output=True, timeout=30)
    return boxes_from_tesseract(done.stdout.decode("utf-8", "replace")) if done.returncode == 0 else []


@dataclass(frozen=True)
class Shape:
    """An icon or control without text, in pixels of the screenshot it was found in."""
    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> tuple[int, int]:
        return int(round(self.x + self.width / 2)), int(round(self.y + self.height / 2))

    def line(self) -> str:
        cx, cy = self.center
        return f"({cx}, {cy}), {int(round(self.width))}x{int(round(self.height))}"


def _overlaps(a: tuple[float, float, float, float], b: tuple[float, float, float, float], gap: float = 0.0) -> bool:
    return (a[0] - gap < b[0] + b[2] and b[0] - gap < a[0] + a[2]
            and a[1] - gap < b[1] + b[3] and b[1] - gap < a[1] + a[3])


def shapes_from_boxes(boxes: list[tuple[float, float, float, float]], text: list[TextBox],
                      scale: float, size: tuple[float, float] | None = None) -> list[Shape]:
    """Outline boxes (screenshot pixels) as controls: merge outlines within 3 points
    of each other, then keep the ones that look like a control and cover no line
    of text. With the screenshot's ``size``, a shape touching its edge is the
    window's rounded corner or border, not a control."""
    merged: list[list[float]] = []
    for box in sorted(boxes, key=lambda b: (b[1], b[0])):
        current = [float(v) for v in box]
        changed = True
        while changed:
            changed = False
            for other in merged:
                if _overlaps(current, other, 3 * scale):
                    x, y = min(current[0], other[0]), min(current[1], other[1])
                    right = max(current[0] + current[2], other[0] + other[2])
                    bottom = max(current[1] + current[3], other[1] + other[3])
                    current = [x, y, right - x, bottom - y]
                    merged.remove(other)
                    changed = True
                    break
        merged.append(current)
    # A one-character "line" is often an icon the recognizer misread as a
    # glyph, so only longer lines rule a shape out.
    words = [(t.x, t.y, t.width, t.height) for t in text if len(t.text.replace(" ", "")) > 1]
    keep = []
    for x, y, w, h in merged:
        short, long = min(w, h), max(w, h)
        if short < 8 * scale or long > 80 * scale or long > 4 * short:
            continue
        if any(_overlaps((x, y, w, h), word) for word in words):
            continue
        edge = 2 * scale
        if size and (x <= edge or y <= edge or x + w >= size[0] - edge or y + h >= size[1] - edge):
            continue
        keep.append(Shape(*(round(v, 1) for v in (x, y, w, h))))
    # Rows are 16-point bands of the centre, so a toolbar's icons of slightly
    # different heights stay one row, read left to right.
    keep.sort(key=lambda s: (int((s.y + s.height / 2) // (16 * scale)), s.x))
    return keep


def render_shapes(shapes: list[Shape]) -> str:
    lines = [s.line() for s in shapes[:MAX_SHAPES]]
    if len(shapes) > MAX_SHAPES:
        lines.append(f"…and {len(shapes) - MAX_SHAPES} more; zoom into a region to see them.")
    return "\n".join(lines)


def detect_shapes(png: bytes, width: float, height: float, text: list[TextBox], scale: float) -> list[Shape]:
    """Icons and controls without text in a PNG ``width`` x ``height`` pixels; empty
    when Vision is missing."""
    if not png or not available():
        return []
    import Foundation
    import Quartz
    import Vision

    found: list[Shape] = []
    with _lock:
        data = Foundation.NSData.dataWithBytes_length_(png, len(png))
        # Dark icons on a light window and light icons on a dark one are
        # outlined by separate passes; each is filtered on its own, then a
        # shape both passes found is kept once.
        for dark_on_light in (True, False):
            handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, {})
            request = Vision.VNDetectContoursRequest.alloc().init()
            request.setContrastAdjustment_(2.0)
            request.setDetectsDarkOnLight_(dark_on_light)
            request.setMaximumImageDimension_(1024)
            ok, _error = handler.performRequests_error_([request], None)
            if not ok or not request.results():
                continue
            boxes = []
            for contour in request.results()[0].topLevelContours() or []:
                (nx, ny), (nw, nh) = Quartz.CGPathGetBoundingBox(contour.normalizedPath())
                boxes.append((nx * width, (1.0 - ny - nh) * height, nw * width, nh * height))
            for shape in shapes_from_boxes(boxes, text, scale, (width, height)):
                box = (shape.x, shape.y, shape.width, shape.height)
                if not any(_overlaps(box, (o.x, o.y, o.width, o.height)) for o in found):
                    found.append(shape)
    found.sort(key=lambda s: (int((s.y + s.height / 2) // (16 * scale)), s.x))
    return found


__all__ = ["MAX_LINES", "MAX_SHAPES", "Shape", "TextBox", "available", "boxes_from_normalized", "detect_shapes",
           "matching", "pick_languages", "recognize", "render", "render_shapes", "shapes_from_boxes"]
