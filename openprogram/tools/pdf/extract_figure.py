"""extract_figure — caption-anchored PDF figure extraction.

A deterministic tool (no LLM call) for cropping individual figures from
academic PDFs by their caption text. Pure pymupdf. Lives in
``tools/pdf/`` because it doesn't need agent reasoning — given a
``pdf_path`` and a ``caption_prefix`` the output is fully determined.

How it works
------------
Academic figures are vector graphics + text, so the raster ``xref``
table that ``pymupdf`` exposes via ``page.get_images()`` returns at
best a logo or legend swatch. This module instead:

1. Locates the caption text block via a caller-supplied prefix (e.g.
   ``"Figure 2:"``). The first match across pages wins; a 1-indexed
   ``page_hint`` short-circuits the scan.
2. Walks UP from the caption to find the previous *real body block* —
   either a paragraph (≥ 30 chars, sentence terminator, column-
   spanning width) or a section heading (short capitalised title,
   also column-spanning). Sub-figure captions ``"(a) ..."``, axis
   ticks, narrow in-figure legend titles, and other figure labels are
   all skipped via dedicated filters.
3. Absorbs paragraph-continuation lines downward from the found body
   block. PyMuPDF often splits one paragraph into per-line blocks; the
   first found body block is only the FIRST line, so we walk down to
   find the actual paragraph bottom.
4. Treats any other ``Figure N:`` caption on the same page as a hard
   vertical boundary — this prevents Figure 3 from bleeding into the
   top of Figure 4 when both share a page.
5. Conservatively accumulates caption-continuation lines below the
   caption itself: small vertical gap (≤ 8 pt), inside caption
   x-range, contains lowercase letters (excludes panel-title rows like
   ``"Base Vanilla K-SFT OP-SFT+KΔ-FT"``), no crossing into the next
   figure.
6. Renders the resulting bbox via ``page.get_pixmap(clip=...)`` at
   user-specified DPI.

Known limitations
-----------------
* **Wrapfigure layouts** where body text flows around an inset figure:
  the above-caption search will stop at the first body paragraph on
  the wrap side, giving a too-short crop.
* **Captions fused with prose** by PyMuPDF's text extractor may not
  match the prefix anchor.
* All thresholds tuned for ~10pt body text. Atypical text sizes may
  need parameter adjustment.

For an LLM-driven / 2D-layout approach (more general, more edge-case
failure modes) see the experimental ``openprogram.tools.pdf.figures``.
For an industrial-grade alternative shell out to Allen AI's Scala
``pdffigures2.jar`` directly.

Usage
-----

Single figure::

    from openprogram.tools.pdf.extract_figure import extract_one_figure

    result = extract_one_figure(
        pdf_path="paper.pdf",
        caption_prefix="Figure 2:",
        out_path="figures/fig2.png",
        page_hint=4,
    )
    print(result.page, result.bbox)

Batch::

    from openprogram.tools.pdf.extract_figure import extract_figures

    results = extract_figures(
        pdf_path="paper.pdf",
        captions=[
            ("Figure 1:", "fig1.png"),
            ("Figure 2:", "fig2.png"),
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
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FigureCrop:
    """One extracted figure.

    Attributes
    ----------
    page : int
        1-indexed PDF page where the caption was found.
    bbox : tuple[float, float, float, float]
        ``(x0, y0, x1, y1)`` rendered region in PDF points.
    image_path : Path
        Absolute path to the rendered PNG.
    caption_prefix : str
        Prefix string used to anchor the search.
    """

    page: int
    bbox: tuple[float, float, float, float]
    image_path: Path
    caption_prefix: str


@dataclass(frozen=True)
class CaptionRef:
    """A caption discovered in a PDF (without extraction).

    Returned by :func:`list_captions`.

    Attributes
    ----------
    kind : str
        ``"Figure"`` or ``"Table"``.
    number : int
        The integer following the kind keyword.
    label : str
        Normalized human-readable label, e.g. ``"Figure 3"``.
    prefix : str
        Caption prefix verbatim from the PDF (e.g. ``"Figure 3:"``)
        suitable for passing to :func:`extract_one_figure`.
    page : int
        1-indexed page number.
    bbox : tuple[float, float, float, float]
        Caption block bbox in PDF points.
    """

    kind: str
    number: int
    label: str
    prefix: str
    page: int
    bbox: tuple[float, float, float, float]


# ---------------------------------------------------------------------------
# Tunables (defaults that work on ~10pt NeurIPS / arXiv layouts)
# ---------------------------------------------------------------------------

_DEFAULT_DPI = 300
_DEFAULT_MARGIN_PT = 4.0
_DEFAULT_MAX_CAPTION_LINES = 6
_MIN_FIG_HEIGHT_PT = 95.0
_CAPTION_CONTINUATION_GAP_PT = 8.0
_MIN_PARAGRAPH_WIDTH_FRAC = 0.55
_MIN_HEADING_WIDTH_FRAC = 0.40

_OTHER_FIG_PAT = re.compile(r"^\s*Figure\s+\d+[:.|]")
_SUBCAP_PAT = re.compile(r"^\s*\(?[a-z]\)\s+[A-Z]")
# Discovery — matches Figure/Fig./Fig with various separators
_DISCOVERY_PAT = re.compile(
    r"^\s*(Figure|Fig\.|Fig|Table|Tab\.|Tab)\s+(\d+)\s*[:.\|]"
)
_KIND_NORMALIZE = {
    "figure": "Figure", "fig.": "Figure", "fig": "Figure",
    "table": "Table", "tab.": "Table", "tab": "Table",
}


# ---------------------------------------------------------------------------
# Internal heuristics
# ---------------------------------------------------------------------------


def _looks_like_body(text: str, block_width: float, cap_width: float) -> bool:
    """Whether a text block represents body prose or a section heading."""
    t = text.strip()
    if len(t) < 5 or _SUBCAP_PAT.match(t):
        return False
    if (
        len(t) >= 30
        and any(p in t for p in (".", "!", "?", "。", "！", "？"))
        and block_width >= cap_width * _MIN_PARAGRAPH_WIDTH_FRAC
    ):
        return True
    tokens = t.split()
    if (
        2 <= len(tokens) <= 12
        and sum(tok[0].isalpha() and tok[0].isupper() for tok in tokens) >= 2
        and block_width >= cap_width * _MIN_HEADING_WIDTH_FRAC
    ):
        if sum(c.isalpha() for c in t) >= len(t) * 0.5:
            return True
    return False


def _other_fig_bounds(
    text_blocks: list, cap_idx: int, cap_y0: float
) -> tuple[float, float]:
    """y-bounds set by other Figure-N captions on the same page."""
    prev_max = 0.0
    next_min = float("inf")
    for k, b in enumerate(text_blocks):
        if k == cap_idx or not _OTHER_FIG_PAT.match(b[4].strip()):
            continue
        if b[3] < cap_y0:
            prev_max = max(prev_max, b[3])
        else:
            next_min = min(next_min, b[1])
    return prev_max, next_min


def _absorb_caption_lines(
    text_blocks: list,
    cap_idx: int,
    cap_x0: float,
    cap_x1: float,
    cap_y1: float,
    next_fig_y_min: float,
    max_lines: int,
) -> float:
    """Walk DOWN absorbing caption-continuation lines; returns final y."""
    cap_y_end = cap_y1
    added = 0
    for j in range(cap_idx + 1, len(text_blocks)):
        nb_x0, nb_y0, nb_x1, nb_y1, nb_text, *_ = text_blocks[j]
        if nb_y0 > cap_y_end + _CAPTION_CONTINUATION_GAP_PT:
            break
        if nb_x0 < cap_x0 - 5 or nb_x1 > cap_x1 + 30:
            break
        if nb_y0 >= next_fig_y_min - 1:
            break
        stripped = nb_text.strip()
        if _OTHER_FIG_PAT.match(stripped):
            break
        if not any(c.islower() for c in stripped):
            break
        cap_y_end = nb_y1
        added += 1
        if added >= max_lines:
            break
    return cap_y_end


def _find_prev_body(
    text_blocks: list, cap_idx: int, cap_x0: float, cap_x1: float, cap_y0: float
) -> float:
    """Walk UP to find the previous body / heading block bottom.

    Returns the y-coordinate to use as the figure's top edge. When no
    body block is found above the caption (e.g. figure sits at the top
    of the page with nothing above it), instead of defaulting to the
    page top (which produces a huge top whitespace margin), use the
    *topmost* in-figure text block that overlaps the caption column as
    the figure's actual top.
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
        if not _looks_like_body(pb_text, pb_x1 - pb_x0, cap_width):
            continue
        body_idx = j
        prev_y_bottom = pb_y1
        break

    # PyMuPDF may split one paragraph into per-line blocks; absorb the rest.
    if body_idx is not None:
        cur = prev_y_bottom
        for k in range(body_idx + 1, cap_idx):
            nx0, ny0, nx1, ny1, ntext, *_ = text_blocks[k]
            if nx1 < cap_x0 - 5 or nx0 > cap_x1 + 5:
                continue
            if ny0 - cur > _CAPTION_CONTINUATION_GAP_PT:
                break
            if not any(c.isalpha() for c in ntext):
                break
            if _SUBCAP_PAT.match(ntext.strip()):
                break
            cur = ny1
        prev_y_bottom = cur
        return prev_y_bottom

    # Fallback when no body block was found above: tighten to the
    # topmost in-figure text block (axis label, panel title, etc.)
    # that sits inside the caption column above the caption. Subtract
    # a small margin so the actual figure top edge isn't clipped.
    topmost_y = None
    for k in range(cap_idx):
        kx0, ky0, kx1, ky1, ktext, *_ = text_blocks[k]
        if kx1 < cap_x0 - 5 or kx0 > cap_x1 + 5:
            continue
        if not ktext.strip():
            continue
        if topmost_y is None or ky0 < topmost_y:
            topmost_y = ky0
    if topmost_y is not None:
        # Subtract more than the outer margin so figure plot box edges
        # (rendered as vector strokes, not text) aren't clipped.
        return max(0.0, topmost_y - 12.0)
    return 0.0


def _parse_caption_pairs(captions: str | Iterable) -> list[tuple[str, str]]:
    """Parse the captions parameter into (prefix, filename) pairs.

    Accepts either:
    - A multiline string with ``caption_prefix => filename`` per line
    - An iterable of (prefix, filename) tuples
    """
    if isinstance(captions, str):
        pairs: list[tuple[str, str]] = []
        for line in captions.splitlines():
            line = line.strip()
            if not line:
                continue
            if "=>" not in line:
                raise ValueError(f"caption line missing ' => filename': {line!r}")
            prefix, filename = line.split("=>", 1)
            pairs.append((prefix.strip(), filename.strip()))
        return pairs
    return [(p, f) for p, f in captions]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_one_figure(
    pdf_path: Path | str,
    caption_prefix: str,
    out_path: Path | str,
    *,
    include_caption: bool = True,
    dpi: int = _DEFAULT_DPI,
    page_hint: int | None = None,
    max_caption_lines: int = _DEFAULT_MAX_CAPTION_LINES,
    margin_pt: float = _DEFAULT_MARGIN_PT,
) -> FigureCrop:
    """Crop one figure from a PDF, anchored on its caption.

    See module docstring for the full algorithm and limitations.

    Parameters
    ----------
    pdf_path : path-like
        Source PDF file.
    caption_prefix : str
        Literal prefix like ``"Figure 2:"`` or ``"Figure 2 |"``.
    out_path : path-like
        Output PNG. Parent directory is created.
    include_caption : bool, default True
        Whether to extend the crop downward to include the caption.
    dpi : int, default 300
        Render DPI.
    page_hint : int | None
        1-indexed page to search first.
    max_caption_lines : int, default 6
        Maximum continuation lines absorbed past the caption block.
    margin_pt : float, default 4.0
        Pixel padding on all sides of the bbox.

    Returns
    -------
    FigureCrop

    Raises
    ------
    ValueError
        Caption not found anywhere in the PDF.
    ImportError
        ``pymupdf`` not installed.
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

            cap_x0, cap_y0, cap_x1, cap_y1, *_ = text_blocks[cap_idx]
            prev_fig_max, next_fig_min = _other_fig_bounds(
                text_blocks, cap_idx, cap_y0
            )
            cap_y_end = _absorb_caption_lines(
                text_blocks, cap_idx, cap_x0, cap_x1, cap_y1,
                next_fig_min, max_caption_lines,
            )
            prev_y_bottom = _find_prev_body(
                text_blocks, cap_idx, cap_x0, cap_x1, cap_y0
            )

            fig_top = max(prev_y_bottom, prev_fig_max) + margin_pt
            fig_bottom = (
                cap_y_end + margin_pt if include_caption else cap_y0 - margin_pt
            )
            if next_fig_min != float("inf"):
                fig_bottom = min(fig_bottom, next_fig_min - margin_pt)
            fig_left = cap_x0 - margin_pt
            fig_right = cap_x1 + margin_pt

            bbox = fitz.Rect(fig_left, fig_top, fig_right, fig_bottom)
            pix = page.get_pixmap(
                clip=bbox, matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0)
            )
            pix.save(str(out_path))
            return FigureCrop(
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
    captions: str | Iterable,
    out_dir: Path | str,
    *,
    dpi: int = _DEFAULT_DPI,
    page_hints: str | dict | None = None,
    include_caption: bool = True,
    skip_missing: bool = False,
    margin_pt: float = _DEFAULT_MARGIN_PT,
    max_caption_lines: int = _DEFAULT_MAX_CAPTION_LINES,
) -> list[FigureCrop]:
    """Batch-extract multiple figures from one PDF.

    Parameters
    ----------
    pdf_path : path-like
        Source PDF.
    captions : str or iterable
        Either a multiline string with ``PREFIX => filename`` per line
        (one figure to extract per line), or an iterable of
        ``(prefix, filename)`` tuples.
    out_dir : path-like
        Directory to write rendered PNGs. Created if missing.
        Filenames in ``captions`` may be absolute or relative to this
        directory.
    dpi : int, default 300
        Render resolution.
    page_hints : str or dict, optional
        Page hints to speed up search. As string: ``PREFIX => page``
        per line. As dict: ``{caption_prefix: page_number}``.
    include_caption : bool, default True
        Whether to include the caption block in the crop.
    skip_missing : bool, default False
        When True, captions not found in the PDF yield no entry
        and no exception. When False (default), missing captions
        raise ``ValueError``.
    margin_pt : float, default 4.0
        Pixel padding around the computed bbox.
    max_caption_lines : int, default 6
        Maximum caption-continuation lines absorbed past the
        caption block.

    Returns
    -------
    list[FigureCrop]
        One entry per successfully extracted figure, in input order.

    Raises
    ------
    ValueError
        Caption not found (unless ``skip_missing=True``).
    ImportError
        ``pymupdf`` not installed.
    """
    pairs = _parse_caption_pairs(captions)
    hints_dict: dict[str, int] = {}
    if isinstance(page_hints, str):
        for line in page_hints.splitlines():
            line = line.strip()
            if not line or "=>" not in line:
                continue
            k, v = line.split("=>", 1)
            try:
                hints_dict[k.strip()] = int(v.strip())
            except ValueError:
                continue
    elif isinstance(page_hints, dict):
        hints_dict = {k: int(v) for k, v in page_hints.items()}

    out_dir_path = Path(out_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)

    results: list[FigureCrop] = []
    for prefix, filename in pairs:
        out_path = Path(filename)
        if not out_path.is_absolute():
            out_path = out_dir_path / out_path
        try:
            result = extract_one_figure(
                pdf_path,
                prefix,
                out_path,
                include_caption=include_caption,
                dpi=dpi,
                page_hint=hints_dict.get(prefix),
                max_caption_lines=max_caption_lines,
                margin_pt=margin_pt,
            )
            results.append(result)
        except ValueError:
            if not skip_missing:
                raise
    return results


def list_captions(
    pdf_path: Path | str,
    *,
    include_tables: bool = False,
    pages: tuple[int, int] | None = None,
) -> list[CaptionRef]:
    """Scan a PDF and return every Figure / Table caption found.

    Useful for discovery before extraction, or for callers who want
    to inspect what's available before calling :func:`extract_all_figures`.

    Parameters
    ----------
    pdf_path : path-like
    include_tables : bool, default False
        When True, also return Table captions.
    pages : (start, end) 1-indexed inclusive, optional
        Restrict scan to a page range.

    Returns
    -------
    list[CaptionRef]
        Sorted by (page, y). Each entry has the literal caption prefix
        suitable for :func:`extract_one_figure`.

    Raises
    ------
    ImportError
        ``pymupdf`` not installed.
    """
    try:
        import fitz  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise ImportError("pymupdf is required: pip install pymupdf") from e

    pdf_path = Path(pdf_path)
    doc = fitz.open(str(pdf_path))
    n_pages = len(doc)
    p_start, p_end = (1, n_pages) if pages is None else (
        max(1, pages[0]), min(n_pages, pages[1])
    )

    out: list[CaptionRef] = []
    try:
        for page_idx in range(p_start - 1, p_end):
            page = doc[page_idx]
            blocks = page.get_text("blocks")
            for b in blocks:
                if b[6] != 0:
                    continue
                stripped = b[4].strip()
                m = _DISCOVERY_PAT.match(stripped)
                if not m:
                    continue
                kind = _KIND_NORMALIZE[m.group(1).lower()]
                if kind == "Table" and not include_tables:
                    continue
                number = int(m.group(2))
                # Reconstruct the verbatim prefix to match extract_one_figure's
                # prefix-string contract — take the matched span end and
                # extract that many characters from the original text.
                prefix = stripped[: m.end()]
                out.append(CaptionRef(
                    kind=kind,
                    number=number,
                    label=f"{kind} {number}",
                    prefix=prefix,
                    page=page_idx + 1,
                    bbox=(b[0], b[1], b[2], b[3]),
                ))
    finally:
        doc.close()

    out.sort(key=lambda c: (c.page, c.bbox[1]))
    return out


def extract_all_figures(
    pdf_path: Path | str,
    out_dir: Path | str,
    *,
    include_tables: bool = False,
    filename_template: str = "{kind_short}{number:02d}.png",
    dpi: int = _DEFAULT_DPI,
    include_caption: bool = True,
    skip_missing: bool = True,
    margin_pt: float = _DEFAULT_MARGIN_PT,
    max_caption_lines: int = _DEFAULT_MAX_CAPTION_LINES,
) -> list[FigureCrop]:
    """One-shot: discover every figure in the PDF and crop each.

    Caller doesn't list captions or pages — the tool scans the PDF
    once for ``Figure N:`` (and optionally ``Table N:``) markers,
    then extracts each found caption in turn.

    Parameters
    ----------
    pdf_path : path-like
        Source PDF.
    out_dir : path-like
        Where to write PNGs. Created if missing.
    include_tables : bool, default False
        Also extract Table captions. Note: table content typically sits
        BELOW its caption (figures sit above), but the underlying
        algorithm searches above the caption — tables may extract
        poorly. Disabled by default.
    filename_template : str, default ``"{kind_short}{number:02d}.png"``
        Output filename per figure. Available fields:
        ``{kind}`` (e.g. ``"Figure"``), ``{kind_short}`` (e.g. ``"fig"``),
        ``{number}`` (int), ``{page}``.
    dpi : int, default 300
    include_caption : bool, default True
    skip_missing : bool, default True
        When True (default), captions that fail to extract are skipped
        silently. When False, raises on the first failure.
    margin_pt : float, default 4.0
    max_caption_lines : int, default 6

    Returns
    -------
    list[FigureCrop]
        One entry per successfully cropped figure.
    """
    refs = list_captions(pdf_path, include_tables=include_tables)
    out_dir_path = Path(out_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)

    short = {"Figure": "fig", "Table": "tab"}
    results: list[FigureCrop] = []
    for ref in refs:
        fname = filename_template.format(
            kind=ref.kind,
            kind_short=short.get(ref.kind, ref.kind.lower()),
            number=ref.number,
            page=ref.page,
        )
        try:
            results.append(extract_one_figure(
                pdf_path,
                ref.prefix,
                out_dir_path / fname,
                include_caption=include_caption,
                dpi=dpi,
                page_hint=ref.page,
                max_caption_lines=max_caption_lines,
                margin_pt=margin_pt,
            ))
        except ValueError:
            if not skip_missing:
                raise
    return results


__all__ = [
    "FigureCrop", "CaptionRef",
    "extract_one_figure", "extract_figures",
    "list_captions", "extract_all_figures",
]
