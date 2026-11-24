"""Explicit installation of the pinned optional Office component.

Every release from :data:`FIRST_RELEASE_WITH_OFFICE` uploads the pinned
archive under :data:`OFFICE_ARCHIVE_NAME`. An installed version downloads it
from its own release first, so it keeps working after newer releases change
the pack, then from the newest release, which serves a build that has no
release of its own while the pin is unchanged. The size and SHA-256 below are
verified whichever release answered.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path


from .assets import (
    MAX_ASSETS, OfficeAssetPack, _safe_relative, install_office_pack,
    prepared_office_cache, validate_prepared_office_pack,
)

OFFICE_RELEASES = "https://github.com/Fzkuji/OpenProgram/releases"
OFFICE_ARCHIVE_NAME = "OfficeAssets-d15d12b-dc31dd9d-24d2d8e1.zip"
OFFICE_ARCHIVE_SHA256 = "30b98a46d2f21f8050174f28922f671d91c7dccb25e2718eacaf8bf23ba1aabd"
OFFICE_DOWNLOAD_BYTES = 732695641
#: The first release that uploads the Office archive; earlier ones carry none.
FIRST_RELEASE_WITH_OFFICE = "1.3.0"
MAX_EXTRACTED_BYTES = 4 * 1024**3
_RELEASE_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")


class OfficeArchiveUnpublished(RuntimeError):
    """No release reachable from this version carries the pinned archive.

    ``update_to`` is the first release that does when this version predates
    it, else ``None`` (update to the newest release).
    """

    def __init__(self, version: str | None) -> None:
        self.version = version
        self.update_to = FIRST_RELEASE_WITH_OFFICE if _predates_office(version) else None
        if self.update_to:
            message = (
                f"OpenProgram {version} cannot download Office support: releases before "
                f"{self.update_to} do not include it. Update OpenProgram to {self.update_to} "
                "or newer, then install again."
            )
        else:
            message = (
                "No OpenProgram release offers the Office files this version needs. "
                "Update OpenProgram to the newest version, then install again."
            )
        super().__init__(message)


def _release_parts(version: str | None) -> tuple[int, ...] | None:
    match = _RELEASE_VERSION.fullmatch(version or "")
    return tuple(int(part) for part in match.groups()) if match else None


def _predates_office(version: str | None) -> bool:
    parts = _release_parts(version)
    return parts is not None and parts < _release_parts(FIRST_RELEASE_WITH_OFFICE)


def installed_version() -> str | None:
    """The installed ``openprogram`` version, ``None`` from a bare checkout."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("openprogram")
    except PackageNotFoundError:
        return None


def office_download_urls(version: str | None) -> list[str]:
    """Where the pinned archive is, in the order to ask."""
    urls = []
    if _release_parts(version) is not None:
        urls.append(f"{OFFICE_RELEASES}/download/v{version}/{OFFICE_ARCHIVE_NAME}")
    urls.append(f"{OFFICE_RELEASES}/latest/download/{OFFICE_ARCHIVE_NAME}")
    return urls


def _download(client, url: str, archive: Path) -> bool:
    """Write ``url`` to ``archive`` and verify it; ``False`` when it is not there."""
    digest = hashlib.sha256()
    size = 0
    with client.stream("GET", url) as response:
        if response.status_code == 404:
            return False
        response.raise_for_status()
        with archive.open("wb") as output:
            for chunk in response.iter_bytes(1024 * 1024):
                size += len(chunk)
                if size > OFFICE_DOWNLOAD_BYTES:
                    raise ValueError("Office download exceeds the expected size")
                digest.update(chunk)
                output.write(chunk)
    if size != OFFICE_DOWNLOAD_BYTES or digest.hexdigest() != OFFICE_ARCHIVE_SHA256:
        raise ValueError("Office download verification failed")
    return True


def __getattr__(name: str):
    """``safe_http``, imported at the first download: the HTTP client takes
    0.06 s to import, and this module is loaded with the Programs by every command."""
    if name == "safe_http":
        from openprogram.host.security import safe_http

        globals()["safe_http"] = safe_http
        return safe_http
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def download_office_pack() -> OfficeAssetPack:
    """Download only after explicit consent; publish only fully verified bytes."""
    from openprogram.host.security import safe_http

    target = prepared_office_cache()
    target.parent.mkdir(parents=True, exist_ok=True)
    version = installed_version()
    with tempfile.TemporaryDirectory(prefix=".office-install-", dir=target.parent) as name:
        temporary = Path(name)
        archive = temporary / "office.zip"
        with safe_http.safe_client("office.assets", timeout=30) as client:
            if not any(_download(client, url, archive) for url in office_download_urls(version)):
                raise OfficeArchiveUnpublished(version)
        unpacked = temporary / "pack"
        unpacked.mkdir()
        with zipfile.ZipFile(archive) as bundle:
            entries = bundle.infolist()
            if len(entries) > MAX_ASSETS * 2 or sum(e.file_size for e in entries) > MAX_EXTRACTED_BYTES:
                raise ValueError("Office archive exceeds installation limits")
            seen: set[str] = set()
            for entry in entries:
                relative = _safe_relative(entry.filename.rstrip("/"))
                if ":" in relative:
                    raise ValueError("Invalid Office archive path")
                kind = stat.S_IFMT(entry.external_attr >> 16)
                if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or relative in seen:
                    raise ValueError("Invalid Office archive entry")
                seen.add(relative)
                output = unpacked / relative
                if entry.is_dir():
                    output.mkdir(parents=True, exist_ok=True)
                else:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(entry) as source, output.open("xb") as destination:
                        shutil.copyfileobj(source, destination, 1024 * 1024)
        install_office_pack(unpacked, target)
    return validate_prepared_office_pack(target)
