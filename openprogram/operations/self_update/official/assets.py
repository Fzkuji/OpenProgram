"""The files an official release publishes for version stores.

| Asset                          | What it is                                       |
| ------------------------------ | ------------------------------------------------ |
| ``version-<platform>.tar.zst`` | One sealed version, without its venv links       |
| ``repo.bundle``                | The official Git line up to this release         |
| ``base-<platform>.tar.zst``    | The dependency base                              |
| ``toolchain-<platform>.tar.zst`` | ``node_modules`` for Web builds                |
| ``SHA256SUMS``                 | Hash of every asset above                        |
| ``update-manifest.json``       | What is in the release; names the hash of the sums |
| ``update-manifest.json.sig``   | Ed25519 signature of the manifest's exact bytes  |

Everything is read from a directory, whoever filled it. The signature is
checked before the manifest is parsed, the sums against the manifest, and each
asset against the sums, so nothing is unpacked or fetched on the word of a file
that was not signed. The manifest also records each asset's size, so a download
knows what to expect before it starts.

Every release carries its base and toolchain, because a machine can arrive from
any older release. A machine that already holds them does not download them
(:func:`assets_to_download`).
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile

from openprogram.operations.self_update.versions import build as builder
from openprogram.operations.self_update.versions import layout as paths
from openprogram.operations.self_update.versions import store
from openprogram.operations.self_update.versions.layout import Layout
from openprogram.host._platform import filesystem
from openprogram.host._platform.architecture import interpreter_architecture

MANIFEST_FILE = "update-manifest.json"
SIGNATURE_FILE = f"{MANIFEST_FILE}.sig"
SUMS_FILE = "SHA256SUMS"
BUNDLE_FILE = "repo.bundle"
LOCAL_REF = "refs/heads/local"
RELEASE_REF = "refs/heads/release"
MANIFEST_SCHEMA = 1
MAX_MANIFEST_BYTES = 1024 * 1024
GIT_TIMEOUT = 600

# Hex of the raw Ed25519 public keys a release may be signed with. More than
# one allows a rotation. Empty means this build accepts no official release.
RELEASE_PUBLIC_KEYS: tuple[str, ...] = ("e40d13b9572ab80002742e74a4243faa3312a8acec0d08a63ce71999e8531381",)

_RELEASE = re.compile(r"\d+\.\d+\.\d+")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_ASSET = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_PLATFORM = re.compile(r"(macos|linux|windows)-(arm64|x86_64)")
_SIDECAR_PREFIX = "platform-"
# A venv's interpreter links and config name absolute paths on the build machine.
_NOT_PORTABLE = ("venv/pyvenv.cfg", "venv/bin", "venv/Scripts")


class ReleaseError(RuntimeError):
    """A release cannot be used; the message says why, and nothing changed."""


def current_platform() -> str:
    """This machine, in the release matrix's ``<platform>-<arch>`` words."""
    system = {"darwin": "macos", "win32": "windows"}.get(sys.platform, sys.platform)
    # The running interpreter's architecture: an x64 install on Windows on Arm
    # updates to the x64 release, not to an Arm one it never installed.
    return f"{system}-{interpreter_architecture()}"


def git(
    repository: Path, *arguments: str, timeout: float = GIT_TIMEOUT, raw: bool = False,
) -> subprocess.CompletedProcess:
    """Run Git with no hook, no user config and a fixed committer."""
    environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_COMMITTER_NAME": "OpenProgram",
        "GIT_COMMITTER_EMAIL": "updates@openprogram.invalid",
        "GIT_EDITOR": "true",
    }
    for name in ("SYSTEMROOT", "TMPDIR", "TEMP", "TMP", "HOME"):
        if name in os.environ:
            environment[name] = os.environ[name]
    command = ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "core.fsmonitor=false",
               "-c", "commit.gpgsign=false", *arguments]
    decoding = {} if raw else {"text": True, "errors": "replace"}
    try:
        return subprocess.run(
            command, cwd=str(repository), env=environment, stdin=subprocess.DEVNULL,
            capture_output=True, timeout=timeout, **decoding,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseError(f"git {arguments[0]} could not run: {exc}") from exc


def git_output(repository: Path, *arguments: str) -> str:
    result = git(repository, *arguments)
    if result.returncode != 0:
        raise ReleaseError(f"git {arguments[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


# --- Making a release -------------------------------------------------------


def _pack(source: Path, target: Path, *, skip: Sequence[str] = ()) -> None:
    """Write ``source``'s contents as a ``.tar.zst``, refusing links that leave it."""
    import zstandard

    def clean(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        name = info.name.replace(os.sep, "/")
        if any(name == entry or name.startswith(f"{entry}/") for entry in skip):
            return None
        if info.issym():
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), info.linkname))
            if info.linkname.startswith("/") or resolved.startswith(".."):
                raise ReleaseError(f"{source.name} has a link that leaves it: {name} -> {info.linkname}")
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        return info

    target.parent.mkdir(parents=True, exist_ok=True)
    compressor = zstandard.ZstdCompressor(level=12, threads=-1)
    with open(target, "wb") as raw, compressor.stream_writer(raw) as stream:
        with tarfile.open(fileobj=stream, mode="w|", format=tarfile.PAX_FORMAT) as archive:
            for child in sorted(source.iterdir(), key=lambda entry: entry.name):
                archive.add(child, arcname=child.name, filter=clean)


def package_platform(
    layout: Layout, version_id: str, output: Path, *,
    repository: Path, platform: str | None = None,
) -> dict:
    """Write one platform's assets for a version built in ``layout``."""
    name = platform or current_platform()
    if _PLATFORM.fullmatch(name) is None:
        raise ReleaseError(f"not a release platform: {name!r}")
    manifest = store.read_manifest(layout, version_id)
    base_id = str(manifest["base"])
    lock = subprocess.run(
        ["git", "show", f"{manifest['commit']}:package-lock.json"],
        cwd=str(repository), capture_output=True, timeout=120,
    )
    if lock.returncode != 0:
        raise ReleaseError(f"the commit has no package-lock.json: {lock.stderr.decode('utf-8', 'replace')}")
    toolchain = builder.toolchain_id(lock.stdout)

    version_asset = f"version-{name}.tar.zst"
    _pack(layout.version(version_id), output / version_asset,
          skip=[*_NOT_PORTABLE, store.FILES_FILE, store.PRISTINE_FILE])

    base_asset = f"base-{name}.tar.zst"
    _pack(layout.base(base_id), output / base_asset)

    toolchain_asset = None
    if (layout.toolchain / toolchain / "node_modules").is_dir():
        toolchain_asset = f"toolchain-{name}.tar.zst"
        _pack(layout.toolchain / toolchain, output / toolchain_asset)

    entry = {
        "platform": name,
        "commit": manifest["commit"],
        "version_id": version_id,
        "base": base_id,
        "toolchain": toolchain,
        "shell_compat": manifest["shell_compat"],
        "version": version_asset,
        "base_asset": base_asset,
        "toolchain_asset": toolchain_asset,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / f"{_SIDECAR_PREFIX}{name}.json").write_text(json.dumps(entry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return entry


def _create_bundle(repository: Path, commit: str, release: str, target: Path) -> None:
    """A full bundle, so any older installation can fetch it."""
    ref = f"refs/openprogram/release-bundle/{release}"
    git_output(repository, "update-ref", ref, commit)
    try:
        git_output(repository, "bundle", "create", "--quiet", str(target), ref)
    finally:
        git(repository, "update-ref", "-d", ref)


def _sign(data: bytes, private_key_pem: bytes) -> bytes:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    key = load_pem_private_key(private_key_pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ReleaseError("the signing key is not an Ed25519 key")
    return key.sign(data)


def public_key_hex(private_key_pem: bytes) -> str:
    """The value to add to :data:`RELEASE_PUBLIC_KEYS` for this signing key."""
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PublicFormat, load_pem_private_key,
    )

    key = load_pem_private_key(private_key_pem, password=None)
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def generate_signing_key(path: Path) -> str:
    """Write a new Ed25519 private key to ``path``, readable only by its owner.

    Returns the public value for :data:`RELEASE_PUBLIC_KEYS`. An existing file
    is never replaced, because losing a key strands every installed copy.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

    pem = Ed25519PrivateKey.generate().private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ReleaseError(f"{path} already exists, and a signing key is never replaced; choose another path") from exc
    except OSError as exc:
        raise ReleaseError(f"could not create {path}: {exc}") from exc
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(pem)
    return public_key_hex(pem)


def sign_file(path: Path, private_key_pem: bytes) -> Path:
    """Write ``<path>.sig``: the hex signature of the file's exact bytes."""
    target = path.with_name(f"{path.name}.sig")
    target.write_text(_sign(path.read_bytes(), private_key_pem).hex() + "\n", encoding="ascii")
    return target


def finalize_release(
    directory: Path, *, release: str, commit: str, repository: Path, private_key_pem: bytes,
) -> dict:
    """Add the bundle, sums and signed manifest to a directory of platform assets."""
    if _RELEASE.fullmatch(release) is None or _COMMIT.fullmatch(commit) is None:
        raise ReleaseError("a release needs an X.Y.Z version and a full commit id")
    sidecars = sorted(directory.glob(f"{_SIDECAR_PREFIX}*.json"))
    if not sidecars:
        raise ReleaseError(f"no platform assets in {directory}")
    platforms: dict[str, dict] = {}
    for sidecar in sidecars:
        entry = json.loads(sidecar.read_text(encoding="utf-8"))
        if entry.pop("commit") != commit:
            raise ReleaseError(f"{sidecar.name} was built from another commit")
        platforms[entry.pop("platform")] = entry

    _create_bundle(repository, commit, release, directory / BUNDLE_FILE)
    produced = {SUMS_FILE, MANIFEST_FILE, SIGNATURE_FILE, *(path.name for path in sidecars)}
    assets = sorted(path for path in directory.iterdir() if path.is_file() and path.name not in produced)
    sums = "".join(f"{sha256_file(path)}  {path.name}\n" for path in assets).encode("ascii")
    (directory / SUMS_FILE).write_bytes(sums)

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "release": release,
        "commit": commit,
        "sha256sums": hashlib.sha256(sums).hexdigest(),
        "asset_bytes": {path.name: path.stat().st_size for path in assets},
        "platforms": platforms,
    }
    data = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    (directory / MANIFEST_FILE).write_bytes(data)
    (directory / SIGNATURE_FILE).write_text(_sign(data, private_key_pem).hex() + "\n", encoding="ascii")
    for sidecar in sidecars:
        sidecar.unlink()
    return manifest


# --- Checking a release -----------------------------------------------------


@dataclass(frozen=True)
class Release:
    """One verified release, as seen by this machine's platform."""

    directory: Path
    release: str
    commit: str
    platform: str
    version_id: str
    base: str
    toolchain: str
    shell_compat: object
    version_asset: str
    base_asset: str | None
    toolchain_asset: str | None
    sums: dict[str, str] = field(default_factory=dict)
    asset_bytes: dict[str, int] = field(default_factory=dict)

    def asset(self, name: str) -> Path:
        return self.directory / name

    @property
    def files(self) -> tuple[str, ...]:
        """Every asset this platform needs."""
        names = (BUNDLE_FILE, self.version_asset, self.base_asset, self.toolchain_asset)
        return tuple(name for name in names if name is not None)


def verify_signature(data: bytes, signature_text: str, keys: Iterable[str] | None = None) -> None:
    """Raise :class:`ReleaseError` unless a trusted key signed exactly ``data``."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    trusted = list(RELEASE_PUBLIC_KEYS if keys is None else keys)
    if not trusted:
        raise ReleaseError("this build has no release signing key, so it accepts no official release")
    try:
        signature = bytes.fromhex(signature_text.strip())
    except ValueError as exc:
        raise ReleaseError("the release signature is malformed") from exc
    for key in trusted:
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(key)).verify(signature, data)
            return
        except (InvalidSignature, ValueError):
            continue
    raise ReleaseError("the release signature does not match a trusted key")


def _read_sums(data: bytes) -> dict[str, str]:
    sums: dict[str, str] = {}
    for line in data.decode("ascii", "replace").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or re.fullmatch(r"[0-9a-f]{64}", digest) is None or _ASSET.fullmatch(name) is None or name in sums:
            raise ReleaseError(f"{SUMS_FILE} has a malformed line: {line!r}")
        sums[name] = digest
    return sums


def _asset_name(entry: dict, key: str, *, required: bool = False) -> str | None:
    value = entry.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or _ASSET.fullmatch(value) is None:
        raise ReleaseError(f"the manifest has an invalid {key}: {value!r}")
    return value


def inspect_release(
    data: bytes, signature: str, sums_data: bytes, *, directory: Path,
    platform: str | None = None, trusted_keys: Iterable[str] | None = None,
) -> Release:
    """Check the manifest, its signature and the sums, before any asset is read."""
    name = platform or current_platform()
    verify_signature(data, signature, trusted_keys)

    try:
        manifest = json.loads(data)
    except ValueError as exc:
        raise ReleaseError(f"the release manifest is unreadable: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ReleaseError("the release manifest is unreadable")
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ReleaseError(f"this build does not read release manifest schema {manifest.get('schema')!r}")
    release, commit = manifest.get("release"), manifest.get("commit")
    if not isinstance(release, str) or _RELEASE.fullmatch(release) is None:
        raise ReleaseError(f"the manifest names an invalid release: {release!r}")
    if not isinstance(commit, str) or _COMMIT.fullmatch(commit) is None:
        raise ReleaseError(f"the manifest names an invalid commit: {commit!r}")
    if hashlib.sha256(sums_data).hexdigest() != manifest.get("sha256sums"):
        raise ReleaseError(f"{SUMS_FILE} does not match the signed manifest")
    entry = (manifest.get("platforms") or {}).get(name)
    if not isinstance(entry, dict):
        raise ReleaseError(f"release {release} has nothing for {name}")

    version_asset = _asset_name(entry, "version", required=True)
    base_asset = _asset_name(entry, "base_asset")
    toolchain_asset = _asset_name(entry, "toolchain_asset")
    version_id, base = entry.get("version_id"), entry.get("base")
    toolchain = entry.get("toolchain")
    if not paths.valid_id(version_id) or not isinstance(base, str) or paths.BASE_ID.fullmatch(base) is None:
        raise ReleaseError("the manifest names an invalid version or base")
    if not paths.valid_id(toolchain):
        raise ReleaseError(f"the manifest names an invalid toolchain: {toolchain!r}")

    sums = _read_sums(sums_data)
    described = Release(
        directory=directory, release=release, commit=commit, platform=name,
        version_id=str(version_id), base=base, toolchain=str(toolchain),
        shell_compat=entry.get("shell_compat"), version_asset=str(version_asset),
        base_asset=base_asset, toolchain_asset=toolchain_asset, sums=sums,
        asset_bytes=_asset_bytes(manifest),
    )
    for asset in described.files:
        if asset not in sums:
            raise ReleaseError(f"{SUMS_FILE} does not list {asset}")
    return described


def _asset_bytes(manifest: dict) -> dict[str, int]:
    sizes = manifest.get("asset_bytes", {})
    if not isinstance(sizes, dict) or not all(type(size) is int and size >= 0 for size in sizes.values()):
        raise ReleaseError("the manifest names invalid asset sizes")
    return sizes


def _has_own_commits(layout: Layout) -> bool:
    """Whether ``local`` is ahead of the official line, as far as this machine knows it.

    A new release only adds to that line, so a machine with none now has none
    after updating. An unreadable answer counts as yes.
    """
    ahead = git(layout.repo, "rev-list", "--count", f"{RELEASE_REF}..{LOCAL_REF}")
    return ahead.returncode != 0 or ahead.stdout.strip() != "0"


def assets_to_download(layout: Layout, release: Release) -> tuple[str, ...]:
    """The files of ``release`` this machine lacks.

    A base already here is skipped. So is a toolchain that is already here or
    would go unused, because only building the owner's own commits needs it.
    """
    skip = set()
    if layout.base(release.base).is_dir():
        skip.add(release.base_asset)
    if (layout.toolchain / release.toolchain / "node_modules").is_dir() or not _has_own_commits(layout):
        skip.add(release.toolchain_asset)
    return tuple(name for name in release.files if name not in skip)


def read_release(
    directory: Path, *, platform: str | None = None, trusted_keys: Iterable[str] | None = None,
    layout: Layout | None = None,
) -> Release:
    """Check a release directory and describe it for this platform.

    Raises :class:`ReleaseError` for anything missing, altered or unsigned. With
    ``layout``, a base that machine already holds may be absent, and so may the
    toolchain, which a build can fetch for itself; whatever is present is checked.
    """
    try:
        if (directory / MANIFEST_FILE).stat().st_size > MAX_MANIFEST_BYTES:
            raise ReleaseError("the release manifest is too large")
        data = (directory / MANIFEST_FILE).read_bytes()
        signature = (directory / SIGNATURE_FILE).read_text(encoding="ascii", errors="replace")
        sums_data = (directory / SUMS_FILE).read_bytes()
    except OSError as exc:
        raise ReleaseError(f"the release is incomplete: {exc}") from exc
    release = inspect_release(
        data, signature, sums_data, directory=directory, platform=platform, trusted_keys=trusted_keys,
    )
    may_be_absent = set()
    if layout is not None:
        may_be_absent.add(release.toolchain_asset)
        if layout.base(release.base).is_dir():
            may_be_absent.add(release.base_asset)
    for asset in release.files:
        if asset in may_be_absent and not (directory / asset).exists():
            continue
        try:
            digest = sha256_file(directory / asset)
        except OSError as exc:
            raise ReleaseError(f"the release is incomplete: {exc}") from exc
        if digest != release.sums[asset]:
            raise ReleaseError(f"{asset} does not match its checksum")
    return release


def needs_new_shell(layout: Layout, release: Release) -> str | None:
    """Why the installed signed shell or launcher cannot host this release, if so."""
    try:
        supported = json.loads((layout.root / "identity" / "shell-compat.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        supported = None  # No shell installed here.
    if isinstance(supported, dict):
        value = release.shell_compat
        if not isinstance(value, int) or not supported.get("min", value + 1) <= value <= supported.get("max", value - 1):
            return f"release {release.release} needs a newer signed shell"
    try:
        launcher_minor = (layout.root / "identity" / "python-minor").read_text(encoding="ascii").strip()
    except OSError:
        return None
    if launcher_minor != paths.base_minor(release.base):
        return f"release {release.release} moves Python to {paths.base_minor(release.base)}, and the installed launcher is built for {launcher_minor}"
    return None


# --- Installing from a release ----------------------------------------------


def fetch_release(layout: Layout, release: Release) -> None:
    """Move ``refs/heads/release`` to this release, forward only."""
    bundle = release.asset(BUNDLE_FILE)
    heads = git_output(layout.repo, "bundle", "list-heads", str(bundle)).splitlines()
    ref = next((line.split()[1] for line in heads if line.split()[0] == release.commit), None)
    if ref is None:
        raise ReleaseError(f"{BUNDLE_FILE} does not contain {release.commit}")
    fetched = git(layout.repo, "fetch", "--no-tags", "--quiet", str(bundle), f"{ref}:{RELEASE_REF}")
    if fetched.returncode != 0:
        raise ReleaseError(
            f"release {release.release} does not follow the official line this installation is on: "
            f"{fetched.stderr.strip()}"
        )


def _extract(archive: Path, destination: Path) -> None:
    import zstandard

    destination.mkdir(parents=True)
    try:
        with open(archive, "rb") as raw, zstandard.ZstdDecompressor().stream_reader(raw) as stream:
            with tarfile.open(fileobj=stream, mode="r|") as contents:
                contents.extractall(destination, filter="data")
    except (OSError, ValueError, tarfile.TarError, zstandard.ZstdError) as exc:
        raise ReleaseError(f"{archive.name} could not be unpacked: {exc}") from exc


def _install_tree(archive: Path, staging: Path, target: Path, check: Callable[[Path], bool], what: str) -> None:
    shutil.rmtree(staging, ignore_errors=True)
    staging.parent.mkdir(parents=True, exist_ok=True)
    try:
        _extract(archive, staging)
        if not check(staging):
            raise ReleaseError(f"{archive.name} holds no {what}")
        filesystem.replace_file(staging, target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def install_carried_base(layout: Layout, release: Release) -> bool:
    """Unpack the base when the release carries it and it is new here."""
    target = layout.base(release.base)
    if target.is_dir() or release.base_asset is None:
        return False
    _install_tree(release.asset(release.base_asset), layout.base_staging(release.base), target,
                  lambda staging: paths.base_python(staging).is_file() or paths.base_python(staging).is_symlink(),
                  "Python")
    return True


def install_carried_toolchain(layout: Layout, release: Release) -> bool:
    """Unpack ``node_modules`` when the release carries it and it is new here."""
    target = layout.toolchain / release.toolchain
    if (target / "node_modules").is_dir() or release.toolchain_asset is None:
        return False
    if not release.asset(release.toolchain_asset).is_file():
        return False  # Not downloaded; the build fetches its own.
    shutil.rmtree(target, ignore_errors=True)
    _install_tree(release.asset(release.toolchain_asset), layout.toolchain / f".staging-{release.toolchain}", target,
                  lambda staging: (staging / "node_modules").is_dir(), "node_modules")
    return True


def install_version(layout: Layout, release: Release, *, update_id: str, parent: str | None) -> str:
    """Unpack the prebuilt version into the store and return its id."""
    staging = layout.staging(update_id)
    shutil.rmtree(staging, ignore_errors=True)
    layout.versions.mkdir(parents=True, exist_ok=True)
    try:
        _extract(release.asset(release.version_asset), staging)
        try:
            manifest = json.loads((staging / store.VERSION_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ReleaseError(f"{release.version_asset} has no readable version.json: {exc}") from exc
        expected = (release.version_id, release.commit, release.base)
        if (manifest.get("id"), manifest.get("commit"), manifest.get("base")) != expected:
            raise ReleaseError(f"{release.version_asset} does not hold what the manifest says")
        (staging / store.VERSION_FILE).unlink()
        builder.seal_prebuilt(layout, staging, manifest, parent=parent)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return release.version_id
