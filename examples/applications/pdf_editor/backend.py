"""PDF editor operations.

The page renders and edits PDFs itself with PDF.js (bundled under
ui/vendor): text selection, highlights, ink, text boxes, stamps and form
values live in PDF.js until it serialises them with ``saveDocument()``.
Everything else is written here with pypdf, which the OpenProgram runtime
already ships:

* annotations PDF.js cannot create (underline, strikethrough, notes,
  rectangles, ellipses, lines, arrows), with appearance streams so every
  viewer draws them;
* page organisation (rotate, delete, reorder, duplicate, blank pages, merge,
  extract, split);
* form values;
* text access for Agents and Programs (list, info, read, find, summarize).

The editor keeps unsaved work in a *draft*: an immutable staged copy of the
PDF outside the project. Each edit makes a new draft, so undo is returning to
an earlier one. ``save`` writes a draft into the project through
``context.write_file`` (capability ``files.project.write``). Agent operations
work on project files directly.
"""
from __future__ import annotations

import base64
from contextlib import suppress
import math
import os
import re
import tempfile
import time
import uuid
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path

CHUNK_LIMIT = 600_000          # raw bytes per chunk; base64 stays well under the 1 MiB event limit
TEXT_LIMIT = 400_000           # characters returned by read_text
SUMMARY_INPUT_LIMIT = 120_000  # characters sent to the model
DRAFT_LIMIT = 200 * 1024 * 1024
DRAFT_TTL = 14 * 24 * 3600
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", ".next", "dist", "build"}
DRAFT_ID = re.compile(r"^d_[0-9a-f]{32}$")
UPLOAD_ID = re.compile(r"^u_[0-9a-f]{32}$")

COLORS = {
    "yellow": "#ffd43b", "green": "#69db7c", "blue": "#74c0fc", "pink": "#f783ac", "red": "#e03131",
    "orange": "#ff922b", "purple": "#9775fa", "black": "#1f1f24", "gray": "#868e96", "white": "#ffffff",
}
DEFAULT_COLORS = {"highlight": "#ffd43b", "underline": "#1971c2", "strikethrough": "#e03131", "squiggly": "#e8590c",
                  "note": "#ffd43b", "rectangle": "#e03131", "ellipse": "#e03131", "line": "#e03131",
                  "arrow": "#e03131", "free_text": "#1f1f24"}
MARKUP = {"highlight": "/Highlight", "underline": "/Underline", "strikethrough": "/StrikeOut", "squiggly": "/Squiggly"}
KINDS = {"/Highlight": "highlight", "/Underline": "underline", "/StrikeOut": "strikethrough", "/Squiggly": "squiggly",
         "/Text": "note", "/FreeText": "free_text", "/Ink": "ink", "/Square": "rectangle", "/Circle": "ellipse",
         "/Line": "line", "/Polygon": "polygon", "/PolyLine": "polyline", "/Stamp": "stamp", "/Caret": "caret",
         "/FileAttachment": "attachment", "/Sound": "sound"}
SKIP_ANNOTS = {"/Popup", "/Widget", "/Link"}
PAPER = {"a4": (595.28, 841.89), "letter": (612.0, 792.0), "legal": (612.0, 1008.0), "a5": (419.53, 595.28)}


# ---------------------------------------------------------------- project files

def _root(context) -> Path:
    path = getattr(context, "project_path", "") or ""
    if not path:
        raise ValueError("No project is bound to this application; open it from a project")
    return Path(path).resolve()


def _resolve(context, relative: str) -> Path:
    """An existing project-relative PDF that stays inside the project."""
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError("path is required")
    root = _root(context)
    if os.path.isabs(relative):
        raise ValueError(f"{relative} is outside the project; use a project-relative path")
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"{relative} is outside the project")
    if not target.is_file():
        raise FileNotFoundError(f"{relative} does not exist")
    if target.suffix.lower() != ".pdf":
        raise ValueError(f"{relative} is not a PDF")
    return target


def _output(context, relative: str, *, overwrite: bool, existing: set[str] = frozenset()) -> str:
    """A project-relative .pdf path to write; refuses to replace a file unless overwrite."""
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError("output path is required")
    relative = relative.strip().replace("\\", "/")
    if os.path.isabs(relative):
        raise ValueError(f"{relative} is outside the project; use a project-relative path")
    if not relative.lower().endswith(".pdf"):
        raise ValueError(f"{relative} must end in .pdf")
    root = _root(context)
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or target == root:
        raise ValueError(f"{relative} is outside the project")
    if ".git" in [part.lower() for part in target.relative_to(root).parts]:
        raise ValueError("cannot write into .git")
    rel = target.relative_to(root).as_posix()
    if target.exists() and not overwrite and rel not in existing:
        raise FileExistsError(f"{rel} already exists; pass overwrite to replace it")
    return rel


def _read_bytes(context, relative: str) -> bytes:
    reader = getattr(context, "read_bytes", None)
    if callable(reader):
        return reader(relative)
    return _resolve(context, relative).read_bytes()


def _write(context, relative: str, data: bytes) -> dict:
    writer = getattr(context, "write_file", None)
    if not callable(writer):
        raise PermissionError("Writing PDFs needs the files.project.write capability")
    result = writer(relative, data)
    target = _root(context) / relative
    info = {"path": relative, "bytes": len(data)}
    if isinstance(result, dict):
        info.update({k: v for k, v in result.items() if k in ("path", "bytes")})
    if target.exists():
        info["revision"] = _revision(target)
        info["pages"] = _page_count(data)
    return info


def _revision(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_size}-{stat.st_mtime_ns}"


def _rel(context, path: Path) -> str:
    return path.relative_to(_root(context)).as_posix()


# ---------------------------------------------------------------- drafts (staged copies)

def _stage(context) -> Path:
    key = str(getattr(context, "instance_id", "") or "") + "|" + str(getattr(context, "project_path", "") or "")
    folder = Path(tempfile.gettempdir()) / "openprogram-pdf-editor" / sha256(key.encode()).hexdigest()[:24]
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _sweep(folder: Path) -> None:
    cutoff = time.time() - DRAFT_TTL
    for item in folder.iterdir():
        try:
            if item.stat().st_mtime < cutoff:
                item.unlink()
        except OSError:
            pass


def _draft_path(context, draft: str) -> Path:
    if not isinstance(draft, str) or not DRAFT_ID.match(draft):
        raise ValueError("invalid draft id")
    path = _stage(context) / f"{draft}.pdf"
    if not path.is_file():
        raise FileNotFoundError("This draft expired; reopen the file")
    os.utime(path)
    return path


def _new_draft(context, data: bytes) -> str:
    folder = _stage(context)
    _sweep(folder)
    draft = "d_" + uuid.uuid4().hex
    temporary = folder / f".{draft}.tmp"
    temporary.write_bytes(data)
    os.replace(temporary, folder / f"{draft}.pdf")
    return draft


def _source(value, context) -> tuple[bytes, dict]:
    """Bytes of a project PDF (``path``) or of a staged draft (``draft``)."""
    if value.get("draft"):
        return _draft_path(context, value["draft"]).read_bytes(), {"draft": value["draft"]}
    if value.get("path"):
        path = _resolve(context, value["path"])
        return _read_bytes(context, value["path"]), {"path": _rel(context, path)}
    raise ValueError("Provide path")


# ---------------------------------------------------------------- pypdf helpers

def _reader(data: bytes):
    from pypdf import PdfReader
    reader = PdfReader(BytesIO(data))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:  # pragma: no cover - depends on crypto backend
            raise ValueError("This PDF is password protected") from exc
    return reader


def _open_pdf(context, relative: str):
    return _reader(_read_bytes(context, relative))


def _page_count(data: bytes) -> int:
    try:
        return len(_reader(data).pages)
    except Exception:
        return 0


def _writer(reader):
    from pypdf import PdfWriter
    len(reader.pages)  # flatten inherited page attributes before cloning
    return PdfWriter(clone_from=reader)


def _to_bytes(writer) -> bytes:
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def _compact(data: bytes) -> bytes:
    """Rewrite from the trailer so objects no page reaches (removed pages) are dropped."""
    return _to_bytes(_writer(_reader(data)))


def _page_range(total: int, start, end) -> range:
    first = 1 if start in (None, "") else int(start)
    last = total if end in (None, "") else int(end)
    if total == 0:
        return range(0)
    first = max(1, min(first, total))
    last = max(first, min(last, total))
    return range(first, last + 1)


def _pages_arg(value, total: int, name: str = "pages", *, required: bool = True) -> list[int]:
    """1-based page numbers from a list of ints and "a-b" range strings, validated."""
    raw = value.get(name)
    if raw in (None, "", []):
        if required:
            raise ValueError(f"{name} is required")
        return []
    if isinstance(raw, (int, str)):
        raw = [raw]
    pages: list[int] = []
    for item in raw:
        if isinstance(item, bool):
            raise ValueError(f"{name} must be page numbers")
        if isinstance(item, int):
            pages.append(item)
            continue
        text = str(item).strip()
        match = re.fullmatch(r"(\d+)\s*[-–]\s*(\d+|end)", text)
        if match:
            first, last = int(match.group(1)), total if match.group(2) == "end" else int(match.group(2))
            if last < first:
                raise ValueError(f"page range {text} is reversed")
            pages.extend(range(first, last + 1))
        elif text.isdigit():
            pages.append(int(text))
        else:
            raise ValueError(f"{text!r} is not a page number or range")
    for page in pages:
        if not 1 <= page <= total:
            raise ValueError(f"page {page} is out of range (the PDF has {total} pages)")
    return pages


def _page_text(page) -> str:
    try:
        text = page.extract_text() or ""
    except Exception:
        text = ""
    text = re.sub(r"-\n(?=[a-z])", "", text)
    return re.sub(r"[ \t]+\n", "\n", text).strip()


def _outline(reader) -> list[dict]:
    items: list[dict] = []

    def walk(entries, level):
        for entry in entries:
            if isinstance(entry, list):
                walk(entry, level + 1)
                continue
            try:
                page = reader.get_destination_page_number(entry) + 1
            except Exception:
                page = None
            title = str(getattr(entry, "title", "") or "").strip()
            if title:
                items.append({"title": title, "page": page, "level": level})
            if len(items) >= 2000:
                return

    try:
        walk(reader.outline, 0)
    except Exception:
        return []
    return items


def _hex(color, kind: str = "highlight") -> str:
    if color in (None, ""):
        return DEFAULT_COLORS.get(kind, "#ffd43b")
    text = str(color).strip().lower()
    text = COLORS.get(text, text)
    if re.fullmatch(r"#[0-9a-f]{3}", text):
        text = "#" + "".join(c * 2 for c in text[1:])
    if not re.fullmatch(r"#[0-9a-f]{6}", text):
        raise ValueError(f"color {color!r} is not a #rrggbb value or one of {', '.join(COLORS)}")
    return text


def _rgb(color: str) -> list[float]:
    return [round(int(color[i:i + 2], 16) / 255, 4) for i in (1, 3, 5)]


def _color_of(value) -> str | None:
    try:
        parts = [float(v) for v in value]
    except Exception:
        return None
    if len(parts) == 1:
        parts = parts * 3
    elif len(parts) == 4:
        c, m, y, k = parts
        parts = [(1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k)]
    elif len(parts) != 3:
        return None
    return "#" + "".join(f"{max(0, min(255, round(p * 255))):02x}" for p in parts)


def _pdf_date(moment: datetime | None = None) -> str:
    moment = moment or datetime.now(timezone.utc)
    return "D:" + moment.strftime("%Y%m%d%H%M%S") + "Z"


def _iso_date(value) -> str:
    match = re.match(r"D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?", str(value or ""))
    if not match:
        return ""
    y, mo, d, h, mi, s = (int(g) if g else default for g, default in zip(match.groups(), (0, 1, 1, 0, 0, 0)))
    try:
        return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).isoformat()
    except ValueError:
        return ""


def _text(value) -> str:
    if value is None:
        return ""
    try:
        return str(value.get_object() if hasattr(value, "get_object") else value)
    except Exception:
        return ""


# ---------------------------------------------------------------- text geometry

def _mul(m, n):
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return [a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D, e * A + f * C + E, e * B + f * D + F]


def _apply(m, x, y):
    return m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]


def _chars(page) -> list[dict]:
    """Characters of a page with their quads in user space (estimated from font widths).

    Virtual separators (between text runs) have ``quad: None``.
    """
    from pypdf._font import Font
    fonts: dict[int, tuple] = {}
    runs: list[tuple] = []

    def visit(text, cm, tm, font, size):
        if text and text.strip("\n"):
            runs.append((text.replace("\n", " "), list(cm), list(tm), font, float(size or 0)))

    try:
        page.extract_text(visitor_text=visit)
    except Exception:
        return []
    chars: list[dict] = []
    last = None
    for text, cm, tm, font, size in runs:
        if not size:
            continue
        if font is not None and id(font) not in fonts:
            try:
                parsed = Font.from_font_resource(font)
                reverse = {v: k for k, v in (parsed.character_map or {}).items()}
                fonts[id(font)] = (parsed, reverse)
            except Exception:
                fonts[id(font)] = (None, {})
        parsed, reverse = fonts.get(id(font), (None, {}))
        matrix = _mul(tm, cm)
        scale = math.hypot(matrix[2], matrix[3]) or 1
        origin = _apply(matrix, 0, 0)
        if last is not None:
            gap_line = abs(origin[1] - last["y"]) > 0.5 * last["h"] or origin[0] < last["x1"] - last["h"]
            if gap_line or origin[0] > last["x1"] + 0.15 * last["h"]:
                if chars and chars[-1]["c"] != " ":
                    chars.append({"c": " ", "quad": None})
        x = 0.0
        for ch in text:
            if parsed is not None:
                widths = parsed.character_widths
                width = widths.get(reverse.get(ch, ch), widths.get(ch, widths.get("default", 500)))
            else:
                width = 500
            advance = (width or 0) / 1000 * size
            if advance <= 0:
                advance = 0.5 * size
            down, up = -0.22 * size, 0.86 * size
            quad = [*_apply(matrix, x, up), *_apply(matrix, x + advance, up),
                    *_apply(matrix, x, down), *_apply(matrix, x + advance, down)]
            chars.append({"c": ch, "quad": quad, "line": (round(origin[1], 1), round(matrix[1], 3))})
            x += advance
        end = _apply(matrix, x, 0)
        last = {"x1": end[0], "y": end[1], "h": size * scale}
    return chars


def _quads_for(chars: list[dict], start: int, end: int) -> list[list[float]]:
    """One quad per line covering chars[start:end]."""
    quads, group, key = [], [], None
    for item in chars[start:end]:
        if item["quad"] is None:
            continue
        if key is not None and item["line"] != key and group:
            quads.append(_union(group))
            group = []
        key = item["line"]
        group.append(item["quad"])
    if group:
        quads.append(_union(group))
    return quads


def _union(quads: list[list[float]]) -> list[float]:
    first, last = quads[0], quads[-1]
    if abs(first[1] - first[5]) > 0 and abs(first[0] - first[4]) < 0.01:  # upright text
        xs = [q[i] for q in quads for i in (0, 2, 4, 6)]
        ys = [q[i] for q in quads for i in (1, 3, 5, 7)]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        return [x0, y1, x1, y1, x0, y0, x1, y0]
    return [first[0], first[1], last[2], last[3], first[4], first[5], last[6], last[7]]


def _find_phrase(chars: list[dict], phrase: str, case_sensitive: bool = False) -> list[tuple[int, int]]:
    text = "".join(item["c"] for item in chars)
    pattern = re.compile(r"\s+".join(re.escape(part) for part in phrase.split()), 0 if case_sensitive else re.IGNORECASE)
    return [m.span() for m in pattern.finditer(text)]


def _covered_text(chars: list[dict], quads: list[list[float]]) -> str:
    boxes = []
    for q in quads:
        xs, ys = q[0::2], q[1::2]
        boxes.append((min(xs), min(ys), max(xs), max(ys)))
    out = []
    for item in chars:
        if item["quad"] is None:
            out.append(" ")
            continue
        q = item["quad"]
        cx, cy = sum(q[0::2]) / 4, sum(q[1::2]) / 4
        if any(x0 - 0.5 <= cx <= x1 + 0.5 and y0 - 0.5 <= cy <= y1 + 0.5 for x0, y0, x1, y1 in boxes):
            out.append(item["c"])
        elif out and out[-1] != " ":
            out.append(" ")
    return re.sub(r"\s+", " ", "".join(out)).strip()


# ---------------------------------------------------------------- annotation writing

def _num(value) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text if text not in ("-0", "") else "0"


def _pdf_string(text: str) -> str:
    safe = text.encode("cp1252", errors="replace").decode("cp1252")
    return "(" + safe.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")"


def _appearance(writer, rect, content: str, resources=None):
    from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject
    stream = DecodedStreamObject()
    stream.set_data(content.encode("latin-1", errors="replace"))
    stream.update({
        NameObject("/Type"): NameObject("/XObject"),
        NameObject("/Subtype"): NameObject("/Form"),
        NameObject("/BBox"): ArrayObject([FloatObject(v) for v in rect]),
        NameObject("/Resources"): resources if resources is not None else DictionaryObject(),
    })
    return writer._add_object(stream)


def _gs(writer, opacity: float, multiply: bool = False):
    from pypdf.generic import DictionaryObject, FloatObject, NameObject
    state = DictionaryObject({NameObject("/Type"): NameObject("/ExtGState"),
                              NameObject("/CA"): FloatObject(opacity), NameObject("/ca"): FloatObject(opacity)})
    if multiply:
        state[NameObject("/BM")] = NameObject("/Multiply")
    return DictionaryObject({NameObject("/ExtGState"): DictionaryObject({NameObject("/GS0"): writer._add_object(state)})})


def _bounds(points: list[tuple[float, float]], pad: float = 0) -> list[float]:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return [min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad]


def _floats(value, name: str, count: int | None = None) -> list[float]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be a list of numbers")
    try:
        numbers = [float(v) for v in value]
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a list of numbers") from None
    if any(not math.isfinite(v) for v in numbers):
        raise ValueError(f"{name} must be finite numbers")
    if count is not None and len(numbers) != count:
        raise ValueError(f"{name} needs {count} numbers")
    return numbers


def _ellipse_path(x0, y0, x1, y1) -> str:
    k = 0.5522847498
    cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    n = _num
    return (f"{n(cx + rx)} {n(cy)} m "
            f"{n(cx + rx)} {n(cy + k * ry)} {n(cx + k * rx)} {n(cy + ry)} {n(cx)} {n(cy + ry)} c "
            f"{n(cx - k * rx)} {n(cy + ry)} {n(cx - rx)} {n(cy + k * ry)} {n(cx - rx)} {n(cy)} c "
            f"{n(cx - rx)} {n(cy - k * ry)} {n(cx - k * rx)} {n(cy - ry)} {n(cx)} {n(cy - ry)} c "
            f"{n(cx + k * rx)} {n(cy - ry)} {n(cx + rx)} {n(cy - k * ry)} {n(cx + rx)} {n(cy)} c h")


def _wrap(text: str, size: float, width: float) -> list[str]:
    from pypdf._font import Font
    helv = Font.from_core_font_name("/Helvetica")
    lines = []
    for paragraph in text.splitlines() or [""]:
        line = ""
        for word in paragraph.split(" "):
            candidate = (line + " " + word) if line else word
            if line and helv.get_text_width(candidate) / 1000 * size > width:
                lines.append(line)
                line = word
            else:
                line = candidate
        lines.append(line)
    return lines


def _build_annotation(writer, page, item: dict, *, author_default: str = ""):
    """A pypdf annotation dictionary (with appearance) for one editor item, in user space."""
    from pypdf.generic import (ArrayObject, BooleanObject, DictionaryObject, FloatObject, NameObject,
                               NumberObject, TextStringObject)
    kind = str(item.get("type", "")).lower().replace("-", "_")
    if kind == "strikeout":
        kind = "strikethrough"
    color = _hex(item.get("color"), kind)
    rgb = _rgb(color)
    opacity = float(item.get("opacity", 1 if kind != "highlight" else 1))
    opacity = max(0.05, min(1.0, opacity))
    width = max(0.25, min(48.0, float(item.get("width", 2 if kind not in MARKUP else 1))))
    contents = str(item.get("contents") or "")
    author = str(item.get("author") or author_default or "")
    n = _num
    R, G, B = (n(v) for v in rgb)
    annot = DictionaryObject()
    annot[NameObject("/Type")] = NameObject("/Annot")
    annot[NameObject("/F")] = NumberObject(4)
    annot[NameObject("/C")] = ArrayObject([FloatObject(v) for v in rgb])
    given = str(item.get("id") or "")
    if not re.fullmatch(r"op-[0-9a-f]{8,32}", given):
        given = "op-" + uuid.uuid4().hex
    annot[NameObject("/NM")] = TextStringObject(given)
    annot[NameObject("/M")] = TextStringObject(_pdf_date())
    annot[NameObject("/CreationDate")] = TextStringObject(_pdf_date())
    if author:
        annot[NameObject("/T")] = TextStringObject(author)
    if contents:
        annot[NameObject("/Contents")] = TextStringObject(contents)
    if opacity < 1:
        annot[NameObject("/CA")] = FloatObject(opacity)

    if kind in MARKUP:
        quads = item.get("quads")
        if not isinstance(quads, list) or not quads:
            raise ValueError(f"{kind} needs quads (or a text phrase)")
        quads = [_floats(q, "quad", 8) for q in quads]
        points = [(q[i], q[i + 1]) for q in quads for i in (0, 2, 4, 6)]
        rect = _bounds(points, 1)
        annot[NameObject("/Subtype")] = NameObject(MARKUP[kind])
        annot[NameObject("/Rect")] = ArrayObject([FloatObject(v) for v in rect])
        annot[NameObject("/QuadPoints")] = ArrayObject([FloatObject(v) for q in quads for v in q])
        ops = []
        for q in quads:
            tl, tr, bl, br = (q[0], q[1]), (q[2], q[3]), (q[4], q[5]), (q[6], q[7])
            height = math.hypot(tl[0] - bl[0], tl[1] - bl[1]) or 1
            if kind == "highlight":
                ops.append(f"{n(tl[0])} {n(tl[1])} m {n(tr[0])} {n(tr[1])} l {n(br[0])} {n(br[1])} l {n(bl[0])} {n(bl[1])} l h f")
            else:
                thickness = max(0.6, height / 14)
                if kind == "strikethrough":
                    t = 0.45
                else:
                    t = 0.12 if kind == "underline" else 0.1
                a = (bl[0] + (tl[0] - bl[0]) * t, bl[1] + (tl[1] - bl[1]) * t)
                b = (br[0] + (tr[0] - br[0]) * t, br[1] + (tr[1] - br[1]) * t)
                if kind == "squiggly":
                    steps = max(2, int(math.hypot(b[0] - a[0], b[1] - a[1]) / (height / 4)))
                    path = [f"{n(a[0])} {n(a[1])} m"]
                    for i in range(1, steps + 1):
                        f = i / steps
                        dy = (height / 16) * (1 if i % 2 else -1)
                        path.append(f"{n(a[0] + (b[0] - a[0]) * f)} {n(a[1] + (b[1] - a[1]) * f + dy)} l")
                    ops.append(f"{n(thickness)} w " + " ".join(path) + " S")
                else:
                    ops.append(f"{n(thickness)} w {n(a[0])} {n(a[1])} m {n(b[0])} {n(b[1])} l S")
        paint = (f"/GS0 gs {R} {G} {B} rg " if kind == "highlight" else f"/GS0 gs {R} {G} {B} RG ") + " ".join(ops)
        resources = _gs(writer, opacity if kind != "highlight" else min(opacity, 1.0), multiply=kind == "highlight")
        ap = _appearance(writer, rect, paint, resources)
    elif kind == "note":
        at = item.get("at") or (item.get("rect") or [None, None])[:2]
        x, y = _floats(at, "at", 2)
        rect = [x, y - 22, x + 22, y]
        annot[NameObject("/Subtype")] = NameObject("/Text")
        annot[NameObject("/Rect")] = ArrayObject([FloatObject(v) for v in rect])
        annot[NameObject("/Name")] = NameObject("/Comment")
        annot[NameObject("/Open")] = BooleanObject(item.get("open") is True)
        x0, y0 = rect[0], rect[1]
        dark = " ".join(n(v * 0.55) for v in rgb)
        content = (f"{R} {G} {B} rg {dark} RG 1 w "
                   f"{n(x0 + 2)} {n(y0 + 5)} m {n(x0 + 2)} {n(y0 + 20)} l {n(x0 + 20)} {n(y0 + 20)} l {n(x0 + 20)} {n(y0 + 5)} l "
                   f"{n(x0 + 9)} {n(y0 + 5)} l {n(x0 + 5)} {n(y0 + 1.5)} l {n(x0 + 5.5)} {n(y0 + 5)} l h B "
                   f"0.9 w {n(x0 + 5.5)} {n(y0 + 15.5)} m {n(x0 + 16.5)} {n(y0 + 15.5)} l S "
                   f"{n(x0 + 5.5)} {n(y0 + 12)} m {n(x0 + 16.5)} {n(y0 + 12)} l S "
                   f"{n(x0 + 5.5)} {n(y0 + 8.5)} m {n(x0 + 12.5)} {n(y0 + 8.5)} l S")
        ap = _appearance(writer, rect, content)
    elif kind in ("rectangle", "ellipse"):
        x0, y0, x1, y1 = _floats(item.get("rect"), "rect", 4)
        x0, x1, y0, y1 = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
        if x1 - x0 < 1 or y1 - y0 < 1:
            raise ValueError(f"{kind} is too small")
        rect = [x0, y0, x1, y1]
        annot[NameObject("/Subtype")] = NameObject("/Square" if kind == "rectangle" else "/Circle")
        annot[NameObject("/Rect")] = ArrayObject([FloatObject(v) for v in rect])
        annot[NameObject("/BS")] = DictionaryObject({NameObject("/W"): FloatObject(width), NameObject("/S"): NameObject("/S")})
        fill = item.get("fill")
        h = width / 2
        shape = (f"{n(x0 + h)} {n(y0 + h)} {n(x1 - x0 - width)} {n(y1 - y0 - width)} re" if kind == "rectangle"
                 else _ellipse_path(x0 + h, y0 + h, x1 - h, y1 - h))
        paint = "S"
        content = f"/GS0 gs {n(width)} w {R} {G} {B} RG "
        if fill:
            fill_rgb = _rgb(_hex(fill, kind))
            annot[NameObject("/IC")] = ArrayObject([FloatObject(v) for v in fill_rgb])
            content += " ".join(n(v) for v in fill_rgb) + " rg "
            paint = "B"
        ap = _appearance(writer, rect, content + shape + " " + paint, _gs(writer, opacity))
    elif kind in ("line", "arrow"):
        pts = item.get("points")
        if not isinstance(pts, list) or len(pts) != 2:
            raise ValueError(f"{kind} needs two points")
        (x1, y1), (x2, y2) = (_floats(p, "point", 2) for p in pts)
        if math.hypot(x2 - x1, y2 - y1) < 2:
            raise ValueError(f"{kind} is too short")
        head = width * 3 + 5
        rect = _bounds([(x1, y1), (x2, y2)], head + width)
        annot[NameObject("/Subtype")] = NameObject("/Line")
        annot[NameObject("/Rect")] = ArrayObject([FloatObject(v) for v in rect])
        annot[NameObject("/L")] = ArrayObject([FloatObject(v) for v in (x1, y1, x2, y2)])
        annot[NameObject("/BS")] = DictionaryObject({NameObject("/W"): FloatObject(width), NameObject("/S"): NameObject("/S")})
        if kind == "arrow":
            annot[NameObject("/LE")] = ArrayObject([NameObject("/None"), NameObject("/OpenArrow")])
            annot[NameObject("/IT")] = NameObject("/LineArrow")
        content = f"/GS0 gs {n(width)} w 1 J 1 j {R} {G} {B} RG {n(x1)} {n(y1)} m {n(x2)} {n(y2)} l S"
        if kind == "arrow":
            angle = math.atan2(y2 - y1, x2 - x1)
            for side in (-1, 1):
                a = angle + math.pi + side * math.radians(28)
                content += f" {n(x2 + head * math.cos(a))} {n(y2 + head * math.sin(a))} m {n(x2)} {n(y2)} l S"
        ap = _appearance(writer, rect, content, _gs(writer, opacity))
    elif kind == "free_text":
        x0, y0, x1, y1 = _floats(item.get("rect"), "rect", 4)
        x0, x1, y0, y1 = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
        if not contents:
            raise ValueError("free_text needs contents")
        size = max(4.0, min(144.0, float(item.get("font_size", 12))))
        lines = _wrap(contents, size, max(20.0, x1 - x0 - 4))
        needed = len(lines) * size * 1.2 + 4
        if y1 - y0 < needed:
            y0 = y1 - needed
        rect = [x0, y0, x1, y1]
        annot[NameObject("/Subtype")] = NameObject("/FreeText")
        annot[NameObject("/Rect")] = ArrayObject([FloatObject(v) for v in rect])
        annot[NameObject("/DA")] = TextStringObject(f"/Helv {n(size)} Tf {R} {G} {B} rg")
        annot[NameObject("/BS")] = DictionaryObject({NameObject("/W"): FloatObject(0)})
        del annot["/C"]
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica"),
                                 NameObject("/Encoding"): NameObject("/WinAnsiEncoding")})
        resources = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/Helv"): writer._add_object(font)})})
        body = [f"BT /Helv {n(size)} Tf {R} {G} {B} rg {n(size * 1.2)} TL {n(x0 + 2)} {n(y1 - size - 1)} Td"]
        for index, line in enumerate(lines):
            body.append(("" if index == 0 else "T* ") + _pdf_string(line) + " Tj")
        body.append("ET")
        ap = _appearance(writer, rect, " ".join(body), resources)
    else:
        raise ValueError(f"unsupported annotation type {item.get('type')!r}")
    annot[NameObject("/AP")] = DictionaryObject({NameObject("/N"): ap})
    annot[NameObject("/P")] = page.indirect_reference
    return annot


def _attach(writer, page, annot, *, popup: bool):
    from pypdf.generic import ArrayObject, BooleanObject, DictionaryObject, FloatObject, NameObject, NumberObject
    ref = writer._add_object(annot)
    if "/Annots" not in page:
        page[NameObject("/Annots")] = ArrayObject()
    annots = page["/Annots"]
    if hasattr(annots, "get_object"):
        annots = annots.get_object()
    annots.append(ref)
    if popup and annot.get("/Contents"):
        x0, y0, x1, y1 = [float(v) for v in annot["/Rect"]]
        box = page.mediabox
        px0 = min(float(box.right) - 200, x1 + 6)
        pop = DictionaryObject({
            NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Popup"),
            NameObject("/Rect"): ArrayObject([FloatObject(v) for v in (px0, max(float(box.bottom), y1 - 120), px0 + 200, y1)]),
            NameObject("/Parent"): ref, NameObject("/Open"): BooleanObject(False), NameObject("/F"): NumberObject(28),
        })
        pop_ref = writer._add_object(pop)
        annot[NameObject("/Popup")] = pop_ref
        annots.append(pop_ref)
    return ref


def _ensure_names(writer, author: str = "") -> None:
    """Give every annotation a stable /NM, replacing PDF.js's per-session editor ids.

    Annotations PDF.js just created also get ``author`` (/T) and a modification date.
    """
    from pypdf.generic import TextStringObject, NameObject
    for page in writer.pages:
        for ref in page.get("/Annots") or []:
            try:
                annot = ref.get_object()
            except Exception:
                continue
            name = _text(annot.get("/NM"))
            if annot.get("/Subtype") in ("/Widget", "/Link", "/Popup"):
                continue
            if not name or name.startswith("pdfjs_internal_editor_"):
                # Unnamed annotations are the ones PDF.js just wrote.
                annot[NameObject("/NM")] = TextStringObject("op-" + uuid.uuid4().hex)
                if author and "/T" not in annot:
                    annot[NameObject("/T")] = TextStringObject(author)
                if "/M" not in annot:
                    annot[NameObject("/M")] = TextStringObject(_pdf_date())


def _annotation_id(annot, ref, page_number: int, index: int) -> str:
    name = _text(annot.get("/NM"))
    if name:
        return name
    if getattr(ref, "idnum", None) is not None:
        return f"{ref.idnum}R"
    return f"p{page_number}i{index}"


def _describe(annot, ref, page_number: int, index: int, chars_for) -> dict:
    subtype = str(annot.get("/Subtype", ""))
    kind = KINDS.get(subtype, subtype.lstrip("/").lower())
    if kind == "line" and annot.get("/LE"):
        ends = [str(e) for e in annot["/LE"]]
        if any(e not in ("/None",) for e in ends):
            kind = "arrow"
    try:
        rect = [round(float(v), 2) for v in annot.get("/Rect", [0, 0, 0, 0])]
    except Exception:
        rect = [0, 0, 0, 0]
    entry = {
        "id": _annotation_id(annot, ref, page_number, index),
        "page": page_number,
        "type": kind,
        "rect": rect,
        "color": _color_of(annot.get("/C") or annot.get("/IC") or []) or "",
        "author": _text(annot.get("/T")),
        "contents": _text(annot.get("/Contents")),
        "modified": _iso_date(annot.get("/M")),
        "created": _iso_date(annot.get("/CreationDate")),
    }
    if annot.get("/IRT"):
        entry["reply_to"] = True
    if subtype in ("/Highlight", "/Underline", "/StrikeOut", "/Squiggly") and annot.get("/QuadPoints"):
        values = [float(v) for v in annot["/QuadPoints"]]
        quads = [values[i:i + 8] for i in range(0, len(values) - 7, 8)]
        try:
            entry["text"] = _covered_text(chars_for(), quads)[:2000]
        except Exception:
            entry["text"] = ""
    if subtype == "/FreeText":
        if not entry["contents"]:
            entry["contents"] = _text(annot.get("/RC"))
        # A text box's colour is its text colour, in /DA.
        if match := re.search(r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+rg", _text(annot.get("/DA"))):
            entry["color"] = _color_of(match.groups()) or entry["color"]
    if subtype in ("/Ink", "/Square", "/Circle", "/Line", "/Polygon", "/PolyLine"):
        entry["width"] = round(_width_of(annot), 2)
    return entry


def _list_annotations(reader, pages=None, types=None) -> list[dict]:
    found = []
    for number, page in enumerate(reader.pages, start=1):
        if pages and number not in pages:
            continue
        annots = page.get("/Annots") or []
        try:
            annots = annots.get_object()
        except AttributeError:
            pass
        cache: dict[str, list] = {}

        def chars_for(page=page, cache=cache):
            if "c" not in cache:
                cache["c"] = _chars(page)
            return cache["c"]

        for index, ref in enumerate(annots):
            try:
                annot = ref.get_object()
            except Exception:
                continue
            if annot.get("/Subtype") in SKIP_ANNOTS:
                continue
            entry = _describe(annot, ref, number, index, chars_for)
            if types and entry["type"] not in types:
                continue
            found.append(entry)
    return found


def _add_annotations(data: bytes, items: list, *, author: str = "") -> tuple[bytes, list[str]]:
    if not isinstance(items, list):
        raise ValueError("annotations must be a list")
    reader = _reader(data)
    writer = _writer(reader)
    ids = []
    total = len(writer.pages)
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each annotation must be an object")
        page_number = item.get("page")
        if not isinstance(page_number, int) or not 1 <= page_number <= total:
            raise ValueError(f"annotation page {page_number!r} is out of range (the PDF has {total} pages)")
        page = writer.pages[page_number - 1]
        annot = _build_annotation(writer, page, item, author_default=author)
        _attach(writer, page, annot, popup=annot.get("/Subtype") == "/Text")
        ids.append(_text(annot["/NM"]))
    _ensure_names(writer)
    return _to_bytes(writer), ids


def _remove_annotations(data: bytes, ids: list) -> tuple[bytes, list[str]]:
    from pypdf.generic import ArrayObject, NameObject
    if not isinstance(ids, list) or not all(isinstance(i, str) and i for i in ids):
        raise ValueError("ids must be a list of annotation ids")
    wanted = set(ids)
    reader = _reader(data)
    writer = _writer(reader)
    # Ids are resolved against the source (object numbers change when cloning).
    source_ids = {}
    for number, page in enumerate(reader.pages, start=1):
        for index, ref in enumerate(page.get("/Annots") or []):
            try:
                annot = ref.get_object()
            except Exception:
                continue
            source_ids[(number, index)] = _annotation_id(annot, ref, number, index)
    removed = []
    for number, page in enumerate(writer.pages, start=1):
        annots = page.get("/Annots")
        if not annots:
            continue
        annots = annots.get_object() if hasattr(annots, "get_object") else annots
        keep, dropped_refs = [], set()
        for index, ref in enumerate(annots):
            identity = source_ids.get((number, index))
            if identity in wanted:
                removed.append(identity)
                dropped_refs.add(getattr(ref, "idnum", None))
            else:
                keep.append(ref)
        if dropped_refs:
            final = []
            for ref in keep:
                try:
                    annot = ref.get_object()
                except Exception:
                    continue
                parent = annot.get("/Parent") if annot.get("/Subtype") == "/Popup" else annot.get("/IRT")
                if parent is not None and getattr(parent, "idnum", None) in dropped_refs:
                    continue
                final.append(ref)
            page[NameObject("/Annots")] = ArrayObject(final)
    missing = sorted(wanted - set(removed))
    if missing:
        raise ValueError(f"no annotation with id {', '.join(missing)}")
    return _to_bytes(writer), removed


RESTYLE_COLOR = {*MARKUP, "note", "free_text", "ink", "rectangle", "ellipse", "line", "arrow"}
RESTYLE_WIDTH = {"ink", "rectangle", "ellipse", "line", "arrow"}
UPDATE_KEYS = ("color", "fill", "width", "opacity", "contents")


def _annotation_ids(reader) -> dict[str, tuple[int, int]]:
    """Annotation id -> (1-based page, index in /Annots), as list_annotations names them."""
    found = {}
    for number, page in enumerate(reader.pages, start=1):
        for index, ref in enumerate(page.get("/Annots") or []):
            try:
                annot = ref.get_object()
            except Exception:
                continue
            found.setdefault(_annotation_id(annot, ref, number, index), (number, index))
    return found


def _width_of(annot) -> float:
    border = annot.get("/BS")
    if border is not None and border.get_object().get("/W") is not None:
        return float(border.get_object()["/W"])
    with suppress(Exception):
        return float(annot["/Border"][2])
    return 1.0


def _ink_appearance(writer, annot, color: str, width: float, opacity: float):
    """Strokes /InkList again in a new colour and width."""
    rgb = " ".join(_num(v) for v in _rgb(color))
    paths, points = [], []
    for stroke in annot.get("/InkList") or []:
        values = [float(v) for v in stroke.get_object()]
        pts = [(values[i], values[i + 1]) for i in range(0, len(values) - 1, 2)]
        if not pts:
            continue
        points += pts
        if len(pts) == 1:
            pts = pts * 2
        paths.append(f"{_num(pts[0][0])} {_num(pts[0][1])} m " + " ".join(f"{_num(x)} {_num(y)} l" for x, y in pts[1:]) + " S")
    if not points:
        raise ValueError("this drawing has no strokes to restyle")
    rect = _bounds(points, width / 2 + 1)
    content = f"/GS0 gs {_num(width)} w 1 J 1 j {rgb} RG " + " ".join(paths)
    return rect, _appearance(writer, rect, content, _gs(writer, opacity))


def _restyle(writer, page, annot, update: dict) -> None:
    """Rewrites one annotation's colour, fill, width, opacity or text, appearance included."""
    from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, TextStringObject
    subtype = str(annot.get("/Subtype", ""))
    kind = KINDS.get(subtype, subtype.lstrip("/").lower())
    if kind == "line" and any(str(e) != "/None" for e in annot.get("/LE") or []):
        kind = "arrow"
    restyled = any(update.get(key) is not None for key in ("color", "fill", "width", "opacity"))
    if restyled and kind not in RESTYLE_COLOR:
        raise ValueError(f"{kind} annotations cannot be restyled; only their text can change")
    if update.get("width") is not None and kind not in RESTYLE_WIDTH:
        raise ValueError(f"{kind} annotations have no stroke width")
    if update.get("fill") is not None and kind not in ("rectangle", "ellipse"):
        raise ValueError("only rectangles and ellipses have a fill")
    contents = _text(annot.get("/Contents")) if update.get("contents") is None else str(update["contents"])
    if kind == "free_text" and update.get("contents") is not None and not contents.strip():
        raise ValueError("a text box needs some text")
    current = _color_of(annot.get("/C") or [])
    size = 12.0
    if kind == "free_text":
        appearance = _text(annot.get("/DA"))
        if match := re.search(r"([\d.]+)\s+Tf", appearance):
            size = float(match.group(1))
        if match := re.search(r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+rg", appearance):
            current = _color_of(match.groups())
    color = _hex(update["color"] if update.get("color") is not None else current, kind)
    try:
        opacity = float(update["opacity"] if update.get("opacity") is not None else annot.get("/CA", 1))
    except (TypeError, ValueError):
        raise ValueError("opacity must be a number from 0.05 to 1") from None
    opacity = max(0.05, min(1.0, opacity))
    width = max(0.25, min(48.0, float(update["width"]))) if update.get("width") is not None else _width_of(annot)
    stamp = TextStringObject(_pdf_date())

    if kind == "ink":
        rect, ap = _ink_appearance(writer, annot, color, width, opacity)
        annot[NameObject("/C")] = ArrayObject([FloatObject(v) for v in _rgb(color)])
        annot[NameObject("/BS")] = DictionaryObject({NameObject("/W"): FloatObject(width), NameObject("/S"): NameObject("/S")})
        annot[NameObject("/Rect")] = ArrayObject([FloatObject(v) for v in rect])
        annot[NameObject("/AP")] = DictionaryObject({NameObject("/N"): ap})
    elif kind in RESTYLE_COLOR:
        rect = [float(v) for v in annot.get("/Rect", [0, 0, 0, 0])]
        item = {"type": kind, "color": color, "opacity": opacity, "width": width, "contents": contents}
        if kind in MARKUP:
            values = [float(v) for v in annot.get("/QuadPoints") or []]
            item["quads"] = [values[i:i + 8] for i in range(0, len(values) - 7, 8)]
        elif kind == "note":
            item["at"] = [min(rect[0], rect[2]), max(rect[1], rect[3])]
        elif kind in ("line", "arrow"):
            x1, y1, x2, y2 = (float(v) for v in annot["/L"])
            item["points"] = [[x1, y1], [x2, y2]]
        else:
            item["rect"] = rect
            item["font_size"] = size
        if kind in ("rectangle", "ellipse"):
            fill = update.get("fill")
            if fill is None:
                fill = _color_of(annot.get("/IC") or [])
            if fill and fill != "none":
                item["fill"] = fill
        built = _build_annotation(writer, page, item)
        # Keep the annotation's identity, author, dates, flags and popup; take
        # the new colour, geometry and appearance.
        for key in ("/C", "/IC", "/CA", "/BS", "/DA", "/Rect", "/AP"):
            if key in built:
                annot[NameObject(key)] = built[key]
            elif key in annot and key != "/BS":
                del annot[key]
    if contents:
        annot[NameObject("/Contents")] = TextStringObject(contents)
    elif "/Contents" in annot:
        del annot["/Contents"]
    annot[NameObject("/M")] = stamp


def _update_annotations(data: bytes, updates: list) -> tuple[bytes, dict[str, str]]:
    """Restyles annotations already in the PDF, found by id; returns the bytes and old id -> id.

    An annotation addressed by object number or position gets a /NM so its id
    stays the same from now on; the mapping tells the caller its new id.
    """
    from pypdf.generic import NameObject, TextStringObject
    if not isinstance(updates, list) or not updates:
        raise ValueError("update must list {id, color, width, contents, …} objects")
    for update in updates:
        if not isinstance(update, dict) or not isinstance(update.get("id"), str) or not update["id"]:
            raise ValueError("each update needs the id of an annotation (from list_annotations)")
        if all(update.get(key) is None for key in UPDATE_KEYS):
            raise ValueError(f"update {update['id']} changes nothing; give {', '.join(UPDATE_KEYS)}")
    reader = _reader(data)
    where = _annotation_ids(reader)
    missing = sorted({u["id"] for u in updates} - set(where))
    if missing:
        raise ValueError(f"no annotation with id {', '.join(missing)}")
    writer = _writer(reader)
    renamed = {}
    for update in updates:
        number, index = where[update["id"]]
        page = writer.pages[number - 1]
        annots = page["/Annots"]
        annots = annots.get_object() if hasattr(annots, "get_object") else annots
        annot = annots[index].get_object()
        _restyle(writer, page, annot, update)
        if not _text(annot.get("/NM")):
            annot[NameObject("/NM")] = TextStringObject("op-" + uuid.uuid4().hex)
        renamed[update["id"]] = _text(annot["/NM"])
    return _to_bytes(writer), renamed


# ---------------------------------------------------------------- page organisation

def _sequence(context, data: bytes, entries: list) -> bytes:
    """Rebuild the page list from entries.

    Each entry is ``{"page": n}`` (a page of this PDF, 1-based), optionally
    with ``"rotate": ±90/180`` and ``"file": "other.pdf"`` (a page of another
    project PDF), or ``{"blank": true, "width"?, "height"?, "size"?}``.
    """
    from pypdf import PdfReader
    from pypdf.generic import ArrayObject, NameObject, NumberObject, RectangleObject
    from pypdf._page import PageObject
    if not isinstance(entries, list) or not entries:
        raise ValueError("A PDF needs at least one page")
    reader = _reader(data)
    writer = _writer(reader)
    base_refs = [page.indirect_reference for page in writer.pages]
    # Rotations are relative to the page as it was, also for a page used twice.
    base_rotation = [int(page.rotation or 0) for page in writer.pages]
    total = len(base_refs)
    used: dict = {}
    external: dict[str, bytes] = {}
    refs = []
    previous_size = None
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("each page entry must be an object")
        rotate = entry.get("rotate", 0) or 0
        if not isinstance(rotate, int) or rotate % 90:
            raise ValueError("rotate must be a multiple of 90")
        if entry.get("blank"):
            size = entry.get("size")
            if size:
                if str(size).lower() not in PAPER:
                    raise ValueError(f"size must be one of {', '.join(PAPER)}")
                width, height = PAPER[str(size).lower()]
            else:
                width = float(entry.get("width") or (previous_size or (595.28, 841.89))[0])
                height = float(entry.get("height") or (previous_size or (595.28, 841.89))[1])
            if not (36 <= width <= 14400 and 36 <= height <= 14400):
                raise ValueError("blank page size must be between 36 and 14400 points")
            page = PageObject.create_blank_page(writer, width, height)
            ref = writer._add_object(page)
            previous_size = (width, height)
        else:
            number = entry.get("page")
            if not isinstance(number, int) or isinstance(number, bool):
                raise ValueError("page entries need a page number")
            source_file = entry.get("file")
            if source_file:
                path = _resolve(context, source_file)
                key = _rel(context, path)
                if key not in external:
                    external[key] = _read_bytes(context, key)
                count = len(_reader(external[key]).pages)
                if not 1 <= number <= count:
                    raise ValueError(f"page {number} is out of range for {key} ({count} pages)")
                # A fresh reader per use, so a page used twice is copied twice.
                uses = used.setdefault(("file", key, number), 0)
                used[("file", key, number)] = uses + 1
                other = PdfReader(BytesIO(external[key]))
                ref = writer.add_page(other.pages[number - 1]).indirect_reference
                writer.remove_page(len(writer.pages) - 1, clean=False)
            else:
                if not 1 <= number <= total:
                    raise ValueError(f"page {number} is out of range (the PDF has {total} pages)")
                uses = used.get(number, 0)
                used[number] = uses + 1
                if uses == 0:
                    ref = base_refs[number - 1]
                else:
                    original = base_refs[number - 1].get_object()
                    copy = original.clone(writer, force_duplicate=True, ignore_fields=("/Annots", "/Parent"))
                    annots = ArrayObject()
                    for annot_ref in original.get("/Annots") or []:
                        annot = annot_ref.get_object()
                        if annot.get("/Subtype") in ("/Widget", "/Popup"):
                            continue
                        clone = annot.clone(writer, force_duplicate=True, ignore_fields=("/P", "/Popup", "/Parent"))
                        if "/NM" in clone:
                            del clone["/NM"]
                        annots.append(writer._add_object(clone) if not hasattr(clone, "indirect_reference") or clone.indirect_reference is None else clone.indirect_reference)
                    if annots:
                        copy[NameObject("/Annots")] = annots
                    ref = copy.indirect_reference or writer._add_object(copy)
            page = ref.get_object()
            box = page.mediabox
            previous_size = (float(box.width), float(box.height))
        page = ref.get_object()
        if not entry.get("blank") and not entry.get("file"):
            page[NameObject("/Rotate")] = NumberObject((base_rotation[entry["page"] - 1] + rotate) % 360)
        elif rotate:
            page[NameObject("/Rotate")] = NumberObject((int(page.rotation or 0) + rotate) % 360)
        refs.append(ref)
    pages_root = writer._root_object["/Pages"].get_object()
    pages_ref = writer._root_object.raw_get("/Pages")
    for ref in refs:
        page = ref.get_object()
        page[NameObject("/Parent")] = pages_ref
        if "/MediaBox" not in page:
            page[NameObject("/MediaBox")] = RectangleObject([0, 0, 612, 792])
    pages_root[NameObject("/Kids")] = ArrayObject(refs)
    pages_root[NameObject("/Count")] = NumberObject(len(refs))
    for key in ("/Resources", "/MediaBox", "/CropBox", "/Rotate"):
        if key in pages_root:
            del pages_root[key]
    kept = {getattr(r, "idnum", None) for r in refs}
    _prune_references(writer, kept)
    _ensure_names(writer)
    return _compact(_to_bytes(writer))


def _prune_references(writer, kept: set) -> None:
    """Drop outline items, named destinations and form fields that point at removed pages."""
    from pypdf.generic import ArrayObject, NameObject

    def target(dest):
        try:
            dest = dest.get_object()
        except AttributeError:
            pass
        if isinstance(dest, dict) and "/D" in dest:
            dest = dest["/D"]
        if isinstance(dest, list) and dest:
            first = dest[0]
            return getattr(first, "idnum", None)
        return None

    def dead(ref_id):
        return ref_id is not None and ref_id not in kept

    root = writer._root_object
    # Outline: unlink items whose destination page is gone (children are kept by re-parenting their first-level list).
    outlines = root.get("/Outlines")
    if outlines is not None:
        def walk(parent):
            node_ref = parent.get("/First")
            previous = None
            while node_ref is not None:
                node = node_ref.get_object()
                following = node.get("/Next")
                destination = node.get("/Dest")
                if destination is None and isinstance(node.get("/A"), dict):
                    destination = node["/A"].get("/D")
                walk(node)
                if dead(target(destination)) and "/First" not in node:
                    if previous is None:
                        if following is not None:
                            parent[NameObject("/First")] = following
                        else:
                            for key in ("/First", "/Last"):
                                if key in parent:
                                    del parent[key]
                    else:
                        if following is not None:
                            previous[NameObject("/Next")] = following
                        elif "/Next" in previous:
                            del previous["/Next"]
                    if following is not None:
                        nxt = following.get_object()
                        if previous is not None:
                            nxt[NameObject("/Prev")] = previous.indirect_reference or node.get("/Prev")
                        elif "/Prev" in nxt:
                            del nxt["/Prev"]
                    elif previous is not None:
                        parent[NameObject("/Last")] = previous.indirect_reference or node_ref
                else:
                    previous = node
                node_ref = following
        with suppress(Exception):  # a malformed outline keeps the rest of the PDF usable
            walk(outlines.get_object())
    # Named destinations.
    try:
        names = root.get("/Names", {}).get_object().get("/Dests")

        def prune_names(node):
            node = node.get_object()
            if "/Names" in node:
                values = node["/Names"]
                keep = ArrayObject()
                for i in range(0, len(values) - 1, 2):
                    if not dead(target(values[i + 1])):
                        keep.extend([values[i], values[i + 1]])
                node[NameObject("/Names")] = keep
            for kid in node.get("/Kids", []):
                prune_names(kid)

        if names is not None:
            prune_names(names)
    except Exception:  # noqa: S110 - a malformed name tree is left as it is
        pass
    # Form fields whose widgets all sit on removed pages.
    try:
        form = root.get("/AcroForm")
        if form is not None:
            form = form.get_object()

            def alive(field):
                field = field.get_object()
                kids = field.get("/Kids")
                if kids:
                    kept_kids = ArrayObject([k for k in kids if alive(k)])
                    field[NameObject("/Kids")] = kept_kids
                    return bool(kept_kids)
                page = field.get("/P")
                return not dead(getattr(page, "idnum", None)) if page is not None else True

            form[NameObject("/Fields")] = ArrayObject([f for f in form.get("/Fields", []) if alive(f)])
    except Exception:  # noqa: S110 - a malformed form is left as it is
        pass


def _entries_for(action: str, value: dict, total: int) -> list[dict]:
    """Translate one page action into a full page sequence."""
    current = [{"page": n} for n in range(1, total + 1)]
    if action == "rotate":
        pages = set(_pages_arg(value, total))
        degrees = value.get("degrees", 90)
        if degrees not in (90, 180, 270, -90, -180, -270):
            raise ValueError("degrees must be 90, 180, 270 or -90")
        return [dict(e, rotate=degrees) if e["page"] in pages else e for e in current]
    if action == "delete":
        pages = set(_pages_arg(value, total))
        if len(pages) >= total:
            raise ValueError("Cannot delete every page")
        return [e for e in current if e["page"] not in pages]
    if action == "duplicate":
        pages = set(_pages_arg(value, total))
        out = []
        for entry in current:
            out.append(entry)
            if entry["page"] in pages:
                out.append({"page": entry["page"]})
        return out
    if action == "move":
        pages = _pages_arg(value, total)
        to = value.get("to")
        if not isinstance(to, int) or not 1 <= to <= total:
            raise ValueError(f"to must be a position from 1 to {total}")
        moving = [e for e in current if e["page"] in set(pages)]
        rest = [e for e in current if e["page"] not in set(pages)]
        index = min(to - 1, len(rest))
        return rest[:index] + moving + rest[index:]
    if action == "reorder":
        order = value.get("order")
        if not isinstance(order, list) or sorted(order) != list(range(1, total + 1)):
            raise ValueError(f"order must list every page from 1 to {total} exactly once")
        return [{"page": n} for n in order]
    if action == "insert_blank":
        after = value.get("after", total)
        count = value.get("count", 1)
        if not isinstance(after, int) or not 0 <= after <= total:
            raise ValueError(f"after must be from 0 (before the first page) to {total}")
        if not isinstance(count, int) or not 1 <= count <= 100:
            raise ValueError("count must be from 1 to 100")
        blank = {"blank": True}
        if value.get("size"):
            blank["size"] = value["size"]
        reference = current[max(0, after - 1)]["page"]
        blanks = [dict(blank, like=reference) for _ in range(count)]
        return current[:after] + blanks + current[after:]
    raise ValueError("action must be rotate, delete, duplicate, move, reorder, insert_blank or extract")


def _resolve_like(data: bytes, entries: list[dict]) -> list[dict]:
    """Blank pages "like" page n take that page's size."""
    if not any("like" in e for e in entries):
        return entries
    reader = _reader(data)
    out = []
    for entry in entries:
        if "like" in entry:
            entry = dict(entry)
            page = reader.pages[entry.pop("like") - 1]
            if "size" not in entry:
                box = page.mediabox
                width, height = float(box.width), float(box.height)
                if int(page.get("/Rotate", 0) or 0) % 180:
                    width, height = height, width
                entry["width"], entry["height"] = width, height
        out.append(entry)
    return out


def _extract(context, data: bytes, pages: list[int]) -> bytes:
    """A PDF of the given pages (in order) that keeps their annotations, form fields and outline entries."""
    return _sequence(context, data, [{"page": n} for n in pages])


def _split_parts(value: dict, total: int) -> list[list[int]]:
    if value.get("ranges"):
        ranges = value["ranges"]
        if not isinstance(ranges, list):
            raise ValueError("ranges must be a list such as [\"1-3\", \"4-end\"]")
        return [_pages_arg({"pages": r if isinstance(r, list) else [r]}, total) for r in ranges]
    if value.get("every"):
        every = value["every"]
        if not isinstance(every, int) or every < 1:
            raise ValueError("every must be a positive number of pages")
        return [list(range(s, min(s + every, total + 1))) for s in range(1, total + 1, every)]
    if value.get("at"):
        cuts = sorted(set(_pages_arg({"pages": value["at"]}, total)))
        starts = [1] + [c for c in cuts if c > 1]
        return [list(range(s, (starts[i + 1] if i + 1 < len(starts) else total + 1))) for i, s in enumerate(starts)]
    raise ValueError("Provide every, at or ranges")


# ---------------------------------------------------------------- forms

def _field_type(field) -> str:
    kind = field.get("/FT")
    flags = int(field.get("/Ff", 0) or 0)
    if kind == "/Tx":
        return "text"
    if kind == "/Btn":
        if flags & (1 << 16):
            return "button"
        return "radio" if flags & (1 << 15) else "checkbox"
    if kind == "/Ch":
        return "combo" if flags & (1 << 17) else "list"
    if kind == "/Sig":
        return "signature"
    return "unknown"


def _form_fields(reader) -> list[dict]:
    fields = reader.get_fields() or {}
    pages: dict[str, int] = {}
    states: dict[str, set] = {}
    for number, page in enumerate(reader.pages, start=1):
        for ref in page.get("/Annots") or []:
            try:
                widget = ref.get_object()
            except Exception:
                continue
            if widget.get("/Subtype") != "/Widget":
                continue
            node, parts = widget, []
            while node is not None:
                if "/T" in node:
                    parts.append(_text(node["/T"]))
                node = node.get("/Parent")
                node = node.get_object() if node is not None else None
            name = ".".join(reversed(parts))
            pages.setdefault(name, number)
            ap = widget.get("/AP")
            if ap and "/N" in ap:
                normal = ap["/N"].get_object()
                if hasattr(normal, "keys"):
                    states.setdefault(name, set()).update(str(k).lstrip("/") for k in normal.keys() if k != "/Off")
    out = []
    for name, field in fields.items():
        kind = _field_type(field)
        if kind == "unknown" and "/Kids" in field and "/FT" not in field:
            continue
        flags = int(field.get("/Ff", 0) or 0)
        value = field.get("/V")
        entry = {"name": name, "type": kind, "page": pages.get(name),
                 "readonly": bool(flags & 1), "required": bool(flags & 2)}
        if kind == "text":
            entry["value"] = _text(value)
            entry["multiline"] = bool(flags & (1 << 12))
            if field.get("/MaxLen") is not None:
                entry["max_length"] = int(field["/MaxLen"])
        elif kind in ("checkbox", "radio"):
            options = sorted(states.get(name, set()) or {str(s).lstrip("/") for s in (field.get("/_States_") or []) if s != "/Off"})
            entry["options"] = options
            current = str(value).lstrip("/") if value is not None else "Off"
            entry["value"] = (current != "Off") if kind == "checkbox" else (None if current == "Off" else current)
        elif kind in ("combo", "list"):
            options = []
            for option in field.get("/Opt") or []:
                option = option.get_object() if hasattr(option, "get_object") else option
                options.append(_text(option[1] if isinstance(option, list) else option))
            entry["options"] = options
            entry["value"] = [_text(v) for v in value] if isinstance(value, list) else _text(value)
        if field.get("/TU"):
            entry["label"] = _text(field["/TU"])
        out.append(entry)
    return out


def _fill(data: bytes, values: dict) -> tuple[bytes, list[str]]:
    from pypdf.generic import BooleanObject, NameObject
    if not isinstance(values, dict) or not values:
        raise ValueError("values must map field names to values")
    reader = _reader(data)
    known = {f["name"]: f for f in _form_fields(reader)}
    if not known:
        raise ValueError("This PDF has no form fields")
    prepared: dict[str, object] = {}
    for name, value in values.items():
        field = known.get(name)
        if field is None:
            raise ValueError(f"no field named {name!r}; fields: {', '.join(sorted(known)[:40])}")
        if field["readonly"]:
            raise ValueError(f"field {name!r} is read-only")
        kind = field["type"]
        if kind == "text":
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise ValueError(f"field {name!r} takes text")
            text = str(value)
            if field.get("max_length") and len(text) > field["max_length"]:
                raise ValueError(f"field {name!r} allows at most {field['max_length']} characters")
            prepared[name] = text
        elif kind == "checkbox":
            on = field["options"][0] if field["options"] else "Yes"
            if isinstance(value, bool):
                prepared[name] = "/" + on if value else "/Off"
            elif str(value).lstrip("/") in (*field["options"], "Off"):
                prepared[name] = "/" + str(value).lstrip("/")
            else:
                raise ValueError(f"field {name!r} is a checkbox: pass true or false")
        elif kind == "radio":
            choice = str(value).lstrip("/")
            if choice not in field["options"] and choice != "Off":
                raise ValueError(f"field {name!r} takes one of: {', '.join(field['options'])}")
            prepared[name] = "/" + choice
        elif kind in ("combo", "list"):
            choice = str(value)
            if field["options"] and choice not in field["options"] and kind == "list":
                raise ValueError(f"field {name!r} takes one of: {', '.join(field['options'])}")
            prepared[name] = choice
        else:
            raise ValueError(f"field {name!r} is a {kind} field and cannot be filled")
    writer = _writer(reader)
    by_page: dict[int, dict] = {}
    for name, value in prepared.items():
        page = known[name]["page"] or 1
        by_page.setdefault(page, {})[name] = value
    for page_number, page_values in by_page.items():
        page = writer.pages[page_number - 1]
        writer.update_page_form_field_values(page, page_values, auto_regenerate=False)
    writer.set_need_appearances_writer(True)
    if "/AcroForm" in writer._root_object:
        writer._root_object["/AcroForm"][NameObject("/NeedAppearances")] = BooleanObject(True)
    return _to_bytes(writer), sorted(prepared)


# ---------------------------------------------------------------- read operations

def list_files(value, context):
    """PDF files in the bound project, newest first."""
    root = _root(context)
    query = str((value or {}).get("query", "")).lower()
    found = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in files:
            if not name.lower().endswith(".pdf") or name.startswith("."):
                continue
            path = Path(folder) / name
            relative = path.relative_to(root).as_posix()
            if query and query not in relative.lower():
                continue
            stat = path.stat()
            found.append({"path": relative, "size": stat.st_size, "modified": int(stat.st_mtime * 1000)})
            if len(found) >= 2000:
                break
    found.sort(key=lambda f: -f["modified"])
    return {"project": root.name, "files": found[:500], "truncated": len(found) > 500}


def info(value, context):
    """Page count, metadata, outline, annotation and field counts, and the revision used to fetch bytes."""
    path = _resolve(context, value["path"])
    data = _read_bytes(context, value["path"])
    reader = _reader(data)
    meta = reader.metadata or {}
    try:
        annotations = len(_list_annotations_fast(reader))
    except Exception:
        annotations = 0
    try:
        fields = len(reader.get_fields() or {})
    except Exception:
        fields = 0
    return {
        "path": _rel(context, path),
        "pages": len(reader.pages),
        "title": str(meta.get("/Title") or "").strip(),
        "author": str(meta.get("/Author") or "").strip(),
        "size": path.stat().st_size,
        "modified": int(path.stat().st_mtime * 1000),
        "revision": _revision(path),
        "outline": _outline(reader),
        "annotations": annotations,
        "form_fields": fields,
        "writable": callable(getattr(context, "write_file", None)),
    }


def _list_annotations_fast(reader) -> list:
    out = []
    for page in reader.pages:
        for ref in page.get("/Annots") or []:
            with suppress(Exception):  # an unreadable annotation is skipped
                if ref.get_object().get("/Subtype") not in SKIP_ANNOTS:
                    out.append(ref)
    return out


def chunk(value, context):
    """One base64 slice of a project PDF or a draft, so the page can render it locally."""
    if value.get("draft"):
        path = _draft_path(context, value["draft"])
    else:
        path = _resolve(context, value.get("path", ""))
        if value.get("revision") and value["revision"] != _revision(path):
            raise ValueError("The file changed on disk; reopen it")
    offset = max(0, int(value.get("offset", 0)))
    length = max(1, min(int(value.get("length", CHUNK_LIMIT)), CHUNK_LIMIT))
    with path.open("rb") as handle:
        handle.seek(offset)
        data = handle.read(length)
    size = path.stat().st_size
    return {"offset": offset, "size": size, "data": base64.b64encode(data).decode("ascii"),
            "done": offset + len(data) >= size}


def read_text(value, context):
    """Extracted text of a page range (1-based, inclusive)."""
    reader = _open_pdf(context, value["path"])
    total = len(reader.pages)
    limit = max(1_000, min(int(value.get("max_chars", TEXT_LIMIT)), TEXT_LIMIT))
    pages, used, truncated = [], 0, False
    selected = _page_range(total, value.get("start_page"), value.get("end_page"))
    for number in selected:
        text = _page_text(reader.pages[number - 1])
        if used + len(text) > limit:
            text = text[: max(0, limit - used)]
            truncated = True
        pages.append({"page": number, "text": text})
        used += len(text)
        if truncated:
            break
    return {"path": value["path"], "total_pages": total,
            "start_page": selected.start if total else 0,
            "end_page": pages[-1]["page"] if pages else 0,
            "pages": pages, "truncated": truncated}


def find(value, context):
    """Pages and snippets where a phrase occurs (whitespace-insensitive)."""
    query = str(value.get("query", "")).strip()
    if not query:
        raise ValueError("query is required")
    flags = 0 if value.get("case_sensitive") else re.IGNORECASE
    pattern = re.compile(r"\s+".join(re.escape(part) for part in query.split()), flags)
    limit = max(1, min(int(value.get("max_results", 100)), 500))
    reader = _open_pdf(context, value["path"])
    matches, total, pages_with = [], 0, set()
    for index, page in enumerate(reader.pages, start=1):
        text = _page_text(page)
        for found in pattern.finditer(text):
            total += 1
            pages_with.add(index)
            if len(matches) < limit:
                start, end = found.span()
                before = text[max(0, start - 70):start]
                after = text[end:end + 70]
                matches.append({"page": index, "match": found.group(0),
                                "snippet": re.sub(r"\s+", " ", f"{before}{found.group(0)}{after}").strip(),
                                "before": re.sub(r"\s+", " ", before), "after": re.sub(r"\s+", " ", after)})
    return {"path": value["path"], "query": query, "total": total, "pages": sorted(pages_with),
            "matches": matches, "truncated": total > len(matches)}


def _llm(prompt: str) -> str:
    from openprogram.runtime.agent import llm
    return llm(prompt)


def summarize(value, context):
    """A model summary of a PDF page range, or of supplied text."""
    focus = str(value.get("focus", "")).strip()
    if value.get("text"):
        text, source = str(value["text"]), {"kind": "text"}
    elif value.get("path"):
        context.progress("Extracting text")
        extracted = read_text({"path": value["path"], "start_page": value.get("start_page"),
                               "end_page": value.get("end_page"), "max_chars": SUMMARY_INPUT_LIMIT}, context)
        text = "\n\n".join(f"[Page {p['page']}]\n{p['text']}" for p in extracted["pages"] if p["text"])
        if not text.strip():
            raise ValueError("No extractable text on these pages (the PDF may be scanned images)")
        source = {"kind": "pdf", "path": value["path"], "start_page": extracted["start_page"],
                  "end_page": extracted["end_page"], "total_pages": extracted["total_pages"],
                  "truncated": extracted["truncated"]}
    else:
        raise ValueError("Provide path or text")
    text = text[:SUMMARY_INPUT_LIMIT]
    context.progress("Summarizing")
    prompt = (
        "Summarize the document below for a researcher. Start with one sentence stating its main claim or purpose, "
        "then list the key points as short bullets, then a short 'Limitations' paragraph if the text supports one. "
        "Cite page numbers like (p. 3) when the text has [Page N] markers. Use Markdown. Do not invent content."
        + (f"\nFocus on: {focus}" if focus else "")
        + "\n\n---\n" + text
    )
    summary = _llm(prompt)
    if not isinstance(summary, str):
        summary = str(summary)
    return {"summary": summary.strip(), "source": source}


def list_annotations(value, context):
    """Annotations of a PDF (links, form widgets and popups excluded)."""
    data, source = _source(value, context)
    reader = _reader(data)
    total = len(reader.pages)
    pages = set(_pages_arg(value, total, required=False))
    types = value.get("types")
    if types is not None and (not isinstance(types, list) or not all(isinstance(t, str) for t in types)):
        raise ValueError("types must be a list of annotation types")
    found = _list_annotations(reader, pages or None, set(types) if types else None)
    return {**source, "pages": total, "annotations": found, "count": len(found)}


def form_fields(value, context):
    """Form fields of a PDF with their type, page, value and options."""
    data, source = _source(value, context)
    reader = _reader(data)
    fields = _form_fields(reader)
    return {**source, "fields": fields, "count": len(fields)}


# ---------------------------------------------------------------- agent write operations

def _write_target(value, context, source: str) -> str:
    output = value.get("output")
    if output:
        return _output(context, output, overwrite=bool(value.get("overwrite")), existing=set())
    return source


def annotate(value, context):
    """Highlight, underline or strike out a phrase, or add a note, shape or text box, then save the PDF."""
    path = _resolve(context, value.get("path", ""))
    relative = _rel(context, path)
    data = _read_bytes(context, relative)
    reader = _reader(data)
    total = len(reader.pages)
    kind = str(value.get("type", "")).lower().replace("-", "_")
    if kind == "strikeout":
        kind = "strikethrough"
    allowed = (*MARKUP, "note", "rectangle", "ellipse", "line", "arrow", "free_text")
    if kind not in allowed:
        raise ValueError(f"type must be one of {', '.join(allowed)}")
    color = _hex(value.get("color"), kind)
    pages = _pages_arg(value, total, "page", required=False) if value.get("page") is not None else []
    phrase = str(value.get("text") or "").strip()
    occurrence = value.get("occurrence", "first")
    if occurrence not in ("first", "all") and not (isinstance(occurrence, int) and occurrence >= 1):
        raise ValueError("occurrence must be \"first\", \"all\" or a 1-based match number")
    base = {"type": kind, "color": color, "contents": value.get("note") or value.get("contents") or "",
            "author": value.get("author") or "Agent"}
    for key in ("width", "opacity", "fill", "font_size"):
        if value.get(key) is not None:
            base[key] = value[key]
    items, matched = [], []
    if kind in MARKUP or (kind == "note" and phrase and not value.get("at")):
        if not phrase:
            raise ValueError(f"{kind} needs text: the phrase to mark")
        hits = []
        for number in (pages or range(1, total + 1)):
            chars = _chars(reader.pages[number - 1])
            for start, end in _find_phrase(chars, phrase, bool(value.get("case_sensitive"))):
                quads = _quads_for(chars, start, end)
                if quads:
                    hits.append((number, quads, "".join(c["c"] for c in chars[start:end])))
        if not hits:
            where = f" on page {pages[0]}" if len(pages) == 1 else ""
            raise ValueError(f"{phrase!r} was not found{where}; check the wording with find or read_text")
        if occurrence == "first":
            hits = hits[:1]
        elif isinstance(occurrence, int):
            if occurrence > len(hits):
                raise ValueError(f"{phrase!r} occurs {len(hits)} time(s); occurrence {occurrence} does not exist")
            hits = [hits[occurrence - 1]]
        for number, quads, text in hits[:200]:
            if kind == "note":
                q = quads[-1]
                items.append({**base, "page": number, "at": [max(q[2], q[6]) + 2, max(q[1], q[3]) + 4]})
            else:
                items.append({**base, "page": number, "quads": quads})
            matched.append({"page": number, "text": re.sub(r"\s+", " ", text).strip()})
    else:
        if len(pages) != 1:
            raise ValueError(f"{kind} needs page (one page number)")
        page = reader.pages[pages[0] - 1]
        box = page.mediabox
        left, bottom, right, top = float(box.left), float(box.bottom), float(box.right), float(box.top)
        item = {**base, "page": pages[0]}
        if kind == "note":
            item["at"] = _floats(value.get("at") or [left + 36, top - 36], "at", 2)
            if not item["contents"]:
                raise ValueError("note needs note: the comment text")
        elif kind in ("line", "arrow"):
            item["points"] = value.get("points")
        elif kind == "free_text":
            item["rect"] = value.get("rect") or [left + 36, top - 96, left + 300, top - 36]
            item["contents"] = value.get("note") or value.get("contents") or phrase
        else:
            item["rect"] = value.get("rect")
        if kind != "note" and kind != "free_text" and item.get("rect") is None and item.get("points") is None:
            raise ValueError(f"{kind} needs {'points' if kind in ('line', 'arrow') else 'rect'} in PDF points")
        items.append(item)
        matched.append({"page": pages[0]})
    updated, ids = _add_annotations(data, items)
    target = _write_target(value, context, relative)
    written = _write(context, target, updated)
    return {**written, "added": [{"id": i, **m} for i, m in zip(ids, matched)], "count": len(ids)}


def remove_annotation(value, context):
    """Delete annotations by id (from list_annotations), then save the PDF."""
    path = _resolve(context, value.get("path", ""))
    relative = _rel(context, path)
    ids = value.get("ids")
    if ids is None and value.get("id"):
        ids = [value["id"]]
    updated, removed = _remove_annotations(_read_bytes(context, relative), ids)
    written = _write(context, _write_target(value, context, relative), updated)
    return {**written, "removed": removed, "count": len(removed)}


def pages(value, context):
    """Rotate, delete, duplicate, move, reorder, insert blank pages, or extract pages to a new file."""
    path = _resolve(context, value.get("path", ""))
    relative = _rel(context, path)
    data = _read_bytes(context, relative)
    total = _page_count(data)
    action = value.get("action")
    if action == "extract":
        selected = _pages_arg(value, total)
        output = _output(context, value.get("output") or "", overwrite=bool(value.get("overwrite")))
        if output == relative:
            raise ValueError("output must be a new file")
        written = _write(context, output, _extract(context, data, selected))
        return {**written, "action": "extract", "source": relative, "extracted": selected}
    entries = _resolve_like(data, _entries_for(str(action or ""), value, total))
    updated = _sequence(context, data, entries)
    written = _write(context, _write_target(value, context, relative), updated)
    return {**written, "action": action, "previous_pages": total}


def merge(value, context):
    """Insert the pages of other project PDFs into a PDF (or into a new file)."""
    path = _resolve(context, value.get("path", ""))
    relative = _rel(context, path)
    sources = value.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("sources must list one or more project PDFs")
    data = _read_bytes(context, relative)
    total = _page_count(data)
    after = value.get("after", total)
    if not isinstance(after, int) or not 0 <= after <= total:
        raise ValueError(f"after must be from 0 (before the first page) to {total}")
    inserted = []
    for source in sources:
        other = _resolve(context, source)
        other_rel = _rel(context, other)
        count = _page_count(_read_bytes(context, other_rel))
        inserted.extend({"page": n, "file": other_rel} for n in range(1, count + 1))
    current = [{"page": n} for n in range(1, total + 1)]
    updated = _sequence(context, data, current[:after] + inserted + current[after:])
    written = _write(context, _write_target(value, context, relative), updated)
    return {**written, "inserted_pages": len(inserted), "after": after,
            "sources": [_rel(context, _resolve(context, s)) for s in sources]}


def split(value, context):
    """Split a PDF into several new files by page count, cut points or ranges."""
    path = _resolve(context, value.get("path", ""))
    relative = _rel(context, path)
    data = _read_bytes(context, relative)
    total = _page_count(data)
    parts = _split_parts(value, total)
    if len(parts) > 200:
        raise ValueError("That would create more than 200 files")
    stem = relative[:-4]
    folder = str(value.get("folder") or "").strip().strip("/")
    if folder:
        stem = f"{folder}/{Path(stem).name}"
    outputs = [f"{stem} (part {i}).pdf" for i in range(1, len(parts) + 1)]
    names = [_output(context, o, overwrite=bool(value.get("overwrite"))) for o in outputs]
    files = []
    for name, part in zip(names, parts):
        if not part:
            raise ValueError("a part has no pages")
        written = _write(context, name, _extract(context, data, part))
        files.append({**written, "source_pages": [part[0], part[-1]]})
    return {"source": relative, "files": files, "count": len(files)}


def fill_form(value, context):
    """Set form field values and save the PDF."""
    path = _resolve(context, value.get("path", ""))
    relative = _rel(context, path)
    updated, names = _fill(_read_bytes(context, relative), value.get("values"))
    written = _write(context, _write_target(value, context, relative), updated)
    reader = _reader(updated)
    current = {f["name"]: f.get("value") for f in _form_fields(reader)}
    return {**written, "filled": [{"name": n, "value": current.get(n)} for n in names]}


# ---------------------------------------------------------------- editor (UI) operations

def upload(value, context):
    """Receive one base64 chunk of a PDF the page serialised; the final chunk turns it into a draft."""
    folder = _stage(context)
    upload_id = value.get("upload") or ("u_" + uuid.uuid4().hex)
    if not UPLOAD_ID.match(str(upload_id)):
        raise ValueError("invalid upload id")
    part = folder / f"{upload_id}.part"
    offset = int(value.get("offset", 0))
    raw = base64.b64decode(value.get("data", ""), validate=True)
    if len(raw) > CHUNK_LIMIT:
        raise ValueError("chunk too large")
    if offset == 0:
        part.write_bytes(raw)
    else:
        if not part.exists() or part.stat().st_size != offset:
            raise ValueError("upload out of order; start again")
        with part.open("ab") as handle:
            handle.write(raw)
    if part.stat().st_size > DRAFT_LIMIT:
        part.unlink(missing_ok=True)
        raise ValueError("PDF too large")
    if not value.get("final"):
        return {"upload": upload_id, "received": part.stat().st_size}
    data = part.read_bytes()
    part.unlink(missing_ok=True)
    if not data.startswith(b"%PDF"):
        raise ValueError("not a PDF")
    reader = _reader(data)
    writer = _writer(reader)
    _ensure_names(writer, str(value.get("author") or "")[:120])
    normalised = _to_bytes(writer)
    draft = _new_draft(context, normalised)
    return _draft_summary(draft, normalised)


def _draft_summary(draft: str, data: bytes) -> dict:
    reader = _reader(data)
    return {"draft": draft, "size": len(data), "pages": len(reader.pages),
            "annotations": _list_annotations(reader), "outline": _outline(reader)}


def draft_apply(value, context):
    """Apply editor changes to a draft (or the project file) and return the new draft.

    ``update`` restyles annotations already in the PDF: ``[{id, color, fill,
    width, opacity, contents}]``; the result's ``updated`` maps each id to the
    id the annotation has afterwards.
    """
    data, _ = _source(value, context)
    steps = 0
    renamed = {}
    # Restyling goes first: its ids name annotations of the source as listed.
    if value.get("update"):
        data, renamed = _update_annotations(data, value["update"])
        steps += 1
    if value.get("remove"):
        data, _removed = _remove_annotations(data, value["remove"])
        steps += 1
    if value.get("annotations"):
        data, _ids = _add_annotations(data, value["annotations"], author=str(value.get("author") or ""))
        steps += 1
    if value.get("sequence"):
        data = _sequence(context, data, _resolve_like(data, value["sequence"]))
        steps += 1
    if value.get("fill"):
        data, _names = _fill(data, value["fill"])
        steps += 1
    if not steps:
        raise ValueError("Nothing to apply")
    draft = _new_draft(context, data)
    return {**_draft_summary(draft, data), **({"updated": renamed} if renamed else {})}


def save(value, context):
    """Write a draft (or, for a copy, the unchanged file ``from``) into the project.

    Saving over a file checks ``base_revision`` so a change made on disk
    meanwhile is not silently replaced.
    """
    if value.get("draft"):
        data = _draft_path(context, value["draft"]).read_bytes()
    elif value.get("from"):
        data = _read_bytes(context, _rel(context, _resolve(context, value["from"])))
    else:
        raise ValueError("Provide draft or from")
    target = value.get("path", "")
    overwrite = bool(value.get("overwrite"))
    base_revision = value.get("base_revision")
    root = _root(context)
    relative = _output(context, target, overwrite=True)
    existing = root / relative
    if value.get("as_copy") and existing.exists() and not overwrite:
        raise FileExistsError(f"{relative} already exists; choose another name or replace it")
    if base_revision and existing.exists() and _revision(existing) != base_revision and not overwrite:
        raise ValueError(f"{relative} changed on disk after it was opened; save a copy or replace it")
    written = _write(context, relative, data)
    return {**written, "draft": value.get("draft"), "as_copy": bool(value.get("as_copy"))}


def export(value, context):
    """Write page groups of a draft or project PDF to new files (extract and split from the editor)."""
    data, _ = _source(value, context)
    total = _page_count(data)
    parts = value.get("parts")
    if not isinstance(parts, list) or not parts or len(parts) > 200:
        raise ValueError("parts must list 1 to 200 {pages, output} objects")
    plans = []
    seen = set()
    for part in parts:
        if not isinstance(part, dict):
            raise ValueError("each part needs pages and output")
        selected = _pages_arg(part, total)
        output = _output(context, part.get("output", ""), overwrite=bool(value.get("overwrite")))
        if output in seen:
            raise ValueError(f"{output} is used twice")
        seen.add(output)
        plans.append((output, selected))
    files = [{**_write(context, output, _extract(context, data, selected)), "source_pages": selected}
             for output, selected in plans]
    return {"files": files, "count": len(files)}


operations = {
    "list_files": list_files,
    "info": info,
    "chunk": chunk,
    "read_text": read_text,
    "find": find,
    "summarize": summarize,
    "list_annotations": list_annotations,
    "annotate": annotate,
    "remove_annotation": remove_annotation,
    "pages": pages,
    "merge": merge,
    "split": split,
    "form_fields": form_fields,
    "fill_form": fill_form,
    "upload": upload,
    "draft_apply": draft_apply,
    "save": save,
    "export": export,
}
