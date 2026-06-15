"""pdf_layout — faithful positioned-text rendering of PDF pages.

This module deliberately contains *no* table-detection heuristics.
Earlier attempts to algorithmically reconstruct tables (caption
anchoring, column gutters, row-gap cut-offs) worked for some layouts
and broke on the next one — every new paper template is a new edge
case.

The robust split of labour: do the part that is *reliable* here, and
leave the part that needs *judgement* to an LLM.

* Reliable, algorithmic — clustering a page's words into rows by their
  y-coordinate and laying them out at x-scaled positions into a
  fixed-width text block. The PDF text layer carries an exact bbox for
  every word, so this is lossless; columns end up visually aligned.

* Needs judgement — deciding which rows form a table, where a table
  starts and ends, separating a table from a neighbouring text column.
  That is what :mod:`openprogram.functions.agentics.extract_pdf_tables`
  hands to an LLM, which reasons about any layout instead of
  pattern-matching a fixed set of templates.

Public surface: :func:`page_layout`, :func:`render_page_png`,
:func:`pdf_pages`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

try:
    import fitz  # type: ignore
except ImportError:  # pragma: no cover
    fitz = None  # type: ignore

_ROW_Y_TOLERANCE = 4.0       # words within this Δy are treated as one row
_LAYOUT_TARGET_WIDTH = 110   # characters the widest page row maps to
# A page whose text layer yields fewer than this many words is treated
# as scanned / image-only — render it to a PNG for the LLM instead.
_MIN_WORDS_FOR_TEXT = 12


@dataclass
class PageLayout:
    """One page rendered for LLM consumption.

    Attributes
    ----------
    page : int
        1-indexed page number.
    text : str
        Words laid out as a fixed-width text block (empty if scanned).
    scanned : bool
        True when the page has no usable text layer; ``text`` is empty
        and the caller should fall back to :func:`render_page_png`.
    """

    page: int
    text: str
    scanned: bool


def page_layout(page) -> PageLayout:
    """Render one PyMuPDF page as fixed-width positioned text."""
    words = [w for w in page.get_text("words") if w[4].strip()]
    page_no = page.number + 1
    if len(words) < _MIN_WORDS_FOR_TEXT:
        return PageLayout(page=page_no, text="", scanned=True)

    words.sort(key=lambda w: (round(w[1], 1), w[0]))
    rows: list[list] = []
    for w in words:
        if rows and abs(w[1] - rows[-1][0]) < _ROW_Y_TOLERANCE:
            rows[-1][1].append(w)
        else:
            rows.append([w[1], [w]])

    x0 = min(w[0] for w in words)
    x1 = max(w[2] for w in words)
    scale = max((x1 - x0) / _LAYOUT_TARGET_WIDTH, 1.0)

    lines: list[str] = []
    for _y, row_words in rows:
        row_words.sort(key=lambda w: w[0])
        line = ""
        for w in row_words:
            col = int((w[0] - x0) / scale)
            if len(line) < col:
                line += " " * (col - len(line))
            elif line and not line.endswith(" "):
                line += " "
            line += w[4]
        lines.append(line.rstrip())
    return PageLayout(page=page_no, text="\n".join(lines), scanned=False)


def render_page_png(page, out_path: str | Path, *, dpi: int = 150) -> Path:
    """Render a page to a PNG (used for scanned / image-only pages)."""
    if fitz is None:
        raise ImportError("pymupdf is required: pip install pymupdf")
    out_path = Path(out_path)
    mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    pix.save(str(out_path))
    return out_path.resolve()


def pdf_pages(
    pdf_path: str | Path,
    *,
    pages: tuple[int, int] | None = None,
) -> list[PageLayout]:
    """Render a PDF (or page range) as a list of :class:`PageLayout`."""
    if fitz is None:
        raise ImportError("pymupdf is required: pip install pymupdf")
    doc = fitz.open(str(pdf_path))
    n = len(doc)
    if pages is None:
        lo, hi = 1, n
    else:
        lo, hi = max(1, pages[0]), min(n, pages[1])
    out = [page_layout(doc[i]) for i in range(lo - 1, hi)]
    doc.close()
    return out


__all__ = ["PageLayout", "page_layout", "render_page_png", "pdf_pages"]
