#!/usr/bin/env python3
"""Write the electron-builder configuration of a thin signed shell.

    thin-shell-config.py OUTPUT_CONFIG [SEED_DIR]

The same ``apps/desktop/package.json`` build section with the runtime taken out
and the seed put in. The main process that ships is ``shell/`` only; the rest
of the Desktop code lives in each sealed version.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

DESKTOP = Path(__file__).resolve().parents[2] / "apps" / "desktop"


def config(seed: str) -> dict:
    build = json.loads((DESKTOP / "package.json").read_text(encoding="utf-8"))["build"]
    build["files"] = ["shell/**"]
    build["extraResources"] = [{"from": seed, "to": "seed", "filter": ["**/*"]}]
    build.pop("afterPack", None)
    if "mac" in build:
        build["mac"] = {key: value for key, value in build["mac"].items() if key != "signIgnore"}
    return build


def main(argv: list[str]) -> int:
    output = Path(argv[1])
    output.write_text(json.dumps(config(argv[2] if len(argv) > 2 else "build/seed"), indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
