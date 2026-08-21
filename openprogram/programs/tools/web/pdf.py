"""pdf tool — extract text from PDF files.

Text extraction wraps ``pypdf`` (previously ``PyPDF2``), imported
lazily. Pagination via the same ``offset`` / ``limit`` convention as
the read tool so agents can page through long documents.

Figures and tables are NOT handled here. They need layout judgement
that pure geometry gets wrong on the next template, so they live as
LLM-driven agentic functions in
``openprogram.programs.workflow.pdf`` — see
``extract_pdf_figures`` and ``extract_pdf_tables`` there.

Why pypdf over pdfplumber / pdfminer:
* pure Python, no system deps (pdfminer pulls in a chunky native stack)
* handles 95% of text-heavy PDFs; agents that need layout-preserving
  extraction can fall back to bash ``pdftotext``
"""

from __future__ import annotations

import os
from typing import Any

from openprogram.programs._helpers import read_int_param, read_string_param
from openprogram.programs._runtime import function


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
    **kw: Any,
) -> str:
    file_path = file_path or read_string_param(kw, "file_path", "filePath", "path")
    offset = read_int_param(kw, "offset", default=offset or 1) or 1
    limit = read_int_param(kw, "limit", default=limit)
    max_chars = read_int_param(kw, "max_chars", "maxChars", default=max_chars or MAX_CHARS_DEFAULT) or MAX_CHARS_DEFAULT

    if not file_path:
        return "Error: `file_path` is required."
    if not os.path.isabs(file_path):
        return f"Error: file_path must be absolute, got {file_path!r}"
    from openprogram.sandbox import validate_read_path
    violation = validate_read_path(file_path)
    if violation:
        return f"Error: sandbox policy: {violation}"
    if not os.path.exists(file_path):
        return f"Error: file not found: {file_path}"
    if not file_path.lower().endswith(".pdf"):
        return f"Error: expected a .pdf file, got {file_path}"

    try:
        import pypdf  # type: ignore
    except ImportError:
        return (
            "Error: PDF support is unavailable in this installation; "
            "reinstall the complete OpenProgram release."
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

    return "".join(out_parts) + truncated_note



# Register as an AgentTool. ``execute`` stays a plain callable so any
# existing import-and-call sites keep working; the return value (an
# AgentTool) is discarded — it's already in the registry.
function(
    name=NAME,
    description=DESCRIPTION,
    parameters=SPEC["parameters"],
    toolset=['research'],
    max_result_chars=50_000,
    persist_full=True,
    check_fn=_tool_check_fn,
)(execute)

__all__ = ["NAME", "SPEC", "execute", "DESCRIPTION", "_tool_check_fn"]
