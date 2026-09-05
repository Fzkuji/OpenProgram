from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def _installed_app_version(app_path: Path) -> str:
    # This script also runs before the checkout is installed. Import only the
    # stdlib-only compatibility seam; metadata below comes from the App's own
    # isolated interpreter, never from this checkout or the caller's PATH.
    sys.path.insert(0, str(ROOT))
    try:
        from openprogram._compat import desktop_bundle_metadata, no_window_creation_flags
    finally:
        sys.path.pop(0)
    try:
        resources, bundle_version = desktop_bundle_metadata(app_path)
    except (OSError, ValueError, KeyError) as exc:
        raise SystemExit(f"could not read installed App version: {exc}") from exc
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
            encoding="utf-8",
            creationflags=no_window_creation_flags(),
            env={"PATH": os.defpath, "HOME": os.devnull,
                 **{name: os.environ[name] for name in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP")
                    if name in os.environ}},
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"could not read installed App metadata: {exc}") from exc
    if metadata_version != bundle_version:
        raise SystemExit(
            "installed App version mismatch: "
            f"bundle {bundle_version} != Python metadata {metadata_version}"
        )
    return bundle_version


def _wheel_version(wheel_path: Path) -> str:
    try:
        with zipfile.ZipFile(wheel_path) as archive:
            metadata_files = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA")
            ]
            if len(metadata_files) != 1:
                raise SystemExit(
                    "OpenProgram wheel must contain exactly one dist-info METADATA"
                )
            metadata = BytesParser().parsebytes(
                archive.read(metadata_files[0]), headersonly=True
            )
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise SystemExit(f"could not read OpenProgram wheel metadata: {exc}") from exc
    name = metadata.get("Name", "").strip().lower().replace("_", "-")
    version = metadata.get("Version", "").strip()
    if name != "openprogram":
        raise SystemExit(f"unexpected wheel project: {name or '<missing>'}")
    if not VERSION_PATTERN.fullmatch(version):
        raise SystemExit(f"invalid OpenProgram wheel version: {version or '<missing>'}")
    return version


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag")
    parser.add_argument("--installed-app", type=Path)
    parser.add_argument("--require-source-match", action="store_true")
    parser.add_argument("--wheel", type=Path)
    args = parser.parse_args()
    if args.require_source_match and args.installed_app is None:
        parser.error("--require-source-match requires --installed-app")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    python_version = project["project"]["version"]
    desktop_version = json.loads(
        (ROOT / "apps" / "desktop" / "package.json").read_text(encoding="utf-8")
    )["version"]
    installer = (ROOT / "scripts" / "release" / "install-release.sh").read_text(
        encoding="utf-8"
    )
    windows_installer = (
        ROOT / "scripts" / "release" / "install-release.ps1"
    ).read_text(encoding="utf-8")
    expected_tag = f"v{python_version}"
    errors = []
    if desktop_version != python_version:
        errors.append(
            f"desktop version {desktop_version} != Python version {python_version}"
        )
    if args.tag and args.tag != expected_tag:
        errors.append(f"tag {args.tag} != {expected_tag}")
    expected_default = (
        f'OPENPROGRAM_VERSION="${{OPENPROGRAM_VERSION:-{python_version}}}"'
    )
    if expected_default not in installer:
        errors.append("POSIX release installer default version does not match project version")
    windows_default = (
        'if ($env:OPENPROGRAM_VERSION) { $env:OPENPROGRAM_VERSION } '
        f'else {{ "{python_version}" }}'
    )
    if windows_default not in windows_installer:
        errors.append(
            "Windows release installer default version does not match project version"
        )
    if args.installed_app is not None:
        installed_version = _installed_app_version(args.installed_app)
        if args.require_source_match and installed_version != python_version:
            errors.append(
                f"source version {python_version} != installed App version "
                f"{installed_version}"
            )
    if args.wheel is not None:
        wheel_version = _wheel_version(args.wheel)
        if wheel_version != python_version:
            errors.append(
                f"wheel version {wheel_version} != source version {python_version}"
            )
    if errors:
        raise SystemExit("; ".join(errors))
    print(f"release version: {python_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
