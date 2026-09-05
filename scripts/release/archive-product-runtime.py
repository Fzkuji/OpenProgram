#!/usr/bin/env python3
"""Create the Windows product-runtime ZIP with a stable archive layout."""

from __future__ import annotations

import argparse
import os
import stat
import zipfile
from pathlib import Path, PurePosixPath


_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def _filesystem_path(path: Path) -> str:
    """Return a Windows extended-length path without changing archive names."""

    value = str(path.resolve())
    if os.name != "nt" or value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return f"\\\\?\\UNC\\{value[2:]}"
    return f"\\\\?\\{value}"


def _runtime_entries(runtime_root: Path) -> list[tuple[PurePosixPath, str, bool]]:
    """Enumerate content through long-path-safe filesystem handles."""

    entries: list[tuple[PurePosixPath, str, bool]] = []
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)

    def visit(filesystem_dir: str, relative_dir: PurePosixPath) -> None:
        with os.scandir(filesystem_dir) as iterator:
            children = sorted(iterator, key=lambda child: child.name)
        for child in children:
            child_stat = child.stat(follow_symlinks=False)
            attributes = getattr(child_stat, "st_file_attributes", 0)
            if stat.S_ISLNK(child_stat.st_mode) or attributes & reparse_flag:
                raise RuntimeError(f"runtime contains a reparse point: {child.path}")
            relative = relative_dir / child.name
            is_directory = stat.S_ISDIR(child_stat.st_mode)
            entries.append((relative, child.path, is_directory))
            if is_directory:
                visit(child.path, relative)

    visit(_filesystem_path(runtime_root), PurePosixPath())
    return entries


def create_windows_archive(runtime_root: Path, output: Path) -> None:
    """Write a deterministic ZIP rooted at ``runtime/``.

    Windows release runtimes must not contain reparse points. They are not
    portable ZIP content and could make extraction escape the selected release
    directory. Ordinary inherited NTFS permissions are intentionally ignored:
    the archive carries files, not ACL policy.
    """

    runtime_root = runtime_root.resolve()
    if runtime_root.name != "runtime":
        raise RuntimeError(f"runtime root must end in 'runtime': {runtime_root}")
    if not (runtime_root / "runtime-manifest.json").is_file():
        raise RuntimeError(f"runtime manifest not found: {runtime_root}")

    entries = _runtime_entries(runtime_root)

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            allowZip64=True,
        ) as archive:
            root_info = zipfile.ZipInfo("runtime/", _ZIP_EPOCH)
            root_info.create_system = 0
            root_info.external_attr = 0x10  # DOS directory flag; no ACL policy.
            archive.writestr(root_info, b"")
            for relative, filesystem_path, is_directory in entries:
                name = (PurePosixPath("runtime") / relative).as_posix()
                if is_directory:
                    info = zipfile.ZipInfo(f"{name}/", _ZIP_EPOCH)
                    info.create_system = 0
                    info.external_attr = 0x10
                    archive.writestr(info, b"")
                    continue
                info = zipfile.ZipInfo(name, _ZIP_EPOCH)
                info.create_system = 0
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0x20  # DOS archive flag; file stays writable.
                with open(filesystem_path, "rb") as source, archive.open(
                    info, "w"
                ) as target:
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("runtime_root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    create_windows_archive(args.runtime_root, args.output)
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
