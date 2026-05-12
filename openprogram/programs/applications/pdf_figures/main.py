"""
pdf_figures — extract figures from an academic PDF, with VLM verification.

The deterministic tool ``openprogram.tools.pdf.extract_figure`` gets every
figure on the first try ~80% of the time. This application wraps it with
a VLM-driven verification + retry loop so the caller doesn't have to
hand-fix bad crops:

    1. Heuristic pass: ``extract_all_figures`` produces a candidate crop
       per discovered ``Figure N:`` caption.
    2. For each crop, render the full page once. Ask the VLM:
       "is this crop a complete, well-bounded figure?" Show it both the
       crop and the full page.
    3. If the VLM says no, it returns a corrected bbox in PDF points.
       Re-render via ``extract_with_bbox`` and ask again.
    4. Repeat up to ``max_retries`` times per figure, then accept.

Public entry: :func:`extract_pdf_figures`.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path

from openprogram.agentic_programming.function import agentic_function
from openprogram.agentic_programming.runtime import Runtime
from openprogram.programs.applications.pdf_figures._heuristic import (
    FigureCrop,
    extract_all_figures,
    extract_with_bbox,
    render_full_page,
)


_VERIFY_PROMPT = """\
You are verifying that a PDF figure crop is correctly bounded.

I'll show you two images:
  1. The candidate crop of the figure.
  2. The full PDF page it came from (rendered at 144 DPI).

Page dimensions in PDF points: {page_w:.0f} x {page_h:.0f}.
The crop's current bbox in PDF points: ({x0:.0f}, {y0:.0f}, {x1:.0f}, {y1:.0f}).
The caption ("{label}") is at approximately y={cap_y0:.0f}-{cap_y1:.0f}.

Decide whether the crop is correct. A correct crop:
  - Includes the full figure body (no axes / labels / panels cut off).
  - Includes the caption text fully.
  - Does NOT include body paragraphs from the page.
  - Does NOT include parts of other figures / tables on the same page.

Reply with JSON ONLY, no prose:

  {{"ok": true}}                                              if correct
  {{"ok": false, "bbox": [x0, y0, x1, y1], "reason": "..."}}  if wrong

When wrong, give the corrected bbox in PDF points (NOT pixels). Origin is
top-left of the page. Use the page dimensions and current bbox as
reference. ``reason`` is a one-sentence explanation.\
"""


_JSON_RE = re.compile(r"\{[\s\S]*\}")


def _parse_verdict(reply: str) -> dict:
    """Tolerantly parse the VLM's JSON verdict.

    Strips markdown fences and prose around the JSON object.
    Returns ``{"ok": True}`` on parse failure (treat ambiguous reply
    as "accept" to avoid infinite retry).
    """
    if reply is None:
        return {"ok": True}
    text = reply.strip()
    if text.startswith("```"):
        text = "\n".join(line for line in text.splitlines() if not line.startswith("```"))
    m = _JSON_RE.search(text)
    if not m:
        return {"ok": True}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"ok": True}


def _page_dimensions(pdf_path: Path | str, page: int) -> tuple[float, float]:
    """Return ``(width, height)`` in PDF points for the given 1-indexed page."""
    import fitz  # type: ignore
    doc = fitz.open(str(pdf_path))
    try:
        rect = doc[page - 1].rect
        return rect.width, rect.height
    finally:
        doc.close()


@agentic_function(input={
    "pdf_path": {
        "description": "Path to source PDF",
        "placeholder": "/path/to/paper.pdf",
        "multiline": False,
    },
    "out_dir": {
        "description": "Output directory for PNGs",
        "placeholder": "/path/to/figures/",
        "multiline": False,
    },
    "max_retries": {
        "description": "Max VLM-guided retries per figure (default 3)",
        "placeholder": "3",
        "multiline": False,
    },
    "include_tables": {
        "description": "Also extract Table captions (default False)",
        "placeholder": "false",
        "multiline": False,
    },
    "filename_template": {
        "description": "Output filename template",
        "placeholder": "fig{number:02d}.png",
        "multiline": False,
    },
    "dpi": {
        "description": "Render DPI (default 300)",
        "placeholder": "300",
        "multiline": False,
    },
    "runtime": {"hidden": True},
})
def extract_pdf_figures(
    pdf_path: str,
    out_dir: str,
    max_retries: int = 3,
    include_tables: bool = False,
    filename_template: str = "fig{number:02d}.png",
    dpi: int = 300,
    runtime: Runtime = None,
) -> list[dict]:
    """Extract all figures from a PDF, with VLM-verified crops.

    For each ``Figure N:`` caption found in the PDF:

    1. Run the heuristic extractor to get a candidate crop.
    2. Show the crop + full page to the VLM; ask whether the crop is
       correctly bounded.
    3. If the VLM says wrong, it returns a corrected bbox in PDF
       points. Re-render and ask again.
    4. Repeat up to ``max_retries`` times, then accept the latest crop.

    The caller doesn't need to inspect outputs or tweak parameters —
    the VLM is the inspector. The heuristic produces a good first
    guess on standard layouts; the VLM catches the failure cases
    (wrapfigure, page-top whitespace, multi-figure conflicts).

    Parameters
    ----------
    pdf_path : str
        Source PDF.
    out_dir : str
        Where to write PNGs. Created if missing.
    max_retries : int, default 3
        Maximum VLM-guided retries per figure before accepting.
    include_tables : bool, default False
        Also extract Table captions.
    filename_template : str, default ``"fig{number:02d}.png"``
        Template fields: ``{number}``, ``{kind}``, ``{kind_short}``,
        ``{page}``.
    dpi : int, default 300
        Render resolution for final crops.
    runtime : Runtime
        Injected by ``@agentic_function``. Must be a VLM-capable
        provider (gemini-2.5-pro, claude-sonnet, gpt-4o, etc.).

    Returns
    -------
    list[dict]
        Per-figure record with ``label``, ``page``, ``bbox``,
        ``image_path``, ``retries_used``, ``verified``.
    """
    pdf_path = str(pdf_path)
    out_dir_path = Path(out_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)

    # Step 1 — initial heuristic pass
    crops = extract_all_figures(
        pdf_path,
        out_dir_path,
        include_tables=include_tables,
        filename_template=filename_template,
        dpi=dpi,
    )

    # Cache full-page renders (one per unique page) for VLM context
    page_cache_dir = out_dir_path / "_page_cache"
    page_renders: dict[int, Path] = {}

    def _page_render(p: int) -> Path:
        if p not in page_renders:
            page_renders[p] = render_full_page(
                pdf_path, p, page_cache_dir / f"page_{p:03d}.png", dpi=144,
            )
        return page_renders[p]

    results: list[dict] = []
    for crop in crops:
        page_w, page_h = _page_dimensions(pdf_path, crop.page)
        current = crop
        retries_used = 0
        verified = False

        for attempt in range(max_retries):
            page_img = _page_render(current.page)
            prompt = _VERIFY_PROMPT.format(
                page_w=page_w, page_h=page_h,
                x0=current.bbox[0], y0=current.bbox[1],
                x1=current.bbox[2], y1=current.bbox[3],
                label=current.caption_prefix,
                cap_y0=current.bbox[3] - 40,  # caption sits near bottom of crop
                cap_y1=current.bbox[3],
            )
            reply = runtime.exec(content=[
                {"type": "image", "image_path": str(current.image_path)},
                {"type": "image", "image_path": str(page_img)},
                {"type": "text", "text": prompt},
            ])
            verdict = _parse_verdict(str(reply))

            if verdict.get("ok", True):
                verified = True
                break

            new_bbox = verdict.get("bbox")
            if not (isinstance(new_bbox, list) and len(new_bbox) == 4):
                # VLM said wrong but didn't give a usable bbox — give up retrying
                break

            # Re-render with corrected bbox
            extract_with_bbox(
                pdf_path, current.page, tuple(new_bbox),
                current.image_path, dpi=dpi,
            )
            current = FigureCrop(
                page=current.page,
                bbox=tuple(new_bbox),
                image_path=current.image_path,
                caption_prefix=current.caption_prefix,
            )
            retries_used += 1

        record = asdict(current)
        record["image_path"] = str(current.image_path)
        record["retries_used"] = retries_used
        record["verified"] = verified
        results.append(record)

    return results


__all__ = ["extract_pdf_figures"]
