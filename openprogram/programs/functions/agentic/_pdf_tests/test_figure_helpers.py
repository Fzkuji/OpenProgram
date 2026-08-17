"""Tests for the deterministic helpers of extract_pdf_figures.

The agentic function itself needs a live vision model and is not unit
tested here — only the pure pixel/JSON plumbing around it.
"""

from __future__ import annotations

from openprogram.programs.functions.agentic.extract_pdf_figures import (
    _parse_json_array,
    _parse_pages,
    _slug,
)


def test_parse_pages():
    assert _parse_pages("", 12) == (1, 12)
    assert _parse_pages("5", 12) == (5, 5)
    assert _parse_pages("3-9", 12) == (3, 9)
    assert _parse_pages("-4", 12) == (1, 4)


def test_parse_json_array_plain():
    arr = _parse_json_array('[{"label": "Figure 1", "figure_bbox": [1,2,3,4]}]')
    assert len(arr) == 1
    assert arr[0]["label"] == "Figure 1"


def test_parse_json_array_fenced_with_prose():
    reply = 'Here are the figures:\n```json\n[{"label": "Fig 2"}]\n```\n'
    arr = _parse_json_array(reply)
    assert arr == [{"label": "Fig 2"}]


def test_parse_json_array_empty_and_malformed():
    assert _parse_json_array("[]") == []
    assert _parse_json_array("no json here") == []
    assert _parse_json_array("[{bad json}]") == []


def test_slug():
    assert _slug("Figure 1") == "figure_1"
    assert _slug("Fig. 3(a)") == "fig_3_a"
    assert _slug("") == "figure"
