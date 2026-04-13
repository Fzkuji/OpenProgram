"""build_catalog — render a function registry into a compact prompt catalog."""

from __future__ import annotations

import json


def build_catalog(available: dict) -> str:
    """Render the available function registry into JSON for LLM consumption.

    The registry format follows the Pattern 3 examples in the meta-function docs.
    Function objects are omitted, while descriptive metadata is preserved.
    """
    catalog = {}
    for name, spec in (available or {}).items():
        catalog[name] = {
            "description": spec.get("description", ""),
            "input": spec.get("input", {}),
            "output": spec.get("output", {}),
        }
    return json.dumps(catalog, indent=2, ensure_ascii=False, default=str)
