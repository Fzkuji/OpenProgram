"""Sealed versions: writing the seal, reading it back, and boot records.

A sealed version carries ``files.tsv`` (relative path, size, mtime in
nanoseconds, for every sealed file), ``pristine.zip`` (every sealed file except
the frontend bundle) and ``version.json``. The bundle is the same between most
versions, so one zip per distinct bundle is kept in ``pristine-frontend/``.
"""
from __future__ import annotations

import contextlib
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import threading
import time
from typing import Iterable, Iterator
import zipfile

from . import layout as paths
from .layout import Layout, valid_id
from openprogram.host._platform import filesystem

VERSION_FILE = "version.json"
FILES_FILE = "files.tsv"
PRISTINE_FILE = "pristine.zip"
SEAL_FILES = frozenset({VERSION_FILE, FILES_FILE, PRISTINE_FILE})
REQUIRED_MANIFEST = (
    "id", "commit", "base", "shell_compat", "built_by", "built_at",
    "components", "parent", "frontend_sha",
)
ATTEMPT_WINDOW_SECONDS = 600


class SealError(RuntimeError):
    """A version cannot be sealed, or its seal does not hold."""


def iter_files(version_dir: Path) -> Iterator[str]:
    """Every sealed file and symlink, as a ``/`` path, sorted by bytes."""
    found: list[str] = []
    for current, directories, names in os.walk(version_dir):
        directory = Path(current)
        for name in list(directories):
            if (directory / name).is_symlink():
                names.append(name)
                directories.remove(name)
        for name in names:
            relative = (directory / name).relative_to(version_dir).as_posix()
            if relative in SEAL_FILES:
                continue
            if "\t" in relative or "\n" in relative or "\r" in relative:
                raise SealError(f"a sealed path has a control character: {relative!r}")
            found.append(relative)
    yield from sorted(found, key=lambda value: value.encode("utf-8"))


def _hash_entry(path: Path) -> bytes:
    if path.is_symlink():
        return hashlib.sha256(os.readlink(path).encode("utf-8")).digest()
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.digest()


def bundle_digest(bundle: Path) -> str:
    """Content identity of a frontend bundle."""
    digest = hashlib.sha256()
    for relative in iter_files(bundle):
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(_hash_entry(bundle / relative))
    return digest.hexdigest()


def _zip_entry(archive: zipfile.ZipFile, source: Path, name: str) -> None:
    info = source.lstat()
    entry = zipfile.ZipInfo(name, date_time=time.gmtime(max(info.st_mtime, 315532800))[:6])
    entry.create_system = 3
    entry.external_attr = (info.st_mode & 0xFFFF) << 16
    if stat.S_ISLNK(info.st_mode):
        archive.writestr(entry, os.readlink(source).encode("utf-8"))
        return
    entry.compress_type = zipfile.ZIP_DEFLATED
    with open(source, "rb") as stream, archive.open(entry, "w", force_zip64=True) as output:
        shutil.copyfileobj(stream, output, 1 << 20)


def _write_zip(target: Path, root: Path, names: Iterable[str]) -> None:
    temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", allowZip64=True) as archive:
            for name in names:
                _zip_entry(archive, root / name, name)
        with open(temporary, "r+b") as stream:  # Windows flushes only a handle opened for writing.
            os.fsync(stream.fileno())
        filesystem.replace_file(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_tree(root: Path) -> None:
    if os.name == "nt":
        return  # Directory handles cannot be fsynced; files were closed flushed.
    for current, directories, names in os.walk(root):
        directory = Path(current)
        for name in names:
            path = directory / name
            if not path.is_symlink():
                with open(path, "rb") as stream:
                    os.fsync(stream.fileno())
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def write_files_tsv(version_dir: Path) -> None:
    """Record every sealed file as it is now."""
    lines: list[str] = []
    for relative in iter_files(version_dir):
        info = (version_dir / relative).lstat()
        lines.append(f"{relative}\t{info.st_size}\t{info.st_mtime_ns}\n")
    (version_dir / FILES_FILE).write_text("".join(lines), encoding="utf-8", newline="\n")


def read_files_tsv(version_dir: Path) -> dict[str, tuple[int, int]]:
    recorded: dict[str, tuple[int, int]] = {}
    for line in (version_dir / FILES_FILE).read_text(encoding="utf-8").splitlines():
        relative, size, mtime = line.split("\t")
        recorded[relative] = (int(size), int(mtime))
    return recorded


def seal(layout: Layout, staging: Path, manifest: dict) -> Path:
    """Finish ``staging`` as ``versions/<id>`` and return that path.

    Files are not marked read-only: a direct edit is noticed by the launcher,
    and read-only trees are awkward to delete on Windows.
    """
    missing = [key for key in REQUIRED_MANIFEST if key not in manifest and key != "frontend_sha"]
    if missing:
        raise SealError(f"version.json is missing {', '.join(missing)}")
    version_id = paths.require_id(manifest["id"], "version id")
    target = layout.version(version_id)
    if target.exists():
        raise SealError(f"version {version_id} already exists")
    site = paths.site_packages(staging, manifest["base"])
    bundle = paths.frontend_bundle(site)
    frontend_prefix = bundle.relative_to(staging).as_posix() + "/"
    frontend_sha = bundle_digest(bundle)
    layout.frontends.mkdir(parents=True, exist_ok=True)
    frontend_zip = layout.frontends / f"{frontend_sha}.zip"
    if not frontend_zip.exists():
        _write_zip(frontend_zip, bundle, iter_files(bundle))
    sealed = list(iter_files(staging))
    _write_zip(
        staging / PRISTINE_FILE, staging,
        (name for name in sealed if not name.startswith(frontend_prefix)),
    )
    (staging / VERSION_FILE).write_text(
        json.dumps({**manifest, "frontend_sha": frontend_sha}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_files_tsv(staging)
    _fsync_tree(staging)
    filesystem.replace_file(staging, target)
    return target


def changed_files(version_dir: Path, base_id: str) -> list[str]:
    """What differs from the seal: the same check the launcher makes."""
    recorded = read_files_tsv(version_dir)
    differences = []
    for relative, (size, mtime) in recorded.items():
        try:
            info = (version_dir / relative).lstat()
        except OSError:
            differences.append(relative)
            continue
        if info.st_size != size or info.st_mtime_ns != mtime:
            differences.append(relative)
    site = paths.site_packages(version_dir, base_id) / "openprogram"
    for root in (site, version_dir / "desktop"):
        for current, directories, names in os.walk(root):
            if Path(current).name == "__pycache__":
                directories[:] = []
                continue
            for name in names:
                relative = (Path(current) / name).relative_to(version_dir).as_posix()
                if relative not in recorded:
                    differences.append(relative)
    return differences


def read_manifest(layout: Layout, version_id: str) -> dict:
    try:
        manifest = json.loads((layout.version(version_id) / VERSION_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SealError(f"version {version_id} has no readable version.json: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("id") != version_id:
        raise SealError(f"version {version_id} has a mismatched version.json")
    return manifest


def list_versions(layout: Layout) -> list[dict]:
    """Sealed versions, newest first. A directory without a seal is skipped."""
    if not layout.versions.is_dir():
        return []
    manifests = []
    for entry in layout.versions.iterdir():
        if entry.name.startswith(".") or not valid_id(entry.name):
            continue
        try:
            manifests.append(read_manifest(layout, entry.name))
        except SealError:
            continue
    return sorted(manifests, key=lambda item: str(item["id"]), reverse=True)


def committed_versions(layout: Layout) -> set[str] | None:
    """Versions a switch committed, from their pinning refs; ``None`` if unknown."""
    try:
        result = subprocess.run(
            ["git", "-C", str(layout.repo), "for-each-ref", "--format=%(refname)", "refs/openprogram/version/"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return {line.rsplit("/", 1)[-1] for line in result.stdout.splitlines() if line}


def recent_attempts(layout: Layout, version_id: str, *, now: float | None = None) -> int:
    """Abnormal starts of a version within the launcher's 10-minute window."""
    moment = time.time() if now is None else now
    try:
        lines = layout.attempts(version_id).read_text(encoding="ascii").split()
    except (OSError, UnicodeDecodeError):
        return 0
    return sum(1 for line in lines if line.isdigit() and moment - int(line) < ATTEMPT_WINDOW_SECONDS)


MIGRATION_TOUCH_SECONDS = 5


@contextmanager
def migration_progress() -> Iterator[None]:
    """Show, while stores migrate at start, that this version is not stuck.

    The controller and the trial extend their start limits for as long as
    ``boot/<id>.migrating`` keeps moving. Outside a version store this is a no-op.
    """
    runtime = paths.current_runtime()
    if runtime is None:
        yield
        return
    marker = runtime.layout.migrating(runtime.version)
    done = threading.Event()

    def touch() -> None:
        while not done.wait(MIGRATION_TOUCH_SECONDS):
            with contextlib.suppress(OSError):
                marker.touch()

    with contextlib.suppress(OSError):
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    thread = threading.Thread(target=touch, name="migration-progress", daemon=True)
    thread.start()
    try:
        yield
    finally:
        done.set()
        thread.join(timeout=1)
        with contextlib.suppress(OSError):
            marker.unlink()


def forget_last_attempt(layout: Layout, version_id: str) -> None:
    """Take back this start's record: it lost the worker lock, so it is not an abnormal end."""
    try:
        with open(layout.attempts(version_id), "r+", encoding="ascii") as stream:
            lines = stream.read().split()
            stream.seek(0)
            stream.truncate(0)
            stream.write("".join(f"{line}\n" for line in lines[:-1]))
    except (OSError, UnicodeDecodeError):
        pass


def clear_attempts(layout: Layout, version_id: str) -> None:
    """Forget start records: the version shut down cleanly or served stably."""
    try:
        with open(layout.attempts(version_id), "r+", encoding="ascii") as stream:
            stream.truncate(0)
    except OSError:
        pass
