"""
pdf_figures.extract — caption-anchored PDF figure extraction.

Single entry function `extract_pdf_figures` plus a single-figure variant
`extract_one_figure`. All work is deterministic (pymupdf only) — no LLM
call, but the function keeps the `runtime` parameter for application
protocol consistency.

The algorithm and its known limitations are documented in detail in the
docstring of `extract_one_figure`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from openprogram.agentic_programming.runtime import Runtime


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
    """Walk UP to find the previous body / heading block bottom."""
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


# ---------------------------------------------------------------------------
# Public functions (the application entry points)
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

    The algorithm:

    1. Locate the caption text block by matching `caption_prefix` (e.g.
       ``"Figure 2:"``) against stripped text blocks. First match wins;
       a 1-indexed ``page_hint`` short-circuits the scan.
    2. Walk UP from the caption to find the previous body / section
       heading block — skipping sub-figure captions, axis ticks,
       in-figure legend titles, and other narrow / non-prose blocks.
    3. Absorb paragraph-continuation lines downward (PyMuPDF often
       splits one paragraph into per-line blocks).
    4. Use other ``Figure N:`` captions on the same page as hard
       y-bounds (prevents Figure 3 bleeding into Figure 4).
    5. Conservatively absorb caption-continuation lines below the
       caption itself (small gap, inside x-range, contains lowercase).
    6. Render the resulting bbox via pymupdf at the requested DPI.

    Known limitations (when this function will under-perform):

    * **Wrapfigure layouts** — body text flowing around an inset
      figure. The above-caption search stops at the first body
      paragraph on the wrap side, producing too-short crops.
    * **Atypical text sizes** — thresholds tuned for ~10pt body
      text. Very small or very large body fonts may need parameter
      tuning.
    * **Caption fused with prose** — if PyMuPDF emitted the caption
      glued to surrounding text in a single block, the prefix anchor
      can fail.

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
        Maximum continuation lines to absorb past the caption block.
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
                raise ValueError(
                    f"caption line missing ' => filename': {line!r}"
                )
            prefix, filename = line.split("=>", 1)
            pairs.append((prefix.strip(), filename.strip()))
        return pairs
    return [(p, f) for p, f in captions]


def extract_pdf_figures(
    pdf_path: str,
    captions: str | Iterable,
    out_dir: str,
    dpi: int = _DEFAULT_DPI,
    page_hints: str | dict | None = None,
    *,
    include_caption: bool = True,
    skip_missing: bool = False,
    margin_pt: float = _DEFAULT_MARGIN_PT,
    max_caption_lines: int = _DEFAULT_MAX_CAPTION_LINES,
    runtime: Runtime = None,
) -> list[FigureCrop]:
    """Extract one or more figures from an academic PDF by caption prefix.

    Application entry point. Single deterministic step (no LLM call) — the
    `runtime` parameter is accepted for application-protocol consistency
    but ignored.

    Use this when you have an academic PDF and want clean PNG crops of
    specific figures for embedding in markdown / wiki. Works well on
    NeurIPS / arXiv layouts with ~10pt body text and clearly anchored
    captions like ``"Figure 2:"`` / ``"Table 1:"``.

    The two callable surfaces of this application:

    - ``extract_pdf_figures`` (this function) — batch entry point. Pass a
      newline-separated ``PREFIX => filename`` list to crop many figures
      from one PDF in a single call.
    - ``extract_one_figure`` — single-figure entry, returns a ``FigureCrop``
      record. Use directly when you only need one figure or need full
      control over per-figure parameters.

    Both share the same underlying caption-anchored algorithm documented
    on ``extract_one_figure``.

    Known limitations:

    - Wrapfigure layouts (body text flowing around an inset figure)
      produce too-short crops.
    - Captions fused with surrounding prose by PyMuPDF's text extractor
      may not match the prefix anchor.
    - Thresholds tuned for ~10pt body text.

    Parameters
    ----------
    pdf_path : str
        Path to the source PDF.
    captions : str or iterable
        Either a multiline string with ``PREFIX => filename`` per line
        (one figure to extract per line), or an iterable of
        ``(prefix, filename)`` tuples.
    out_dir : str
        Directory to write rendered PNGs. Created if missing. Filenames
        in ``captions`` may be absolute or relative to this directory.
    dpi : int, default 300
        Render resolution.
    page_hints : str or dict, optional
        Page hints to speed up search. As a string: ``PREFIX => page``
        per line. As a dict: ``{caption_prefix: page_number}``.
    include_caption : bool, default True
        Whether to include the caption block in the crop.
    skip_missing : bool, default False
        When True, captions not found in the PDF yield no entry and no
        exception. When False (default), missing captions raise
        ``ValueError``.
    margin_pt : float, default 4.0
        Pixel padding around the computed bbox.
    max_caption_lines : int, default 6
        Maximum caption-continuation lines absorbed past the caption block.
    runtime : Runtime, optional
        Unused. Accepted for application-protocol consistency.

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

    Examples
    --------
    Multiline-string captions form (most common when invoked by an
    agent or as a CLI)::

        extract_pdf_figures(
            pdf_path="paper.pdf",
            captions=(
                "Figure 1: => fig1.png\\n"
                "Figure 2: => fig2.png\\n"
                "Figure 7: => fig7.png\\n"
            ),
            out_dir="figures/",
            page_hints="Figure 7: => 17",
        )

    Programmatic tuple form::

        extract_pdf_figures(
            pdf_path="paper.pdf",
            captions=[("Figure 1:", "fig1.png"), ("Figure 2:", "fig2.png")],
            out_dir="figures/",
        )
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


__all__ = ["FigureCrop", "extract_one_figure", "extract_pdf_figures"]
