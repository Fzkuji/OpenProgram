#!/usr/bin/env python3
"""Restore unpacked modes lost by ASAR extraction before local repacking."""
import os
from pathlib import Path
import stat
import sys


def regular_without_links(path: Path, root: Path) -> bool:
    return (not any(parent.is_symlink() for parent in [path, *path.parents]
                    if parent != root and root in parent.parents)
            and path.is_file() and not path.is_symlink())


def restore_permissions(archive: Path, stage: Path) -> None:
    unpacked = Path(f"{archive}.unpacked")
    for directory, _, filenames in os.walk(unpacked, followlinks=False):
        for filename in filenames:
            source = Path(directory) / filename
            target = stage / source.relative_to(unpacked)
            if (stat.S_ISREG(source.lstat().st_mode)
                    and regular_without_links(target, stage)):
                target.chmod(stat.S_IMODE(source.stat().st_mode))

    # ASAR has no executable metadata for unpacked files. Repair installations
    # already affected by an earlier refresh, only for node-pty's known helper.
    package = stage / "node_modules/node-pty"
    helpers = [*package.glob("prebuilds/*/spawn-helper"),
               package / "build/Release/spawn-helper"]
    for helper in helpers:
        if regular_without_links(helper, stage):
            helper.chmod(helper.stat().st_mode | stat.S_IXUSR)


if __name__ == "__main__":
    restore_permissions(Path(sys.argv[1]), Path(sys.argv[2]))
