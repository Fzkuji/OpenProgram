"""Tests for the positioned-text layout primitive (_layout.py).

These cover the deterministic, algorithmic part only. The LLM-driven
table reconstruction lives in ``extract_pdf_tables.py`` and is not
unit-tested here (it needs a live model).
"""

from __future__ import annotations

import pytest

fitz = pytest.importorskip("pymupdf")

from openprogram.programs.workflow.document.pdf_layout import (
    page_layout,
    pdf_pages,
)
from openprogram.programs.workflow.document.extract_pdf_tables import (
    _parse_pages,
)


def _make_pdf(path: str) -> None:
    doc = fitz.open()
    page = doc.new_page()
    # Two columns of text at distinct x positions on the same rows
    # (enough words to clear the scanned-page threshold).
    rows = [
        ("Model", "Score"), ("base", "68.5"), ("ours", "87.6"),
        ("alpha", "12.1"), ("beta", "34.2"), ("gamma", "56.3"),
        ("delta", "78.4"), ("eps", "90.5"),
    ]
    for i, (label, value) in enumerate(rows):
        y = 100 + i * 20
        page.insert_text((72, y), label)
        page.insert_text((300, y), value)
    doc.save(path)
    doc.close()


def test_page_layout_preserves_columns(tmp_path):
    pdf = tmp_path / "doc.pdf"
    _make_pdf(str(pdf))
    doc = fitz.open(str(pdf))
    pl = page_layout(doc[0])
    doc.close()
    assert pl.page == 1
    assert not pl.scanned
    lines = pl.text.splitlines()
    assert len(lines) == 8
    # Each row keeps both column values, left-to-right.
    assert lines[0].split() == ["Model", "Score"]
    assert lines[1].split() == ["base", "68.5"]
    # The right column is positioned after the left, not merged onto it.
    assert lines[1].index("68.5") > lines[1].index("base")


def test_scanned_page_flagged(tmp_path):
    doc = fitz.open()
    doc.new_page()  # blank page, no text layer
    pdf = tmp_path / "blank.pdf"
    doc.save(str(pdf))
    doc.close()
    pages = pdf_pages(str(pdf))
    assert len(pages) == 1
    assert pages[0].scanned
    assert pages[0].text == ""


def test_parse_pages():
    assert _parse_pages("", 10) == (1, 10)
    assert _parse_pages("3", 10) == (3, 3)
    assert _parse_pages("2-7", 10) == (2, 7)
    assert _parse_pages("4-", 10) == (4, 10)
