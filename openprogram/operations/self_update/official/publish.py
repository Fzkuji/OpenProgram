"""The publisher's side: layers, packs and the signed pointers.

``scripts/release/publish-updates.py`` drives this; nothing here talks to
GitHub. A publish:

1. reads the live pointers (``latest.json`` and ``channel.json``) and the
   layer manifests they name, over the same download address clients use;
2. builds the ``code`` layer from a commit (its tracked tree under
   ``source/``, the built frontend under ``frontend/``) and writes the objects
   no live layer already has into one new pack, its manifest last;
3. takes a runtime layer for every platform in :data:`RUNTIME_PLATFORMS`
   built for the code's base id (``uv.lock``, the pyproject dependencies,
   ``product-runtime.json``, ``apps/tailnet``): a live one, one the Release
   workflow uploaded (``runtime-<platform>-<base>.json``), or one built here
   by :func:`build_runtime_layer`. Missing any, it is refused, so no pointer
   ever names code some platform cannot run. The newest App shell layer of
   each platform in :data:`SHELL_PLATFORMS` rides along; a feature release
   needs its own, which the Release workflow builds from each App
   (:func:`build_shell_layer`, ``shell-<platform>-<version>.json``). The shell
   changes only with a feature release, which always carries a new commit too;
   a client still compares the shell layer itself, not the code;
4. writes ``latest.json`` and, for a release, ``channel.json``, each carrying
   its signature, with a sequence one above every pointer published before.

Old packs are dropped by :func:`unreferenced`: a pack that neither live
pointer, nor the last few published, nor a runtime layer waiting to be
published, names. :func:`repack` gathers what the live pointers still use into
one pack when the live packs hold mostly dead bytes, so storage stays bounded
without delta chains.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import tomllib

from openprogram.host._platform import runtime_layout as paths

from . import channel as channels
from . import objects, signing
from .channel import Channel, LayerRef, Transport
from .objects import Entry, Layer, Location
from .repository import ReleaseError

SHELL_INPUTS = ("apps/desktop/shell", "apps/desktop/package.json", "apps/desktop/build")
KEEP_POINTERS = 3
RELEASE_TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?")
TAILNET = "openprogram-tailnet"
#: Every platform a published update must carry a runtime layer for: the
#: platforms the Release workflow builds Apps for.
RUNTIME_PLATFORMS = ("macos-arm64", "macos-x86_64", "linux-x86_64", "linux-arm64", "windows-x86_64")
#: The platforms whose App replaces itself from a shell layer (``official/shell.py``):
#: a feature release is announced only with each one's.
SHELL_PLATFORMS = ("macos-arm64", "macos-x86_64", "windows-x86_64", "linux-x86_64", "linux-arm64")
#: A layer the Release workflow uploaded, waiting for the publish that names it:
#: ``runtime-<platform>-<base>.json`` or ``shell-<platform>-<version>.json``.
LAYER_RECORD = re.compile(r"(?:runtime-(?:macos|linux|windows)-(?:arm64|x86_64)-py\d+\.\d+-[0-9a-f]{16}"
                          r"|shell-(?:macos|windows|linux)-(?:arm64|x86_64)-\d+\.\d+\.\d+)\.json")


_RELEASED_APP = re.compile(r"desktop-(?P<layout>full|thin)-(?P<platform>mac|win|linux)-(?P<arch>arm64|x86_64)/(?P<name>[^/]+)")


def released_shell_platforms(manifest: dict) -> set[str]:
    """The platforms whose App a feature release published in a form that replaces itself.

    Read from the release's own ``release-manifest.json``: a signed thin macOS
    App, the Windows x64 App, an AppImage. The legs that upload shell layers
    are the same (``release.yml``); an App that was not built, or only as a
    deb, has no shell layer, and its users keep the installer.
    """
    found: set[str] = set()
    for item in manifest.get("files") or []:
        match = _RELEASED_APP.fullmatch(str((item or {}).get("path") or ""))
        if match is None:
            continue
        name, arch = match["name"], match["arch"]
        if match["platform"] == "mac" and match["layout"] == "thin" and name.endswith(".zip") \
                and "-unsigned." not in name:
            found.add(f"macos-{arch}")
        elif match["platform"] == "win" and arch == "x86_64" and name.endswith(".exe"):
            found.add("windows-x86_64")
        elif match["platform"] == "linux" and name.endswith(".AppImage"):
            found.add(f"linux-{arch}")
    return found & set(SHELL_PLATFORMS)


class Refused(ReleaseError):
    """This commit cannot be published this way; the message says what it needs."""


def feature_release(tag: str) -> bool:
    """``vX.Y.0``: a release that builds installers; anything finer is code-only."""
    match = RELEASE_TAG.fullmatch(tag)
    if match is None:
        raise Refused(f"not a release tag: {tag!r} (vX.Y.Z or vX.Y.Z.W)")
    return match[3] == "0" and match[4] is None


# --- Reading the commit --------------------------------------------------------------


def _git(repo: Path, *arguments: str, input: bytes | None = None) -> bytes:
    result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *arguments], cwd=str(repo), input=input,
                            capture_output=True, timeout=600)
    if result.returncode != 0:
        raise ReleaseError(f"git {arguments[0]} failed: {result.stderr.decode('utf-8', 'replace').strip()}")
    return result.stdout


def show(repo: Path, commit: str, path: str) -> bytes | None:
    result = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=str(repo), capture_output=True, timeout=120)
    return result.stdout if result.returncode == 0 else None


def base_id(repo: Path, commit: str) -> str:
    """The base a commit needs, as a build of it computes it."""
    parts = {name: show(repo, commit, path) for name, path in (
        ("uv_lock", "uv.lock"), ("pyproject", "pyproject.toml"),
        ("product_runtime", "scripts/release/product-runtime.json"),
    )}
    missing = [name for name, value in parts.items() if value is None]
    if missing:
        raise ReleaseError(f"{commit[:12]} has no {', '.join(missing)}")
    tailnet = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{commit}:{paths.TAILNET_SOURCE}"],
                             cwd=str(repo), capture_output=True, text=True, timeout=60)
    return paths.make_base_id(**parts, tailnet=tailnet.stdout.strip() if tailnet.returncode == 0 else "")  # type: ignore[arg-type]


def shell_compat(repo: Path, commit: str) -> int:
    try:
        value = json.loads(show(repo, commit, "apps/desktop/shell/compat.json") or b"").get("shell_compat")
    except (ValueError, AttributeError):
        value = None
    if type(value) is not int:
        raise Refused(f"{commit[:12]} declares no shell contract in apps/desktop/shell/compat.json")
    return value


def version(repo: Path, commit: str) -> str:
    return str(tomllib.loads((show(repo, commit, "pyproject.toml") or b"").decode("utf-8"))["project"]["version"])


def shell_changed(repo: Path, before: str, after: str) -> bool:
    """Whether the shell's inputs differ; unknown history counts as changed."""
    result = subprocess.run(["git", "diff", "--quiet", before, after, "--", *SHELL_INPUTS], cwd=str(repo),
                            capture_output=True, timeout=120)
    return result.returncode != 0


def source_entries(repo: Path, commit: str) -> tuple[list[Entry], dict[str, Callable[[], bytes]]]:
    """The commit's tracked tree under ``source/``, each file with its Git blob id."""
    listing = _git(repo, "ls-tree", "-r", "-z", "--full-tree", commit).split(b"\0")
    rows = []
    for line in listing:
        if not line:
            continue
        head, _, path = line.partition(b"\t")
        mode, kind, blob = head.decode("ascii").split()
        if kind != "blob":
            raise Refused(f"{commit[:12]} has a {kind} at {path.decode('utf-8', 'replace')}, which updates cannot carry")
        rows.append((mode, blob, path.decode("utf-8")))
    contents: dict[str, bytes] = {}
    if rows:
        output = _git(repo, "cat-file", "--batch", input="".join(f"{blob}\n" for _m, blob, _p in rows).encode("ascii"))
        position = 0
        while position < len(output):
            end = output.index(b"\n", position)
            name, _kind, size = output[position:end].decode("ascii").split()
            start = end + 1
            contents[name] = output[start:start + int(size)]
            position = start + int(size) + 1
    entries: list[Entry] = []
    readers: dict[str, Callable[[], bytes]] = {}
    for mode, blob, path in rows:
        data = contents[blob]
        relative = objects.safe_path("source/" + path)
        if mode == "120000":
            entries.append(Entry(relative, symlink=objects.safe_link(relative, data.decode("utf-8")), git=blob))
            continue
        entry, found = objects.bytes_entry(relative, 0o755 if mode == "100755" else 0o644, data, git=blob)
        entries.append(entry)
        for digest, read in found.items():
            readers.setdefault(digest, read)
    return entries, readers


# --- Live state --------------------------------------------------------------------------


def read_objects(
    transport: Transport, wanted: dict[str, Location], sizes: dict[str, int] | None = None,
) -> dict[str, bytes]:
    """Objects as merged ranges of the published packs, each checked; ``sizes`` for files, trees without."""
    sizes = sizes or {}
    found: dict[str, bytes] = {}
    by_pack: dict[str, list[tuple[str, int, int]]] = {}
    for digest, where in wanted.items():
        by_pack.setdefault(where.pack, []).append((digest, where.offset, where.length))
    with tempfile.TemporaryDirectory(prefix="openprogram-publish-") as scratch:
        for pack, items in sorted(by_pack.items()):
            spans = objects.merge_ranges(items)
            for span, path in zip(spans, transport.spans(pack, spans, Path(scratch)), strict=True):
                data = path.read_bytes()
                for digest, offset, length in span.members:
                    found[digest] = objects.unpack_object(data[offset - span.start:offset - span.start + length],
                                                          digest, sizes.get(digest))
                path.unlink()
    return found


def read_object(transport: Transport, where: Location, digest: str, size: int | None = None) -> bytes:
    return read_objects(transport, {digest: where}, {digest: size} if size is not None else None)[digest]


def load_layer(transport: Transport, name: str, ref: LayerRef) -> Layer:
    return objects.read_layer(name, ref.manifest, ref.where, lambda wanted: read_objects(transport, wanted))


@dataclass
class Live:
    """What clients read now."""

    latest: Channel | None = None
    announced: Channel | None = None
    layers: dict[str, tuple[LayerRef, Layer]] = field(default_factory=dict)  # Of ``latest``.
    announced_layers: dict[str, tuple[LayerRef, Layer]] = field(default_factory=dict)
    # Pointers that were missing or unreadable on the release, taken from the
    # publisher's history instead; the next upload puts them back.
    repair: list[str] = field(default_factory=list)
    floor: int = 0  # The highest sequence ever published, from history.
    # Live layers in another layer format: left out of ``layers``, so the code
    # layer is written whole again and the others are refused until rebuilt.
    other_schema: set[str] = field(default_factory=set)

    @property
    def announced_code(self) -> Layer | None:
        found = self.announced_layers.get("code")
        return found[1] if found else None

    @property
    def sequence(self) -> int:
        return max([self.floor, *(pointer.sequence for pointer in (self.latest, self.announced) if pointer)])

    def packs(self) -> dict[str, dict]:
        found: dict[str, dict] = {}
        for pointer in (self.announced, self.latest):
            if pointer is not None:
                found.update(pointer.packs)
        return found

    def locations(self) -> dict[str, Location]:
        """Every object the live layers have, by sha256: a new layer reuses them."""
        found: dict[str, Location] = {}
        for _ref, layer in self.layers.values():
            found.update(layer.objects)
        return found

    def used(self) -> set[Location]:
        """Every object and tree either live pointer's layers use."""
        found: set[Location] = set()
        for layers in (self.layers, self.announced_layers):
            for ref, layer in layers.values():
                found |= set(layer.objects.values()) | {where for _digest, where in layer.trees.values()}
                found.add(ref.where)
        return found

    def trees(self) -> dict[str, tuple[str, Location]]:
        """Every live directory tree, by content: an unchanged directory keeps its tree."""
        found: dict[str, tuple[str, Location]] = {}
        for _ref, layer in self.layers.values():
            found.update(layer.trees)
        return found


def read_live(
    transport: Transport, *, trusted_keys: Iterable[str] | None = None, history: Iterable[Channel] = (),
) -> Live:
    """The live pointers and their layers.

    A pointer missing from the release (caught between its delete and its
    upload) or unreadable there is taken from the publisher's ``history`` and
    listed in :attr:`Live.repair`, so one bad upload never stops later runs.
    """
    history = list(history)
    live = Live(floor=max((pointer.sequence for pointer in history), default=0))
    for name in (channels.LATEST, channels.ANNOUNCED):
        try:
            found = channels.read(transport, name=name, trusted_keys=trusted_keys)
        except channels.Unreachable:
            raise  # Not knowing what is live is no ground to publish over it.
        except ReleaseError:
            found = None  # There but unreadable: taken from history and put back.
        recorded = max((pointer for pointer in history if pointer.pointer == name),
                       key=lambda pointer: pointer.sequence, default=None)
        if recorded is not None and (found is None or found.sequence < recorded.sequence):
            found = recorded
            live.repair.append(name)
        setattr(live, "latest" if name == channels.LATEST else "announced", found)
    for pointer, layers in ((live.latest, live.layers), (live.announced, live.announced_layers)):
        if pointer is None:
            continue
        for name, ref in pointer.layers.items():
            same = live.layers.get(name)
            if same and same[0] == ref:
                layers[name] = same
                continue
            try:
                layers[name] = (ref, load_layer(transport, name, ref))
            except objects.OtherSchema:
                live.other_schema.add(name)
    return live


# --- Writing layers ------------------------------------------------------------------------


def write_layer(
    packs: Path, name: str, meta: dict, entries: list[Entry], readers: dict[str, Callable[[], bytes]],
    live: Live, *, level: int = objects.LEVEL,
) -> tuple[LayerRef, Layer, str | None]:
    """One layer: its new objects and then its changed trees, root last, as one pack in ``packs``.

    Returns where the root tree is, the layer, and the new pack's name.
    """
    reuse = live.locations()
    digests = {digest for entry in entries if entry.symlink is None for digest, _size in entry.parts()}
    known = {digest: reuse[digest] for digest in digests if digest in reuse}
    new = {digest: readers[digest] for digest in digests if digest not in known}
    layer = Layer(name, meta, sorted(entries, key=lambda entry: entry.path))
    root: dict[str, str] = {}

    def trailer(here: dict[str, Location], add: Callable[[bytes], Location]) -> None:
        layer.objects = {**known, **here}
        root["digest"] = objects.write_trees(layer, add, reuse=live.trees())[0]

    pack, locations = objects.write_pack(packs, new, level=level, trailer=trailer)

    def placed(where: Location) -> Location:
        return Location(pack, where.offset, where.length) if where.pack == objects.HOME and pack else where

    layer.objects = {digest: placed(where) for digest, where in layer.objects.items()}
    layer.trees = {key: (digest, placed(where)) for key, (digest, where) in layer.trees.items()}
    return LayerRef(root["digest"], locations[root["digest"]]), layer, pack


def code_layer(
    repo: Path, commit: str, frontend: Path, packs: Path, live: Live, *, level: int = objects.LEVEL,
) -> tuple[LayerRef, Layer, str | None]:
    """The code layer of ``commit`` with the frontend built from it."""
    if not (frontend / "index.html").is_file():
        raise ReleaseError(f"{frontend} holds no frontend bundle")
    entries, readers = source_entries(repo, commit)
    bundle, found = objects.scan_tree(frontend, "frontend/")
    for digest, read in found.items():
        readers.setdefault(digest, read)
    meta = {
        "commit": commit, "tree": _git(repo, "rev-parse", f"{commit}^{{tree}}").decode("ascii").strip(),
        "version": version(repo, commit), "base": base_id(repo, commit), "shell_compat": shell_compat(repo, commit),
    }
    return write_layer(packs, "code", meta, entries + bundle, readers, live, level=level)


@dataclass(frozen=True)
class LayerRecord:
    """A runtime or shell layer built and waiting to be published, named by what it is for.

    ``key`` is a runtime layer's base id or a shell layer's App version. Built
    here it is ``runtime/<name>.json`` in the publisher's state; uploaded by
    the Release workflow it is :attr:`record` on the ``updates`` release.
    """

    name: str
    key: str
    ref: LayerRef
    packs: dict[str, dict]

    def encode(self) -> str:
        return json.dumps({"name": self.name, "key": self.key, "manifest": self.ref.manifest,
                           "pack": self.ref.where.pack, "offset": self.ref.where.offset,
                           "length": self.ref.where.length, "packs": self.packs}, indent=1, sort_keys=True) + "\n"

    @property
    def record(self) -> str:
        return f"{self.name}-{self.key}.json"

    @classmethod
    def read(cls, path: Path) -> "LayerRecord":
        return cls.decode(path.read_bytes())

    @classmethod
    def decode(cls, data: bytes) -> "LayerRecord":
        try:
            value = json.loads(data)
            return cls(value["name"], value["key"],
                       LayerRef(value["manifest"], Location(value["pack"], value["offset"], value["length"])),
                       value["packs"])
        except (ValueError, KeyError, TypeError) as exc:
            raise ReleaseError(f"a layer record is malformed: {exc}") from exc


def _uploaded(
    transport: Transport, name: str, key: str, meta_key: str, platform: str,
) -> tuple[LayerRecord, Layer] | None:
    data = transport.small(f"{name}-{key}.json")
    if data is None:
        return None
    built = LayerRecord.decode(data)
    try:
        layer = load_layer(transport, built.name, built.ref)
    except objects.OtherSchema:
        return None  # Uploaded by an older publisher: as good as not uploaded.
    if built.name != name or built.key != key or layer.meta.get(meta_key) != key \
            or layer.meta.get("platform") != platform:
        raise ReleaseError(f"{name}-{key}.json does not describe that layer")
    if not layer.packs() | {built.ref.where.pack} <= set(built.packs):
        raise ReleaseError(f"{name}-{key}.json does not list every pack its layer is in")
    return built, layer


def uploaded_runtime_layer(transport: Transport, platform: str, base: str) -> tuple[LayerRecord, Layer] | None:
    """The runtime layer the Release workflow uploaded for ``platform`` and ``base``, checked, or ``None``."""
    return _uploaded(transport, f"runtime-{platform}", base, "base", platform)


def uploaded_shell_layer(transport: Transport, platform: str, version: str) -> tuple[LayerRecord, Layer] | None:
    """The App shell layer the Release workflow uploaded for ``platform`` at ``version``, checked, or ``None``."""
    return _uploaded(transport, f"shell-{platform}", version, "version", platform)


def pack_info(path: Path) -> dict:
    return {"bytes": path.stat().st_size, "sha256": objects.sha256_file(path)}


def build_runtime_layer(
    base: Path, *, platform: str, packs: Path, state: Path, live: Live, base_name: str | None = None,
    tailnet: Path | None = None, level: int = objects.LEVEL,
) -> LayerRecord:
    """Turn a base directory into a runtime layer: a pack in ``packs`` and ``state/runtime/<layer>.json``.

    Objects the live runtime layers already publish are not packed again, so a
    base whose dependencies moved costs only the files that changed.
    """
    identity = base_name or base.name
    if paths.BASE_ID.fullmatch(identity) is None:
        raise ReleaseError(f"{identity!r} is not a base id; pass the base's id")
    if not paths.base_python(base).exists():
        raise ReleaseError(f"{base} holds no Python")
    entries, readers = objects.scan_tree(base)
    if tailnet is not None and not any(entry.path == f"bin/{tailnet.name}" for entry in entries):
        entry, found = objects.file_entry(tailnet, f"bin/{tailnet.name}", 0o755)
        entries.append(entry)
        readers = {**found, **readers}
    name = f"runtime-{platform}"
    built = _record(packs, name, identity, {"base": identity, "platform": platform}, entries, readers, live,
                    level=level)
    (state / "runtime").mkdir(parents=True, exist_ok=True)
    (state / "runtime" / f"{name}.json").write_text(built.encode(), encoding="utf-8")
    return built


def _record(
    packs: Path, name: str, key: str, meta: dict, entries: list[Entry], readers: dict[str, Callable[[], bytes]],
    live: Live, *, level: int,
) -> LayerRecord:
    ref, layer, _pack = write_layer(packs, name, meta, entries, readers, live, level=level)
    known = live.packs()
    used = layer.packs() | {ref.where.pack}
    infos = {pack: known[pack] if pack in known and not (packs / pack).is_file() else pack_info(packs / pack)
             for pack in sorted(used)}
    return LayerRecord(name, key, ref, infos)


def build_shell_layer(
    app: Path, *, platform: str, version: str, compat: dict, packs: Path, live: Live, level: int = objects.LEVEL,
    python_minor: str | None = None,
) -> LayerRecord:
    """Turn an App into the ``shell-<platform>`` layer of ``version``: a pack in ``packs`` and its record.

    ``app`` is ``OpenProgram.app`` as notarized (macOS, unpacked from the
    release's .zip), the unpacked install folder (Windows), or the AppImage
    (Linux, one file, whose seed's Python line comes as ``python_minor``),
    byte for byte. Modes and links are kept; empty directories go in the meta,
    since a tree cannot hold them. Anything else a layer cannot carry is
    refused, so a client never assembles an App that differs from the signed
    one. Objects the live shell layers already publish are not packed again.
    """
    from . import shell as shells

    if platform not in SHELL_PLATFORMS:
        raise ReleaseError(f"{platform} has no shell layer; its App keeps its installer")
    system = shells.system_of(platform)
    if not isinstance(compat, dict) or not all(type(compat.get(key)) is int for key in ("shell_compat", "min", "max")):
        raise ReleaseError("the shell's compat.json declares no shell_compat, min and max")
    if system == "linux":
        if not app.is_file() or app.is_symlink() or not os.access(app, os.X_OK):
            raise ReleaseError(f"{app} is not an executable AppImage")
        entry, readers = objects.file_entry(app, shells.APPIMAGE, 0o755)
        entries, relative, directories = [entry], shells.APPIMAGE, []
    else:
        if not app.is_dir():
            raise ReleaseError(f"{app} is not an App")
        odd = shells.unsupported_files(app)
        if odd:
            raise ReleaseError(f"{app} holds what a layer cannot carry: {', '.join(odd[:5])}")
        if system == "macos" and shells.bundle_version(app) != version:
            raise ReleaseError(f"{app} is {shells.bundle_version(app)}, not {version}")
        entries, readers = objects.scan_tree(app)
        relative = shells.executable(app, system)
        if not any(entry.path == relative and entry.symlink is None for entry in entries):
            raise ReleaseError(f"{app} has no {relative}")
        python_minor, directories = python_minor or shells.python_minor(app), shells.empty_directories(app)
    meta = {
        "platform": platform, "version": version, "executable": relative,
        "compat": {key: compat[key] for key in ("shell_compat", "min", "max")},
        "python_minor": python_minor, "directories": directories,
    }
    return _record(packs, f"shell-{platform}", version, meta, entries, readers, live, level=level)


def waiting_runtime_layers(state: Path) -> dict[str, LayerRecord]:
    directory = state / "runtime"
    if not directory.is_dir():
        return {}
    found = {}
    for path in sorted(directory.glob("runtime-*.json")):
        layer = LayerRecord.read(path)
        found[layer.name] = layer
    return found


# --- Deciding and composing -----------------------------------------------------------------


@dataclass
class Decision:
    """What publishing ``commit`` would do, before anything is built."""

    commit: str
    version: str
    base: str
    shell_compat: int
    runtime_changed: bool
    shell_changed: bool
    runtime: dict[str, LayerRecord | LayerRef]
    refused: str = ""
    # The runtime and shell layers already read, by name: from the map that supplied each ref.
    layers: dict[str, Layer] = field(default_factory=dict)
    shell: dict[str, LayerRecord | LayerRef] = field(default_factory=dict)
    # The platforms a feature release must bring a shell layer for: those it published an App for.
    shell_platforms: tuple[str, ...] = SHELL_PLATFORMS

    def describe(self) -> str:
        layers = ", ".join(sorted(self.runtime)) or "none"
        shells = ", ".join(f"{name} {self.layers[name].meta.get('version')}" for name in sorted(self.shell)) or "none"
        changes = [what for what, changed in (("runtime", self.runtime_changed), ("shell", self.shell_changed)) if changed]
        text = (f"{self.commit[:12]} ({self.version}), base {self.base}, shell_compat {self.shell_compat}; "
                f"runtime layers: {layers}; shell layers: {shells}; "
                f"CI-built inputs changed: {', '.join(changes) or 'none'}")
        return f"{text}\nrefused: {self.refused}" if self.refused else text


def decide(
    repo: Path, commit: str, live: Live, state: Path, *, release: str | None = None,
    transport: Transport | None = None, shell_platforms: Iterable[str] | None = None,
) -> Decision:
    """Whether ``commit`` can be published now, and with which runtime and shell layers.

    Every platform in :data:`RUNTIME_PLATFORMS` needs a runtime layer for the
    code's base: a live one, one waiting here, or one the Release workflow
    uploaded (read through ``transport``). Each platform in
    :data:`SHELL_PLATFORMS` keeps its newest live shell layer; a feature
    release whose shell inputs changed (its App version is one of them) needs
    the ones the Release workflow uploaded for its version, for each platform
    in ``shell_platforms`` (the release's published Apps, by default all), each
    able to host the new code and the announced code it first starts on.
    """
    base = base_id(repo, commit)
    compat = shell_compat(repo, commit)
    previous = live.layers.get("code")
    live_base = previous[1].meta.get("base") if previous else None
    runtime: dict[str, LayerRecord | LayerRef] = {}
    read: dict[str, Layer] = {}
    for layers in (live.layers, live.announced_layers):
        for name, (ref, layer) in layers.items():
            if name.startswith("runtime-") and layer.meta.get("base") == base and name not in runtime:
                runtime[name], read[name] = ref, layer
    for name, waiting in waiting_runtime_layers(state).items():
        if waiting.key == base:
            runtime[name] = waiting
            read.pop(name, None)
    if transport is not None:
        for platform in RUNTIME_PLATFORMS:
            if f"runtime-{platform}" not in runtime:
                uploaded = uploaded_runtime_layer(transport, platform, base)
                if uploaded is not None:
                    runtime[uploaded[0].name], read[uploaded[0].name] = uploaded
    from . import shell as shells

    shell: dict[str, LayerRecord | LayerRef] = {}
    for layers in (live.layers, live.announced_layers):
        for name, (ref, layer) in layers.items():
            if name.startswith("shell-") and (
                    name not in shell or shells.newer(layer.meta.get("version"), read[name].meta.get("version"))):
                shell[name], read[name] = ref, layer
    decision = Decision(commit, version(repo, commit), base, compat, live_base not in (None, base), False, runtime,
                        layers=read, shell=shell)
    if shell_platforms is not None:
        decision.shell_platforms = tuple(platform for platform in SHELL_PLATFORMS if platform in set(shell_platforms))
    if transport is not None and release is not None and feature_release(release):
        for platform in SHELL_PLATFORMS:
            uploaded = uploaded_shell_layer(transport, platform, decision.version)
            if uploaded is not None:
                shell[uploaded[0].name], read[uploaded[0].name] = uploaded
    missing = [platform for platform in RUNTIME_PLATFORMS if f"runtime-{platform}" not in runtime]
    if missing:
        why = (f"its runtime inputs changed (base {live_base} -> {base}: uv.lock, pyproject dependencies, "
               "product-runtime.json or apps/tailnet), and " if decision.runtime_changed else "")
        if any(name.startswith("runtime-") for name in live.other_schema):
            why = "the live runtime layers are in an older layer format, and "
        decision.refused = (
            f"{why}base {base} has no runtime layer for {', '.join(missing)}. The Release workflow builds them: "
            "`gh workflow run release.yml -R Fzkuji/OpenProgram --ref main -f runtime_layers_only=true` "
            "(a feature release vX.Y.0 runs it too); or run `publish-updates.py build-runtime-layer` on each platform"
        )
    announced = live.announced
    if announced is not None:
        announced_base = live.announced_code.meta.get("base") if live.announced_code else None
        decision.shell_changed = announced.shell_compat != compat or shell_changed(repo, announced.commit, commit)
        moved_runtime = announced_base not in (None, base)
    else:
        moved_runtime = decision.runtime_changed
    old_shells = sorted(name for name in live.other_schema if name.startswith("shell-") and name not in shell)
    if old_shells and not decision.refused:
        decision.refused = (
            f"the live {', '.join(old_shells)} layers are in an older layer format and nothing replaces them: "
            "build them again with `gh workflow run release.yml -R Fzkuji/OpenProgram --ref main "
            "-f runtime_layers_only=true`, or publish the next feature release vX.Y.0")
    if release is not None and not decision.refused:
        if release != f"v{decision.version}":
            decision.refused = f"{release} does not match the version in pyproject.toml ({decision.version})"
        elif not feature_release(release) and (moved_runtime or decision.shell_changed):
            what = " and ".join(word for word, changed in (("runtime", moved_runtime), ("shell", decision.shell_changed))
                                if changed)
            decision.refused = (f"this needs a feature release v{'.'.join(release[1:].split('.')[:2])}.0 "
                                f"({what} changed since the last announced release)")
        elif feature_release(release) and (decision.shell_changed or announced is None):
            decision.refused = _shell_refusal(decision, release, announced)
    return decision


def _shell_refusal(decision: Decision, release: str, announced: Channel | None) -> str:
    """Why a feature release cannot be announced with the shell layers it has, or ``""``."""
    from . import shell as shells

    lacking = [platform for platform in decision.shell_platforms
               if f"shell-{platform}" not in decision.shell
               or decision.layers[f"shell-{platform}"].meta.get("version") != decision.version]
    if lacking:
        return (f"{release} changes the App shell, and {', '.join(lacking)} has no shell layer of {decision.version}. "
                "The Release workflow uploads one from each signed App; announce once it finished")
    for platform in decision.shell_platforms:
        meta = decision.layers[f"shell-{platform}"].meta
        if not shells.hosts(meta, decision.shell_compat, decision.base):
            return f"the {platform} shell of {release} cannot host its own code (shell_compat {decision.shell_compat})"
        if announced is not None and not shells.hosts(
                {**meta, "python_minor": None}, announced.shell_compat, decision.base):
            return (f"the {platform} shell of {release} cannot open the announced {announced.name} "
                    f"(shell_compat {announced.shell_compat}), which it first starts on after the swap")
    return ""


def _shell_version(name: str, decision: Decision) -> dict:
    """A shell layer's App version, in the pointer, so an App sees a newer shell without reading the layer."""
    if not name.startswith("shell-"):
        return {}
    return {"version": str(decision.layers[name].meta.get("version") or "")}


def _write_pointer(out: Path, name: str, fields: dict, key_pem: bytes, trusted_keys: Iterable[str] | None) -> None:
    blob = channels.seal(channels.encode(fields, name=name), key_pem)
    channels.open_pointer(blob, name=name, trusted_keys=trusted_keys)  # What every client checks.
    (out / name).write_bytes(blob)


@dataclass
class Publication:
    """A publish, written out and ready to upload: new packs first, then each pointer (it carries its signature)."""

    directory: Path
    sequence: int
    new_packs: list[str]
    pointers: list[str]  # LATEST, and ANNOUNCED for a release.
    packs: dict[str, dict]


def compose(
    repo: Path, commit: str, frontend: Path, live: Live, decision: Decision, out: Path, *, key_pem: bytes,
    release: str | None = None, notes: str = "", summary: str = "", published_at: str | None = None,
    level: int = objects.LEVEL, trusted_keys: Iterable[str] | None = None,
) -> Publication:
    """Write the new pack and the signed pointers of a publish into ``out``."""
    if decision.refused:
        raise Refused(decision.refused)
    out.mkdir(parents=True, exist_ok=True)
    code_ref, code, code_pack = code_layer(repo, commit, frontend, out, live, level=level)
    layers: dict[str, LayerRef] = {"code": code_ref}
    known = live.packs()
    packs: dict[str, dict] = {}

    def listed(pack: str) -> dict:
        if pack == code_pack:
            return pack_info(out / pack)
        if pack not in known:
            raise ReleaseError(f"{pack} is used by a live layer but no live pointer lists it")
        return known[pack]

    for pack in code.packs() | {code_ref.where.pack}:
        packs[pack] = listed(pack)
    for name, item in sorted({**decision.runtime, **decision.shell}.items()):
        if isinstance(item, LayerRecord):
            layers[name] = item.ref
            packs.update(item.packs)
        else:
            layers[name] = item
            for pack in decision.layers[name].packs() | {item.where.pack}:
                packs[pack] = listed(pack)
    for name, layer in [("code", code), *decision.layers.items()]:
        unlisted = layer.packs() - set(packs)
        if unlisted:
            raise ReleaseError(f"layer {name} is in packs the pointer would not list: {', '.join(sorted(unlisted))}")
    moment = published_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sequence = live.sequence + 1
    fields = {
        "sequence": sequence, "published_at": moment, "version": decision.version, "build": commit[:12],
        "commit": commit, "shell_compat": decision.shell_compat, "packs": packs,
        "layers": {name: {"manifest": ref.manifest, "pack": ref.where.pack, "offset": ref.where.offset,
                          "length": ref.where.length, **_shell_version(name, decision)}
                   for name, ref in layers.items()},
    }
    pointers = [channels.LATEST]
    written = {channels.LATEST: {**fields, "announce": release is not None, "notes": notes if release else summary}}
    if release is not None:
        pointers.append(channels.ANNOUNCED)
        written[channels.ANNOUNCED] = {**fields, "announce": True, "notes": notes}
    for name, value in written.items():
        _write_pointer(out, name, value, key_pem, trusted_keys)
    new_packs = sorted({code_pack} - {None})  # type: ignore[arg-type]
    waiting = [*decision.runtime.values(), *decision.shell.values()]
    new_packs += sorted(item.ref.where.pack for item in waiting
                        if isinstance(item, LayerRecord) and item.ref.where.pack not in known)
    for item in waiting:
        if isinstance(item, LayerRecord):
            new_packs += [pack for pack in item.packs if pack not in known and pack not in new_packs]
    return Publication(out, sequence, sorted(set(new_packs)), pointers, packs)


def unreferenced(
    assets: Iterable[str], history: list[Channel], live: list[Channel | None], *, pending: Iterable[str] = (),
    keep: int = KEEP_POINTERS,
) -> list[str]:
    """Packs on the release that nothing may still read.

    Kept: what the ``live`` pointers name (``latest.json`` and
    ``channel.json``, also ones just published), what the last ``keep``
    pointers in ``history`` name (a client may be partway through one), and
    ``pending``: the packs of runtime layers built but not yet published.
    """
    recent = sorted(history, key=lambda pointer: pointer.sequence)[-keep:]
    used = {pack for pointer in [*recent, *live] if pointer is not None for pack in pointer.packs}
    used |= set(pending)
    return sorted(name for name in assets if objects.PACK.fullmatch(name) and name not in used)


def dead_fraction(live: Live) -> float:
    """How much of the packs either live pointer names no layer of theirs uses."""
    if live.other_schema:
        return 0.0  # A repack rewrites only layers this build reads; the next publish replaces the rest.
    total = sum(info["bytes"] for info in live.packs().values())
    used = sum(where.length for where in live.used())
    return 0.0 if total == 0 else max(0.0, 1 - used / total)


def repack(
    transport: Transport, live: Live, out: Path, *, key_pem: bytes, local: Path | None = None,
    published_at: str | None = None, level: int = objects.LEVEL,
    trusted_keys: Iterable[str] | None = None,
) -> Publication:
    """Write every layer of the live ``latest.json`` again into one new pack, and a pointer naming it.

    Objects are read from the publisher's copies of the packs in ``local``
    when there, else as ranges of the published packs. The announced pointer
    is left as it is; :func:`unreferenced` keeps its packs.
    """
    if live.latest is None:
        raise ReleaseError("nothing is published, so there is nothing to repack")
    if live.other_schema:
        raise ReleaseError(f"{', '.join(sorted(live.other_schema))} are in an older layer format; publish first")
    out.mkdir(parents=True, exist_ok=True)

    def reader(digest: str, where: Location, size: int) -> Callable[[], bytes]:
        def read() -> bytes:
            copy = local / where.pack if local is not None else None
            if copy is not None and copy.is_file():
                with open(copy, "rb") as stream:
                    stream.seek(where.offset)
                    return objects.unpack_object(stream.read(where.length), digest, size)
            return read_object(transport, where, digest, size)
        return read

    readers: dict[str, Callable[[], bytes]] = {}
    for _ref, layer in live.layers.values():
        for entry in layer.files():
            for digest, size in entry.parts():
                readers.setdefault(digest, reader(digest, layer.objects[digest], size))
    roots: dict[str, str] = {}

    def trailer(here: dict[str, Location], add: Callable[[bytes], Location]) -> None:
        for name, (_ref, layer) in sorted(live.layers.items()):
            again = Layer(name, layer.meta, layer.entries, {digest: here[digest] for digest in layer.objects})
            roots[name] = objects.write_trees(again, add)[0]

    pack, locations = objects.write_pack(out, readers, level=level, trailer=trailer)
    assert pack is not None
    layers = {}
    for name, digest in roots.items():
        where = locations[digest]
        layers[name] = {"manifest": digest, "pack": pack, "offset": where.offset, "length": where.length}
    previous = json.loads(live.latest.data)
    previous.pop("schema", None)
    sequence = live.sequence + 1
    fields = {**previous, "sequence": sequence, "layers": layers, "packs": {pack: pack_info(out / pack)},
              "published_at": published_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    fields.pop("pointer", None)
    _write_pointer(out, channels.LATEST, fields, key_pem, trusted_keys)
    return Publication(out, sequence, [pack], [channels.LATEST], fields["packs"])
