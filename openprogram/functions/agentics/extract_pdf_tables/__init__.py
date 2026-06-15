"""extract_pdf_tables — pull tables out of any PDF as Markdown.

Table layouts vary endlessly across publishers and templates: ruled
vs. borderless, single- vs. multi-column, captions above vs. below,
tables sharing a y-range with a neighbouring text column. Algorithmic
detection wins some and loses the next — every new template is a new
edge case.

So the geometry stays algorithmic only where it is *reliable* (the
``pdf_layout`` helper lays a page's words out at their real x/y positions
into fixed-width text) and the *judgement* — which rows are a table,
where it starts and ends, separating it from prose — is handed to the
LLM, page by page. The LLM reasons about whatever layout it sees
instead of matching a fixed template set.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from openprogram.agentic_programming.function import agentic_function
from openprogram.agentic_programming.runtime import Runtime

from ..pdf_layout import pdf_pages, render_page_png

_PROMPT = (
    "You are given ONE page of a PDF. Extract EVERY table on this page "
    "as a GitHub-flavoured Markdown table.\n\n"
    "Rules:\n"
    "- Reproduce every cell value EXACTLY — do not round, summarise, or "
    "invent numbers.\n"
    "- A page may hold several tables, or a table beside body text, or "
    "a table in one column of a two-column layout. Include only the "
    "tabular content; ignore body paragraphs, figure captions, page "
    "headers/footers and page numbers.\n"
    "- If a table has a caption (e.g. 'Table 3: ...'), put it on a line "
    "immediately before that table.\n"
    "- Separate multiple tables with one blank line.\n"
    "- If the page contains NO table at all, reply with exactly the "
    "single word NONE.\n"
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


@agentic_function(input={
    "pdf_path": {
        "description": "Absolute path to the .pdf file to extract tables from.",
        "placeholder": "/Users/me/papers/example.pdf",
    },
    "pages": {
        "description": "Optional 1-based page range, e.g. '3' or '2-7'. "
                        "Empty means the whole document.",
        "placeholder": "2-7",
    },
})
def extract_pdf_tables(
    pdf_path: str,
    runtime: Runtime,
    pages: str = "",
) -> list[dict]:
    """Extract every table from a PDF as Markdown, one LLM pass per page.

    Works on any layout — ruled or borderless tables, single- or
    multi-column pages, scanned pages (rendered to an image for the
    LLM). The fixed-width positioned text (or page image) is handed to
    the model, which decides what is a table and reconstructs it. Each
    result dict is ``{"page", "markdown"}``, one per page that held at
    least one table, ``markdown`` being GitHub-flavoured Markdown.
    """
    try:
        import fitz  # type: ignore
    except ImportError:
        raise ImportError("pymupdf is required: pip install pymupdf")

    src = Path(pdf_path)
    if not src.is_absolute():
        raise ValueError(f"pdf_path must be absolute, got {pdf_path!r}")
    if not src.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    doc = fitz.open(str(src))
    n_pages = len(doc)
    lo, hi = _parse_pages(pages, n_pages)
    layouts = pdf_pages(src, pages=(lo, hi))

    results: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="pdf-tables-") as tmp:
        for pl in layouts:
            if pl.scanned:
                # No text layer — hand the LLM a rendered page image.
                img = render_page_png(
                    doc[pl.page - 1], Path(tmp) / f"p{pl.page}.png"
                )
                content = [
                    {"type": "text", "text": _PROMPT},
                    {"type": "image", "path": str(img)},
                ]
            else:
                content = [{"type": "text", "text": (
                    f"{_PROMPT}\n"
                    f"Page {pl.page} (fixed-width positioned text — every "
                    f"word sits at its real position, so columns align):\n\n"
                    f"{pl.text}"
                )}]
            reply = str(runtime.exec(content=content)).strip()
            if reply and reply.upper() != "NONE":
                results.append({"page": pl.page, "markdown": reply})

    doc.close()
    return results
