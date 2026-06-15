"""Integration tests for pure-Python functions — no LLM required.

These tests import and execute real functions (not mocked) to verify
end-to-end correctness after installation. They run in CI alongside
unit tests.
"""

import json
import os
import tempfile

import pytest


# ---------------------------------------------------------------------------
# parse_json — extracts JSON from LLM-style text
# ---------------------------------------------------------------------------

class TestParseJson:

    def test_direct_json(self):
        from openprogram.functions.agentics.json_parsing import parse_json
        assert parse_json('{"key": "value"}') == {"key": "value"}

    def test_markdown_fence(self):
        from openprogram.functions.agentics.json_parsing import parse_json
        text = 'Here is the result:\n```json\n{"score": 8}\n```\nDone.'
        assert parse_json(text) == {"score": 8}

    def test_bare_json_in_text(self):
        from openprogram.functions.agentics.json_parsing import parse_json
        text = 'The output is {"status": "ok", "count": 3} as expected.'
        result = parse_json(text)
        assert result["status"] == "ok"
        assert result["count"] == 3

    def test_nested_json(self):
        from openprogram.functions.agentics.json_parsing import parse_json
        text = 'Result: {"data": {"nested": true}, "list": [1, 2]}'
        result = parse_json(text)
        assert result["data"]["nested"] is True
        assert result["list"] == [1, 2]

    def test_no_json_raises(self):
        from openprogram.functions.agentics.json_parsing import parse_json
        with pytest.raises(ValueError, match="No valid JSON"):
            parse_json("no json here at all")

    def test_multiple_json_returns_first(self):
        from openprogram.functions.agentics.json_parsing import parse_json
        text = '{"first": 1} and then {"second": 2}'
        assert parse_json(text) == {"first": 1}
