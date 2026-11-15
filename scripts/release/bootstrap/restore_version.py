#!/usr/bin/env python3
"""Put a sealed version back after its files were edited directly.

Run by the launcher as ``<base python> -I -B bootstrap/restore_version.py
<version>`` when the seal does not match. Standard library only, and not part
of any version. Exits 0 when the version is clean again.

1. Compare real content with the pristine zips. If only metadata differs,
   rewrite ``files.tsv`` and stop.
2. Save changed and added files, and a patch with repository-relative paths,
   under ``unapplied/<stamp>/``.
3. Restore from the zips and remove added files, except ``__pycache__``.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import zipfile

SEAL_FILES = {"version.json", "files.tsv", "pristine.zip"}
_FRONTEND = "openprogram/interfaces/server/_frontend/"


class CaptureError(RuntimeError):
    pass


def _site_prefix(manifest: dict) -> str:
    if os.name == "nt":
        return "venv/Lib/site-packages/"
    minor = str(manifest["base"]).split("-", 1)[0].removeprefix("py")
    return f"venv/lib/python{minor}/site-packages/"


def _iter_tree(root: Path) -> list[str]:
    found = []
    for current, directories, names in os.walk(root):
        directory = Path(current)
        for name in list(directories):
            if (directory / name).is_symlink():
                names.append(name)
                directories.remove(name)
        for name in names:
            found.append((directory / name).relative_to(root).as_posix())
    return found


class _Pristine:
    """Sealed content by relative path, across both zips."""

    def __init__(self, version_dir: Path, manifest: dict) -> None:
        self.frontend_prefix = _site_prefix(manifest) + _FRONTEND
        self.main = zipfile.ZipFile(version_dir / "pristine.zip")
        frontend = version_dir.parent.parent / "pristine-frontend" / f"{manifest['frontend_sha']}.zip"
        self.frontend = zipfile.ZipFile(frontend)
        self.entries: dict[str, tuple[zipfile.ZipFile, zipfile.ZipInfo]] = {}
        for info in self.main.infolist():
            self.entries[info.filename] = (self.main, info)
        for info in self.frontend.infolist():
            self.entries[self.frontend_prefix + info.filename] = (self.frontend, info)

    def read(self, name: str) -> bytes:
        archive, info = self.entries[name]
        return archive.read(info)

    def mode(self, name: str) -> int:
        return self.entries[name][1].external_attr >> 16

    def close(self) -> None:
        self.main.close()
        self.frontend.close()


def _current_bytes(path: Path) -> bytes:
    if path.is_symlink():
        return os.readlink(path).encode("utf-8")
    return path.read_bytes()


def _is_bytecode(name: str) -> bool:
    return "__pycache__/" in name


def _repository_path(name: str, manifest: dict) -> str:
    """Where a sealed file lives in the repository."""
    prefix = _site_prefix(manifest)
    if name.startswith(prefix):
        return name[len(prefix):]
    if name.startswith("desktop/"):
        return "apps/" + name
    return name


def _patch(old: bytes | None, new: bytes | None, path: str) -> str:
    try:
        before = (old or b"").decode("utf-8").splitlines(keepends=True)
        after = (new or b"").decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return f"# binary file not included in this patch: {path}\n"
    lines = list(difflib.unified_diff(
        before, after,
        fromfile="/dev/null" if old is None else f"a/{path}",
        tofile="/dev/null" if new is None else f"b/{path}",
    ))
    return "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines)


def _write_tsv(version_dir: Path) -> None:
    lines = []
    names = sorted(
        (name for name in _iter_tree(version_dir) if name not in SEAL_FILES),
        key=lambda value: value.encode("utf-8"),
    )
    for name in names:
        info = (version_dir / name).lstat()
        lines.append(f"{name}\t{info.st_size}\t{info.st_mtime_ns}\n")
    temporary = version_dir / f"files.tsv.{os.getpid()}.tmp"
    temporary.write_text("".join(lines), encoding="utf-8", newline="\n")
    os.replace(temporary, version_dir / "files.tsv")


def _restore_file(pristine: _Pristine, version_dir: Path, name: str) -> None:
    target = version_dir / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)
    mode = pristine.mode(name)
    data = pristine.read(name)
    if stat.S_ISLNK(mode):
        os.symlink(data.decode("utf-8"), target)
        return
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    with open(temporary, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if stat.S_IMODE(mode):
        os.chmod(temporary, stat.S_IMODE(mode))
    os.replace(temporary, target)


def capture(root: Path, version: str) -> dict:
    version_dir = root / "versions" / version
    manifest = json.loads((version_dir / "version.json").read_text(encoding="utf-8"))
    if manifest.get("id") != version:
        raise CaptureError("version.json does not name this version")
    pristine = _Pristine(version_dir, manifest)
    try:
        present = {name for name in _iter_tree(version_dir) if name not in SEAL_FILES}
        sealed = set(pristine.entries)
        added = sorted(name for name in present - sealed if not _is_bytecode(name))
        removed = sorted(name for name in sealed - present if not _is_bytecode(name))
        changed = sorted(
            name for name in present & sealed
            if not _is_bytecode(name) and _current_bytes(version_dir / name) != pristine.read(name)
        )
        restored_bytecode = sorted(
            name for name in sealed
            if _is_bytecode(name) and (
                name not in present or _current_bytes(version_dir / name) != pristine.read(name)
            )
        )
        if not added and not removed and not changed and not restored_bytecode:
            _write_tsv(version_dir)
            return {"applied": False, "changed": [], "added": [], "removed": []}

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        saved = None
        if added or removed or changed:
            saved = root / "unapplied" / stamp
            (saved / "files").mkdir(parents=True)
            patch_parts: list[str] = []
            for name in [*changed, *added]:
                path = _repository_path(name, manifest)
                destination = saved / "files" / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                source = version_dir / name
                if source.is_symlink():
                    (saved / "files" / f"{path}.symlink").write_text(os.readlink(source), encoding="utf-8")
                else:
                    shutil.copyfile(source, destination)
                old = pristine.read(name) if name in sealed else None
                patch_parts.append(_patch(old, _current_bytes(source), path))
            for name in removed:
                patch_parts.append(_patch(pristine.read(name), None, _repository_path(name, manifest)))
            (saved / "changes.patch").write_text("".join(patch_parts), encoding="utf-8")
            (saved / "manifest.json").write_text(json.dumps({
                "version": version,
                "commit": manifest.get("commit"),
                "changed": [_repository_path(name, manifest) for name in changed],
                "added": [_repository_path(name, manifest) for name in added],
                "removed": [_repository_path(name, manifest) for name in removed],
                "reported": False,
            }, indent=2) + "\n", encoding="utf-8")

        for name in added:
            (version_dir / name).unlink()
        for name in [*changed, *removed, *restored_bytecode]:
            _restore_file(pristine, version_dir, name)
        _write_tsv(version_dir)
        return {
            "applied": saved is not None,
            "saved": None if saved is None else str(saved),
            "changed": changed, "added": added, "removed": removed,
        }
    finally:
        pristine.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("version")
    arguments = parser.parse_args(argv)
    try:
        result = capture(Path(arguments.root), arguments.version)
    except (CaptureError, OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        print(f"restore_version: {exc}", file=sys.stderr)
        return 1
    # The launcher's stdout may be the worker's or a protocol stream.
    print(json.dumps(result), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
