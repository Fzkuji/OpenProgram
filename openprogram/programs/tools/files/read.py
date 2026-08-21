"""read function — read a file from disk and return its contents."""

from __future__ import annotations

import os

from openprogram.programs._runtime import function
from openprogram.worktree.path_resolve import resolve_path


MAX_LINES_DEFAULT = 2000
MAX_LINE_LENGTH = 2000
PDF_PAGES_DEFAULT = 20
PDF_MAX_CHARS = 100_000

_DESCRIPTION = (
    "Read a file from disk and return its contents as text, with line numbers "
    "in `cat -n` style (1-based).\n"
    "\n"
    "- Paths MUST be absolute.\n"
    "- By default reads up to 2000 lines from the top. Use `offset` and `limit` "
    "to page through larger files.\n"
    "- Individual lines longer than 2000 characters are truncated with an ellipsis.\n"
    "- PDF files are read as extracted text, one `[page N]` block per page. "
    "For PDFs `offset` and `limit` are PAGES (1-based; default: first "
    f"{PDF_PAGES_DEFAULT} pages).\n"
    "- Other binary files are not supported — use bash if you need hex dumps."
)


def _is_pdf(file_path: str) -> bool:
    """PDF by extension, or by the %PDF magic for extensionless files."""
    if file_path.lower().endswith(".pdf"):
        return True
    try:
        with open(file_path, "rb") as f:
            return f.read(5) == b"%PDF-"
    except OSError:
        return False


def _read_pdf(file_path: str, offset: int, limit: int) -> str:
    """Extract text page by page via pypdf. ``offset``/``limit`` are
    page-based here (1-based first page / page count); a caller leaving
    the line default gets the first PDF_PAGES_DEFAULT pages."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return (
            "Error: PDF reading is unavailable in this installation; "
            "reinstall the complete OpenProgram release."
        )

    try:
        reader = PdfReader(file_path)
        total = len(reader.pages)
    except Exception as e:
        return f"Error reading {file_path}: {type(e).__name__}: {e}"

    start = max(1, offset) - 1
    pages = PDF_PAGES_DEFAULT if limit == MAX_LINES_DEFAULT else max(1, limit)
    end = min(total, start + pages)
    if start >= total:
        return (f"# {file_path} (PDF, {total} pages)\n"
                f"(empty range: offset {offset} is past the last page)")

    chunks: list[str] = []
    chars = 0
    truncated_at: int | None = None
    any_text = False
    for i in range(start, end):
        try:
            text = (reader.pages[i].extract_text() or "").strip()
            # pypdf leaves unpaired surrogates behind on math-font
            # glyphs (U+1D4xx split in a broken CMap). A lone
            # surrogate poisons every later utf-8 encode — history
            # persistence and the provider payload both crash on it.
            # errors="replace" turns the bad code points into "?".
            text = text.encode("utf-8", "replace").decode("utf-8")
        except Exception:
            text = ""
        if text:
            any_text = True
        chunks.append(f"[page {i + 1}]\n{text}" if text
                      else f"[page {i + 1}]\n(no text on this page)")
        chars += len(chunks[-1])
        if chars > PDF_MAX_CHARS:
            truncated_at = i + 1
            break

    if not any_text:
        return (f"Error: {file_path}: PDF 无文本层 — pages {start + 1}-{end} "
                f"of {total} contain no extractable text (likely a scanned "
                "image PDF; OCR is required to read it).")

    shown_end = truncated_at or end
    header = (f"# {file_path} (PDF, pages {start + 1}-{shown_end} of {total}; "
              "offset/limit are pages)")
    body = "\n\n".join(chunks)
    if truncated_at is not None:
        body += (f"\n\n…[truncated at page {truncated_at}: output exceeded "
                 f"{PDF_MAX_CHARS} chars — use offset={truncated_at + 1} "
                 "to continue]")
    return header + "\n" + body


def _mark_read_baseline(file_path: str) -> None:
    """Record this file's on-disk state as the agent's read-before-edit
    baseline (Claude-Code-style): a later edit/write validates against
    it so the agent can't clobber a concurrent user change unseen.
    Fingerprints the WHOLE file even on a paged read — the contract is
    about the file changing on disk, not the page. No-op outside a turn.
    """
    try:
        from openprogram.store.snapshot import read_tracking as _rt
        _rt.mark_seen(file_path)
    except Exception:
        pass


def execute(file_path: str,
            offset: int = 1,
            limit: int = MAX_LINES_DEFAULT) -> str:
    """Read a file and return its contents with line numbers.

    Args:
        file_path: Absolute path of the file to read.
        offset: Line number to start reading from (1-based). Default 1.
            For PDF files this is the first PAGE to read.
        limit: Maximum number of lines to return. Default 2000.
            For PDF files this is the number of PAGES (default 20).
    """
    # Worktree-aware resolution: relative paths bind to the active
    # worktree root when one is set; absolute paths outside the
    # worktree get a soft warning but still proceed (D6).
    resolved_path, outside_warning = resolve_path(file_path)
    file_path = resolved_path
    if not os.path.isabs(file_path):
        return f"Error: file_path must be absolute, got {file_path!r}"
    from openprogram.sandbox import validate_read_path
    violation = validate_read_path(file_path)
    if violation:
        return f"Error: sandbox policy: {violation}"
    if not os.path.exists(file_path):
        return f"Error: file not found: {file_path}"
    if os.path.isdir(file_path):
        return f"Error: {file_path} is a directory, not a file"

    if _is_pdf(file_path):
        _mark_read_baseline(file_path)
        out = _read_pdf(file_path, offset, limit)
        if outside_warning:
            out = f"{outside_warning}\n{out}"
        return out

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        return f"Error reading {file_path}: {type(e).__name__}: {e}"

    _mark_read_baseline(file_path)

    total = len(lines)
    start = max(1, offset) - 1
    end = min(total, start + max(1, limit))
    selected = lines[start:end]

    out_lines = []
    for i, line in enumerate(selected, start=start + 1):
        text = line.rstrip("\n")
        if len(text) > MAX_LINE_LENGTH:
            text = text[:MAX_LINE_LENGTH] + "…[truncated]"
        out_lines.append(f"{i:>6}\t{text}")

    header = f"# {file_path} (lines {start + 1}-{end} of {total})"
    if outside_warning:
        header = f"{outside_warning}\n{header}"
    if not out_lines:
        return header + "\n(empty range)"
    return header + "\n" + "\n".join(out_lines)


# Workflows import ``execute`` as a normal Python function; model tool
# dispatch uses the separately registered ``read`` AgentTool.
read = function(
    name="read",
    accept_edits_safe=True,
    description=_DESCRIPTION,
    max_result_chars=200_000,
    persist_full=False,
    toolset=["core", "research"],
    path_params={"file_path": "read"},
)(execute)


__all__ = ["execute", "read"]
