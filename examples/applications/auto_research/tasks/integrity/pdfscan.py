"""Text in a PDF that a human reader cannot see: white or near-white fill, fonts under
2pt, text placed outside the page's crop box, invisible render modes (3 and 7) and zero
fill opacity. Plain code.

Backends, the first importable one wins: PyMuPDF (fitz), pdfminer.six, then a scan of the
page content streams written here (pages and fonts read with pypdf when importable,
otherwise the raw file) together with `pdftotext -bbox-layout` for tiny words. Each hidden piece of text comes back as a run {page, reasons, text, x, y, size}.
"""

import math
import re
import shutil
import subprocess
import zlib
from pathlib import Path

WHITE_DISTANCE = 30.0     # RGB (0-255) distance from white at or under which text counts as white
TINY_PT = 2.0             # effective font size under which text counts as unreadable
INVISIBLE_MODES = (3, 7)  # render modes that paint nothing
REASONS = ("white", "tiny", "offpage", "invisible", "transparent")


def backend() -> str:
    """The backend scan() uses here."""
    try:
        import fitz  # noqa: F401
        return "pymupdf"
    except ImportError:
        pass
    try:
        import pdfminer.high_level  # noqa: F401
        return "pdfminer"
    except ImportError:
        pass
    streams = "pypdf" if _pypdf() else "raw"
    return f"content-stream({streams})" + ("+pdftotext" if shutil.which("pdftotext") else "")


def scan(pdf: Path, which: str = "") -> tuple[list[dict], str]:
    """Hidden text runs in pdf and the backend that found them."""
    which = which or backend()
    if which == "pymupdf":
        return _fitz_runs(pdf), which
    if which == "pdfminer":
        runs = _pdfminer_runs(pdf)
        for r in stream_runs(pdf):  # pdfminer does not keep the render mode or opacity
            r["reasons"] = [x for x in r["reasons"] if x in ("invisible", "transparent")]
            if r["reasons"]:
                runs.append(r)
        return runs, which
    runs = stream_runs(pdf)
    if "pdftotext" in which:
        words = pdftotext_runs(pdf)
        if words is not None:
            # pdftotext decodes tiny words better; it drops text outside the crop box,
            # so off-page text stays with the stream scan
            runs = [r for r in runs if set(r["reasons"]) - {"tiny"}]
            for r in runs:
                r["reasons"] = [x for x in r["reasons"] if x != "tiny"]
            runs += [w for w in words if "tiny" in w["reasons"]]
    return runs, which


def reasons_for(rgb, alpha, render, size, x, y, box) -> list[str]:
    out = []
    if rgb is not None and white_distance(rgb) <= WHITE_DISTANCE:
        out.append("white")
    if size is not None and 0 < size < TINY_PT:
        out.append("tiny")
    if box and x is not None and not (box[0] - 1 <= x <= box[2] + 1 and box[1] - 1 <= y <= box[3] + 1):
        out.append("offpage")
    if render in INVISIBLE_MODES:
        out.append("invisible")
    if alpha is not None and alpha < 0.05:
        out.append("transparent")
    return out


def white_distance(rgb) -> float:
    return math.sqrt(sum(((1 - c) * 255) ** 2 for c in rgb))


def to_rgb(values) -> tuple | None:
    v = [float(x) for x in values]
    if len(v) == 1:
        return v[0], v[0], v[0]
    if len(v) == 3:
        return tuple(v)
    if len(v) == 4:
        c, m, y, k = v
        return (1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k)
    return None


def merge(pieces: list[dict]) -> list[dict]:
    """Consecutive hidden pieces on one page with the same reasons become one run."""
    runs: list[dict] = []
    for p in pieces:
        if not p["text"].strip() or not p["reasons"]:
            continue
        last = runs[-1] if runs else None
        if last and last["page"] == p["page"] and last["reasons"] == p["reasons"]:
            last["text"] = (last["text"] + " " + p["text"].strip()).strip()
        else:
            runs.append({**p, "text": p["text"].strip()})
    return runs


# PyMuPDF ------------------------------------------------------------------------------

def _fitz_runs(pdf: Path) -> list[dict]:
    import fitz
    pieces = []
    with fitz.open(str(pdf)) as doc:
        for pno, page in enumerate(doc, 1):
            box = tuple(page.cropbox)
            height = page.mediabox.height
            fills = [(d["rect"], d["fill"]) for d in page.get_drawings() if d.get("fill") and
                     white_distance(d["fill"]) > WHITE_DISTANCE]
            for span in page.get_texttrace():
                text = spaced(span.get("chars", ()), span.get("size") or 10)
                x0, y0, x1, y1 = span["bbox"]
                rgb = to_rgb(span.get("color") or (0,))
                if rgb and white_distance(rgb) <= WHITE_DISTANCE and any(
                        fitz.Rect(span["bbox"]).intersects(r) for r, _ in fills):
                    rgb = None  # white on a coloured box is readable
                # texttrace bboxes are top-down; the crop box is bottom-up
                cx, cy = (x0 + x1) / 2, height - (y0 + y1) / 2
                render = 3 if span.get("type") == 3 else 0
                reasons = reasons_for(rgb, span.get("opacity"), render, span.get("size"), cx, cy, box)
                pieces.append({"page": pno, "reasons": reasons, "text": text, "x": round(cx, 1),
                               "y": round(cy, 1), "size": round(span.get("size") or 0, 2)})
    return merge(pieces)


def spaced(chars, size: float) -> str:
    """Characters of a texttrace span, with a space where the gap between glyphs is a
    word space (TeX draws no space characters)."""
    out, last = [], None
    for c in chars:
        bbox = c[3] if len(c) > 3 else None
        if last is not None and bbox is not None and not -size <= bbox[0] - last <= 0.12 * size:
            out.append(" ")
        out.append(chr(c[0]))
        last = bbox[2] if bbox is not None else last
    return "".join(out)


# pdfminer.six -------------------------------------------------------------------------

def _pdfminer_runs(pdf: Path) -> list[dict]:
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTAnno, LTChar

    def chars(obj):
        if isinstance(obj, (LTChar, LTAnno)):
            yield obj
            return
        for child in getattr(obj, "_objs", []) or []:
            yield from chars(child)

    pieces = []
    for pno, page in enumerate(extract_pages(str(pdf)), 1):
        box = page.bbox
        for ch in chars(page):
            if isinstance(ch, LTAnno):  # a word space or line break pdfminer inferred
                if pieces and pieces[-1]["page"] == pno:
                    pieces[-1]["text"] += " "
                continue
            color = getattr(ch.graphicstate, "ncolor", None)
            color = color if isinstance(color, (list, tuple)) else [color] if color is not None else []
            rgb = to_rgb(color) if color else None
            cx, cy = (ch.x0 + ch.x1) / 2, (ch.y0 + ch.y1) / 2
            reasons = reasons_for(rgb, None, None, ch.size, cx, cy, box)
            pieces.append({"page": pno, "reasons": reasons, "text": ch.get_text(), "x": round(cx, 1),
                           "y": round(cy, 1), "size": round(ch.size, 2)})
    # characters of one hidden run arrive one by one: join them without spaces
    runs: list[dict] = []
    for p in pieces:
        if not p["reasons"]:
            if runs and not runs[-1].get("closed"):
                runs[-1]["closed"] = True
            continue
        last = runs[-1] if runs else None
        if last and not last.get("closed") and last["page"] == p["page"] and last["reasons"] == p["reasons"]:
            last["text"] += p["text"]
        else:
            runs.append(dict(p))
    for r in runs:
        r.pop("closed", None)
        r["text"] = " ".join(r["text"].split())
    return [r for r in runs if r["text"]]


# pdftotext -bbox-layout ---------------------------------------------------------------

PAGE_TAG = re.compile(r'<page width="([\d.]+)" height="([\d.]+)">')
WORD_TAG = re.compile(r'<word xMin="([-\d.]+)" yMin="([-\d.]+)" xMax="([-\d.]+)" yMax="([-\d.]+)">(.*?)</word>', re.S)


def pdftotext_runs(pdf: Path) -> list[dict] | None:
    """Tiny and off-page words from pdftotext's word boxes, or None without pdftotext."""
    if not shutil.which("pdftotext"):
        return None
    done = subprocess.run(["pdftotext", "-bbox-layout", str(pdf), "-"], capture_output=True, text=True)
    if done.returncode != 0:
        return None
    import html
    pieces = []
    for pno, chunk in enumerate(re.split(r"(?=<page )", done.stdout)[1:], 1):
        page = PAGE_TAG.search(chunk)
        if not page:
            continue
        width, height = float(page.group(1)), float(page.group(2))
        for x0, y0, x1, y1, word in WORD_TAG.findall(chunk):
            x0, y0, x1, y1 = map(float, (x0, y0, x1, y1))
            reasons = []
            if y1 - y0 < TINY_PT:
                reasons.append("tiny")
            if x1 <= 0 or x0 >= width or y1 <= 0 or y0 >= height:
                reasons.append("offpage")
            pieces.append({"page": pno, "reasons": reasons, "text": html.unescape(word),
                           "x": round(x0, 1), "y": round(height - y1, 1), "size": round(y1 - y0, 2)})
    return merge(pieces)


# Content streams ----------------------------------------------------------------------

def _pypdf():
    try:
        import pypdf
        return pypdf
    except ImportError:
        return None


def stream_runs(pdf: Path) -> list[dict]:
    pages = _pypdf_pages(pdf) if _pypdf() else None
    if pages is None:
        pages = raw_pages(pdf.read_bytes())
    pieces = []
    for page in pages:
        pieces += interpret(page["data"], page["page"], page["box"], page["fonts"], page["alphas"])
    return merge(pieces)


def _pypdf_pages(pdf: Path) -> list[dict] | None:
    pypdf = _pypdf()
    try:
        reader = pypdf.PdfReader(str(pdf))
        out = []
        for pno, page in enumerate(reader.pages, 1):
            contents = page.get_contents()
            data = contents.get_data() if contents is not None else b""
            res = page.get("/Resources")
            res = res.get_object() if res is not None else {}
            fonts, alphas = {}, {}
            for name, ref in (res.get("/Font") or {}).items():
                font = ref.get_object()
                tu = font.get("/ToUnicode")
                if tu is not None:
                    fonts[name.lstrip("/")] = parse_tounicode(tu.get_object().get_data())
            for name, ref in (res.get("/ExtGState") or {}).items():
                gs = ref.get_object()
                if "/ca" in gs:
                    alphas[name.lstrip("/")] = float(gs["/ca"])
            box = tuple(float(v) for v in page.cropbox)
            out.append({"page": pno, "data": data, "box": box, "fonts": fonts, "alphas": alphas})
        return out
    except Exception:
        return None


OBJ = re.compile(rb"(\d+)\s+\d+\s+obj\b(.*?)\bendobj", re.S)
BOX = re.compile(rb"/(?:CropBox|MediaBox)\s*\[\s*([-\d.\s]+)\]")


def raw_pages(raw: bytes) -> list[dict]:
    """Pages and their content streams read straight from the file, for PDFs whose page
    objects are not compressed; otherwise every stream that draws text, page unknown (0)."""
    objs: dict[int, tuple[bytes, bytes | None]] = {}
    for num, body in OBJ.findall(raw):
        head, stream = body, None
        m = re.search(rb"stream\r?\n(.*?)\r?\n?endstream", body, re.S)
        if m:
            head, stream = body[:m.start()], m.group(1)
            if b"/FlateDecode" in head:
                try:
                    stream = zlib.decompress(stream)
                except zlib.error:
                    stream = None
        objs[int(num)] = (head, stream)
    alphas = {}
    for name, num in re.findall(rb"/([\w.+-]+)\s+(\d+)\s+0\s+R", raw):
        head = objs.get(int(num), (b"", None))[0]
        m = re.search(rb"/ca\s+([\d.]+)", head)
        if m:
            alphas[name.decode()] = float(m.group(1))
    for name, value in re.findall(rb"/([\w.+-]+)\s*<<[^>]*?/ca\s+([\d.]+)", raw):
        alphas[name.decode()] = float(value)
    default = BOX.search(raw)
    default_box = tuple(float(v) for v in default.group(1).split()) if default else None
    pages = []
    for num, (head, _) in objs.items():
        if not re.search(rb"/Type\s*/Page(?![a-zA-Z])", head):
            continue
        box = BOX.search(head)
        refs = re.search(rb"/Contents\s*(\[[^\]]*\]|\d+\s+\d+\s+R)", head)
        nums = [int(n) for n in re.findall(rb"(\d+)\s+\d+\s+R", refs.group(1))] if refs else []
        data = b"\n".join(objs[n][1] or b"" for n in nums if n in objs)
        pages.append({"page": len(pages) + 1, "data": data,
                      "box": tuple(float(v) for v in box.group(1).split()) if box else default_box,
                      "fonts": {}, "alphas": alphas})
    if not any(p["data"] for p in pages):
        pages = [{"page": 0, "data": s, "box": default_box, "fonts": {}, "alphas": alphas}
                 for _, (h, s) in objs.items() if s and b"BT" in s]
    return pages


def parse_tounicode(data: bytes) -> dict[bytes, str]:
    """bfchar and bfrange entries of a ToUnicode CMap: code bytes -> text."""
    out: dict[bytes, str] = {}

    def uni(hexstr: bytes) -> str:
        try:
            return bytes.fromhex(hexstr.decode()).decode("utf-16-be", errors="ignore")
        except ValueError:
            return ""

    for block in re.findall(rb"beginbfchar(.*?)endbfchar", data, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            out[bytes.fromhex(src.decode())] = uni(dst)
    for block in re.findall(rb"beginbfrange(.*?)endbfrange", data, re.S):
        for lo, hi, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]+>|\[[^\]]*\])", block):
            a, b, width = int(lo, 16), int(hi, 16), len(lo) // 2
            if b - a > 5000:
                continue
            if dst.startswith(b"["):
                targets = [uni(t) for t in re.findall(rb"<([0-9A-Fa-f]+)>", dst)]
            else:
                start = int(dst[1:-1], 16)
                targets = [chr(start + i) if start + i < 0x110000 else "" for i in range(b - a + 1)]
            for i, t in enumerate(targets[:b - a + 1]):
                out[(a + i).to_bytes(width, "big")] = t
    return out


def decode(raw: bytes, cmap: dict[bytes, str] | None) -> str:
    if cmap:
        widths = sorted({len(k) for k in cmap}, reverse=True)
        out, i = [], 0
        while i < len(raw):
            for w in widths:
                if raw[i:i + w] in cmap:
                    out.append(cmap[raw[i:i + w]])
                    i += w
                    break
            else:
                out.append(chr(raw[i]) if 32 <= raw[i] < 127 else "")
                i += 1
        return "".join(out)
    return "".join(chr(b) if 32 <= b < 127 or 160 <= b else "" for b in raw)


# Tokenizer and interpreter ------------------------------------------------------------

DELIM = b"()<>[]{}/%"
SPACE = b" \t\r\n\f\x00"


def tokens(data: bytes):
    """(operator, operands) for each operator in a content stream."""
    i, n, stack = 0, len(data), [[]]
    while i < n:
        c = data[i]
        if c in SPACE:
            i += 1
        elif c == ord("%"):
            while i < n and data[i] not in b"\r\n":
                i += 1
        elif c == ord("("):
            depth, j, buf = 1, i + 1, bytearray()
            while j < n and depth:
                ch = data[j]
                if ch == ord("\\") and j + 1 < n:
                    nxt = data[j + 1]
                    esc = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}
                    if nxt in esc:
                        buf.append(esc[nxt])
                        j += 2
                    elif 48 <= nxt <= 55:
                        m = re.match(rb"[0-7]{1,3}", data[j + 1:j + 4])
                        buf.append(int(m.group(0), 8) & 255)
                        j += 1 + len(m.group(0))
                    elif nxt in b"\r\n":
                        j += 2
                    else:
                        buf.append(nxt)
                        j += 2
                    continue
                if ch == ord("("):
                    depth += 1
                elif ch == ord(")"):
                    depth -= 1
                    if not depth:
                        break
                buf.append(ch)
                j += 1
            stack[-1].append(bytes(buf))
            i = j + 1
        elif data[i:i + 2] == b"<<":
            stack.append([])
            i += 2
        elif data[i:i + 2] == b">>":
            inner = stack.pop() if len(stack) > 1 else []
            stack[-1].append({"dict": inner})
            i += 2
        elif c == ord("<"):
            j = data.find(b">", i)
            j = n if j < 0 else j
            hexs = re.sub(rb"\s", b"", data[i + 1:j])
            stack[-1].append(bytes.fromhex((hexs + b"0" * (len(hexs) % 2)).decode(errors="ignore") or ""))
            i = j + 1
        elif c == ord("["):
            stack.append([])
            i += 1
        elif c == ord("]"):
            inner = stack.pop() if len(stack) > 1 else []
            stack[-1].append(inner)
            i += 1
        elif c == ord("/"):
            j = i + 1
            while j < n and data[j] not in SPACE and data[j] not in DELIM:
                j += 1
            stack[-1].append(("name", data[i + 1:j].decode("latin-1")))
            i = j
        else:
            j = i
            while j < n and data[j] not in SPACE and data[j] not in DELIM:
                j += 1
            word = data[i:j] or data[i:i + 1]
            i = j if j > i else i + 1
            try:
                stack[-1].append(float(word))
                continue
            except ValueError:
                pass
            op = word.decode("latin-1")
            if op == "ID":  # inline image data runs to EI
                end = re.compile(rb"\sEI(?=[\s]|$)").search(data, i)
                i = end.end() if end else n
                stack = [[]]
                continue
            yield op, stack[0] if len(stack) == 1 else []
            stack = [[]]


def mul(m1, m2):
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (a1 * a2 + b1 * c2, a1 * b2 + b1 * d2, c1 * a2 + d1 * c2, c1 * b2 + d1 * d2,
            e1 * a2 + f1 * c2 + e2, e1 * b2 + f1 * d2 + f2)


IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def interpret(data: bytes, page: int, box, fonts: dict, alphas: dict) -> list[dict]:
    """Every text-showing operator in a content stream with the state it is drawn in."""
    g = {"ctm": IDENTITY, "fill": (0.0, 0.0, 0.0), "alpha": 1.0, "render": 0, "font": "", "size": 0.0, "leading": 0.0}
    saved: list[dict] = []
    tm = tlm = IDENTITY
    pieces = []

    def nums(args):
        return [a for a in args if isinstance(a, float)]

    for op, args in tokens(data):
        v = nums(args)
        if op == "q":
            saved.append(dict(g))
        elif op == "Q":
            if saved:
                g = saved.pop()
        elif op == "cm" and len(v) == 6:
            g["ctm"] = mul(tuple(v), g["ctm"])
        elif op in ("rg", "g", "k", "sc", "scn") and v:
            if not any(isinstance(a, tuple) for a in args):
                g["fill"] = to_rgb(v[-4:] if op == "k" else v) or g["fill"]
        elif op == "cs":
            g["fill"] = (0.0, 0.0, 0.0)
        elif op == "gs" and args and isinstance(args[0], tuple):
            g["alpha"] = alphas.get(args[0][1], g["alpha"])
        elif op == "BT":
            tm = tlm = IDENTITY
        elif op == "Tf" and v:
            g["size"] = v[-1]
            names = [a for a in args if isinstance(a, tuple)]
            g["font"] = names[0][1] if names else ""
        elif op == "Tr" and v:
            g["render"] = int(v[0])
        elif op == "TL" and v:
            g["leading"] = v[0]
        elif op in ("Td", "TD") and len(v) == 2:
            if op == "TD":
                g["leading"] = -v[1]
            tlm = mul((1, 0, 0, 1, v[0], v[1]), tlm)
            tm = tlm
        elif op == "Tm" and len(v) == 6:
            tm = tlm = tuple(v)
        elif op in ("T*", "'", '"'):
            tlm = mul((1, 0, 0, 1, 0, -g["leading"]), tlm)
            tm = tlm
        if op in ("Tj", "TJ", "'", '"'):
            strings = []
            for a in args:
                if isinstance(a, bytes):
                    strings.append(decode(a, fonts.get(g["font"])))
                elif isinstance(a, list):
                    for item in a:
                        if isinstance(item, bytes):
                            strings.append(decode(item, fonts.get(g["font"])))
                        elif isinstance(item, float) and item < -200:
                            strings.append(" ")
            m = mul(tm, g["ctm"])
            size = abs(g["size"]) * math.sqrt(abs(m[0] * m[3] - m[1] * m[2]))
            text = "".join(strings)
            reasons = reasons_for(g["fill"], g["alpha"], g["render"], size, m[4], m[5], box)
            pieces.append({"page": page, "reasons": reasons, "text": text, "x": round(m[4], 1),
                           "y": round(m[5], 1), "size": round(size, 2)})
    return pieces
