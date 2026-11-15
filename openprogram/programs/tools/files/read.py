"""read function — read a file from disk and return its contents."""

from __future__ import annotations

import os
import mimetypes

from openprogram.programs._host.runtime import function
from openprogram.state.worktree.path_resolve import resolve_path
import logging

_log = logging.getLogger(__name__)


MAX_LINES_DEFAULT = 2000
MAX_LINE_LENGTH = 2000
PDF_PAGES_DEFAULT = 20
PDF_MAX_CHARS = 100_000
_BINARY_EXTENSIONS = {".docx", ".docm", ".pptx", ".pptm", ".xlsx", ".xlsm", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico"}

_DESCRIPTION = (
    "Read a file from disk and return its contents as text, with line numbers "
    "in `cat -n` style (1-based).\n"
    "\n"
    "- Paths MUST be absolute.\n"
    "- By default reads up to 2000 lines from the top. Use `offset` and `limit` "
    "to page through larger files.\n"
    "- Individual lines longer than 2000 characters are truncated with an ellipsis.\n"
    "- A range too long for one result stops at the last whole line that fits "
    "and gives the offset to continue from.\n"
    "- PDF files are read as extracted text, one `[page N]` block per page. "
    "For PDFs `offset` and `limit` are PAGES (1-based; default: first "
    f"{PDF_PAGES_DEFAULT} pages).\n"
    "- Other binary files return type/size metadata and establish the read baseline. Use document tools to inspect their contents."
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


def _is_binary(file_path: str) -> bool:
    if os.path.splitext(file_path)[1].lower() in _BINARY_EXTENSIONS:
        return True
    try:
        with open(file_path, "rb") as stream:
            sample = stream.read(8192)
        return b"\0" in sample
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

    from openprogram.state.store.session.weight import quarters

    chunks: list[str] = []
    chars = 0
    used_q = 0
    # The page's text with its header and note, the numbers at their widest,
    # within one read's weight (``_page_quarters``).
    max_q = _page_quarters() - quarters(
        _pdf_header(file_path, total, total, total) + "\n" + _pdf_note(total))
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
        chunk = (f"[page {i + 1}]\n{text}" if text
                 else f"[page {i + 1}]\n(no text on this page)")
        # A page that would take the text past one read's length starts
        # the next read instead; the first page is kept whatever its size.
        joined = len(chunk) + (2 if chunks else 0)
        joined_q = quarters(chunk) + (8 if chunks else 0)
        if chunks and (chars + joined > PDF_MAX_CHARS or used_q + joined_q > max_q):
            truncated_at = i
            break
        chunks.append(chunk)
        chars += joined
        used_q += joined_q

    if not any_text:
        return (f"Error: {file_path}: PDF 无文本层 — pages {start + 1}-{end} "
                f"of {total} contain no extractable text (likely a scanned "
                "image PDF; OCR is required to read it).")

    shown_end = truncated_at or end
    body = "\n\n".join(chunks)
    if truncated_at is not None:
        body += _pdf_note(truncated_at)
    return _pdf_header(file_path, start + 1, shown_end, total) + "\n" + body


def _pdf_header(file_path: str, first: int, last: int, total: int) -> str:
    return f"# {file_path} (PDF, pages {first}-{last} of {total}; offset/limit are pages)"


def _pdf_note(truncated_at: int) -> str:
    return (f"\n\n…[truncated at page {truncated_at}: output exceeded "
            f"one read's length — use offset={truncated_at + 1} to continue]")


def _mark_read_baseline(file_path: str) -> None:
    """Record this file's on-disk state as the agent's read-before-edit
    baseline (Claude-Code-style): a later edit/write validates against
    it so the agent can't clobber a concurrent user change unseen.
    Fingerprints the WHOLE file even on a paged read — the contract is
    about the file changing on disk, not the page. No-op outside a turn.
    """
    try:
        from openprogram.state.store.snapshot import read_tracking as _rt
        _rt.mark_seen(file_path)
    except Exception:
        # The read baseline is bookkeeping; without it a later edit asks for a re-read.
        _log.debug("read baseline not recorded for %s", file_path, exc_info=True)


def execute(file_path: str,
            offset: int | None = 1,
            limit: int | None = MAX_LINES_DEFAULT) -> str:
    """Read a file and return its contents with line numbers.

    Args:
        file_path: Absolute path of the file to read.
        offset: Line number to start reading from (1-based). Null uses default 1.
            For PDF files this is the first PAGE to read.
        limit: Maximum number of lines to return. Null uses default 2000.
            For PDF files this is the number of PAGES (default 20).
    """
    # Strict tool schemas represent omitted optional arguments as null.
    # Normalize these pagination defaults before either text or PDF handling.
    offset = 1 if offset is None else offset
    limit = MAX_LINES_DEFAULT if limit is None else limit

    # An SSH conversation reads the remote disk.
    from ._remote import remote_target
    _remote_fs = remote_target()
    if _remote_fs is not None:
        from . import _remote
        return _remote.read(_remote_fs, file_path, offset, limit)

    # Worktree-aware resolution: relative paths bind to the active
    # worktree root when one is set; absolute paths outside the
    # worktree get a soft warning but still proceed (D6).
    resolved_path, outside_warning = resolve_path(file_path)
    file_path = resolved_path
    if not os.path.isabs(file_path):
        return f"Error: file_path must be absolute, got {file_path!r}"
    if not os.path.exists(file_path):
        try:
            from openprogram.state import attachments as _attachments
            from openprogram.runtime.execution.run_control import get_current_session_id

            session_id = get_current_session_id()
            alias = _attachments.resolve_session_attachment(
                file_path,
                session_id,
                _attachments.readable_roots(session_id),
            )
            if alias is not None:
                file_path = str(alias)
        except Exception:
            # Attachment aliasing is optional; the path is read as given.
            _log.debug("attachment alias unresolved for %s", file_path, exc_info=True)
    from openprogram.host.sandbox import validate_read_path
    violation = validate_read_path(file_path)
    if violation:
        from openprogram.runtime.permissions import refusal
        raise refusal.sandbox("read", violation)
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

    if _is_binary(file_path):
        _mark_read_baseline(file_path)
        try:
            info = os.stat(file_path)
            media_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        except OSError as exc:
            return f"Error reading {file_path}: {type(exc).__name__}: {exc}"
        out = f"# {file_path} (binary, {info.st_size} bytes, {media_type})"
        if outside_warning:
            out = f"{outside_warning}\n{out}"
        return out

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        return f"Error reading {file_path}: {type(e).__name__}: {e}"

    _mark_read_baseline(file_path)

    return numbered_page(
        [line.rstrip("\n") for line in lines], offset, limit,
        shown=file_path, path=file_path, warning=outside_warning)


def _page_quarters() -> int:
    """How much one read's text may weigh, in quarters (``state.store.session.weight``):
    a page ends before the agent loop's bound (``spill.result_cap``,
    smaller on a model with a small window) would cut it. Guides and
    AGENTS.md files are sent beside the result, not in it, so they take
    nothing from the page."""
    from openprogram.state.store.session import spill
    return spill.result_cap() * 4


def _continue_note(shown_end: int, end: int, total: int, path: str) -> str:
    return (f"\n\n…[page ends at line {shown_end} of {total}: lines {shown_end + 1}-{end} "
            f"are not shown, to keep one read within its length — use "
            f'offset={shown_end + 1} to continue: read("{path}", offset={shown_end + 1}, '
            f"limit={end - shown_end})]")


def numbered_page(lines: list[str], offset: int, limit: int, *, shown: str,
                  path: str, warning: str | None = None) -> str:
    """Lines ``offset``..``offset+limit-1`` in ``cat -n`` form, as one page.

    The page is contiguous from ``offset``: when the asked range is longer
    than one read may be, it ends on the last whole line that fits and says
    the offset to continue from, so the model reads on from there rather
    than receiving a head and tail with the middle cut out. The header and
    that note are counted in the page's length, and on a page too short
    for a line of ``MAX_LINE_LENGTH`` a line is cut to what fits.
    """
    from openprogram.state.store.session.weight import quarters

    total = len(lines)
    start = max(1, offset) - 1
    end = min(total, start + max(1, limit))
    # The longest header and note this range can have, numbers at their widest.
    fixed = (quarters(f"# {shown} (lines {total}-{total} of {total})\n")
             + (quarters(warning) + 1 if warning else 0)
             + quarters(_continue_note(total, total, total, path)))
    budget = _page_quarters() - fixed
    # A line is cut to what fits however dense its script (4 quarters a
    # character at most).
    line_cap = max(80, min(MAX_LINE_LENGTH, (budget - quarters(f"{total:>6}\t…[truncated]")) // 4))
    rows: list[str] = []
    used = 0
    for i in range(start, end):
        text = lines[i]
        if len(text) > line_cap:
            text = text[:line_cap] + "…[truncated]"
        row = f"{i + 1:>6}\t{text}"
        weight = quarters(row) + 1
        if rows and used + weight > budget:
            break
        rows.append(row)
        used += weight
    shown_end = start + len(rows) if rows else end
    header = f"# {shown} (lines {start + 1}-{shown_end} of {total})"
    if warning:
        header = f"{warning}\n{header}"
    if not rows:
        return header + "\n(empty range)"
    body = header + "\n" + "\n".join(rows)
    if shown_end < end:
        body += _continue_note(shown_end, end, total, path)
    return body


# Workflows import ``execute`` as a normal Python function; model tool
# dispatch uses the separately registered ``read`` AgentTool.
read = function(
    name="read",
    accept_edits_safe=True,
    description=_DESCRIPTION,
    toolset=["core", "research"],
    path_params={"file_path": "read"},
)(execute)


__all__ = ["execute", "read"]
