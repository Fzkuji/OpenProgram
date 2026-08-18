#!/usr/bin/env python3
"""Create the machine-readable manifest for already-built release artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact_root", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    output = args.output.resolve()
    files = []
    for path in sorted(args.artifact_root.rglob("*")):
        if not path.is_file() or path.resolve() == output:
            continue
        files.append(
            {
                "path": path.relative_to(args.artifact_root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    if not files:
        parser.error("artifact root contains no files")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {"schema": 1, "version": args.version.removeprefix("v"), "files": files},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
