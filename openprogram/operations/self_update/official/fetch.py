"""Find the latest official release and bring it here over the network.

The order is what keeps a bad server harmless:

1. Ask which release is latest (``github_release.github``).
2. Read ``update-manifest.json``, its signature and ``SHA256SUMS``, and check
   them in memory. Nothing is written until they pass.
3. Download only the files this machine lacks for its platform into a staging
   directory, each held to the size in the signed manifest and the hash in the
   signed sums. A download that stops resumes on the next run.
4. Hand the directory to the update controller, which checks everything again.

A refusal removes what was downloaded and changes nothing else.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
import importlib.metadata
from pathlib import Path
import re
import shutil
import sys
from typing import Iterator

from openprogram.host._platform import locking
from openprogram.operations.self_update.github_release import github
from openprogram.operations.self_update.versions.layout import Layout, current_runtime, disk_shortfall

from . import assets, replay
from .assets import Release, ReleaseError

DISK_FACTOR = 3  # The archives, plus what they unpack to.
GIB = 1024 ** 3
PROXY_HINT = "Check your connection (behind a proxy, set HTTPS_PROXY) and run `openprogram upgrade` again."
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")


@dataclass(frozen=True)
class Latest:
    """The newest release and where its files are."""

    release: str
    source: dict
    notes: str


def installed_version() -> str | None:
    try:
        version = importlib.metadata.version("openprogram")
    except importlib.metadata.PackageNotFoundError:
        return None
    return version if _VERSION.fullmatch(version) else None


def is_newer(candidate: str, current: str | None) -> bool:
    """Whether ``candidate`` is ahead; an unknown current version counts as behind."""
    if current is None:
        return True
    return tuple(map(int, candidate.split("."))) > tuple(map(int, current.split(".")))


def find_latest() -> Latest:
    found = github.latest_release()
    if found is None:
        raise ReleaseError(f"The latest release could not be read. {PROXY_HINT}")
    return Latest(str(found["tag_name"])[1:], github.release_source(found), str(found.get("body") or ""))


def _small_file(latest: Latest, name: str) -> bytes:
    content = github.release_file(latest.source, latest.release, name)
    if content is None:
        raise ReleaseError(
            f"Release {latest.release} has no {name} that could be read, so it cannot be applied here. "
            f"If it was just published, its files may still be uploading. {PROXY_HINT}"
        )
    return content


def _inspect(
    latest: Latest, directory: Path, *, platform: str | None, trusted_keys: Iterable[str] | None,
) -> tuple[Release, bytes, bytes, bytes]:
    data = _small_file(latest, assets.MANIFEST_FILE)
    if len(data) > assets.MAX_MANIFEST_BYTES:
        raise ReleaseError("the release manifest is too large")
    signature = _small_file(latest, assets.SIGNATURE_FILE)
    sums = _small_file(latest, assets.SUMS_FILE)
    release = assets.inspect_release(
        data, signature.decode("ascii", "replace"), sums,
        directory=directory, platform=platform, trusted_keys=trusted_keys,
    )
    if release.release != latest.release:
        raise ReleaseError(f"the files of release {latest.release} describe release {release.release}")
    for name in release.files:
        if name not in release.asset_bytes:
            raise ReleaseError(f"the release manifest does not give the size of {name}")
    return release, data, signature, sums


def check(
    *, layout: Layout | None = None, platform: str | None = None, trusted_keys: Iterable[str] | None = None,
) -> dict:
    """What ``openprogram upgrade --check`` reports. Downloads only the small files."""
    latest = find_latest()
    current = installed_version()
    status = {
        "current_version": current,
        "latest_version": latest.release,
        "update_available": is_newer(latest.release, current),
        "can_apply": False,
        "detail": "",
        "download_bytes": 0,
        "found_through": latest.source["via"],
        "release_notes": latest.notes,
    }
    if not status["update_available"]:
        return status
    if layout is None:
        runtime = current_runtime()
        layout = runtime.layout if runtime else None
    try:
        release, *_ = _inspect(latest, Path("."), platform=platform, trusted_keys=trusted_keys)
        waiting = assets.needs_new_shell(layout, release) if layout else None
    except ReleaseError as exc:
        status["detail"] = str(exc)
        return status
    names = assets.assets_to_download(layout, release) if layout else release.files
    status["download_bytes"] = sum(release.asset_bytes[name] for name in names)
    status["can_apply"] = waiting is None
    status["detail"] = waiting or ""
    return status


def _downloads(layout: Layout) -> Path:
    return layout.root / "downloads"


def _free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


@contextmanager
def _download_lock(layout: Layout) -> Iterator[None]:
    with open(layout.root / "downloads.lock", "a+b") as stream:
        try:
            locking.flock(stream.fileno(), locking.LOCK_EX | locking.LOCK_NB)
        except BlockingIOError as exc:
            raise ReleaseError("Another update is downloading; try again when it finishes.") from exc
        try:
            yield
        finally:
            locking.flock(stream.fileno(), locking.LOCK_UN)


def _discard(directory: Path) -> None:
    shutil.rmtree(directory, ignore_errors=True)
    try:
        directory.parent.rmdir()  # Only when it holds nothing else.
    except OSError:
        pass


def _check_space(layout: Layout, release: Release) -> None:
    required = sum(release.asset_bytes[name] for name in assets.assets_to_download(layout, release)) * DISK_FACTOR
    free = _free_bytes(layout.root)
    if free < required:
        raise ReleaseError(
            f"Not enough disk space for this update: it needs about {required / GIB:.1f} GB and "
            f"{free / GIB:.1f} GB is free, {disk_shortfall(required, free)} short. "
            "Free some space and run `openprogram upgrade` again."
        )


def _fetch_asset(latest: Latest, release: Release, directory: Path, name: str, *, say) -> None:
    target = directory / name
    size, digest = release.asset_bytes[name], release.sums[name]
    if target.is_file():
        if target.stat().st_size == size and assets.sha256_file(target) == digest:
            return
        target.unlink()
    if say is not None:
        say(f"Downloading {name} ({size / (1024 ** 2):.1f} MB)")
    github.download_release_file(
        github.release_file_url(latest.source, latest.release, name), target, size=size,
        progress=say is not None and sys.stderr.isatty(),
    )
    if assets.sha256_file(target) != digest:
        target.unlink()
        raise github.DownloadFailed(f"{name} does not match its checksum", permanent=True)


def download(
    latest: Latest, layout: Layout, *, platform: str | None = None,
    trusted_keys: Iterable[str] | None = None, say: Callable[[str], None] | None = None,
) -> Path:
    """Verified files of ``latest`` in a staging directory, ready for the controller."""
    directory = _downloads(layout) / latest.release
    release, data, signature, sums = _inspect(latest, directory, platform=platform, trusted_keys=trusted_keys)
    _check_space(layout, release)
    with _download_lock(layout):
        if _downloads(layout).is_dir():
            for other in _downloads(layout).iterdir():
                if other.name != latest.release:
                    shutil.rmtree(other, ignore_errors=True)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / assets.MANIFEST_FILE).write_bytes(data)
        (directory / assets.SIGNATURE_FILE).write_bytes(signature)
        (directory / assets.SUMS_FILE).write_bytes(sums)
        try:
            for name in assets.assets_to_download(layout, release):
                _fetch_asset(latest, release, directory, name, say=say)
        except github.DownloadFailed as exc:
            if exc.permanent:
                _discard(directory)
                raise ReleaseError(f"Release {latest.release} was refused: {exc}. Nothing was changed.") from exc
            raise ReleaseError(f"The download stopped: {exc}. {PROXY_HINT} It resumes from where it stopped.") from exc
        except OSError as exc:
            raise ReleaseError(f"The download could not be saved: {exc}. Nothing was changed.") from exc
    return directory


def update(
    *, layout: Layout | None = None, apply: Callable[[Path], replay.ReplayResult] | None = None,
    platform: str | None = None, trusted_keys: Iterable[str] | None = None,
    say: Callable[[str], None] | None = None,
) -> replay.ReplayResult:
    """Bring this installation to the latest release, if it is behind."""
    if layout is None:
        runtime = current_runtime()
        if runtime is None:
            raise ReleaseError("this installation does not run from the version store")
        layout = runtime.layout
    latest = find_latest()
    current = installed_version()
    if not is_newer(latest.release, current):
        return replay.ReplayResult(
            outcome=replay.UP_TO_DATE, release=latest.release,
            message=f"OpenProgram {current} is the latest version.",
        )
    directory = download(latest, layout, platform=platform, trusted_keys=trusted_keys, say=say)
    if apply is None:
        from openprogram.operations.self_update.control.official_update import apply_release

        apply = lambda files: apply_release(files, platform=platform, trusted_keys=trusted_keys)  # noqa: E731
    result = apply(directory)
    # Kept when the same files are still wanted: another update was running,
    # or the release is waiting for a new shell.
    if result.release is not None and result.outcome != replay.WAITING_FOR_SHELL:
        _discard(directory)
    return result


__all__ = ["Latest", "check", "download", "find_latest", "installed_version", "is_newer", "update"]
