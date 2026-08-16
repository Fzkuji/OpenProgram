from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import subprocess
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def _installed_app_version(app_path: Path) -> str:
    resources = app_path / "Contents" / "Resources"
    with (app_path / "Contents" / "Info.plist").open("rb") as stream:
        bundle_version = plistlib.load(stream)["CFBundleShortVersionString"]
    manifest = json.loads(
        (resources / "runtime" / "runtime-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    manifest_version = manifest.get("openprogram")
    if not isinstance(bundle_version, str) or not VERSION_PATTERN.fullmatch(
        bundle_version
    ):
        raise SystemExit(f"invalid installed App version: {bundle_version}")
    if manifest_version != bundle_version:
        raise SystemExit(
            "installed App version mismatch: "
            f"bundle {bundle_version} != runtime manifest {manifest_version}"
        )

    runtime_root = (resources / "runtime").resolve()
    python = (runtime_root / str(manifest.get("python", ""))).resolve()
    try:
        python.relative_to(runtime_root)
    except ValueError as exc:
        raise SystemExit("installed App Python escapes its runtime") from exc
    if not python.is_file():
        raise SystemExit(f"installed App Python is missing: {python}")
    try:
        metadata_version = subprocess.run(
            [
                str(python),
                "-I",
                "-B",
                "-c",
                "import importlib.metadata as m; print(m.version('openprogram'))",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
            env={"PATH": "/usr/bin:/bin", "HOME": os.devnull},
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"could not read installed App metadata: {exc}") from exc
    if metadata_version != bundle_version:
        raise SystemExit(
            "installed App version mismatch: "
            f"bundle {bundle_version} != Python metadata {metadata_version}"
        )
    return bundle_version


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag")
    parser.add_argument("--installed-app", type=Path)
    parser.add_argument("--require-source-match", action="store_true")
    args = parser.parse_args()
    if args.require_source_match and args.installed_app is None:
        parser.error("--require-source-match requires --installed-app")
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
    if args.installed_app is not None:
        installed_version = _installed_app_version(args.installed_app)
        if args.require_source_match and installed_version != python_version:
            errors.append(
                f"source version {python_version} != installed App version "
                f"{installed_version}"
            )
    if errors:
        raise SystemExit("; ".join(errors))
    print(f"release version: {python_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
