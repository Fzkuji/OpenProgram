"""From a verified pointer to the pieces an update builds from, on this machine.

1. :func:`plan` reads the code layer's manifest (and this platform's runtime
   layer's when its base is new here), decides whether the update applies, and
   works out which objects are missing. Nothing else is written.
2. :func:`download` takes identical files already here into the object store
   (and, of a file published in pieces, the pieces its earlier version here
   still has at the same place), then fetches the rest as merged byte ranges,
   each object checked, and joins the pieces into whole files.
3. Under the update controller: :func:`assemble_base` writes a new base,
   :func:`import_source` puts the source tree into Git as the next official
   commit, and :func:`assemble_frontend` writes the prebuilt frontend for the
   version build.

What a layer path means:

* code ``source/<path>``: the commit's tracked file, with its Git blob id;
  a blob the repository already has is not fetched;
* code ``frontend/<path>``: the built Web, docs and terminal bundle;
* runtime ``<path>``: the base directory (Python, packages, Playwright, Node,
  Git, uv, the Tailscale sidecar);
* shell ``<path>``: the App, relative to the ``.app`` bundle or the Windows
  install folder, or the one AppImage file (:mod:`.shell`). When it is newer
  than the running App, :func:`stage_shell` assembles it beside the App from
  the running App's unchanged files and the downloaded ones, for Restart to
  update to swap in. The shell is compared by its own layer, not by the code:
  a newer shell is taken even when this installation already holds the code
  (``shell_only``).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import os
from pathlib import Path
import shutil

from openprogram.host._platform import runtime_layout as paths
from openprogram.host._platform.filesystem import replace_file
from openprogram.host._platform.runtime_layout import Layout
from openprogram.operations.self_update.versions import pointer as pointers
from openprogram.operations.self_update.versions import store as sealed

from . import channel as channels
from . import objects, repository
from . import shell as shells
from .channel import Channel, LayerRef, Transport
from .objects import Layer, Location, ObjectStore
from .repository import ReleaseError

SOURCE = "source/"
FRONTEND = "frontend/"
GIB = 1024 ** 3

UP_TO_DATE = "up_to_date"
AVAILABLE = "available"
WAITING_FOR_SHELL = "waiting_for_shell"
WAITING_FOR_RUNTIME = "waiting_for_runtime"
_GIT_MODES = {0o644: "100644", 0o755: "100755"}


def object_store(layout: Layout) -> ObjectStore:
    return ObjectStore(layout.root / "objects")


@dataclass
class Plan:
    """What an update needs here."""

    channel: Channel
    code: Layer
    base: str
    state: str
    detail: str = ""
    runtime: Layer | None = None  # Only when the base is new here.
    missing: dict[str, Location] = field(default_factory=dict)
    # Files already here that are likely the same, to adopt; for a file in pieces, its earlier version.
    local: dict[str, Path] = field(default_factory=dict)
    blobs: set[str] = field(default_factory=set)  # Git blobs the repository lacks.
    shell: Layer | None = None  # Only when it is newer than the running App and not staged yet.
    shell_app: shells.RunningApp | None = None
    shell_manifest: str = ""
    shell_trees: set[str] = field(default_factory=set)  # The shell layer's trees, read this time.
    shell_local: dict[str, Path] = field(default_factory=dict)  # The running App's files, by the object they should be.
    shell_staged: bool = False  # The channel's shell layer is staged already, waiting for a restart.
    shell_only: bool = False  # The code is here already; only the App shell is new.
    # The code needs the new App shell: it runs only in the new App, after Restart to update swaps it in.
    needs_swap: bool = False

    @property
    def download_bytes(self) -> int:
        return sum(where.length for where in self.missing.values())

    @property
    def unpacked_bytes(self) -> int:
        sizes = self.sizes()
        return sum(sizes.get(digest, 0) for digest in self.missing) + (
            sum(entry.size for entry in self.shell.files()) if self.shell is not None else 0)

    def layers(self) -> list[Layer]:
        return [layer for layer in (self.code, self.runtime, self.shell) if layer is not None]

    def sizes(self) -> dict[str, int]:
        """Every object of the update's files, whole file or piece, with its size."""
        return {digest: size for layer in self.layers() for digest, size in layer.sizes().items()}

    def files(self) -> dict[str, tuple[Layer, objects.Entry]]:
        """Each whole file of the update, with the layer it is in."""
        return {entry.sha256: (layer, entry) for layer in self.layers() for entry in layer.files()}

    def keep(self) -> set[str]:
        """The objects worth keeping once this update is in: its layers' files and trees.

        A staged shell is a whole App already, so of its layer only the trees
        are kept: the next check reads them from here.
        """
        kept = self.code.digests() | self.code.tree_digests() | self.shell_trees
        if self.runtime is not None:
            kept |= self.runtime.digests() | self.runtime.tree_digests()
            kept |= {ref.manifest for name, ref in self.channel.layers.items() if name.startswith("runtime-")}
        return kept


def _fetch_objects(
    layout: Layout, transport: Transport, wanted: dict[str, Location],
    say: Callable[[str], None] | None = None, sizes: dict[str, int] | None = None,
) -> None:
    """Fetch ``sha256 -> location`` into the object store, ranges merged per pack.

    ``sizes`` holds each file's size; an object not in it is a tree.
    """
    sizes = sizes or {}
    store = object_store(layout)
    by_pack: dict[str, list[tuple[str, int, int]]] = {}
    for digest, where in wanted.items():
        if not store.has(digest):
            by_pack.setdefault(where.pack, []).append((digest, where.offset, where.length))
    for pack, items in sorted(by_pack.items()):
        spans = objects.merge_ranges(items)
        if say is not None:
            size = sum(span.end - span.start for span in spans)
            say(f"Downloading {len(items)} files from {pack} ({size / 1024 ** 2:.1f} MB in {len(spans)} requests)")
        with channels.scratch(layout) as scratch:
            files = transport.spans(pack, spans, Path(scratch))
            for span, path in zip(spans, files, strict=True):
                with open(path, "rb") as stream:
                    for digest, offset, length in span.members:
                        stream.seek(offset - span.start)
                        store.put(digest, objects.unpack_object(stream.read(length), digest, sizes.get(digest)))
                path.unlink()


def load_layer(layout: Layout, transport: Transport, name: str, ref: LayerRef) -> Layer:
    """Read a layer's trees; those already in the object store (unchanged directories) are not fetched."""
    store = object_store(layout)

    def fetch(wanted: dict[str, Location]) -> dict[str, bytes]:
        _fetch_objects(layout, transport, wanted)
        return {digest: store.read(digest) for digest in wanted}

    return objects.read_layer(name, ref.manifest, ref.where, fetch)


def _running(layout: Layout) -> dict | None:
    try:
        return sealed.read_manifest(layout, pointers.read_or_recover(layout).version)
    except Exception:  # noqa: BLE001 - with no readable running version nothing is reused from it.
        return None


def _likely(local: Path, entry: objects.Entry) -> bool:
    """Whether ``local`` is worth checking for ``entry``: the same size, or any earlier version of a file in pieces."""
    try:
        return not local.is_symlink() and local.is_file() and (bool(entry.chunks) or local.stat().st_size == entry.size)
    except OSError:
        return False


def _shell_candidates(layout: Layout, plan: Plan) -> None:
    """The running App's files at the same path and size, hashed as they are placed.

    A file in pieces is taken through the object store instead, so the pieces
    its running version still has (all but the signature's, for a re-signed
    binary) are not downloaded.
    """
    assert plan.shell is not None and plan.shell_app is not None
    store = object_store(layout)
    for entry in plan.shell.files():
        if store.has(entry.sha256) or entry.sha256 in plan.shell_local or entry.sha256 in plan.local:
            continue
        # A Linux layer is the AppImage alone, and the running AppImage is that file.
        app = plan.shell_app
        local = app.root if app.system == "linux" else app.root.joinpath(*entry.path.split("/"))
        if _likely(local, entry):
            (plan.local if entry.chunks else plan.shell_local)[entry.sha256] = local
            continue
        plan.missing.update(plan.shell.wanted(entry, store.has))


def _plan_shell(layout: Layout, published: Channel, transport: Transport, platform: str, result: Plan,
                app: shells.RunningApp | None) -> None:
    """Whether this update brings an App shell newer than the running App, and what of it is missing here."""
    ref = published.shell(platform)
    if app is None or ref is None:
        return
    layer = load_layer(layout, transport, f"shell-{platform}", ref)  # Its trees are kept here, once read.
    _listed(published, layer)
    result.shell_trees = layer.tree_digests() | {ref.manifest}
    if shells.staged_for(layout, ref.manifest):
        result.shell_staged = True
        return
    if layer.meta.get("platform") != platform:
        raise ReleaseError(f"the shell layer of {published.name} is not for {platform}")
    if not shells.newer(layer.meta.get("version"), app.version):
        return
    result.shell, result.shell_app, result.shell_manifest = layer, app, ref.manifest


def _candidates(layout: Layout, plan: Plan) -> None:
    """Find files of the update already on this machine, by path and size; they are checked on adoption."""
    store = object_store(layout)
    running = _running(layout)
    roots: list[tuple[Layer | None, str, Path | None]] = [(plan.code, FRONTEND, None), (plan.runtime, "", None)]
    if running is not None:
        site = paths.site_packages(layout.version(str(running["id"])), str(running["base"]))
        roots = [(plan.code, FRONTEND, paths.frontend_bundle(site)),
                 (plan.runtime, "", layout.base(str(running["base"])))]
    for layer, prefix, root in roots:
        if layer is None:
            continue
        for entry in layer.files():
            if not entry.path.startswith(prefix) or (prefix == "" and entry.path.startswith(SOURCE)):
                continue
            if store.has(entry.sha256) or entry.sha256 in plan.local:
                continue
            if root is not None:
                local = root.joinpath(*entry.path[len(prefix):].split("/"))
                if _likely(local, entry):
                    plan.local[entry.sha256] = local
                    continue
            plan.missing.update(layer.wanted(entry, store.has))


def _listed(published: Channel, layer: Layer) -> None:
    """A layer may only be in packs its pointer lists: those are the packs kept on the release."""
    unlisted = layer.packs() - set(published.packs)
    if unlisted:
        raise ReleaseError(f"the {layer.name} layer of {published.name} is in packs its pointer does not list")


def plan(
    layout: Layout, published: Channel, transport: Transport, *, platform: str | None = None,
) -> Plan:
    """What applying ``published`` here would take. Raises :class:`ReleaseError`."""
    platform = platform or repository.current_platform()
    app = shells.running_app(platform)
    shells.forget_stale(layout, app)  # Swapped in since, or no longer for this App.
    code = load_layer(layout, transport, "code", published.layers["code"])
    base = code.meta.get("base")
    if not isinstance(base, str) or paths.BASE_ID.fullmatch(base) is None:
        raise ReleaseError(f"the code layer names an invalid base: {base!r}")
    _listed(published, code)
    result = Plan(published, code, base, AVAILABLE)
    _plan_shell(layout, published, transport, platform, result, app)
    if repository.has_official(layout, published.commit):
        if result.shell is None and not result.shell_staged:
            result.state, result.detail = UP_TO_DATE, f"This installation already contains {published.name}."
            return result
        # Only the App shell is new: the code was taken before, by an App that could not replace itself then.
        result.shell_only = True
        if result.shell is not None:
            _shell_candidates(layout, result)
        return result
    waiting = repository.needs_new_shell(layout, published.name, published.shell_compat, base)
    if waiting is not None:
        coming = result.shell.meta if result.shell is not None else (
            shells.read_staged(layout) if result.shell_staged else None)
        if coming is None or not shells.hosts(coming, published.shell_compat, base):
            result.state, result.detail = WAITING_FOR_SHELL, waiting
            return result
        result.needs_swap = True
    if not layout.base(base).is_dir():
        ref = published.runtime(platform)
        if ref is None:
            result.state = WAITING_FOR_RUNTIME
            result.detail = f"{published.name} needs a new runtime, which is not published for {platform} yet"
            return result
        result.runtime = load_layer(layout, transport, f"runtime-{platform}", ref)
        _listed(published, result.runtime)
        if result.runtime.meta.get("base") != base:
            raise ReleaseError(f"the {platform} runtime of {published.name} is not the base its code needs")
    sources = code.under(SOURCE)
    result.blobs = repository.missing_blobs(layout, (entry.git for entry in sources if entry.git))
    store = object_store(layout)
    for entry in sources:
        if entry.git is None:
            raise ReleaseError(f"{entry.path} has no Git blob id")
        if entry.symlink is None and entry.git in result.blobs and not store.has(entry.sha256):
            result.missing.update(code.wanted(entry, store.has))
    _candidates(layout, result)
    if result.shell is not None:
        _shell_candidates(layout, result)
    files = result.files()
    for digest in result.local:
        for part, _size in files[digest][1].parts():
            result.missing.pop(part, None)
    return result


def check_space(layout: Layout, update: Plan) -> None:
    from openprogram.operations.self_update.versions.build import required_space

    required = update.download_bytes + update.unpacked_bytes * 2 + required_space(layout, new_base=False)
    free = shutil.disk_usage(layout.root).free
    if free < required:
        raise ReleaseError(
            f"Not enough disk space for this update: it needs about {required / GIB:.1f} GB and "
            f"{free / GIB:.1f} GB is free, {paths.disk_shortfall(required, free)} short. "
            "Free some space and run `openprogram upgrade` again."
        )


def download(layout: Layout, update: Plan, transport: Transport, *, say: Callable[[str], None] | None = None) -> None:
    """Put every object ``update`` needs into the object store."""
    store = object_store(layout)
    files = update.files()
    for digest, local in list(update.local.items()):
        layer, entry = files[digest]
        if (entry.size == _size(local) and store.adopt(digest, local) is not None) or store.has(digest):
            del update.local[digest]
            continue
        if entry.chunks:
            store.take_pieces(entry, local)
        update.missing.update(layer.wanted(entry, store.has))
        del update.local[digest]
    _fetch_objects(layout, transport, update.missing, say=say, sizes=update.sizes())
    update.missing.clear()
    pieces = set()
    for _layer, entry in files.values():
        if entry.chunks:
            store.join(entry)
            pieces |= {digest for digest, _size in entry.chunks}
    for digest in pieces - set(files):
        path = store.path(digest)
        if path.is_file():
            path.chmod(0o644)
            path.unlink()


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return -1


def _require(store: ObjectStore, layer: Layer, prefix: str) -> None:
    for entry in layer.files():
        if entry.path.startswith(prefix) and not store.has(entry.sha256):
            raise ReleaseError(f"{entry.path} has not been downloaded")


def assemble_base(layout: Layout, update: Plan) -> bool:
    """Write the new base from the runtime layer; ``False`` when it is already here."""
    target = layout.base(update.base)
    if target.is_dir() or update.runtime is None:
        return False
    store = object_store(layout)
    _require(store, update.runtime, "")
    staging = layout.base_staging(update.base)
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        objects.materialize(update.runtime, store, staging)
        python = paths.base_python(staging)
        if not (python.is_file() or python.is_symlink()):
            raise ReleaseError("the runtime layer holds no Python")
        replace_file(staging, target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return True


def import_source(layout: Layout, update: Plan) -> str:
    """Put the source tree into Git as the next official commit; returns the local commit."""
    store = object_store(layout)
    files: dict[str, Path] = {}
    entries: list[tuple[str, str, str]] = []
    for entry in update.code.under(SOURCE):
        path = entry.path[len(SOURCE):]
        if entry.symlink is not None:
            if entry.git in update.blobs:
                repository.write_link_blob(layout, entry.symlink, str(entry.git))
            entries.append((path, "120000", str(entry.git)))
            continue
        if entry.git in update.blobs and entry.git not in files:
            if not store.has(entry.sha256):
                raise ReleaseError(f"{entry.path} has not been downloaded")
            files[str(entry.git)] = store.path(entry.sha256)
        entries.append((path, _GIT_MODES[entry.mode], str(entry.git)))
    repository.write_blobs(layout, files)
    meta = update.code.meta
    return repository.import_official(
        layout, published=update.channel.commit, tree=str(meta.get("tree") or ""), entries=entries,
        message=f"OpenProgram {update.channel.name}", when=update.channel.published_at,
    )


def assemble_frontend(layout: Layout, update: Plan, target: Path) -> Path:
    """Write the prebuilt frontend bundle into ``target``."""
    store = object_store(layout)
    _require(store, update.code, FRONTEND)
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    objects.materialize(update.code, store, target, prefix=FRONTEND)
    if not (target / "index.html").is_file():
        raise ReleaseError("the code layer holds no frontend bundle")
    return target


def stage_shell(layout: Layout, update: Plan, transport: Transport) -> Path | None:
    """Assemble the new App shell beside the App, check it and record it; its path.

    It is built in :func:`shell.staging_root`, a folder beside the running App
    on the same drive, so Restart to update swaps it in with a rename. Files
    come from the object store, or from the running App where it has the same
    bytes, each hashed after it is placed; a running file that changed since
    the plan is fetched instead. Links, modes and the layer's empty directories
    are kept, then :func:`shell.verify` checks the whole App before
    ``staged.json`` names it. A Linux layer is one file, the AppImage.
    """
    layer, app = update.shell, update.shell_app
    if layer is None or app is None:
        return None
    store = object_store(layout)
    root = shells.staging_root(app)
    work = root / f".staging-{os.getpid()}"
    shutil.rmtree(work, ignore_errors=True)
    target = work / app.root.name
    single = app.system == "linux"
    if single and [entry.path for entry in layer.entries] != [shells.APPIMAGE]:
        raise ReleaseError(f"the Linux shell layer holds more than {shells.APPIMAGE}")
    sizes = layer.sizes()

    def where(entry) -> Path:
        return target if single else target.joinpath(*entry.path.split("/"))

    try:
        stale = []
        for entry in layer.entries:
            destination = where(entry)
            if entry.symlink is not None:
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(entry.symlink, destination)
                continue
            if store.has(entry.sha256):
                objects.place(store.path(entry.sha256), destination, entry.mode)
                continue
            local = update.shell_local.get(entry.sha256)
            if local is None:
                raise ReleaseError(f"{entry.path} of the App has not been downloaded")
            objects.place(local, destination, entry.mode)
            if objects.sha256_file(destination) != entry.sha256:
                stale.append(entry)
        if stale:
            _fetch_objects(layout, transport, {entry.sha256: layer.objects[entry.sha256] for entry in stale},
                           sizes=sizes)
            for entry in stale:
                objects.place(store.path(entry.sha256), where(entry), entry.mode)
        for directory in layer.meta.get("directories") or []:
            (target / objects.safe_path(directory)).mkdir(parents=True, exist_ok=True)
        if app.system == "windows":
            # The uninstaller is written by the installer, not the build; the App keeps its own.
            for uninstaller in app.root.glob("Uninstall *.exe"):
                if not (target / uninstaller.name).exists():
                    shutil.copy2(uninstaller, target / uninstaller.name)
        shells.verify(target, app, layer.meta)
        final = shells.staged_target(app)
        if final.is_dir() and not final.is_symlink():
            shutil.rmtree(final)
        else:
            final.unlink(missing_ok=True)
        final.parent.mkdir(parents=True, exist_ok=True)
        replace_file(target, final)
        shells.record(layout, manifest=update.shell_manifest, meta=layer.meta, path=final, app=app)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return final


def official_fields(update: Plan) -> dict:
    """What version.json records about the update it was made from."""
    return {"commit": update.channel.commit, "sequence": update.channel.sequence,
            "code_layer": update.channel.layers["code"].manifest}


__all__ = [
    "AVAILABLE", "UP_TO_DATE", "WAITING_FOR_RUNTIME", "WAITING_FOR_SHELL", "Plan", "assemble_base",
    "assemble_frontend", "check_space", "download", "import_source", "load_layer", "object_store",
    "official_fields", "plan", "stage_shell",
]
