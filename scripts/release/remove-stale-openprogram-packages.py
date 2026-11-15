"""Remove only validated OpenProgram package directories before reinstall."""

from __future__ import annotations

import shutil
import sys
import sysconfig
from pathlib import Path


# The Server and CLI now install inside ``openprogram`` as
# openprogram/interfaces/{server,cli}; releases before that shipped them as the
# top-level ``openprogram_server`` and ``openprogram_cli`` packages, which a
# reinstall over such a runtime must still remove.
PACKAGE_NAMES = ("openprogram", "openprogram_server", "openprogram_cli")


def validated_package_targets(site_packages: Path) -> list[Path]:
    site_packages = site_packages.resolve()
    targets: list[Path] = []
    for package_name in PACKAGE_NAMES:
        package = site_packages / package_name
        if package.is_symlink():
            raise RuntimeError(f"refusing symlinked package path: {package}")
        resolved = package.resolve()
        if resolved.parent != site_packages:
            raise RuntimeError(f"package path escapes site-packages: {resolved}")
        if package.exists() and not package.is_dir():
            raise RuntimeError(f"package path is not a directory: {package}")
        if package.is_dir():
            targets.append(package)
    # Uninstalling an older version can leave its dist-info holding only a
    # LICENSE that RECORD did not list. pip then reads it as "openprogram None"
    # and refuses the next install, so a record without METADATA goes too.
    for record in sorted(site_packages.glob("openprogram-*.dist-info")):
        if record.is_dir() and not record.is_symlink() and not (record / "METADATA").exists():
            targets.append(record)
    return targets


def validate_stale_package_trees(site_packages: Path) -> None:
    validated_package_targets(site_packages)


def remove_stale_package_trees(site_packages: Path) -> None:
    targets = validated_package_targets(site_packages)

    for package in targets:
        shutil.rmtree(package)


def main() -> int:
    if len(sys.argv) not in {2, 3} or (len(sys.argv) == 3 and sys.argv[2] != "--check"):
        raise SystemExit(
            "usage: remove-stale-openprogram-packages.py PYTHON [--check]"
        )
    if Path(sys.executable).resolve() != Path(sys.argv[1]).resolve():
        return 0
    site_packages = Path(sysconfig.get_paths()["purelib"])
    if len(sys.argv) == 3:
        validate_stale_package_trees(site_packages)
    else:
        remove_stale_package_trees(site_packages)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
