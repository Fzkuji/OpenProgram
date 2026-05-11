"""pdf tool — extract text and (optionally) figures from PDF files.

Text extraction wraps ``pypdf`` (previously ``PyPDF2``); figure
extraction uses ``pymupdf`` (``fitz``). Both are imported lazily.

Pagination via the same ``offset`` / ``limit`` convention as the read
tool so agents can page through long documents.

Figure extraction (when ``extract_images=True``):
* Embedded raster images per page are written to ``image_out_dir``
  as PNG, named ``fig-p<page>-i<idx>.png``.
* Tiny decorative images (< 80px on a side) are skipped.
* The returned text gets an ``## Figures`` section listing every
  extracted image with its file path, page, and pixel size.
* Vector figures (text + lines, common in academic papers) are NOT
  captured as single embeddings — callers wanting the page as a
  whole image should fall back to ``image_analyze`` on a page
  render, or use the ``render_pages`` parameter (TBD).

Why pypdf over pdfplumber / pdfminer:
* pure Python, no system deps (pdfminer pulls in a chunky native stack)
* handles 95% of text-heavy PDFs; agents that need layout-preserving
  extraction can fall back to bash ``pdftotext``
"""

from __future__ import annotations

import os
from typing import Any

from .._helpers import read_int_param, read_string_param


NAME = "pdf"

MAX_CHARS_DEFAULT = 80_000

DESCRIPTION = (
    "Extract text from a local PDF file. Returns page-delimited text. "
    "Use `offset`/`limit` (1-based page numbers) to page through long "
    "documents. For image-only / scanned PDFs this will return empty "
    "pages — pair with `image_analyze` on page screenshots instead."
)


SPEC: dict[str, Any] = {
    "name": NAME,
    "description": DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to a .pdf file.",
            },
            "offset": {
                "type": "integer",
                "description": "1-based page number to start extraction from. Default 1.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of pages to include. Default: all remaining.",
            },
            "max_chars": {
                "type": "integer",
                "description": f"Overall character cap on the returned text. Default {MAX_CHARS_DEFAULT}.",
            },
            "extract_images": {
                "type": "boolean",
                "description": (
                    "If true, also extract embedded raster images "
                    "into image_out_dir as PNG files. Returns a "
                    "Markdown figure inventory after the text. "
                    "Default false."
                ),
            },
            "image_out_dir": {
                "type": "string",
                "description": (
                    "Absolute directory where extracted images go. "
                    "Required when extract_images is true. Created if "
                    "it does not exist."
                ),
            },
            "min_image_side": {
                "type": "integer",
                "description": (
                    "Pixel threshold below which an embedded image is "
                    "skipped (filters icons / decorations). Default 80."
                ),
            },
        },
        "required": ["file_path"],
    },
}


def _tool_check_fn() -> bool:
    try:
        import pypdf  # noqa: F401

        return True
    except Exception:
        return False


def execute(
    file_path: str | None = None,
    offset: int | None = None,
    limit: int | None = None,
    max_chars: int | None = None,
    extract_images: bool | None = None,
    image_out_dir: str | None = None,
    min_image_side: int | None = None,
    **kw: Any,
) -> str:
    file_path = file_path or read_string_param(kw, "file_path", "filePath", "path")
    offset = read_int_param(kw, "offset", default=offset or 1) or 1
    limit = read_int_param(kw, "limit", default=limit)
    max_chars = read_int_param(kw, "max_chars", "maxChars", default=max_chars or MAX_CHARS_DEFAULT) or MAX_CHARS_DEFAULT
    if extract_images is None:
        extract_images = bool(kw.get("extract_images") or kw.get("extractImages"))
    image_out_dir = image_out_dir or read_string_param(kw, "image_out_dir", "imageOutDir")
    min_image_side = read_int_param(kw, "min_image_side", "minImageSide", default=min_image_side or 80) or 80

    if not file_path:
        return "Error: `file_path` is required."
    if not os.path.isabs(file_path):
        return f"Error: file_path must be absolute, got {file_path!r}"
    if not os.path.exists(file_path):
        return f"Error: file not found: {file_path}"
    if not file_path.lower().endswith(".pdf"):
        return f"Error: expected a .pdf file, got {file_path}"

    try:
        import pypdf  # type: ignore
    except ImportError:
        return (
            "Error: pypdf is not installed. Install with: pip install pypdf"
        )

    try:
        reader = pypdf.PdfReader(file_path)
    except Exception as e:
        return f"Error: cannot open {file_path}: {type(e).__name__}: {e}"

    total = len(reader.pages)
    if total == 0:
        return f"# {file_path}\n(empty PDF)"

    start_idx = max(1, offset) - 1
    end_idx = total if limit is None else min(total, start_idx + max(1, limit))
    selected = range(start_idx, end_idx)

    out_parts: list[str] = [f"# {file_path} (pages {start_idx + 1}-{end_idx} of {total})\n"]
    total_chars = len(out_parts[0])
    truncated_note = ""
    for i in selected:
        page = reader.pages[i]
        try:
            text = page.extract_text() or ""
        except Exception as e:
            text = f"[page {i + 1}: extraction failed: {e}]"
        segment = f"\n## Page {i + 1}\n{text.strip()}\n"
        if total_chars + len(segment) > max_chars:
            remaining_pages = end_idx - i
            truncated_note = (
                f"\n\n…[truncated at {max_chars:,} chars; {remaining_pages} page(s) omitted. "
                "Rerun with `offset` to resume.]"
            )
            break
        out_parts.append(segment)
        total_chars += len(segment)

    text_result = "".join(out_parts) + truncated_note

    if not extract_images:
        return text_result

    if not image_out_dir:
        return text_result + (
            "\n\n## Figures\n\n_extract_images was true but image_out_dir "
            "was not provided; skipped figure extraction._"
        )
    if not os.path.isabs(image_out_dir):
        return text_result + (
            f"\n\n## Figures\n\n_image_out_dir must be absolute, got "
            f"{image_out_dir!r}; skipped figure extraction._"
        )

    try:
        import fitz  # type: ignore
    except ImportError:
        return text_result + (
            "\n\n## Figures\n\n_pymupdf is not installed. "
            "Install with: pip install pymupdf_"
        )

    try:
        doc = fitz.open(file_path)
    except Exception as e:
        return text_result + (
            f"\n\n## Figures\n\n_PyMuPDF cannot open {file_path}: {e}_"
        )

    os.makedirs(image_out_dir, exist_ok=True)
    figs: list[str] = []
    for page_idx in range(len(doc)):
        # Honor the same page window as text extraction.
        if page_idx < start_idx or page_idx >= end_idx:
            continue
        page = doc[page_idx]
        for img_idx, img in enumerate(page.get_images(full=True)):
            xref = img[0]
            try:
                pix = fitz.Pixmap(doc, xref)
                if pix.n - pix.alpha > 3:  # CMYK → RGB
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                if pix.width < min_image_side or pix.height < min_image_side:
                    pix = None
                    continue
                name = f"fig-p{page_idx + 1}-i{img_idx + 1}.png"
                out_path = os.path.join(image_out_dir, name)
                pix.save(out_path)
                figs.append(
                    f"- `{out_path}` — page {page_idx + 1}, "
                    f"{pix.width}×{pix.height}"
                )
                pix = None
            except Exception:
                continue
    doc.close()

    if not figs:
        return text_result + (
            f"\n\n## Figures\n\n_No embeddable raster images found "
            f"in pages {start_idx + 1}-{end_idx}. Many academic-paper "
            f"figures are vector (text + lines) and aren't captured "
            f"as single embeddings; render the page with image_analyze "
            f"if you need them._"
        )

    figs_text = "\n".join(figs)
    return (
        text_result
        + f"\n\n## Figures\n\nExtracted {len(figs)} image(s) into `{image_out_dir}`:\n\n{figs_text}\n"
    )


__all__ = ["NAME", "SPEC", "execute", "DESCRIPTION", "_tool_check_fn"]
