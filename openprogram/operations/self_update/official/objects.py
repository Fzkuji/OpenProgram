"""Objects, packs and layer manifests: the content-addressed update format.

* An **object** is one file's bytes, named by their sha256. A file of
  :data:`CHUNKED_FROM` bytes or more is cut into :data:`CHUNK_SIZE` pieces
  instead and each piece is an object, so a file that changed in a few places
  (a signed binary whose signature and header changed, an archive with one
  member replaced) costs only the pieces that differ. Its tree entry lists the
  pieces; a client joins them and checks the whole file's sha256 too.
* A **pack** (``pack-<12 hex of its sha256>.pack``) is objects written one
  after another, each an independent zstd frame. It has no header or index and
  is never changed once published; where an object is lives in the manifests.
* A layer is a tree of **tree objects**, one per directory, Git style: each
  lists its files (``mode, size, sha256`` and where the object is, or its
  pieces), links and
  subdirectories (the tree object's sha256 and where it is), with ``""`` for
  the pack the tree itself is in. The root tree, with the layer's ``meta``, is
  the **layer manifest**. A directory whose contents did not change keeps its
  tree object, so a client reads only the trees on the path to a change
  (:func:`write_trees`, :func:`read_layer`).

A client fetches only the objects it lacks, as byte ranges of packs
(:func:`merge_ranges`), checks each against its name after decompression and
keeps it in ``<runtime>/objects/`` (:class:`ObjectStore`), sharded by the
first two hex digits. Files are placed from there or from identical files
already here by :func:`place`: a copy-on-write clone where the filesystem has
them, a hard link for read-only trees, else a copy.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys

from openprogram.host._platform.filesystem import replace_file

from .repository import ReleaseError

# 3: a file of CHUNKED_FROM bytes or more is listed as pieces.
SCHEMA = 3
MAX_DEPTH = 64
MAX_ENTRIES = 2_000_000
LEVEL = 19
# One directory's tree; the largest real one is a few hundred KB.
MAX_TREE_BYTES = 16 * 1024 ** 2
# Ranges closer than this are fetched as one request; the bytes between are thrown away.
MERGE_GAP = 64 * 1024
MAX_REQUESTS_PER_PACK = 32
# Files this large are kept as fixed-size pieces. Fixed offsets suit what
# changes in a shipped file: a code signature is rewritten at the end and its
# size in the header, so the pieces between keep their bytes and their names.
# Measured on the signed 1.5.1 App re-signed: 256 KiB pieces from 1 MiB leave
# 21 MB to download (1 MiB from 4 MiB: 47 MB; whole files: 291 MB), while a
# file that changed throughout compresses about 17% larger than whole.
CHUNK_SIZE = 256 * 1024
CHUNKED_FROM = 1024 ** 2
PACK = re.compile(r"pack-[0-9a-f]{12}\.pack")
# In a layer manifest, the pack the manifest itself is in: its name is the hash
# of its bytes, manifest included, so the manifest cannot spell it.
HOME = ""
SHA256 = re.compile(r"[0-9a-f]{64}")
_GIT_BLOB = re.compile(r"[0-9a-f]{40}")
#: The layers a pointer can name: the code, a runtime per platform, and the App
#: shell for the platforms whose App replaces itself from a layer.
LAYER_NAME = re.compile(r"code|runtime-(macos|linux|windows)-(arm64|x86_64)|shell-(macos|windows|linux)-(arm64|x86_64)")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def chunk_spans(size: int) -> list[tuple[int, int]]:
    """``(offset, length)`` of each piece of a file of ``size`` bytes; none below :data:`CHUNKED_FROM`."""
    if size < CHUNKED_FROM:
        return []
    return [(offset, min(CHUNK_SIZE, size - offset)) for offset in range(0, size, CHUNK_SIZE)]


def hash_file(path: Path) -> tuple[int, str, tuple[tuple[str, int], ...]]:
    """A file's size, sha256 and pieces, in one read."""
    whole = hashlib.sha256()
    pieces: list[tuple[str, int]] = []
    size = 0
    with open(path, "rb") as stream:
        while block := stream.read(CHUNK_SIZE):
            whole.update(block)
            pieces.append((sha256_bytes(block), len(block)))
            size += len(block)
    return size, whole.hexdigest(), tuple(pieces) if size >= CHUNKED_FROM else ()


def file_readers(entry: Entry, read: Callable[[int, int], bytes]) -> dict[str, Callable[[], bytes]]:
    """A reader for each object of ``entry``, given one that reads ``(offset, length)`` of its bytes."""
    if not entry.chunks:
        return {entry.sha256: lambda: read(0, entry.size)}
    readers: dict[str, Callable[[], bytes]] = {}
    for (digest, _size), (offset, length) in zip(entry.chunks, chunk_spans(entry.size), strict=True):
        readers.setdefault(digest, lambda offset=offset, length=length: read(offset, length))
    return readers


def path_reader(path: Path) -> Callable[[int, int], bytes]:
    def read(offset: int, length: int) -> bytes:
        with open(path, "rb") as stream:
            stream.seek(offset)
            return stream.read(length)
    return read


def bytes_entry(
    path: str, mode: int, data: bytes, *, git: str | None = None,
) -> tuple[Entry, dict[str, Callable[[], bytes]]]:
    """The entry of a file held in memory, and its objects' readers."""
    chunks = tuple((sha256_bytes(data[offset:offset + length]), length) for offset, length in chunk_spans(len(data)))
    entry = Entry(path, mode, len(data), sha256_bytes(data), git=git, chunks=chunks)
    return entry, file_readers(entry, lambda offset, length: data[offset:offset + length])


def file_entry(
    source: Path, path: str, mode: int, *, git: str | None = None,
) -> tuple[Entry, dict[str, Callable[[], bytes]]]:
    """The entry of the file ``source`` as ``path``, and its objects' readers."""
    size, digest, chunks = hash_file(source)
    entry = Entry(path, mode, size, digest, git=git, chunks=chunks)
    return entry, file_readers(entry, path_reader(source))


def git_blob_id(path: Path) -> str:
    """Git's id for a file's bytes, as ``git hash-object`` gives it."""
    digest = hashlib.sha1()
    digest.update(f"blob {path.stat().st_size}\0".encode("ascii"))
    with open(path, "rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def git_link_blob_id(target: str) -> str:
    data = target.encode("utf-8")
    return hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data).hexdigest()


# --- Manifests ---------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    """One file or link of a layer."""

    path: str
    mode: int = 0o644
    size: int = 0
    sha256: str = ""
    symlink: str | None = None
    git: str | None = None  # Git blob id, for code-layer sources.
    # (sha256, size) of each piece, for a file of CHUNKED_FROM bytes or more.
    chunks: tuple[tuple[str, int], ...] = ()

    def parts(self) -> tuple[tuple[str, int], ...]:
        """The objects this file is published as: its pieces, or the whole file."""
        return self.chunks or ((self.sha256, self.size),)


@dataclass(frozen=True)
class Location:
    pack: str
    offset: int
    length: int


@dataclass
class Layer:
    """A layer, read from its tree objects or about to be written as them."""

    name: str
    meta: dict
    entries: list[Entry]
    objects: dict[str, Location] = field(default_factory=dict)
    # Directory content key -> (tree object, where it is): what a later publish
    # reuses for a directory whose contents did not change. The root is included.
    trees: dict[str, tuple[str, Location]] = field(default_factory=dict)

    def files(self) -> Iterator[Entry]:
        return (entry for entry in self.entries if entry.symlink is None)

    def digests(self) -> set[str]:
        """The whole files, as the object store keeps them."""
        return {entry.sha256 for entry in self.files()}

    def sizes(self) -> dict[str, int]:
        """Every published object of this layer's files, whole file or piece, with its size."""
        return {digest: size for entry in self.files() for digest, size in entry.parts()}

    def wanted(self, entry: Entry, have: Callable[[str], bool] = lambda _digest: False) -> dict[str, Location]:
        """Where the objects of ``entry`` that ``have`` lacks are."""
        return {digest: self.objects[digest] for digest, _size in entry.parts() if not have(digest)}

    def tree_digests(self) -> set[str]:
        return {digest for digest, _where in self.trees.values()}

    def packs(self) -> set[str]:
        """Every pack a reader of this layer touches: its files' and its trees', reused ones in older packs too."""
        return {where.pack for where in self.objects.values()} | {where.pack for _digest, where in self.trees.values()}

    def under(self, prefix: str) -> list[Entry]:
        return [entry for entry in self.entries if entry.path.startswith(prefix)]


def safe_path(value: object) -> str:
    """A relative ``/`` path with no ``..``, or :class:`ReleaseError`."""
    if not isinstance(value, str) or not value or "\\" in value or "\0" in value:
        raise ReleaseError(f"a layer names an unsafe path: {value!r}")
    parts = value.split("/")
    if value.startswith("/") or any(part in ("", ".", "..") for part in parts) or re.match(r"[A-Za-z]:", value):
        raise ReleaseError(f"a layer names an unsafe path: {value!r}")
    return value


def safe_link(path: str, target: object) -> str:
    """A link target that stays inside the layer."""
    if not isinstance(target, str) or not target or target.startswith("/") or "\\" in target or "\0" in target:
        raise ReleaseError(f"{path} links outside its layer")
    depth = len(path.split("/")) - 1
    for part in target.split("/"):
        depth += -1 if part == ".." else 0 if part in ("", ".") else 1
        if depth < 0:
            raise ReleaseError(f"{path} links outside its layer")
    return target


MAX_LINK_HOPS = 40


def resolve_link(path: str, links: dict[str, str]) -> str:
    """Where the link at ``path`` ends, following links in the tree; :class:`ReleaseError` if it leaves it.

    Each link on its own stays inside (:func:`safe_link`), but a chain can
    still climb out (``x -> a/b/..`` with ``a/b -> ..``), so links are
    followed against the tree itself.
    """
    stack = path.split("/")[:-1]
    queue = links[path].split("/")
    hops = 0
    while queue:
        part = queue.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if not stack:
                raise ReleaseError(f"{path} links outside its layer")
            stack.pop()
            continue
        stack.append(part)
        target = links.get("/".join(stack))
        if target is not None:
            hops += 1
            if hops > MAX_LINK_HOPS:
                raise ReleaseError(f"{path} links in a loop")
            stack.pop()
            queue = target.split("/") + queue
    return "/".join(stack)


def check_links(layer: Layer) -> None:
    """No entry under a link, and no link chain that leaves the layer."""
    links = {entry.path: entry.symlink for entry in layer.entries if entry.symlink is not None}
    for entry in layer.entries:
        parts = entry.path.split("/")
        for index in range(1, len(parts)):
            if "/".join(parts[:index]) in links:
                raise ReleaseError(f"{entry.path} is under a link")
    for path in links:
        resolve_link(path, links)


def _dumps(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _file_key(name: str, entry: Entry) -> list:
    if entry.symlink is not None:
        return [name, "l", entry.symlink, entry.git]
    return [name, "f", entry.mode, entry.size, entry.sha256, entry.git, len(entry.chunks)]


def write_trees(
    layer: Layer, add: Callable[[bytes], Location], *, reuse: dict[str, tuple[str, Location]] | None = None,
) -> tuple[str, Location]:
    """Write ``layer`` as tree objects through ``add``, children first; returns the root's name and place.

    A tree is one directory: ``{"entries": [...]}``, each entry a file
    ``{name, mode, size, sha256, at}`` (for a file in pieces ``chunks``,
    ``[[sha256, size, at], ...]``, in place of ``at``), a link ``{name, symlink}`` or a
    directory ``{name, tree, at}``, where ``at`` is ``[pack, offset,
    length]`` and :data:`HOME` names the pack the tree itself is in. The root
    tree also holds ``schema``, ``layer`` and ``meta``: it is the layer
    manifest. A directory whose contents equal one in ``reuse`` is not written
    again; its existing tree is named instead, so an unchanged directory costs
    a client nothing. Every directory's tree, written or reused, is recorded
    in ``layer.trees``.
    """
    reuse = reuse or {}
    nested: dict = {}
    for entry in layer.entries:
        *parents, name = entry.path.split("/")
        node = nested
        for part in parents:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise ReleaseError(f"{entry.path} is under a file")
        if name in node:
            raise ReleaseError(f"{entry.path} is listed twice")
        node[name] = entry
    written: dict[str, tuple[str, Location]] = {}

    def at(where: Location) -> list:
        return [where.pack, where.offset, where.length]

    def visit(node: dict, top: bool) -> tuple[str, Location, str]:
        items, keys = [], []
        for name in sorted(node):
            value = node[name]
            if isinstance(value, dict):
                digest, where, key = visit(value, False)
                items.append({"name": name, "tree": digest, "at": at(where)})
                keys.append([name, "d", key])
                continue
            keys.append(_file_key(name, value))
            if value.symlink is not None:
                item = {"name": name, "symlink": value.symlink}
            else:
                item = {"name": name, "mode": value.mode, "size": value.size, "sha256": value.sha256}
                if value.chunks:
                    item["chunks"] = [[digest, size, at(layer.objects[digest])] for digest, size in value.chunks]
                else:
                    item["at"] = at(layer.objects[value.sha256])
            if value.git is not None:
                item["git"] = value.git
            items.append(item)
        key = sha256_bytes(_dumps(keys))
        known = None if top else reuse.get(key) or written.get(key)
        if known is None:
            tree: dict = {"entries": items}
            if top:
                tree.update({"schema": SCHEMA, "layer": layer.name, "meta": layer.meta})
            data = _dumps(tree)
            known = written[key] = (sha256_bytes(data), add(data))
        layer.trees.setdefault(key, known)
        return known[0], known[1], key

    digest, where, _key = visit(nested, True)
    return digest, where


def _location(value: object, home: str) -> Location:
    if not isinstance(value, list) or len(value) != 3:
        raise ReleaseError("a layer tree has a malformed location")
    pack, offset, length = value
    if pack == HOME:
        pack = home
    if not isinstance(pack, str) or PACK.fullmatch(pack) is None or type(offset) is not int \
            or type(length) is not int or offset < 0 or length <= 0:
        raise ReleaseError("a layer tree has a malformed location")
    return Location(pack, offset, length)


def _chunks(
    path: str, size: int, value: object, home: str, found: dict[str, Location],
) -> tuple[tuple[str, int], ...]:
    """A file's pieces as its tree lists them: there exactly when it is large enough, each the expected size."""
    spans = chunk_spans(size)
    if value is None and not spans:
        return ()
    if not isinstance(value, list) or len(value) != len(spans):
        raise ReleaseError(f"{path} lists the wrong pieces for its size")
    chunks = []
    for item, (_offset, length) in zip(value, spans, strict=True):
        if not isinstance(item, list) or len(item) != 3:
            raise ReleaseError(f"{path} lists a malformed piece")
        digest, piece, where = item
        if not isinstance(digest, str) or SHA256.fullmatch(digest) is None or piece != length:
            raise ReleaseError(f"{path} lists a malformed piece")
        found.setdefault(digest, _location(where, home))
        chunks.append((digest, length))
    return tuple(chunks)


def _tree(data: bytes) -> dict:
    try:
        value = json.loads(data)
    except ValueError as exc:
        raise ReleaseError(f"a layer tree is unreadable: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("entries"), list):
        raise ReleaseError("a layer tree has no entries")
    return value


class OtherSchema(ReleaseError):
    """A layer written in another layer format than :data:`SCHEMA`."""


def read_layer(
    name: str, manifest: str, where: Location,
    fetch: Callable[[dict[str, Location]], dict[str, bytes]],
) -> Layer:
    """Read layer ``name`` from its root tree, fetching each level's trees together. Raises :class:`ReleaseError`.

    ``fetch`` takes ``sha256 -> location`` and returns each object's checked bytes.
    """
    root = _tree(fetch({manifest: where})[manifest])
    if root.get("schema") != SCHEMA:
        raise OtherSchema(f"layer {name} is in layer format {root.get('schema')!r}; this build reads format {SCHEMA}")
    if root.get("layer") != name or LAYER_NAME.fullmatch(name) is None:
        raise ReleaseError(f"the manifest is for layer {root.get('layer')!r}, not {name!r}")
    meta = root.get("meta")
    if not isinstance(meta, dict):
        raise ReleaseError("a layer manifest has no meta")
    layer = Layer(name, meta, [])
    # path -> (tree, where, keys with child directories by path)
    seen: dict[str, tuple[str, Location, list]] = {}
    level: list[tuple[str, str, Location, dict]] = [("", manifest, where, root)]
    depth = 0
    while level:
        depth += 1
        if depth > MAX_DEPTH or len(layer.entries) > MAX_ENTRIES:
            raise ReleaseError("a layer tree is too deep or too large")
        below: list[tuple[str, str, Location]] = []
        for prefix, digest, place_, tree in level:
            keys: list = []
            names: set[str] = set()
            for item in tree["entries"]:
                if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                    raise ReleaseError("a layer tree has a malformed entry")
                part = item["name"]
                if "/" in part or part in names:
                    raise ReleaseError(f"a layer tree names {prefix}{part!r} badly or twice")
                names.add(part)
                path = safe_path(prefix + part)
                blob = item.get("git")
                if blob is not None and (not isinstance(blob, str) or _GIT_BLOB.fullmatch(blob) is None):
                    raise ReleaseError(f"{path} has an invalid Git blob id")
                if "tree" in item:
                    child = item["tree"]
                    if not isinstance(child, str) or SHA256.fullmatch(child) is None:
                        raise ReleaseError(f"{path} names an invalid tree")
                    below.append((path + "/", child, _location(item.get("at"), place_.pack)))
                    keys.append([part, "d", path])
                    continue
                if "symlink" in item:
                    entry = Entry(path, symlink=safe_link(path, item["symlink"]), git=blob)
                else:
                    mode, size, sha = item.get("mode"), item.get("size"), item.get("sha256")
                    if mode not in (0o644, 0o755) or type(size) is not int or size < 0:
                        raise ReleaseError(f"{path} has an invalid mode or size")
                    if not isinstance(sha, str) or SHA256.fullmatch(sha) is None:
                        raise ReleaseError(f"{path} has an invalid hash")
                    chunks = _chunks(path, size, item.get("chunks"), place_.pack, layer.objects)
                    entry = Entry(path, mode, size, sha, git=blob, chunks=chunks)
                    if not chunks:
                        layer.objects.setdefault(sha, _location(item.get("at"), place_.pack))
                layer.entries.append(entry)
                keys.append(_file_key(part, entry))
            seen[prefix] = (digest, place_, keys)
        wanted = {digest: spot for _prefix, digest, spot in below}
        fetched = fetch(wanted) if wanted else {}
        level = [(prefix, digest, spot, _tree(fetched[digest])) for prefix, digest, spot in below]

    done: dict[str, str] = {}

    def key_of(prefix: str) -> str:
        if prefix not in done:
            digest, spot, keys = seen[prefix]
            resolved = [[item[0], "d", key_of(item[2] + "/")] if item[1] == "d" else item for item in keys]
            done[prefix] = sha256_bytes(_dumps(resolved))
            layer.trees.setdefault(done[prefix], (digest, spot))
        return done[prefix]

    key_of("")
    layer.entries.sort(key=lambda entry: entry.path)
    check_links(layer)
    return layer


def scan_tree(
    root: Path, prefix: str = "", *, git_ids: bool = False,
) -> tuple[list[Entry], dict[str, Callable[[], bytes]]]:
    """The entries of a directory, and a reader for each object."""
    entries: list[Entry] = []
    readers: dict[str, Callable[[], bytes]] = {}
    for current, directories, names in os.walk(root):
        directory = Path(current)
        for name in list(directories):
            if (directory / name).is_symlink():
                directories.remove(name)
                names.append(name)
        directories.sort()
        for name in sorted(names):
            path = directory / name
            relative = prefix + path.relative_to(root).as_posix()
            if path.is_symlink():
                target = os.readlink(path).replace(os.sep, "/")
                entries.append(Entry(safe_path(relative), symlink=safe_link(relative, target),
                                     git=git_link_blob_id(target) if git_ids else None))
                continue
            info = path.stat()
            if not stat.S_ISREG(info.st_mode):
                continue
            entry, found = file_entry(path, safe_path(relative), 0o755 if info.st_mode & 0o111 else 0o644,
                                      git=git_blob_id(path) if git_ids else None)
            entries.append(entry)
            for digest, read in found.items():
                readers.setdefault(digest, read)
    return entries, readers


# --- Packs -------------------------------------------------------------------


def write_pack(
    directory: Path, objects: dict[str, Callable[[], bytes]], *, level: int = LEVEL,
    trailer: Callable[[dict[str, Location], Callable[[bytes], Location]], None] | None = None,
) -> tuple[str | None, dict[str, Location]]:
    """Write ``objects`` (sha256 -> reader) as one pack in ``directory``.

    ``trailer`` is given where the objects are, with :data:`HOME` as the pack's
    name since it is not known yet, and a function that appends one more object
    and says where it went: the layers' tree objects, written after the files. Returns the pack's name
    (``None`` when there was nothing to write) and where every object is.
    """
    import zstandard

    order = sorted(objects)

    def compress(data: bytes) -> bytes:
        return zstandard.ZstdCompressor(level=level, write_content_size=True).compress(data)

    def frame(digest: str) -> bytes:
        data = objects[digest]()
        if sha256_bytes(data) != digest:
            raise ReleaseError(f"object {digest[:12]} changed while it was packed")
        return compress(data)

    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f".pack-{os.getpid()}.tmp"
    locations: dict[str, Location] = {}
    digest = hashlib.sha256()
    offset = 0

    def add(output, key: str, data: bytes) -> None:
        nonlocal offset
        output.write(data)
        digest.update(data)
        locations[key] = Location(HOME, offset, len(data))
        offset += len(data)

    with open(temporary, "wb") as output, ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        for key, data in zip(order, pool.map(frame, order)):
            add(output, key, data)
        if trailer is not None:
            def append(data: bytes) -> Location:
                key = sha256_bytes(data)
                if key not in locations:
                    add(output, key, compress(data))
                return locations[key]

            trailer(dict(locations), append)
    if not locations:
        temporary.unlink()
        return None, {}
    name = f"pack-{digest.hexdigest()[:12]}.pack"
    replace_file(temporary, directory / name)
    return name, {key: Location(name, where.offset, where.length) for key, where in locations.items()}


def unpack_object(frame: bytes, digest: str, size: int | None = None) -> bytes:
    """One object from its frame, checked against its name.

    ``size`` is the file's size from its tree; a tree itself (``None``) may be
    at most :data:`MAX_TREE_BYTES`. Decompression stops one byte past that,
    whatever size the frame claims, so a hostile frame cannot fill memory.
    """
    import zstandard

    limit = MAX_TREE_BYTES if size is None else size
    chunks: list[bytes] = []
    total = 0
    try:
        with zstandard.ZstdDecompressor().stream_reader(frame) as reader:
            while chunk := reader.read(min(1 << 20, limit + 1 - total)):
                total += len(chunk)
                if total > limit:
                    raise ReleaseError(f"object {digest[:12]} is larger than its {limit} bytes")
                chunks.append(chunk)
    except zstandard.ZstdError as exc:
        raise ReleaseError(f"object {digest[:12]} could not be decompressed: {exc}") from exc
    data = b"".join(chunks)
    if (size is not None and total != size) or sha256_bytes(data) != digest:
        raise ReleaseError(f"object {digest[:12]} does not match its name")
    return data


@dataclass(frozen=True)
class Span:
    """One request: bytes ``start`` to ``end`` (exclusive) of a pack, covering ``members``."""

    start: int
    end: int
    members: tuple[tuple[str, int, int], ...]  # (sha256, offset, length)


def merge_ranges(
    wanted: Iterable[tuple[str, int, int]], *, gap: int = MERGE_GAP, limit: int = MAX_REQUESTS_PER_PACK,
) -> list[Span]:
    """Requests covering ``(sha256, offset, length)`` of one pack.

    Objects closer than ``gap`` share a request; while there are more than
    ``limit`` requests the two separated by the smallest gap are joined.
    """
    items = sorted(set(wanted), key=lambda item: item[1])
    spans: list[list] = []
    for item in items:
        _digest, offset, length = item
        if spans and offset - spans[-1][1] <= gap:
            spans[-1][1] = max(spans[-1][1], offset + length)
            spans[-1][2].append(item)
        else:
            spans.append([offset, offset + length, [item]])
    while len(spans) > max(1, limit):
        index = min(range(len(spans) - 1), key=lambda position: spans[position + 1][0] - spans[position][1])
        left, right = spans[index], spans.pop(index + 1)
        left[1] = max(left[1], right[1])
        left[2].extend(right[2])
    return [Span(start, end, tuple(members)) for start, end, members in spans]


# --- The local object store ----------------------------------------------------


def _clone(source: Path, target: Path) -> bool:
    """A copy-on-write clone of one file; ``False`` where the filesystem has none."""
    try:
        if sys.platform == "darwin":
            import ctypes

            libc = ctypes.CDLL("/usr/lib/libSystem.dylib", use_errno=True)
            return libc.clonefile(os.fsencode(source), os.fsencode(target), 0) == 0
        if sys.platform.startswith("linux"):
            import fcntl

            with open(source, "rb") as reader, open(target, "wb") as writer:
                try:
                    fcntl.ioctl(writer.fileno(), 0x40049409, reader.fileno())  # FICLONE
                    return True
                except OSError:
                    pass
            target.unlink(missing_ok=True)
    except (OSError, AttributeError):
        target.unlink(missing_ok=True)
    return False


def place(source: Path, target: Path, mode: int, *, link: bool = False) -> None:
    """Put ``source``'s bytes at ``target`` with ``mode``.

    A clone first; ``link`` allows a hard link, for trees nothing writes to
    (a base), whose mode the source then already has; else a copy.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    if link and stat.S_IMODE(source.stat().st_mode) == mode:
        try:
            os.link(source, target)
            return
        except OSError:
            pass
    if not _clone(source, target):
        shutil.copyfile(source, target)
    os.chmod(target, mode)


class ObjectStore:
    """``<runtime>/objects/``: verified objects, by sha256."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, digest: str) -> Path:
        if SHA256.fullmatch(digest) is None:
            raise ReleaseError(f"not an object name: {digest!r}")
        return self.root / digest[:2] / digest[2:]

    def has(self, digest: str) -> bool:
        return self.path(digest).is_file()

    def read(self, digest: str) -> bytes:
        return self.path(digest).read_bytes()

    def put(self, digest: str, data: bytes) -> Path:
        if sha256_bytes(data) != digest:
            raise ReleaseError(f"object {digest[:12]} does not match its name")
        target = self.path(digest)
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
            temporary.write_bytes(data)
            os.chmod(temporary, 0o444)
            replace_file(temporary, target)
        return target

    def adopt(self, digest: str, source: Path) -> Path | None:
        """Take an identical file already on this machine; ``None`` when it is not identical."""
        target = self.path(digest)
        if target.is_file():
            return target
        try:
            if sha256_file(source) != digest:
                return None
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
            temporary.unlink(missing_ok=True)
            if not _clone(source, temporary):
                shutil.copyfile(source, temporary)
            if sha256_file(temporary) != digest:  # It changed while it was taken.
                temporary.unlink()
                return None
            os.chmod(temporary, 0o444)
            replace_file(temporary, target)
        except OSError:
            return None
        return target

    def take_pieces(self, entry: Entry, source: Path) -> None:
        """Keep the pieces of ``entry`` that ``source``, an earlier version of the file, has at the same place."""
        try:
            with open(source, "rb") as stream:
                for (offset, length), (digest, _size) in zip(chunk_spans(entry.size), entry.chunks, strict=True):
                    if self.has(digest):
                        continue
                    stream.seek(offset)
                    block = stream.read(length)
                    if len(block) == length and sha256_bytes(block) == digest:
                        self.put(digest, block)
        except OSError:
            pass

    def join(self, entry: Entry) -> bool:
        """Join the pieces of ``entry`` into its whole file, checked; ``False`` while a piece is missing."""
        if self.has(entry.sha256):
            return True
        if not all(self.has(digest) for digest, _size in entry.chunks):
            return False
        target = self.path(entry.sha256)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        whole = hashlib.sha256()
        with open(temporary, "wb") as output:
            for digest, _size in entry.chunks:
                block = self.read(digest)
                whole.update(block)
                output.write(block)
        if whole.hexdigest() != entry.sha256 or temporary.stat().st_size != entry.size:
            temporary.unlink()
            raise ReleaseError(f"the pieces of {entry.path} do not make the file")
        os.chmod(temporary, 0o444)
        replace_file(temporary, target)
        return True

    def prune(self, keep: set[str]) -> int:
        """Remove every object not in ``keep``; returns how many went."""
        removed = 0
        if not self.root.is_dir():
            return 0
        for shard in self.root.iterdir():
            if not shard.is_dir():
                continue
            for item in shard.iterdir():
                if shard.name + item.name not in keep:
                    item.chmod(0o644)
                    item.unlink(missing_ok=True)
                    removed += 1
        return removed


def materialize(layer: Layer, store: ObjectStore, target: Path, *, prefix: str = "", link: bool = False) -> None:
    """Write the entries of ``layer`` under ``prefix`` into ``target``; every object must be in ``store``.

    A link under ``prefix`` must end under it too, since only that part is written.
    """
    links = {entry.path: entry.symlink for entry in layer.entries if entry.symlink is not None}
    for path in links:
        if path.startswith(prefix):
            end = resolve_link(path, links)
            if prefix and not (end + "/").startswith(prefix):
                raise ReleaseError(f"{path} links outside {prefix.rstrip('/')}")
    for entry in layer.entries:
        if not entry.path.startswith(prefix):
            continue
        destination = target.joinpath(*entry.path[len(prefix):].split("/"))
        if entry.symlink is not None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.unlink(missing_ok=True)
            os.symlink(entry.symlink, destination)
            continue
        place(store.path(entry.sha256), destination, entry.mode, link=link)
