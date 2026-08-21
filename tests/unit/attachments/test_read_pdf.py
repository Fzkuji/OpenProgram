"""read tool — PDF support: per-page text extraction, page-based
offset/limit, default 20-page window, no-text-layer error."""
from __future__ import annotations

import pytest

pytest.importorskip("pypdf")
reportlab = pytest.importorskip("reportlab")

from openprogram.programs.tools.files.read import (  # noqa: E402
    PDF_PAGES_DEFAULT,
    _is_pdf,
    read,
)


def _read(path, **args):
    """Drive the AgentTool through its async execute path (same
    pattern as test_worktree_tools)."""
    import asyncio
    res = asyncio.run(read.execute("test-call", {"file_path": path, **args},
                                   None, None))
    return "\n".join(b.text for b in (res.content or [])
                     if getattr(b, "text", None) is not None)


def _make_pdf(path, page_texts: list[str]) -> str:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path), pagesize=letter)
    for text in page_texts:
        c.drawString(72, 720, text)
        c.showPage()
    c.save()
    return str(path)


def test_is_pdf_by_extension_and_magic(tmp_path) -> None:
    pdf = _make_pdf(tmp_path / "doc.pdf", ["hello"])
    assert _is_pdf(pdf)
    # Extensionless file with %PDF magic.
    magic = tmp_path / "noext"
    magic.write_bytes((tmp_path / "doc.pdf").read_bytes())
    assert _is_pdf(str(magic))
    txt = tmp_path / "plain.txt"
    txt.write_text("hello")
    assert not _is_pdf(str(txt))


def test_read_pdf_pages_labelled(tmp_path) -> None:
    pdf = _make_pdf(tmp_path / "doc.pdf",
                    ["alpha page one", "beta page two", "gamma page three"])
    out = _read(pdf)
    assert "(PDF, pages 1-3 of 3" in out
    assert "[page 1]" in out and "alpha page one" in out
    assert "[page 2]" in out and "beta page two" in out
    assert "[page 3]" in out and "gamma page three" in out


def test_read_pdf_offset_limit_are_pages(tmp_path) -> None:
    pdf = _make_pdf(tmp_path / "doc.pdf",
                    [f"content of page {i}" for i in range(1, 6)])
    out = _read(pdf, offset=2, limit=2)
    assert "(PDF, pages 2-3 of 5" in out
    assert "content of page 2" in out and "content of page 3" in out
    assert "content of page 1" not in out and "content of page 4" not in out
    # Offset past the end is an empty range, not an error.
    assert "empty range" in _read(pdf, offset=99)


def test_read_pdf_default_caps_at_20_pages(tmp_path) -> None:
    pdf = _make_pdf(tmp_path / "doc.pdf",
                    [f"page number {i}" for i in range(1, 26)])
    out = _read(pdf)
    assert f"(PDF, pages 1-{PDF_PAGES_DEFAULT} of 25" in out
    assert f"[page {PDF_PAGES_DEFAULT}]" in out
    assert f"[page {PDF_PAGES_DEFAULT + 1}]" not in out


def test_read_pdf_without_text_layer_reports(tmp_path) -> None:
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    w.add_blank_page(width=612, height=792)
    target = tmp_path / "scan.pdf"
    with open(target, "wb") as f:
        w.write(f)
    out = _read(str(target))
    assert "无文本层" in out and "no extractable text" in out


def test_read_text_file_unchanged(tmp_path) -> None:
    txt = tmp_path / "a.txt"
    txt.write_text("line one\nline two\n")
    out = _read(str(txt))
    assert "line one" in out and "lines 1-2 of 2" in out
