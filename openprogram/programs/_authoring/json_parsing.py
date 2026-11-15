"""Parse JSON out of an LLM's text reply.

Installed harnesses (GUI / Research …) delegate here for the one
fiddly thing every one of them needs: pulling a JSON object out of a
model reply that may be wrapped in ```json fences, prefixed with prose,
or otherwise not clean ``json.loads``-able. One implementation, imported
as ``from openprogram.programs._authoring.json_parsing import parse_json``.
"""

from __future__ import annotations

import json
import re


def parse_json(text: str) -> dict:
    """Extract the first JSON object from text, handling markdown fences."""
    if not isinstance(text, str):
        raise ValueError("JSON response must be text")
    # Try direct parse
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
        # A valid scalar/array is not the object requested by the caller.
        raise ValueError("JSON response must contain an object")
    except json.JSONDecodeError:
        pass

    # Try markdown-fenced JSON
    for match in re.finditer(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text,
                             re.DOTALL | re.IGNORECASE):
        try:
            value = json.loads(match.group(1))
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass

    # Find first '{' and try balanced extraction
    result = _extract_first_json_object(text)
    if result is not None:
        return result

    raise ValueError("No valid JSON found in response")


def _extract_first_json_object(text: str) -> dict | None:
    """Decode object candidates with JSON's own string/escape rules."""
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            value, _ = decoder.raw_decode(text, start)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
        start = text.find("{", start + 1)
    return None


__all__ = ["parse_json"]
