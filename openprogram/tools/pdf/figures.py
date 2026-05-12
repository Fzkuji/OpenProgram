"""figures — Python port of the PDFFigures2 algorithm (Clark & Divvala 2016).

Why this module exists
----------------------
The existing ``pdf.py`` image extractor walks the PDF's raster ``xref``
table and dumps every embedded bitmap. That fails badly on academic
papers because the interesting figures are *vector* graphics — clusters
of paths, rectangles, and short text labels rather than a single
embedded PNG. A caption-anchored heuristic (find "Figure N:" then grab
the rectangle above) is fragile: multi-panel figures with a column-
title row look like body text to such a heuristic, two stacked figures
on one page get conflated, and tables vs. figures aren't distinguished.

PDFFigures2's core idea, ported here:

    Don't try to identify where the figure IS. Identify where body
    text IS. Whatever rectangle on the page is (a) free of body text,
    (b) bounded above/below by body-text blocks or page edges, (c)
    contains some graphical content, and (d) sits in the column of a
    caption — that rectangle IS the figure.

Algorithm steps (numbered to match the docstring of ``extract_figures``):

1. **Extract text spans with font metadata** via ``page.get_text("dict")``.
2. **Identify the body-text font family**: cluster spans by (size, name),
   pick the dominant cluster by total character count; accept anything
   within 0.5pt of that size as part of the body family.
3. **Detect captions** by regex ``^(Figure|Table|Fig\\.|Tab\\.)\\s+\\d+[:.|]``
   on the first line of a text block, where the font differs from the
   body family or is bold. Extend the caption bbox downward across
   consecutive same-font lines until the run ends.
4. **Build body-text mask** as the union of body-family span bboxes.
5. **Collect graphical content**: vector drawings (``page.get_drawings``),
   raster images (``page.get_image_rects``), and non-body / non-caption
   text spans (axis labels, panel titles, in-figure annotations).
6. **For each caption, solve for the figure region**: search the column
   strip directly above (and as fallback, below) the caption, find the
   largest rectangle that doesn't intersect body text and contains some
   graphical content. Column boundaries detected by finding vertical
   whitespace gutters in the body-text mask.
7. **Resolve conflicts** by processing captions top-to-bottom and treating
   already-claimed regions as occupied.
8. **Render & emit** each region with ``page.get_pixmap(clip=...)``.

Public surface: :class:`ExtractedFigure` and :func:`extract_figures`.
Everything else is private (underscore-prefixed).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    import fitz  # type: ignore
except ImportError:  # pragma: no cover
    fitz = None  # type: ignore


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class ExtractedFigure:
    """One detected figure / table.

    Attributes
    ----------
    figure_label : str
        Normalized label, e.g. ``"Figure 1"`` or ``"Table 3"``.
    page : int
        1-indexed page number.
    bbox : (x0, y0, x1, y1)
        Bounding box in PDF points, including caption if requested.
    image_path : Path
        Absolute path to the rendered PNG.
    caption_text : str
        Full concatenated caption text (may be multi-line).
    """

    figure_label: str
    page: int
    bbox: tuple[float, float, float, float]
    image_path: Path
    caption_text: str


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CAPTION_RE = re.compile(r"^\s*(Figure|Table|Fig\.|Tab\.)\s+(\d+)\s*[:.\|]", re.IGNORECASE)
_LABEL_NORMALIZE = {
    "fig.": "Figure",
    "fig": "Figure",
    "figure": "Figure",
    "tab.": "Table",
    "tab": "Table",
    "table": "Table",
}
_BODY_SIZE_TOLERANCE = 0.5  # pt
_CAPTION_LINE_GAP = 1.6  # multiples of line height
_MARGIN = 4.0  # pt
_MIN_FIG_DIM = 30.0  # pt — anything smaller is too tiny to be a real figure
_COLUMN_GUTTER_MIN_WIDTH = 12.0  # pt
_BOLD_FLAG = 1 << 4  # MuPDF font flag bit for bold


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _iter_spans(page) -> Iterable[dict]:
    """Yield every text span on the page as a dict augmented with line bbox."""
    d = page.get_text("dict")
    for block in d.get("blocks", []):
        if block.get("type", 0) != 0:  # text blocks only
            continue
        for line in block.get("lines", []):
            line_bbox = line.get("bbox")
            for span in line.get("spans", []):
                yield {
                    "bbox": tuple(span["bbox"]),
                    "text": span.get("text", ""),
                    "size": round(float(span.get("size", 0.0)), 2),
                    "font": span.get("font", ""),
                    "flags": int(span.get("flags", 0)),
                    "line_bbox": tuple(line_bbox) if line_bbox else tuple(span["bbox"]),
                    "block_no": block.get("number", 0),
                }


def _identify_body_family(spans: list[dict]) -> tuple[set[tuple[float, str]], float]:
    """Return (font_family_keys, dominant_size).

    Cluster by (size, font); dominant cluster wins by total char count.
    Then accept any (size, font) whose size is within tolerance of the
    dominant size as part of the body family. We keep the font name in
    the key so that bold/italic variants are excluded.
    """
    counter: Counter[tuple[float, str]] = Counter()
    for s in spans:
        text = s["text"].strip()
        if not text:
            continue
        counter[(s["size"], s["font"])] += len(text)
    if not counter:
        return set(), 0.0
    (dom_size, dom_font), _ = counter.most_common(1)[0]
    family: set[tuple[float, str]] = set()
    for (sz, fn), _cnt in counter.items():
        if abs(sz - dom_size) <= _BODY_SIZE_TOLERANCE and fn == dom_font:
            family.add((sz, fn))
    # also accept the same font at a slightly different size (math runs)
    for (sz, fn), _cnt in counter.items():
        if fn == dom_font and abs(sz - dom_size) <= _BODY_SIZE_TOLERANCE * 2:
            family.add((sz, fn))
    return family, dom_size


def _is_caption_first_line(text: str) -> re.Match | None:
    return _CAPTION_RE.match(text)


def _bbox_union(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _bbox_intersects(a, b) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _bbox_area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _group_lines(spans: list[dict]) -> list[dict]:
    """Group spans into lines keyed by their (block, line_bbox)."""
    by_line: dict[tuple[int, tuple], dict] = {}
    for s in spans:
        key = (s["block_no"], s["line_bbox"])
        if key not in by_line:
            by_line[key] = {
                "bbox": s["line_bbox"],
                "text": "",
                "size": s["size"],
                "font": s["font"],
                "flags": s["flags"],
                "block_no": s["block_no"],
                "spans": [],
            }
        by_line[key]["spans"].append(s)
        by_line[key]["text"] += s["text"]
    out = list(by_line.values())
    out.sort(key=lambda l: (l["bbox"][1], l["bbox"][0]))
    return out


def _detect_captions(
    lines: list[dict],
    body_family: set[tuple[float, str]],
    dom_size: float,
) -> list[dict]:
    """Return list of caption dicts: {label, number, text, bbox}."""
    captions: list[dict] = []
    used: set[int] = set()
    # Index lines by block; a caption must be the FIRST line of its text block,
    # so we don't fire on inline mentions like "...as shown in Figure 3: ...".
    block_first_line: dict[int, int] = {}
    for idx, ln in enumerate(lines):
        bn = ln["block_no"]
        if bn not in block_first_line:
            block_first_line[bn] = idx
    for i, line in enumerate(lines):
        if i in used:
            continue
        m = _is_caption_first_line(line["text"])
        if not m:
            continue
        # Must be the first line of its block (filters inline references).
        if block_first_line.get(line["block_no"]) != i:
            continue
        # Extend downward across same-font / same-block adjacent lines.
        bbox = line["bbox"]
        text = line["text"]
        line_height = max(1.0, bbox[3] - bbox[1])
        used.add(i)
        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            gap = nxt["bbox"][1] - bbox[3]
            same_block = nxt["block_no"] == line["block_no"]
            same_fontish = nxt["font"] == line["font"] and abs(nxt["size"] - line["size"]) < 0.3
            if gap > _CAPTION_LINE_GAP * line_height:
                break
            if not (same_block or same_fontish):
                break
            # stop if this next line itself starts a new caption
            if _is_caption_first_line(nxt["text"]):
                break
            bbox = _bbox_union(bbox, nxt["bbox"])
            text += " " + nxt["text"]
            used.add(j)
            j += 1
        kind = _LABEL_NORMALIZE.get(m.group(1).lower(), m.group(1).title())
        number = m.group(2)
        captions.append(
            {
                "label": f"{kind} {number}",
                "kind": kind,
                "number": int(number),
                "text": text.strip(),
                "bbox": bbox,
            }
        )
    return captions


def _detect_columns(page_rect, body_boxes: list[tuple]) -> list[tuple[float, float]]:
    """Find column x-ranges by detecting vertical whitespace gutters.

    Rasterize the body-text mask onto a 1D histogram over x at 1pt
    resolution; a gutter is a contiguous run of zero columns at least
    ``_COLUMN_GUTTER_MIN_WIDTH`` pt wide that sits inside the page
    margins.
    """
    x0, _, x1, _ = page_rect
    width = int(x1 - x0)
    if width <= 0:
        return [(x0, x1)]
    hist = [0] * (width + 1)
    for b in body_boxes:
        bx0 = max(int(b[0] - x0), 0)
        bx1 = min(int(b[2] - x0), width)
        for x in range(bx0, bx1 + 1):
            hist[x] += 1
    # find left/right text extent (skip page margins)
    left = next((i for i, v in enumerate(hist) if v > 0), 0)
    right = next((i for i in range(len(hist) - 1, -1, -1) if hist[i] > 0), width)
    # find gutters inside [left, right]
    gutters: list[tuple[int, int]] = []
    run_start = None
    for i in range(left, right + 1):
        if hist[i] == 0:
            if run_start is None:
                run_start = i
        else:
            if run_start is not None and (i - run_start) >= _COLUMN_GUTTER_MIN_WIDTH:
                gutters.append((run_start, i))
            run_start = None
    # columns are the spans between gutters
    cols: list[tuple[float, float]] = []
    cursor = left
    for g0, g1 in gutters:
        cols.append((x0 + cursor, x0 + g0))
        cursor = g1
    cols.append((x0 + cursor, x0 + right))
    if not cols:
        cols = [(x0, x1)]
    # Sanity: if any column is absurdly narrow (< 60pt), the page probably
    # has too little body text for histogram-based detection to work.
    # Fall back to a single full-width column.
    if any((c[1] - c[0]) < 60 for c in cols):
        return [(x0, x1)]
    return cols


def _column_for(bbox, columns: list[tuple[float, float]]) -> tuple[float, float]:
    """Return the column (x_lo, x_hi) that best contains the caption bbox."""
    cx = 0.5 * (bbox[0] + bbox[2])
    for c in columns:
        if c[0] - 2 <= cx <= c[1] + 2:
            return c
    # caption spans multiple columns (wide figure) — return the union
    matching = [c for c in columns if not (c[1] < bbox[0] or c[0] > bbox[2])]
    if matching:
        return (min(c[0] for c in matching), max(c[1] for c in matching))
    return (bbox[0], bbox[2])


def _graphical_content_bboxes(page, body_family, body_boxes) -> list[tuple]:
    """Vector drawings + raster image rects + non-body / non-caption text."""
    out: list[tuple] = []
    try:
        for d in page.get_drawings():
            r = d.get("rect")
            if r is None:
                continue
            out.append(tuple(r))
    except Exception:
        pass
    try:
        for img in page.get_images(full=True):
            xref = img[0]
            try:
                for r in page.get_image_rects(xref):
                    out.append(tuple(r))
            except Exception:
                continue
    except Exception:
        pass
    return out


def _has_graphical(region, graphical: list[tuple]) -> bool:
    for g in graphical:
        if _bbox_intersects(region, g):
            return True
    return False


def _clear_bands(
    column: tuple[float, float],
    search_y0: float,
    search_y1: float,
    body_boxes: list[tuple],
    occupied: list[tuple],
) -> list[tuple[float, float]]:
    """All vertical clear bands inside (column, [search_y0, search_y1]).

    Returned in top-to-bottom order. Caller picks which one to use.
    """
    cx0, cx1 = column
    obstructions: list[tuple[float, float]] = []
    for b in body_boxes + occupied:
        if b[2] <= cx0 or b[0] >= cx1:
            continue
        y0 = max(b[1], search_y0)
        y1 = min(b[3], search_y1)
        if y1 <= y0:
            continue
        obstructions.append((y0, y1))
    obstructions.sort()
    bands: list[tuple[float, float]] = []
    cursor = search_y0
    for o0, o1 in obstructions:
        if o0 > cursor:
            bands.append((cursor, o0))
        cursor = max(cursor, o1)
    if search_y1 > cursor:
        bands.append((cursor, search_y1))
    return [b for b in bands if (b[1] - b[0]) >= _MIN_FIG_DIM]


def _solve_figure_region(
    caption: dict,
    page_rect,
    columns: list[tuple[float, float]],
    body_boxes: list[tuple],
    graphical: list[tuple],
    occupied: list[tuple],
) -> tuple[float, float, float, float] | None:
    """Find the figure rectangle for one caption.

    Tries above the caption first, then below.
    """
    col = _column_for(caption["bbox"], columns)
    cap_top = caption["bbox"][1]
    cap_bot = caption["bbox"][3]
    page_top = page_rect[1]
    page_bot = page_rect[3]
    is_table = caption["kind"].lower().startswith("tab")

    def _pick(col_, search_y0, search_y1, prefer_near_top: bool):
        """Among clear bands with graphical content, pick the one closest
        to the caption (i.e., near the bottom of an above-search range,
        or near the top of a below-search range)."""
        bands = _clear_bands(col_, search_y0, search_y1, body_boxes, occupied)
        if not bands:
            return None
        # Iterate in the direction "closest to caption" first
        ordered = bands if prefer_near_top else list(reversed(bands))
        for b in ordered:
            region = (col_[0], b[0], col_[1], b[1])
            if _has_graphical(region, graphical):
                return region
        return None

    # Tables: caption is usually ABOVE the table content -> search below first.
    # Figures: caption is usually BELOW the figure content -> search above first.
    if is_table:
        first = _pick(col, cap_bot, page_bot, prefer_near_top=True)
        if first:
            return first
        second = _pick(col, page_top, cap_top, prefer_near_top=False)
        if second:
            return second
    else:
        first = _pick(col, page_top, cap_top, prefer_near_top=False)
        if first:
            return first
        second = _pick(col, cap_bot, page_bot, prefer_near_top=True)
        if second:
            return second

    # Full-width fallback
    full_col = (page_rect[0], page_rect[2])
    if is_table:
        r = _pick(full_col, cap_bot, page_bot, prefer_near_top=True)
        if r:
            return r
        return _pick(full_col, page_top, cap_top, prefer_near_top=False)
    else:
        r = _pick(full_col, page_top, cap_top, prefer_near_top=False)
        if r:
            return r
        return _pick(full_col, cap_bot, page_bot, prefer_near_top=True)


def _tighten_to_content(
    region: tuple[float, float, float, float],
    graphical: list[tuple],
    caption_bbox: tuple,
) -> tuple[float, float, float, float]:
    """Shrink region horizontally / vertically to the union of graphical
    content inside it, plus the caption bbox.
    """
    inside = [g for g in graphical if _bbox_intersects(region, g)]
    if not inside:
        return region
    gx0 = min(g[0] for g in inside)
    gy0 = min(g[1] for g in inside)
    gx1 = max(g[2] for g in inside)
    gy1 = max(g[3] for g in inside)
    # clip to region
    gx0 = max(gx0, region[0])
    gy0 = max(gy0, region[1])
    gx1 = min(gx1, region[2])
    gy1 = min(gy1, region[3])
    # union with caption bbox if it's adjacent
    if caption_bbox is not None:
        gx0 = min(gx0, caption_bbox[0])
        gx1 = max(gx1, caption_bbox[2])
        gy0 = min(gy0, caption_bbox[1])
        gy1 = max(gy1, caption_bbox[3])
    return (gx0, gy0, gx1, gy1)


def _slugify(label: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", label.lower()).strip("_")
    return s or "fig"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def extract_figures(
    pdf_path: str | Path,
    out_dir: str | Path,
    *,
    dpi: int = 300,
    include_caption: bool = True,
    pages: tuple[int, int] | None = None,
) -> list[ExtractedFigure]:
    """Extract figures and tables from a PDF using the PDFFigures2 approach.

    Parameters
    ----------
    pdf_path : path-like
        Source PDF.
    out_dir : path-like
        Directory to write rendered PNGs into. Created if missing.
    dpi : int, default 300
        Render resolution.
    include_caption : bool, default True
        If True, expand each region downward (or upward) to include the
        caption bbox in the rendered crop.
    pages : (start, end) 1-indexed inclusive, optional
        Restrict extraction to a page range.

    Returns
    -------
    list[ExtractedFigure]
        One entry per detected figure / table, sorted by (page, y).
    """
    if fitz is None:
        raise ImportError("pymupdf is required: pip install pymupdf")

    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(pdf_path))
    n_pages = len(doc)
    if pages is None:
        p_start, p_end = 1, n_pages
    else:
        p_start = max(1, pages[0])
        p_end = min(n_pages, pages[1])

    results: list[ExtractedFigure] = []
    fig_counter = 0

    for page_idx in range(p_start - 1, p_end):
        page = doc[page_idx]
        page_rect = tuple(page.rect)

        # Step 1
        spans = list(_iter_spans(page))
        if not spans:
            continue

        # Step 2
        body_family, dom_size = _identify_body_family(spans)
        body_boxes = [s["bbox"] for s in spans if (s["size"], s["font"]) in body_family]

        # Step 3
        lines = _group_lines(spans)
        captions = _detect_captions(lines, body_family, dom_size)
        if not captions:
            continue

        # Step 4 — body_boxes already computed.
        # Subtract caption bboxes from body_boxes (captions shouldn't block themselves).
        caption_bboxes = [c["bbox"] for c in captions]
        body_boxes = [
            b for b in body_boxes
            if not any(_bbox_intersects(b, cb) for cb in caption_bboxes)
        ]

        # Step 5
        graphical = _graphical_content_bboxes(page, body_family, body_boxes)
        # add non-body / non-caption text spans as graphical labels
        for s in spans:
            if (s["size"], s["font"]) in body_family:
                continue
            if any(_bbox_intersects(s["bbox"], cb) for cb in caption_bboxes):
                continue
            graphical.append(s["bbox"])

        # column detection
        columns = _detect_columns(page_rect, body_boxes)

        # Step 6+7 — solve top-to-bottom, building occupied set
        captions.sort(key=lambda c: c["bbox"][1])
        occupied: list[tuple] = []
        page_results: list[tuple[dict, tuple]] = []
        for cap in captions:
            occ_with_caps = occupied + [c["bbox"] for c in captions]
            region = _solve_figure_region(
                cap, page_rect, columns, body_boxes, graphical, occ_with_caps
            )
            if region is None:
                continue
            region = _tighten_to_content(region, graphical, None)
            # validate
            if (region[2] - region[0]) < _MIN_FIG_DIM or (region[3] - region[1]) < _MIN_FIG_DIM:
                continue
            occupied.append(region)
            page_results.append((cap, region))

        # Step 8 — render
        for cap, region in page_results:
            if include_caption:
                final = _bbox_union(region, cap["bbox"])
            else:
                final = region
            # apply small margin, clip to page
            final = (
                max(page_rect[0], final[0] - _MARGIN),
                max(page_rect[1], final[1] - _MARGIN),
                min(page_rect[2], final[2] + _MARGIN),
                min(page_rect[3], final[3] + _MARGIN),
            )
            fig_counter += 1
            slug = _slugify(cap["label"])
            fname = f"p{page_idx + 1:02d}_{slug}.png"
            out_path = out_dir / fname
            try:
                clip = fitz.Rect(*final)
                mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
                pix = page.get_pixmap(clip=clip, matrix=mat, alpha=False)
                pix.save(str(out_path))
            except Exception:
                continue
            results.append(
                ExtractedFigure(
                    figure_label=cap["label"],
                    page=page_idx + 1,
                    bbox=tuple(final),
                    image_path=out_path.resolve(),
                    caption_text=cap["text"],
                )
            )

    doc.close()
    return results


__all__ = ["ExtractedFigure", "extract_figures"]
