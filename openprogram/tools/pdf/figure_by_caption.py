"""figure_by_caption — caption-anchored PDF figure extraction.

A stable, regex-anchored heuristic for cropping individual figures from
academic PDFs by their caption text. Tuned for two-column / mixed
NeurIPS / arXiv-style layouts and validated on the DeepSeek-V4 and
LLM-Uncertainty papers (9 / 9 figures clean on the latter).

How it works
------------
Academic figures are vector graphics + text, so the raster ``xref``
table that ``pymupdf`` exposes via ``page.get_images()`` returns at best
a logo or a legend swatch. This module instead:

1. Locates the caption text block via a caller-supplied prefix (e.g.
   ``"Figure 2:"``). The first match across pages wins; a 1-indexed
   ``page_hint`` lets the caller short-circuit the scan.
2. Walks UP from the caption to find the previous *real body block* —
   either a paragraph (≥ 30 chars, sentence terminator, column-spanning
   width) or a section heading (short capitalised title, also
   column-spanning). Sub-figure captions like ``"(a) ..."``, axis
   ticks, narrow in-figure legend titles, and other figure labels are
   all *skipped* via dedicated filters.
3. Absorbs paragraph-continuation lines downward from the found body
   block. PyMuPDF often splits one paragraph into per-line blocks; the
   first found body block is only the FIRST line, so we walk down to
   find the actual paragraph bottom.
4. Treats any other ``Figure N:`` caption on the same page as a hard
   vertical boundary — this prevents Figure 3 from bleeding into the
   top of Figure 4 when both share a page.
5. Conservatively accumulates caption-continuation lines below the
   caption itself: small vertical gap (≤ 8 pt), inside caption x-range,
   contains lowercase letters (excludes panel-title rows like
   ``"Base Vanilla K-SFT OP-SFT+KΔ-FT"``), no crossing into the next
   figure.
6. Renders the resulting bbox via ``page.get_pixmap(clip=...)`` at
   user-specified DPI.

Honest limitations
------------------
* Cannot handle *wrapfigure* layouts where body text flows around an
  inset figure. The above-caption search will stop at the first body
  paragraph it sees, which on a wrapfigure page is the prose left of
  the figure — so the resulting bbox is too short.
* Requires the caption text to be a clean line at the top of a
  pymupdf text block; if the renderer fused the caption with
  surrounding lines, the regex anchor fails.
* All thresholds are tuned for ~10pt body text papers; very small or
  very large text bodies may need parameter adjustment.

For batch extraction on arbitrary papers without per-caller calibration
consider :mod:`openprogram.tools.pdf.figures` (experimental PDFFigures2
port), or shell out to the Allen AI Scala ``pdffigures2.jar`` directly.

Usage
-----

Single figure::

    from openprogram.tools.pdf.figure_by_caption import extract_figure

    result = extract_figure(
        pdf_path="paper.pdf",
        caption_prefix="Figure 2:",
        out_path="figures/fig2.png",
        page_hint=4,
    )
    print(result.page, result.bbox)

Batch::

    from openprogram.tools.pdf.figure_by_caption import extract_figures

    results = extract_figures(
        pdf_path="paper.pdf",
        captions=[
            ("Figure 1:", "fig1.png"),
            ("Figure 2:", "fig2.png"),
            ...
        ],
        out_dir="figures/",
    )
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CaptionFigureResult:
    """One extracted figure.

    Attributes
    ----------
    page : int
        1-indexed PDF page where the caption was found.
    bbox : tuple[float, float, float, float]
        ``(x0, y0, x1, y1)`` of the rendered region in PDF points,
        including caption if ``include_caption=True``.
    image_path : Path
        Absolute path to the rendered PNG.
    caption_prefix : str
        The prefix string the caller passed to anchor the search.
    """

    page: int
    bbox: tuple[float, float, float, float]
    image_path: Path
    caption_prefix: str


# ---------------------------------------------------------------------------
# Tunable constants — defaults that work on NeurIPS/arXiv ~10pt papers
# ---------------------------------------------------------------------------

_DEFAULT_DPI = 300
_DEFAULT_MARGIN_PT = 4.0
_DEFAULT_MAX_CAPTION_LINES = 6

# Minimum vertical distance from caption top to the previous "real
# body" block. Figures are typically ≥ this tall; below it, what we
# think is a body block is more likely an in-figure column header.
_MIN_FIG_HEIGHT_PT = 95.0

# Caption-continuation acceptance: gap to previous accepted line.
_CAPTION_CONTINUATION_GAP_PT = 8.0

# Body-block width thresholds (as fraction of caption block width).
_MIN_PARAGRAPH_WIDTH_FRAC = 0.55  # real prose paragraphs span most of column
_MIN_HEADING_WIDTH_FRAC = 0.40    # section headings span ≥ this much

# Regexes
_OTHER_FIG_PAT = re.compile(r"^\s*Figure\s+\d+[:.|]")
_SUBCAP_PAT = re.compile(r"^\s*\(?[a-z]\)\s+[A-Z]")  # "(a) Recovery ..."


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _looks_like_body(text: str, block_width: float, cap_width: float) -> bool:
    """Whether a text block represents body prose or a section heading.

    Used to find the upper boundary of a figure region by walking up
    from the caption. Rejects sub-figure captions, narrow in-figure
    labels, and short fragments.
    """
    t = text.strip()
    if len(t) < 5:
        return False
    if _SUBCAP_PAT.match(t):
        return False
    min_paragraph_width = cap_width * _MIN_PARAGRAPH_WIDTH_FRAC
    min_heading_width = cap_width * _MIN_HEADING_WIDTH_FRAC
    # Paragraph: prose with sentence terminator + column-spanning width.
    if (
        len(t) >= 30
        and any(p in t for p in (".", "!", "?", "。", "！", "？"))
        and block_width >= min_paragraph_width
    ):
        return True
    # Section heading: short capitalised title + meaningful column span.
    tokens = t.split()
    if (
        2 <= len(tokens) <= 12
        and sum(tok[0].isalpha() and tok[0].isupper() for tok in tokens) >= 2
        and block_width >= min_heading_width
    ):
        alpha_chars = sum(c.isalpha() for c in t)
        if alpha_chars >= len(t) * 0.5:
            return True
    return False


def _find_other_figure_boundaries(
    text_blocks: list, cap_idx: int, cap_y0: float
) -> tuple[float, float]:
    """For multi-figure pages, find y bounds set by other figure captions.

    Returns (prev_fig_y_max, next_fig_y_min): the bottom of the nearest
    other-figure caption above us, and the top of the nearest below us.
    Used as hard boundaries on the figure region.
    """
    prev_fig_y_max = 0.0
    next_fig_y_min = float("inf")
    for k, b in enumerate(text_blocks):
        if k == cap_idx:
            continue
        if not _OTHER_FIG_PAT.match(b[4].strip()):
            continue
        if b[3] < cap_y0:  # above our caption
            if b[3] > prev_fig_y_max:
                prev_fig_y_max = b[3]
        else:  # below our caption
            if b[1] < next_fig_y_min:
                next_fig_y_min = b[1]
    return prev_fig_y_max, next_fig_y_min


def _accumulate_caption_lines(
    text_blocks: list,
    cap_idx: int,
    cap_x0: float,
    cap_x1: float,
    cap_y1: float,
    next_fig_y_min: float,
    max_caption_lines: int,
) -> float:
    """Walk downward from the caption to absorb continuation lines.

    Returns the y-coordinate where the caption (possibly multi-line)
    ends.
    """
    cap_y_end = cap_y1
    added = 0
    for j in range(cap_idx + 1, len(text_blocks)):
        nb = text_blocks[j]
        nb_x0, nb_y0, nb_x1, nb_y1, nb_text, *_ = nb
        if nb_y0 > cap_y_end + _CAPTION_CONTINUATION_GAP_PT:
            break
        if nb_x0 < cap_x0 - 5 or nb_x1 > cap_x1 + 30:
            break
        if nb_y0 >= next_fig_y_min - 1:
            break
        nb_stripped = nb_text.strip()
        if _OTHER_FIG_PAT.match(nb_stripped):
            break
        # Real caption prose has lowercase letters; this filter rejects
        # all-caps panel-title rows like "Base Vanilla K-SFT".
        if not any(c.islower() for c in nb_stripped):
            break
        cap_y_end = nb_y1
        added += 1
        if added >= max_caption_lines:
            break
    return cap_y_end


def _find_prev_body_block(
    text_blocks: list, cap_idx: int, cap_x0: float, cap_x1: float, cap_y0: float
) -> float:
    """Walk UP from caption to find the previous body / heading block.

    Returns the y-bottom of that block (i.e., the proposed fig_top).
    """
    cap_width = cap_x1 - cap_x0
    body_idx = None
    prev_y_bottom = 0.0
    for j in range(cap_idx - 1, -1, -1):
        pb_x0, pb_y0, pb_x1, pb_y1, pb_text, *_ = text_blocks[j]
        if pb_x1 < cap_x0 - 5 or pb_x0 > cap_x1 + 5:
            continue
        if cap_y0 - pb_y1 < _MIN_FIG_HEIGHT_PT:
            continue
        pb_width = pb_x1 - pb_x0
        if not _looks_like_body(pb_text, pb_width, cap_width):
            continue
        body_idx = j
        prev_y_bottom = pb_y1
        break

    # PyMuPDF often splits one paragraph into per-line blocks. After
    # locating the first body line going up, walk back DOWN through
    # consecutive continuation lines of the same paragraph to find the
    # actual paragraph bottom.
    if body_idx is not None:
        cur_y_bottom = prev_y_bottom
        for k in range(body_idx + 1, cap_idx):
            nx0, ny0, nx1, ny1, ntext, *_ = text_blocks[k]
            if nx1 < cap_x0 - 5 or nx0 > cap_x1 + 5:
                continue
            if ny0 - cur_y_bottom > _CAPTION_CONTINUATION_GAP_PT:
                break
            if not any(c.isalpha() for c in ntext):
                break
            if _SUBCAP_PAT.match(ntext.strip()):
                break
            cur_y_bottom = ny1
        prev_y_bottom = cur_y_bottom

    return prev_y_bottom


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_figure(
    pdf_path: Path | str,
    caption_prefix: str,
    out_path: Path | str,
    *,
    include_caption: bool = True,
    dpi: int = _DEFAULT_DPI,
    page_hint: int | None = None,
    max_caption_lines: int = _DEFAULT_MAX_CAPTION_LINES,
    margin_pt: float = _DEFAULT_MARGIN_PT,
) -> CaptionFigureResult:
    """Crop one figure from a PDF by anchoring on its caption.

    Parameters
    ----------
    pdf_path : path-like
        Source PDF file.
    caption_prefix : str
        Literal text that opens the caption — typically ``"Figure 2:"``
        or ``"Figure 2 |"``. Matched as a prefix against stripped text
        blocks; the first match across pages wins.
    out_path : path-like
        Destination PNG file. Parent directory is created.
    include_caption : bool, default True
        Whether to extend the crop downward to include the caption text
        block(s). ``False`` crops only the figure body.
    dpi : int, default 300
        Render resolution.
    page_hint : int | None, default None
        1-indexed page to search first. Speeds up search and avoids
        false matches on papers with repeated figure references.
    max_caption_lines : int, default 6
        Maximum continuation lines to absorb after the caption block.
    margin_pt : float, default 4.0
        Padding added on all sides of the computed bbox.

    Returns
    -------
    CaptionFigureResult

    Raises
    ------
    ValueError
        Caption prefix not found anywhere in the PDF.
    ImportError
        ``pymupdf`` is not installed.
    """
    try:
        import fitz  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise ImportError("pymupdf is required: pip install pymupdf") from e

    pdf_path = Path(pdf_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(pdf_path))
    target = caption_prefix.strip()

    pages_order: list[int] = []
    if page_hint is not None and 1 <= page_hint <= len(doc):
        pages_order.append(page_hint - 1)
    pages_order.extend(
        i for i in range(len(doc)) if i != (page_hint - 1 if page_hint else -1)
    )

    try:
        for page_idx in pages_order:
            page = doc[page_idx]
            blocks = page.get_text("blocks")
            text_blocks = [b for b in blocks if b[6] == 0]
            text_blocks.sort(key=lambda b: (b[1], b[0]))

            cap_idx = None
            for i, b in enumerate(text_blocks):
                if b[4].strip().startswith(target):
                    cap_idx = i
                    break
            if cap_idx is None:
                continue

            cap_block = text_blocks[cap_idx]
            cap_x0, cap_y0, cap_x1, cap_y1, *_ = cap_block

            prev_fig_y_max, next_fig_y_min = _find_other_figure_boundaries(
                text_blocks, cap_idx, cap_y0
            )
            cap_y_end = _accumulate_caption_lines(
                text_blocks, cap_idx, cap_x0, cap_x1, cap_y1,
                next_fig_y_min, max_caption_lines,
            )
            prev_y_bottom = _find_prev_body_block(
                text_blocks, cap_idx, cap_x0, cap_x1, cap_y0
            )

            fig_top = max(prev_y_bottom, prev_fig_y_max) + margin_pt
            fig_bottom = (
                cap_y_end + margin_pt if include_caption else cap_y0 - margin_pt
            )
            if next_fig_y_min != float("inf"):
                fig_bottom = min(fig_bottom, next_fig_y_min - margin_pt)
            fig_left = cap_x0 - margin_pt
            fig_right = cap_x1 + margin_pt

            bbox = fitz.Rect(fig_left, fig_top, fig_right, fig_bottom)
            pix = page.get_pixmap(clip=bbox, matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0))
            pix.save(str(out_path))
            return CaptionFigureResult(
                page=page_idx + 1,
                bbox=(bbox.x0, bbox.y0, bbox.x1, bbox.y1),
                image_path=out_path.resolve(),
                caption_prefix=caption_prefix,
            )
    finally:
        doc.close()

    raise ValueError(f"caption {caption_prefix!r} not found in {pdf_path}")


def extract_figures(
    pdf_path: Path | str,
    captions: Iterable[tuple[str, str | Path]],
    out_dir: Path | str | None = None,
    *,
    include_caption: bool = True,
    dpi: int = _DEFAULT_DPI,
    page_hints: dict[str, int] | None = None,
    skip_missing: bool = False,
    **kwargs,
) -> list[CaptionFigureResult]:
    """Batch-extract multiple figures from one PDF.

    Parameters
    ----------
    pdf_path : path-like
        Source PDF.
    captions : iterable of (caption_prefix, out_name)
        Each entry: a caption prefix to search for, and either an
        absolute output path or a filename relative to ``out_dir``.
    out_dir : path-like, optional
        Base directory for output PNGs when entries in ``captions``
        give relative paths.
    page_hints : dict[str, int], optional
        Map ``caption_prefix`` to 1-indexed page hint for that caption.
    skip_missing : bool, default False
        When True, captions that aren't found yield no entry instead
        of raising.

    Other kwargs forwarded to :func:`extract_figure`.

    Returns
    -------
    list[CaptionFigureResult]
        One entry per successfully extracted figure, in input order.
    """
    out_dir = Path(out_dir) if out_dir is not None else None
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)

    page_hints = page_hints or {}
    results: list[CaptionFigureResult] = []
    for caption_prefix, out_name in captions:
        out_path = Path(out_name)
        if not out_path.is_absolute() and out_dir is not None:
            out_path = out_dir / out_path
        try:
            result = extract_figure(
                pdf_path,
                caption_prefix,
                out_path,
                include_caption=include_caption,
                dpi=dpi,
                page_hint=page_hints.get(caption_prefix),
                **kwargs,
            )
            results.append(result)
        except ValueError:
            if not skip_missing:
                raise
    return results


__all__ = ["CaptionFigureResult", "extract_figure", "extract_figures"]
