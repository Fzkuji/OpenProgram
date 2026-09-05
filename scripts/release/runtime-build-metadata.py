#!/usr/bin/env python3
"""Print runtime build metadata without shell-sensitive ``python -c``."""

from __future__ import annotations

import importlib.metadata
import json
import os
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit(
            "usage: runtime-build-metadata.py PYTHON_EXECUTABLE RUNTIME_ROOT"
        )
    python = Path(sys.argv[1]).resolve()
    runtime = Path(sys.argv[2]).resolve()
    try:
        python.relative_to(runtime)
    except ValueError as exc:
        raise SystemExit(f"managed Python resolved outside runtime: {python}") from exc
    print(
        json.dumps(
            {
                "python_relative": os.path.relpath(python, runtime),
                "openprogram_version": importlib.metadata.version("openprogram"),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
