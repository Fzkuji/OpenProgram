"""extract_pdf_figures — crop figures out of any PDF, LLM-guided.

Locating a figure with pure geometry is a heuristic tower — caption
anchoring, body-text masks, clear-band search, column gutters. It
loses on the cases that matter most:

* a figure that is one embedded raster image vs. a figure that is a
  soup of hundreds of vector paths (or a nested PDF / form XObject) —
  geometry has to special-case each;
* deciding the crop boundary — figure only, or figure plus its
  caption paragraph;
* single- vs. two-column papers — column width has to be guessed
  before a figure's extent can be placed.

This function sidesteps all of it. Each page is *rendered to a
bitmap* and handed to a vision LLM. On pixels it no longer matters
whether a figure is raster or vector or nested — it is just pixels —
and the model sees the real column layout directly. The model returns
each figure's bounding box (and its caption's box separately); the
function maps those back to PDF coordinates and crops the page at high
resolution.

Geometry is used only where it is exact: rendering, and the linear
pixel-to-point coordinate map.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from openprogram.agentic_programming import agentic_function, llm

# Page-preview resolution shown to the model, and the (higher) crop
# resolution used for the saved figure PNGs.
_PREVIEW_DPI = 144
_CROP_DPI = 220
_PAD_PT = 3.0          # padding added around a figure box (points)
_MIN_FIG_PT = 24.0     # ignore boxes smaller than this on either side

_PROMPT = (
    "The image is one page of a PDF, {w} x {h} pixels.\n\n"
    "Find every FIGURE on this page — charts, plots, diagrams, "
    "photographs, schematics, rendered images. Do NOT report tables, "
    "body paragraphs, page headers/footers, equations, or page "
    "numbers.\n\n"
    "Treat a multi-panel figure that shares one caption (panels a, b, "
    "c ...) as a SINGLE figure with one bounding box covering all "
    "panels.\n\n"
    "Reply with ONLY a JSON array, no prose. Each element:\n"
    '  {{"label": "Figure 1",\n'
    '    "caption": "full caption text, or empty string",\n'
    '    "figure_bbox": [x0, y0, x1, y1],\n'
    '    "caption_bbox": [x0, y0, x1, y1] or null}}\n\n'
    "Coordinates are pixels in THIS image, origin at the top-left, "
    "[x0,y0] = top-left corner, [x1,y1] = bottom-right. figure_bbox "
    "covers the graphic only (no caption). caption_bbox covers the "
    "'Figure N: ...' text block, or null if there is no caption.\n"
    "If the page has no figure, reply with exactly: []"
)


def _parse_pages(spec: str, n_pages: int) -> tuple[int, int]:
    spec = (spec or "").strip()
    if not spec:
        return (1, n_pages)
    if "-" in spec:
        lo, _, hi = spec.partition("-")
        return (int(lo or 1), int(hi or n_pages))
    p = int(spec)
    return (p, p)


def _parse_json_array(reply: str) -> list[dict]:
    """Tolerantly pull a JSON array out of an LLM reply."""
    text = reply.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def _slug(label: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", str(label).lower()).strip("_")
    return s or "figure"


@agentic_function(input={
    "pdf_path": {
        "description": "Absolute path to the .pdf file to extract figures from.",
        "placeholder": "/Users/me/papers/example.pdf",
    },
    "pages": {
        "description": "Optional 1-based page range, e.g. '3' or '2-7'. "
                        "Empty means the whole document.",
        "placeholder": "2-7",
    },
    "include_caption": {
        "description": "Crop the caption paragraph together with the figure "
                        "(true) or the graphic alone (false).",
    },
    "out_dir": {
        "description": "Directory for the cropped PNGs. Empty = a "
                        "'<pdfname>_figures' folder beside the PDF.",
        "placeholder": "/Users/me/papers/example_figures",
    },
})
def extract_pdf_figures(
    pdf_path: str,
    pages: str = "",
    include_caption: bool = True,
    out_dir: str = "",
) -> list[dict]:
    """Crop every figure out of a PDF, one vision-LLM pass per page.

    Robust to raster vs. vector vs. nested-PDF figures (the page is
    rasterised before the model sees it) and to single- and two-column
    layouts (the model reads the real layout). ``include_caption``
    chooses whether the caption paragraph is cropped in with the
    graphic. Each result dict is ``{"page", "label", "caption",
    "image_path"}`` in page order; the PNGs are written to ``out_dir``.
    """
    try:
        import pymupdf as fitz
    except ImportError as exc:
        raise ImportError(
            "pymupdf is unavailable in this installation; "
            "reinstall the complete OpenProgram release"
        ) from exc

    src = Path(pdf_path)
    if not src.is_absolute():
        raise ValueError(f"pdf_path must be absolute, got {pdf_path!r}")
    if not src.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    dst = Path(out_dir) if out_dir else src.with_name(f"{src.stem}_figures")
    dst.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(src))
    lo, hi = _parse_pages(pages, len(doc))
    preview_scale = _PREVIEW_DPI / 72.0
    px_to_pt = 72.0 / _PREVIEW_DPI

    results: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="pdf-figures-") as tmp:
        for page_idx in range(lo - 1, hi):
            page = doc[page_idx]
            page_no = page_idx + 1
            rect = page.rect

            # 1. Render the page preview the model reasons over.
            preview = Path(tmp) / f"page{page_no}.png"
            pix = page.get_pixmap(
                matrix=fitz.Matrix(preview_scale, preview_scale), alpha=False
            )
            pix.save(str(preview))

            # 2. Vision LLM → figure boxes (in preview pixels).
            prompt = _PROMPT.format(w=pix.width, h=pix.height)
            reply = llm([
                {"type": "text", "text": prompt},
                {"type": "image", "path": str(preview)},
            ])
            figures = _parse_json_array(str(reply))

            # 3. Map boxes back to PDF points and crop the page.
            for i, fig in enumerate(figures):
                fbox = fig.get("figure_bbox")
                if not (isinstance(fbox, list) and len(fbox) == 4):
                    continue
                x0, y0, x1, y1 = (float(v) * px_to_pt for v in fbox)
                if include_caption:
                    cbox = fig.get("caption_bbox")
                    if isinstance(cbox, list) and len(cbox) == 4:
                        cx0, cy0, cx1, cy1 = (float(v) * px_to_pt for v in cbox)
                        x0, y0 = min(x0, cx0), min(y0, cy0)
                        x1, y1 = max(x1, cx1), max(y1, cy1)
                # pad, normalise, clip to the page
                x0, x1 = sorted((x0, x1))
                y0, y1 = sorted((y0, y1))
                x0 = max(rect.x0, x0 - _PAD_PT)
                y0 = max(rect.y0, y0 - _PAD_PT)
                x1 = min(rect.x1, x1 + _PAD_PT)
                y1 = min(rect.y1, y1 + _PAD_PT)
                if (x1 - x0) < _MIN_FIG_PT or (y1 - y0) < _MIN_FIG_PT:
                    continue

                label = str(fig.get("label") or f"figure {i + 1}")
                fname = f"p{page_no:02d}_{_slug(label)}.png"
                out_path = dst / fname
                try:
                    crop = page.get_pixmap(
                        clip=fitz.Rect(x0, y0, x1, y1),
                        matrix=fitz.Matrix(_CROP_DPI / 72.0, _CROP_DPI / 72.0),
                        alpha=False,
                    )
                    crop.save(str(out_path))
                except Exception:
                    continue
                results.append({
                    "page": page_no,
                    "label": label,
                    "caption": str(fig.get("caption") or ""),
                    "image_path": str(out_path.resolve()),
                })

    doc.close()
    return results
