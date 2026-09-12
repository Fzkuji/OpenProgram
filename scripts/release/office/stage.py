#!/usr/bin/env python3
"""Stage a verified Office pack and its small, lazy parent module before build."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from openprogram.office_assets import (  # noqa: E402
    OFFICE_PATCH_SHA256, install_office_pack, prepared_office_cache,
    validate_prepared_office_pack,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--web-root", type=Path)
    args = parser.parse_args()
    source = args.source
    if source is None:
        source = args.output if args.output.exists() else prepared_office_cache()
    pack = validate_prepared_office_pack(source)
    if source.resolve() != args.output.resolve():
        install_office_pack(source, args.output)
    if args.web_root:
        relative = "npm/public-api.js"
        if relative not in pack.assets:
            raise ValueError("Office parent module is missing")
        destination = args.web_root / "document-assets" / "office" / OFFICE_PATCH_SHA256
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pack.root / relative, destination / "public-api.js")
    print(f"Office resources verified: {pack.root}")


if __name__ == "__main__":
    main()
