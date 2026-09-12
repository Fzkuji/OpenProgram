"""Verified, pinned Office resources shared by release tools and the server."""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import json
import re
from dataclasses import dataclass
from pathlib import Path

_MANIFEST = "openprogram-office-assets.json"
OFFICE_SOURCE = "d15d12b6945be4d8b0f3aa1806120e740d2950ee"
OFFICE_PACKAGE_VERSION = "0.3.34"
OFFICE_FONT_MANIFEST_DIGEST = "e06827b3e04245fe2c636659511290667a93b1ba76d420ddd1a8781b0cf14524"
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_ASSETS = 8192
MAX_LICENSES = 256
MAX_PATH_CHARS = 1024
MAX_HOST_BUILD_ID_CHARS = 128
_BOOTSTRAP = frozenset({
    "office-host.html", "reset.html", "document_editor_service_worker.js", "sw.js",
    "plugins.json", "themes.json", "onlyoffice-runtime-assets.json",
})


@dataclass
class OfficeAssetPack:
    root: Path
    manifest: dict
    manifest_bytes: bytes
    runtime_manifest_bytes: bytes
    assets: dict[str, tuple[int, str, int, int]]
    available: bool = True
    unavailable_reason: str | None = None

    @classmethod
    def unavailable_pack(cls, root: Path, reason: str) -> "OfficeAssetPack":
        return cls(root, {}, b"", b"", {}, False, reason)

    @classmethod
    def from_root(cls, root: Path) -> "OfficeAssetPack":
        root = Path(root).absolute()
        try:
            if root.is_symlink():
                raise ValueError("Office pack root must not be a symlink")
            version = None
            pointer = root / "current.json"
            if pointer.is_file():
                if pointer.is_symlink() or pointer.stat().st_size > 1024:
                    raise ValueError("invalid Office pack pointer")
                selection = json.loads(pointer.read_bytes())
                if not isinstance(selection, dict):
                    raise ValueError("invalid Office pack pointer")
                version = selection.get("version")
                if not isinstance(version, str) or not re.fullmatch(r"[a-f0-9]{64}", version):
                    raise ValueError("invalid Office pack version")
                selected = root / "versions" / version
                if selected.is_symlink() or selected.parent.is_symlink():
                    raise ValueError("invalid Office pack directory")
                root = selected
            manifest_path = root / _MANIFEST
            with manifest_path.open("rb") as handle:
                raw = handle.read(MAX_MANIFEST_BYTES + 1)
            with (root / "onlyoffice-runtime-assets.json").open("rb") as handle:
                runtime_raw = handle.read(MAX_MANIFEST_BYTES + 1)
            if len(raw) > MAX_MANIFEST_BYTES or len(runtime_raw) > MAX_MANIFEST_BYTES:
                raise ValueError("Office asset manifest too large")
            if version and hashlib.sha256(raw).hexdigest() != version:
                raise ValueError("Office selected version digest mismatch")
            manifest = json.loads(raw)
            if not isinstance(manifest, dict) or type(manifest.get("version")) is not int or manifest["version"] != 1:
                raise ValueError("invalid Office asset manifest version")
            if manifest.get("source") != OFFICE_SOURCE or manifest.get("packageVersion") != OFFICE_PACKAGE_VERSION:
                raise ValueError("unverified Office asset identity")
            declared_identity = manifest.get("expectedHostIdentity")
            if declared_identity is not None and declared_identity != hashlib.sha256(runtime_raw).hexdigest():
                raise ValueError("Office native host identity mismatch")
            if type(manifest.get("hostBuildId")) is not str or not manifest["hostBuildId"] or len(manifest["hostBuildId"]) > MAX_HOST_BUILD_ID_CHARS:
                raise ValueError("invalid Office host build identity")
            for key in ("packageVersion", "hostBuildId", "source", "assets", "licenses"):
                if not manifest.get(key):
                    raise ValueError("invalid Office asset manifest")
            if not isinstance(manifest["assets"], list) or len(manifest["assets"]) > MAX_ASSETS or not isinstance(manifest["licenses"], list) or len(manifest["licenses"]) > MAX_LICENSES:
                raise ValueError("invalid Office asset manifest collections")
            assets: dict[str, tuple[int, str, int, int]] = {}
            for item in manifest["assets"]:
                if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                    raise ValueError("invalid Office asset entry")
                rel = item["path"]
                if len(rel) > MAX_PATH_CHARS:
                    raise ValueError("Office asset path too long")
                path = _safe_relative(rel)
                size = item.get("bytes")
                if path in assets or type(size) is not int or size < 0 or not isinstance(item.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"]):
                    raise ValueError("invalid or duplicate Office asset entry")
                target = _contained_file(root, path)
                if target is None:
                    raise ValueError("Office asset unavailable")
                content_size = target.stat().st_size
                if content_size != size:
                    raise ValueError("Office asset size mismatch")
                hasher = hashlib.sha256()
                with target.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        hasher.update(chunk)
                digest = hasher.hexdigest()
                if digest != item["sha256"]:
                    raise ValueError("Office asset digest mismatch")
                stat = target.stat()
                assets[path] = (content_size, digest, stat.st_mtime_ns, stat.st_ino)
            if not _BOOTSTRAP.issubset(assets):
                raise ValueError("Office asset manifest omits bootstrap resource")
            for license_path in manifest["licenses"]:
                if not isinstance(license_path, str) or _contained_file(root, _safe_relative(license_path)) is None:
                    raise ValueError("invalid Office asset license")
            return cls(root, manifest, raw, runtime_raw, assets)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return cls.unavailable_pack(root, "invalid Office asset pack")


def _safe_relative(value: str) -> str:
    if not value or "\\" in value or "\x00" in value:
        raise ValueError("invalid Office asset path")
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("invalid Office asset path")
    return value


def _contained_file(root: Path, relative: str) -> Path | None:
    try:
        target = (root / relative).resolve(strict=True)
        if not target.is_file() or target.is_symlink() or not target.is_relative_to(root.resolve()):
            return None
        current = root
        for part in relative.split("/"):
            current = current / part
            if current.is_symlink():
                return None
        return target
    except (OSError, ValueError):
        return None



OFFICE_PATCH_SHA256 = "dc31dd9d3ee4cf777d3e2d19c5a00d76eec2115e239720cd09298be919741b9a"
OFFICE_LOCK_SHA256 = "7b71a099e703545a80d06454ae2af6f52e52f0c3f1fbe5ead31dfdf11faf2590"
OFFICE_HOST_BUILD_ID = "office-host-0.3.34-r1"
OFFICE_CACHE_KEY = f"{OFFICE_SOURCE}-{OFFICE_PATCH_SHA256[:16]}"


def prepared_office_cache() -> Path:
    from openprogram.paths import get_state_dir
    return get_state_dir() / "cache" / "office" / OFFICE_CACHE_KEY


def validate_prepared_office_pack(root: Path) -> OfficeAssetPack:
    pack = OfficeAssetPack.from_root(root)
    if not pack.available:
        raise ValueError(pack.unavailable_reason or "Office resources unavailable")
    assembly = pack.manifest.get("assembly", {})
    if (pack.manifest.get("hostBuildId") != OFFICE_HOST_BUILD_ID
            or assembly.get("adoptionPatchSha256") != OFFICE_PATCH_SHA256
            or assembly.get("reviewedNpmLockSha256") != OFFICE_LOCK_SHA256
            or pack.manifest.get("expectedHostIdentity") != hashlib.sha256(pack.runtime_manifest_bytes).hexdigest()
            or not set(pack.manifest["licenses"]).issubset(pack.assets)):
        raise ValueError("Office resource build identity mismatch")
    fonts = assembly.get("fonts", {})
    if fonts.get("manifestDigest") != OFFICE_FONT_MANIFEST_DIGEST:
        raise ValueError("Office font build identity mismatch")
    return pack


def _sync_dir(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def install_office_pack(source: Path, target: Path) -> Path:
    """Publish immutable verified bytes, then atomically select the version.

    Earlier versions stay available to running readers and for rollback.
    Concurrent installers can only select a fully verified version.
    """
    pack = validate_prepared_office_pack(source)
    version = hashlib.sha256(pack.manifest_bytes).hexdigest()
    target = Path(target).absolute()
    if target.is_symlink() or (target / "versions").is_symlink():
        raise ValueError("Office installation cannot use symbolic links")
    versions = target / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    destination = versions / version
    if not destination.exists():
        stage = Path(tempfile.mkdtemp(prefix=".staging-", dir=versions))
        try:
            for relative in [*pack.assets, _MANIFEST]:
                original = _contained_file(pack.root, relative)
                if original is None:
                    raise ValueError("Office resource changed during installation")
                output = stage / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                with original.open("rb") as src, output.open("xb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                    dst.flush()
                    os.fsync(dst.fileno())
            validate_prepared_office_pack(stage)
            for directory, _, _ in os.walk(stage, topdown=False):
                _sync_dir(Path(directory))
            try:
                os.rename(stage, destination)
            except OSError:
                if not destination.is_dir():
                    raise
                existing = validate_prepared_office_pack(destination)
                if hashlib.sha256(existing.manifest_bytes).hexdigest() != version:
                    raise ValueError("Office version directory identity mismatch")
            _sync_dir(versions)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    else:
        existing = validate_prepared_office_pack(destination)
        if hashlib.sha256(existing.manifest_bytes).hexdigest() != version:
            raise ValueError("Office version directory identity mismatch")
    fd, name = tempfile.mkstemp(prefix=".current-", dir=target)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"version": version}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target / "current.json")
        _sync_dir(target)
    finally:
        Path(name).unlink(missing_ok=True)
    return destination


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Install or verify pinned local Office resources.")
    parser.add_argument("action", choices=("install", "verify", "cache-path"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--target", type=Path)
    args = parser.parse_args()
    target = args.target or prepared_office_cache()
    if args.action == "cache-path":
        print(target)
    elif args.action == "verify":
        print(validate_prepared_office_pack(target).root)
    elif args.source is None:
        parser.error("install requires --source")
    else:
        print(install_office_pack(args.source, target))


if __name__ == "__main__":
    main()
