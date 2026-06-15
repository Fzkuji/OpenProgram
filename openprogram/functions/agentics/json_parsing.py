"""Parse JSON out of an LLM's text reply.

Installed harnesses (GUI / Research / Wiki …) delegate here for the one
fiddly thing every one of them needs: pulling a JSON object out of a
model reply that may be wrapped in ```json fences, prefixed with prose,
or otherwise not clean ``json.loads``-able. One implementation, imported
as ``from openprogram.functions.agentics.json_parsing import parse_json``.
"""

from __future__ import annotations

import json
import re


def parse_json(text: str) -> dict:
    """Extract the first JSON object from text, handling markdown fences."""
    # Try direct parse
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # Try markdown-fenced JSON
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Find first '{' and try balanced extraction
    result = _extract_first_json_object(text)
    if result is not None:
        return result

    raise ValueError("No valid JSON found in response")


def _extract_first_json_object(text: str) -> dict | None:
    """Find the first valid JSON object in text by bracket balancing.

    More reliable than regex — handles nested braces correctly.
    """
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            c = text[i]
            if escape:
                escape = False
                continue
            if c == "\\":
                escape = True
                continue
            if c == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break  # This { didn't work, try next one
        start = text.find("{", start + 1)
    return None


__all__ = ["parse_json"]
