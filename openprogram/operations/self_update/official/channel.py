"""The signed pointer clients read, and the transport that fetches packs.

Everything lives on one rolling GitHub release, tag ``updates`` (prerelease,
never latest), read only through its download address (:data:`UPDATES_URL`),
never the REST API:

* ``channel.json``: the newest announced release, what installs take;
* ``latest.json``: every publish, daily syncs included, for the development
  instance and tests (``OPENPROGRAM_UPDATE_POINTER=latest``);
* ``pack-<12 hex>.pack``: the objects, fetched as byte ranges;
* ``runtime-<platform>-<base>.json``: a runtime layer the Release workflow
  built and uploaded, waiting for the publish that names it;
* ``shell-<platform>-<version>.json``: the same for the App shell layer of a
  feature release (macOS and Windows), uploaded by its Release workflow.

A pointer file carries its own signature, so one upload replaces it whole and
a reader never sees a pointer from one publish with another's signature::

    {"signed": "<the pointer's JSON text>", "signature": "<hex Ed25519 of that text>"}

The signed text names which pointer it is, so a ``latest.json`` cannot be
served as ``channel.json``, and ``channel.json`` must be announced::

    {"schema": 2, "pointer": "channel", "sequence": 42, "published_at": "...Z",
     "version": "1.5.1", "announce": true, "build": "<commit12>", "notes": "...",
     "commit": "<40 hex>", "shell_compat": 3,
     "layers": {"code": {"manifest": "<sha256>", "pack": "...", "offset": 0, "length": 9},
                "runtime-macos-arm64": {...}, "shell-macos-arm64": {..., "version": "1.5.0"}},
     "packs": {"pack-....pack": {"bytes": 123, "sha256": "<hex>"}}}

``sequence`` only grows, across both pointers. A client remembers the highest
it has verified (``<runtime>/channel-seen.json``) and refuses a lower one, so a
replayed old pointer cannot take it back to old code. A pointer that is
missing (between the delete and the upload that replace it) reads as nothing
published: the next check finds it.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlparse

from openprogram.host._platform.filesystem import replace_file
from openprogram.host._platform.runtime_layout import Layout

from . import signing
from .objects import LAYER_NAME, PACK, SHA256, Location, Span
from .repository import ReleaseError

#: Where the update files are. The one place that names it.
UPDATES_URL = "https://github.com/Fzkuji/OpenProgram/releases/download/updates"
UPDATES_TAG = "updates"
ANNOUNCED = "channel.json"
LATEST = "latest.json"
POINTER_VARIABLE = "OPENPROGRAM_UPDATE_POINTER"
SCHEMA = 2
MAX_POINTER_BYTES = 1024 * 1024
SEEN_FILE = "channel-seen.json"
VERSION = re.compile(r"\d+\.\d+\.\d+(\.\d+)?")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_BUILD = re.compile(r"[0-9a-f]{12}")
_TIME = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")


#: The word each pointer's signed text names itself by.
POINTER_WORDS = {ANNOUNCED: "channel", LATEST: "latest"}


def pointer_name() -> str:
    """``channel.json``, or ``latest.json`` when the environment asks for every publish."""
    return LATEST if os.environ.get(POINTER_VARIABLE, "").strip().lower() in ("latest", LATEST) else ANNOUNCED


@dataclass(frozen=True)
class LayerRef:
    manifest: str
    where: Location


@dataclass(frozen=True)
class Channel:
    """One verified pointer."""

    sequence: int
    published_at: str
    version: str
    announce: bool
    build: str
    notes: str
    commit: str
    shell_compat: int
    layers: dict[str, LayerRef]
    packs: dict[str, dict]
    data: bytes = b""
    signature: bytes = b""
    pointer: str = ""  # Which file it is: ANNOUNCED or LATEST.

    @property
    def name(self) -> str:
        """What it goes by in messages and on disk: the version, or the build for a quiet publish."""
        return self.version if self.announce else f"{self.version}-{self.build}"

    def runtime(self, platform: str) -> LayerRef | None:
        return self.layers.get(f"runtime-{platform}")

    def shell(self, platform: str) -> LayerRef | None:
        """The App shell of ``platform``, on the platforms whose App replaces itself from a layer."""
        return self.layers.get(f"shell-{platform}")


def encode(fields: dict, *, name: str) -> bytes:
    """The signed text of pointer ``name`` (:data:`ANNOUNCED` or :data:`LATEST`)."""
    value = {**fields, "schema": SCHEMA, "pointer": POINTER_WORDS[name]}
    return (json.dumps(value, indent=1, sort_keys=True) + "\n").encode("utf-8")


def seal(data: bytes, key_pem: bytes) -> bytes:
    """The pointer file: the signed text with its signature."""
    return (json.dumps({"signed": data.decode("utf-8"), "signature": signing.sign(data, key_pem).strip()},
                       indent=1) + "\n").encode("utf-8")


def unseal(blob: bytes) -> tuple[bytes, bytes]:
    """The signed text and the signature of a pointer file. Raises :class:`ReleaseError`."""
    if len(blob) > MAX_POINTER_BYTES:
        raise ReleaseError("the update pointer is too large")
    try:
        value = json.loads(blob)
    except ValueError as exc:
        raise ReleaseError(f"the update pointer is unreadable: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("signed"), str) \
            or not isinstance(value.get("signature"), str):
        raise ReleaseError("this build does not read this update pointer")
    return value["signed"].encode("utf-8"), value["signature"].encode("ascii", "replace")


def open_pointer(blob: bytes, *, name: str, trusted_keys: Iterable[str] | None = None) -> Channel:
    """Check and read the pointer file ``name``. Raises :class:`ReleaseError`."""
    data, signature = unseal(blob)
    return parse(data, signature, name=name, trusted_keys=trusted_keys)


def parse(data: bytes, signature: bytes, *, name: str, trusted_keys: Iterable[str] | None = None) -> Channel:
    """Check the signature, then the fields, of pointer ``name``. Raises :class:`ReleaseError`."""
    if len(data) > MAX_POINTER_BYTES:
        raise ReleaseError("the update pointer is too large")
    signing.verify_signature(data, signature.decode("ascii", "replace"), trusted_keys)
    try:
        value = json.loads(data)
    except ValueError as exc:
        raise ReleaseError(f"the update pointer is unreadable: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise ReleaseError("this build does not read this update pointer")
    if value.get("pointer") != POINTER_WORDS.get(name):
        raise ReleaseError(f"the signed pointer is {value.get('pointer')!r}, not {name}")
    if name == ANNOUNCED and value.get("announce") is not True:
        raise ReleaseError(f"{ANNOUNCED} names a publish that was not announced")
    sequence, version, commit = value.get("sequence"), value.get("version"), value.get("commit")
    if type(sequence) is not int or sequence <= 0:
        raise ReleaseError("the update pointer has no sequence")
    if not isinstance(version, str) or VERSION.fullmatch(version) is None:
        raise ReleaseError(f"the update pointer names an invalid version: {version!r}")
    if not isinstance(commit, str) or _COMMIT.fullmatch(commit) is None:
        raise ReleaseError("the update pointer names an invalid commit")
    build = value.get("build")
    if not isinstance(build, str) or _BUILD.fullmatch(build) is None or not commit.startswith(build):
        raise ReleaseError("the update pointer names an invalid build")
    published_at, notes = value.get("published_at"), value.get("notes", "")
    if not isinstance(published_at, str) or _TIME.fullmatch(published_at) is None:
        raise ReleaseError("the update pointer has no publication time")
    if type(value.get("announce")) is not bool or not isinstance(notes, str) or len(notes) > 20_000:
        raise ReleaseError("the update pointer is malformed")
    if type(value.get("shell_compat")) is not int:
        raise ReleaseError("the update pointer names no shell contract")
    packs = value.get("packs")
    if not isinstance(packs, dict) or not all(
        PACK.fullmatch(name) and isinstance(info, dict) and type(info.get("bytes")) is int
        and isinstance(info.get("sha256"), str) and SHA256.fullmatch(info["sha256"])
        for name, info in packs.items()
    ):
        raise ReleaseError("the update pointer lists malformed packs")
    layers: dict[str, LayerRef] = {}
    for layer, ref in (value.get("layers") or {}).items():
        if LAYER_NAME.fullmatch(layer) is None or not isinstance(ref, dict):
            raise ReleaseError(f"the update pointer names an invalid layer {layer!r}")
        manifest, pack, offset, length = (ref.get(key) for key in ("manifest", "pack", "offset", "length"))
        if not isinstance(manifest, str) or SHA256.fullmatch(manifest) is None or pack not in packs \
                or type(offset) is not int or type(length) is not int or offset < 0 or length <= 0 \
                or offset + length > packs[pack]["bytes"]:
            raise ReleaseError(f"the update pointer gives layer {layer} a malformed location")
        if layer.startswith("shell-") and VERSION.fullmatch(str(ref.get("version") or "")) is None:
            raise ReleaseError(f"the update pointer gives shell layer {layer} no App version")
        layers[layer] = LayerRef(manifest, Location(pack, offset, length))
    if "code" not in layers:
        raise ReleaseError("the update pointer has no code layer")
    return Channel(
        sequence=sequence, published_at=published_at, version=version, announce=value["announce"],
        build=build, notes=notes, commit=commit, shell_compat=value["shell_compat"], layers=layers,
        packs=packs, data=data, signature=signature, pointer=name,
    )


# --- Rollback protection ----------------------------------------------------------


def seen_sequence(layout: Layout) -> int:
    try:
        value = json.loads((layout.root / SEEN_FILE).read_text(encoding="utf-8")).get("sequence")
    except (OSError, ValueError, AttributeError):
        return 0
    return value if type(value) is int else 0


def note_seen(layout: Layout, channel: Channel) -> None:
    """Refuse a pointer older than one verified before; remember the newest."""
    seen = seen_sequence(layout)
    if channel.sequence < seen:
        raise ReleaseError(
            f"the published update {channel.sequence} is older than {seen}, which this installation has already "
            "seen; it is refused so old code cannot be pushed back"
        )
    if channel.sequence > seen:
        target = layout.root / SEEN_FILE
        temporary = target.with_name(f".{SEEN_FILE}.{os.getpid()}")
        temporary.write_text(json.dumps({"sequence": channel.sequence}) + "\n", encoding="utf-8")
        replace_file(temporary, target)


# --- Transports --------------------------------------------------------------------


class Unreachable(ReleaseError):
    """An update file could not be read; unlike a missing one (``None``), nothing is known about it."""


class Transport:
    """Reads update files by name: whole small files, and byte ranges of packs."""

    def small(self, name: str) -> bytes | None:
        """The file, ``None`` when it is not there, or :class:`Unreachable`."""
        raise NotImplementedError

    def spans(self, pack: str, spans: list[Span], directory: Path) -> list[Path]:
        """Each span's bytes in a file of its own in ``directory``. Raises :class:`ReleaseError`."""
        raise NotImplementedError

    @property
    def fetched_bytes(self) -> int:
        return 0


class DirectoryTransport(Transport):
    """A local copy of the update files (``openprogram upgrade --from DIR``)."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def small(self, name: str) -> bytes | None:
        try:
            return (self.directory / name).read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise Unreachable(f"{name} could not be read: {exc}") from exc

    def spans(self, pack: str, spans: list[Span], directory: Path) -> list[Path]:
        files = []
        try:
            with open(self.directory / pack, "rb") as stream:
                for index, span in enumerate(spans):
                    stream.seek(span.start)
                    target = directory / f"{pack}.{index}"
                    target.write_bytes(stream.read(span.end - span.start))
                    files.append(target)
        except OSError as exc:
            raise ReleaseError(f"{pack} could not be read: {exc}") from exc
        return files


def _loopback(url: str) -> bool:
    host = urlparse(url).hostname or ""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


class HttpTransport(Transport):
    """The update files over HTTPS, with curl as every other download here.

    A pack's address is followed once through GitHub's redirect to the CDN,
    checking every hop, and its ranges are then read from there, several at a
    time. Plain HTTP is allowed only on this machine, for tests.
    """

    PARALLEL = 6
    ATTEMPTS = 3

    def __init__(self, base_url: str = UPDATES_URL) -> None:
        self.base_url = base_url.rstrip("/")
        self.local = _loopback(self.base_url)
        if not self.base_url.startswith("https://") and not self.local:
            raise ReleaseError(f"updates are read over HTTPS, not {self.base_url}")
        self._final: dict[str, str] = {}
        self._fetched = 0

    @property
    def fetched_bytes(self) -> int:
        return self._fetched

    def _curl(self, *arguments: str, timeout: float) -> subprocess.CompletedProcess:
        from openprogram.operations.self_update.github_release import github

        protocols = "=http,https" if self.local else "=https"
        try:
            return subprocess.run(
                ["curl", "--disable", "--proto", protocols, "--silent", "--show-error", "--connect-timeout", "15",
                 "--speed-limit", "1024", "--speed-time", "60", *arguments],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, env=github._curl_environment(),
            )
        except FileNotFoundError as exc:
            raise ReleaseError("updates need the curl command, and it is not installed") from exc
        except subprocess.TimeoutExpired as exc:
            raise ReleaseError("an update download took too long") from exc

    def small(self, name: str) -> bytes | None:
        url = f"{self.base_url}/{name}"
        if not self.local:
            from openprogram.operations.self_update.github_release import github

            try:
                data = github.release_file(url)
            except github.DownloadFailed as exc:
                raise Unreachable(str(exc)) from exc
        else:
            with tempfile.TemporaryDirectory(prefix="openprogram-small-") as scratch:
                target = Path(scratch) / "body"
                done = self._curl("--max-filesize", str(MAX_POINTER_BYTES), "--write-out", "%{http_code}",
                                  "--output", str(target), url, timeout=60)
                status = done.stdout.decode("ascii", "replace").strip()
                if done.returncode == 0 and status == "404":
                    return None
                if done.returncode != 0 or status != "200":
                    raise Unreachable(f"{name} could not be read: HTTP {status or done.returncode}")
                data = target.read_bytes()
        if data is not None:
            self._fetched += len(data)
        return data

    def _where(self, pack: str) -> str:
        if pack not in self._final:
            url = f"{self.base_url}/{pack}"
            if self.local:
                self._final[pack] = url
            else:
                from openprogram.operations.self_update.github_release import github

                try:
                    self._final[pack] = github._final_download_url(url, pack)
                except github.DownloadFailed as exc:
                    raise ReleaseError(f"{pack} could not be reached: {exc}") from exc
        return self._final[pack]

    def _one(self, pack: str, span: Span, target: Path) -> Path:
        """One range. A server that answers with anything but those bytes (a whole file, say) is not asked again."""
        size = span.end - span.start
        problem = ""
        for _attempt in range(self.ATTEMPTS):
            # --max-filesize stops a body larger than the range before it is written.
            done = self._curl("--range", f"{span.start}-{span.end - 1}", "--max-filesize", str(size),
                              "--write-out", "%{http_code}", "--output", str(target), self._where(pack), timeout=3600)
            status = done.stdout.decode("ascii", "replace").strip()
            got = target.stat().st_size if target.is_file() else 0
            if done.returncode == 0 and status == "206" and got == size:
                self._fetched += size
                return target
            problem = (f"HTTP {status}" if status not in ("206", "") else done.stderr.decode("utf-8", "replace").strip()) \
                or "it did not finish"
            if status in ("403", "410"):  # The CDN address expired: ask GitHub again.
                self._final.pop(pack, None)
            elif status not in ("", "000") and not status.startswith("5"):
                # Not a dropped connection or a server hiccup: asking again gets the same answer.
                break
        target.unlink(missing_ok=True)
        raise ReleaseError(f"{pack} could not be downloaded: {problem}")

    def spans(self, pack: str, spans: list[Span], directory: Path) -> list[Path]:
        from concurrent.futures import ThreadPoolExecutor

        self._where(pack)
        targets = [directory / f"{pack}.{index}" for index in range(len(spans))]
        with ThreadPoolExecutor(max_workers=self.PARALLEL) as pool:
            return list(pool.map(lambda pair: self._one(pack, *pair), zip(spans, targets)))


def read(transport: Transport, *, name: str | None = None, trusted_keys: Iterable[str] | None = None) -> Channel | None:
    """The verified pointer, or ``None`` when none is there now.

    ``None`` also covers the moment between the delete and the upload that
    replace a pointer: nothing is offered this time and the next check reads it.
    """
    pointer = name or pointer_name()
    blob = transport.small(pointer)
    if blob is None:
        return None
    return open_pointer(blob, name=pointer, trusted_keys=trusted_keys)


def scratch(layout: Layout) -> tempfile.TemporaryDirectory:
    """A temporary directory beside the object store, so finished files move in without a copy."""
    layout.root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix=".fetch-", dir=layout.root)
