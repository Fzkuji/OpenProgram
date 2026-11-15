"""Pasted-span display records (Codex text_elements) stored on user turns."""
from openprogram.interfaces.server.ws_actions.chat import _pasted_text_elements


def test_valid_records_are_kept_and_trimmed():
    out = _pasted_text_elements(
        [{"tail_start": 50, "tail_end": 10, "label": "Pasted · 3 lines", "head": "x" * 40}], 60,
    )
    assert out == [{"tail_start": 50, "tail_end": 10, "label": "Pasted · 3 lines", "head": "x" * 32}]


def test_malformed_or_out_of_range_records_are_dropped():
    assert _pasted_text_elements("nope", 60) == []
    assert _pasted_text_elements([
        {"tail_start": 70, "tail_end": 10, "label": "a", "head": ""},   # beyond the text
        {"tail_start": 5, "tail_end": 10, "label": "a", "head": ""},    # inverted
        {"tail_start": 5.0, "tail_end": 1, "label": "a", "head": ""},   # not an int
        {"tail_start": True, "tail_end": 0, "label": "a", "head": ""},  # bool is not an int
        "junk",
    ], 60) == []
