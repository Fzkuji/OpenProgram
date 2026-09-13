"""Explicit installation of the pinned optional Office component."""
from __future__ import annotations

import hashlib
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path

from openprogram.security import safe_http

from openprogram.office_assets import (
    MAX_ASSETS, OfficeAssetPack, _safe_relative, install_office_pack,
    prepared_office_cache, validate_prepared_office_pack,
)

OFFICE_DOWNLOAD_URL = "https://github.com/Fzkuji/OpenProgram/releases/download/v0.9.0/OfficeAssets-d15d12b-dc31dd9d.zip"
OFFICE_ARCHIVE_SHA256 = "6367f66bb618e1c1ddfa27fffbb7ebcf084b652c8484d8923d42492ed0b5447d"
OFFICE_DOWNLOAD_BYTES = 732695641
MAX_EXTRACTED_BYTES = 4 * 1024**3


def download_office_pack() -> OfficeAssetPack:
    """Download only after explicit consent; publish only fully verified bytes."""
    target = prepared_office_cache()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".office-install-", dir=target.parent) as name:
        temporary = Path(name)
        archive = temporary / "office.zip"
        digest = hashlib.sha256()
        size = 0
        with safe_http.safe_client("office.assets", timeout=30, overall_timeout=1800) as client:
            with client.stream("GET", OFFICE_DOWNLOAD_URL) as response, archive.open("wb") as output:
                response.raise_for_status()
                for chunk in response.iter_bytes(1024 * 1024):
                    size += len(chunk)
                    if size > OFFICE_DOWNLOAD_BYTES:
                        raise ValueError("Office download exceeds the expected size")
                    digest.update(chunk)
                    output.write(chunk)
        if size != OFFICE_DOWNLOAD_BYTES or digest.hexdigest() != OFFICE_ARCHIVE_SHA256:
            raise ValueError("Office download verification failed")
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
