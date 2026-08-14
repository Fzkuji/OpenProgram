from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag")
    args = parser.parse_args()
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    python_version = project["project"]["version"]
    desktop_version = json.loads(
        (ROOT / "desktop" / "package.json").read_text(encoding="utf-8")
    )["version"]
    expected_tag = f"v{python_version}"
    errors = []
    if desktop_version != python_version:
        errors.append(
            f"desktop version {desktop_version} != Python version {python_version}"
        )
    if args.tag and args.tag != expected_tag:
        errors.append(f"tag {args.tag} != {expected_tag}")
    if errors:
        raise SystemExit("; ".join(errors))
    print(f"release version: {python_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
