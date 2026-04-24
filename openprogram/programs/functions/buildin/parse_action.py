"""parse_action — extract a function call action from LLM output."""

from __future__ import annotations

import json
import re

from openprogram.programs.functions.buildin._utils import _extract_first_json_object


def parse_action(text: str) -> dict | None:
    """Extract {"call": "name", "args": {...}} from LLM output, or None.

    Searches for JSON with a "call" key in markdown fences or bare JSON.
    """
    # Try markdown-fenced JSON
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if match:
        try:
            obj = json.loads(match.group(1))
            if isinstance(obj, dict) and "call" in obj:
                return obj
        except json.JSONDecodeError:
            pass

    # Try balanced JSON extraction (handles nested objects correctly)
    obj = _extract_first_json_object(text)
    if isinstance(obj, dict) and "call" in obj:
        return obj

    return None
