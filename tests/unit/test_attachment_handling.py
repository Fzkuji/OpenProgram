"""Unit tests for WS attachment handling — caps, filename safety, kind
classification, and the model-facing head preview.

These helpers in ``openprogram/webui/ws_actions/chat.py`` are reachable directly
from user input (uploads / @-mentions) and commit blobs to a git workdir, so a
cap bypass or misclassification is a storage/DoS vector. They are pure (no IO),
so they're tested directly.
"""
from __future__ import annotations

import pytest

from openprogram.webui.ws_actions import chat


# ── caps are what we think they are ───────────────────────────────────────────

def test_cap_constants():
    assert chat.MAX_ATTACH_MB == 32
    assert chat.MAX_ATTACH_BYTES == 32 * 1024 * 1024
    assert chat.MAX_TURN_ATTACH_BYTES == 64 * 1024 * 1024
    assert chat.PREVIEW_CAP == 4096


# ── filename safety (path traversal, weird chars, length) ─────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("../../etc/passwd", "passwd"),     # basename strips traversal
    ("a/b/c.txt", "c.txt"),
    ("", "file"),
    ("   ", "file"),
    ("ok_name-1.txt", "ok_name-1.txt"),
])
def test_safe_attach_name(raw, expected):
    assert chat._safe_attach_name(raw) == expected


def test_safe_attach_name_sanitizes_and_truncates():
    out = chat._safe_attach_name("we!rd@name#.txt")
    assert "!" not in out and "@" not in out and "#" not in out
    assert chat._safe_attach_name("x" * 300) == "x" * 120


# ── kind classification ───────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,name,kind", [
    (b"%PDF-1.7\nstuff", "x.bin", "pdf"),        # magic bytes
    (b"not really a pdf", "report.pdf", "pdf"),  # extension
    (b"plain ascii text", "a.txt", "text"),
    ("héllo utf8".encode("utf-8"), "a.txt", "text"),
    (b"has a \x00 null byte", "a.bin", "binary"),
    (b"\xff\xfe\xff\xfe", "a.bin", "binary"),     # invalid utf-8, no null
])
def test_decoded_kind(raw, name, kind):
    assert chat._decoded_kind(raw, name) == kind


# ── text preview: line count + PREVIEW_CAP truncation ─────────────────────────

def test_count_and_preview_text_small():
    count, preview = chat._count_and_preview(b"a\nb\nc", "text")
    assert count == "3 lines"
    assert preview == "a\nb\nc"
    assert "truncated" not in preview


def test_count_and_preview_text_truncates_at_cap():
    raw = ("line\n" * 5000).encode("utf-8")   # well over PREVIEW_CAP
    count, preview = chat._count_and_preview(raw, "text")
    # head is capped to PREVIEW_CAP bytes + a one-line truncation marker
    assert "truncated" in preview
    assert len(preview) <= chat.PREVIEW_CAP + 60
    assert count.endswith(" lines")


def test_count_and_preview_binary_has_no_preview():
    assert chat._count_and_preview(b"\x00\x01\x02", "binary") == (None, None)


# ── corrupt / non-PDF input degrades gracefully, never raises ─────────────────

def test_pdf_preview_corrupt_falls_back():
    assert chat._pdf_count_and_preview(b"this is not a pdf at all") == (None, None)
    assert chat._pdf_count_and_preview(b"") == (None, None)
