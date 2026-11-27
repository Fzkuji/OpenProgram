#!/usr/bin/env python3
"""Publish in-app updates to the rolling ``updates`` release.

    plan                 say what publishing a commit would do; exit 3 when it is refused
    run                  build the code layer, upload its pack, then the signed pointers
                         (latest.json always; channel.json too with --release vX.Y.Z)
    announce vX.Y.0      after the Release workflow finished: publish the tag's commit
                         as the feature release (its installers, runtime and shell layers exist)
    release-page         create the GitHub Release page of a code-only release (one API call)
    build-runtime-layer  turn a base directory into a runtime layer, to publish with the next run
    upload-runtime-layer the Release workflow's step: build a runtime layer from a base and
                         upload its pack and runtime-<platform>-<base>.json (no pointer, no key)
    upload-shell-layer   the Release workflow's step after building an App: build its shell layer
                         and upload its pack and shell-<platform>-<version>.json (no pointer, no key)
    gc                   delete packs nothing may still read; repack when the live packs are mostly dead
    public-key | generate-key | sign-file
                         the release signing key

``scripts/publishing/publish-github.sh`` runs ``plan`` before it pushes and
``run`` after; for a feature release it only pushes and starts the Release
workflow, and ``announce`` follows once that finished. The code layer's
frontend is built in a version store of the
publisher's own (``$OPENPROGRAM_UPDATES_HOME/store``, default
``~/.openprogram-release/updates``), so a frontend nobody changed is cloned, not
rebuilt. Pointers are signed with ``$OPENPROGRAM_RELEASE_SIGNING_KEY`` (default
``~/.openprogram-release/update-signing-key.pem``).

GitHub is reached only with gh as the publishing account (the workflow's own
token in CI): one call reads the release and its assets, then one upload per
new pack, a delete and an upload for each pointer (packs first; a pointer
carries its signature, so a reader sees the old one, none, or the new one,
never a mix), then a delete per pack nothing may still read. A daily run makes
about 5 calls. See docs/reference/design/distribution/automatic-updates.html.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from openprogram.host._platform.runtime_layout import Layout  # noqa: E402
from openprogram.operations.self_update.official import channel as channels  # noqa: E402
from openprogram.operations.self_update.official import publish, signing  # noqa: E402
from openprogram.operations.self_update.official import shell as shells  # noqa: E402
from openprogram.operations.self_update.official.repository import ReleaseError, current_platform  # noqa: E402
from openprogram.operations.self_update.versions import build as builder  # noqa: E402
from openprogram.operations.self_update.versions import store  # noqa: E402
from openprogram.host._platform import runtime_layout as paths  # noqa: E402

OWNER, REPO = "Fzkuji", "OpenProgram"
PUBLISH_ACCOUNT = "FuzzyKuji"
RELEASE_TITLE = "In-app updates"
RELEASE_BODY = (
    "What installed OpenProgram apps update from: signed pointers (channel.json, latest.json) and content "
    "packs, read by the apps themselves. This is not a release to download; installers are on the vX.Y.0 releases."
)
EXIT_REFUSED = 3
_RELEASE_HOME = Path.home() / ".openprogram-release"


class Failed(RuntimeError):
    pass


def _state() -> Path:
    path = Path(os.environ.get("OPENPROGRAM_UPDATES_HOME") or _RELEASE_HOME / "updates").expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _signing_key(path: str | None = None) -> bytes:
    key = Path(path or os.environ.get("OPENPROGRAM_RELEASE_SIGNING_KEY") or _RELEASE_HOME / "update-signing-key.pem")
    try:
        return key.expanduser().read_bytes()
    except OSError as exc:
        raise Failed(f"the release signing key could not be read at {key}: {exc}") from exc


def _commit(ref: str) -> str:
    result = subprocess.run(["git", "rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise Failed(f"{ref} is not a commit here")
    return result.stdout.strip()


def _notes(release: str) -> str:
    path = ROOT / ".github" / "release-notes" / f"{release}.md"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise Failed(f"a release needs its notes at {path.relative_to(ROOT)}") from exc


def _summary(commit: str) -> str:
    text = subprocess.run(["git", "log", "-1", "--format=%B", commit], cwd=ROOT, capture_output=True, text=True).stdout
    return text.strip()[:20_000]


# --- The frontend, built here -------------------------------------------------------


def _frontend(commit: str) -> Path:
    """The frontend bundle of ``commit``, from a version built on the last one built here."""
    layout = Layout((_state() / "store" / "runtime").resolve())
    layout.root.mkdir(parents=True, exist_ok=True)
    built = store.list_versions(layout)
    found = next((manifest for manifest in built if manifest.get("commit") == commit
                  and not store.changed_files(layout.version(str(manifest["id"])), str(manifest["base"]))), None)
    if found is None:
        parent = str(built[0]["id"]) if built else None
        result = builder.build_version(layout, builder.BuildRequest(
            f"publish-{commit[:12]}", commit, repository=ROOT, parent=parent, built_by="update publisher",
        ))
        found = store.read_manifest(layout, result.version)
        for entry in layout.versions.iterdir():
            if entry.name != found["id"]:
                shutil.rmtree(entry, ignore_errors=True)
        for entry in layout.bases.iterdir():
            if entry.name != found["base"]:
                shutil.rmtree(entry, ignore_errors=True)
    site = paths.site_packages(layout.version(str(found["id"])), str(found["base"]))
    return paths.frontend_bundle(site)


# --- GitHub -------------------------------------------------------------------------------


class GitHub:
    """gh as the publishing account (or ``GH_TOKEN`` in CI); ``calls`` counts every request made."""

    def __init__(self) -> None:
        self.calls = 0
        self.environment = dict(os.environ)
        if not self.environment.get("GH_TOKEN"):
            token = subprocess.run(["gh", "auth", "token", "--hostname", "github.com", "--user", PUBLISH_ACCOUNT],
                                   capture_output=True, text=True)
            if token.returncode != 0 or not token.stdout.strip():
                raise Failed(f"gh has no token for {PUBLISH_ACCOUNT}; run gh auth login and sign in as {PUBLISH_ACCOUNT}")
            self.environment["GH_TOKEN"] = token.stdout.strip()

    def api(self, *arguments: str, missing_ok: bool = False) -> dict | None:
        self.calls += 1
        result = subprocess.run(["gh", "api", *arguments], capture_output=True, text=True, env=self.environment, timeout=3600)
        if result.returncode != 0:
            if missing_ok and "404" in (result.stderr or result.stdout):
                return None
            raise Failed(f"gh api {arguments[-1] if arguments else ''} failed: {(result.stderr or result.stdout).strip()}")
        return json.loads(result.stdout) if result.stdout.strip() else None

    def release(self) -> dict:
        """The ``updates`` release with its assets, made the first time."""
        found = self.api(f"repos/{OWNER}/{REPO}/releases/tags/{channels.UPDATES_TAG}", missing_ok=True)
        if found is None:
            found = self.api("-X", "POST", f"repos/{OWNER}/{REPO}/releases", "-f", f"tag_name={channels.UPDATES_TAG}",
                             "-f", "target_commitish=main", "-f", f"name={RELEASE_TITLE}", "-f", f"body={RELEASE_BODY}",
                             "-F", "prerelease=true", "-f", "make_latest=false")
        return {"id": found["id"], "assets": {asset["name"]: asset["id"] for asset in found.get("assets") or []}}  # type: ignore[index]

    def upload(self, release: dict, path: Path) -> None:
        if path.name in release["assets"]:
            self.delete(release, path.name)
        url = f"https://uploads.github.com/repos/{OWNER}/{REPO}/releases/{release['id']}/assets?name={path.name}"
        made = self.api("-X", "POST", "-H", "Content-Type: application/octet-stream", "--input", str(path), url)
        release["assets"][path.name] = made["id"]  # type: ignore[index]

    def add(self, release: dict, path: Path) -> None:
        """Upload a content-named file unless it is there; another job uploading it at once is fine."""
        if path.name in release["assets"]:
            return
        try:
            self.upload(release, path)
        except Failed as exc:
            if "already_exists" not in str(exc):
                raise

    def published_release(self, tag: str) -> bool:
        found = self.api(f"repos/{OWNER}/{REPO}/releases/tags/{tag}", missing_ok=True)
        return bool(found) and not found.get("draft")  # type: ignore[union-attr]

    def delete(self, release: dict, name: str) -> None:
        self.api("-X", "DELETE", f"repos/{OWNER}/{REPO}/releases/assets/{release['assets'].pop(name)}")


def _history(state: Path) -> list[channels.Channel]:
    """Every pointer published from here: ``history/<sequence>-<channel.json|latest.json>``."""
    found = []
    for path in sorted((state / "history").glob("*-*.json")):
        name = path.name.split("-", 1)[1]
        try:
            found.append(channels.open_pointer(path.read_bytes(), name=name))
        except (OSError, ReleaseError):
            continue
    return found


def _live(transport: channels.Transport) -> publish.Live:
    return publish.read_live(transport, history=_history(_state()))


def _pending(release: dict, transport: channels.Transport) -> set[str]:
    """Packs of layers built but not yet published: runtime layers here, runtime and shell layers uploaded by CI."""
    pending = {pack for layer in publish.waiting_runtime_layers(_state()).values() for pack in layer.packs}
    for name in release["assets"]:
        if publish.LAYER_RECORD.fullmatch(name):
            data = transport.small(name)
            if data is not None:
                pending |= set(publish.LayerRecord.decode(data).packs)
    return pending


def _drop_dead_packs(github: GitHub, release: dict, live: list, transport: channels.Transport, keep: int) -> None:
    for pack in publish.unreferenced(release["assets"], _history(_state()), live,
                                     pending=_pending(release, transport), keep=keep):
        github.delete(release, pack)


def upload(publication: publish.Publication, github: GitHub, release: dict, live: publish.Live,
           transport: channels.Transport, *, keep: int = publish.KEEP_POINTERS) -> None:
    """New packs, then each pointer, then packs nothing may still read."""
    state = _state()
    (state / "packs").mkdir(exist_ok=True)
    for pack in publication.new_packs:
        source = publication.directory / pack
        if not source.is_file():
            source = state / "packs" / pack
        if pack not in release["assets"]:
            github.upload(release, source)
        if source.is_file() and source.parent != state / "packs":
            shutil.copy2(source, state / "packs" / pack)
    (state / "history").mkdir(exist_ok=True)
    pointers = {}
    for name in publication.pointers:
        github.upload(release, publication.directory / name)
        shutil.copy2(publication.directory / name, state / "history" / f"{publication.sequence:08d}-{name}")
        pointers[name] = channels.open_pointer((publication.directory / name).read_bytes(), name=name)
    _drop_dead_packs(github, release, [pointers.get(channels.LATEST, live.latest),
                                       pointers.get(channels.ANNOUNCED, live.announced)], transport, keep)


def _repair(github: GitHub, release: dict, live: publish.Live) -> None:
    """Put back a pointer the release lost (an upload that failed after its delete), from history."""
    for name in live.repair:
        pointer = live.latest if name == channels.LATEST else live.announced
        if pointer is None:
            continue
        path = _state() / "history" / f"{pointer.sequence:08d}-{name}"
        if path.is_file():
            github.upload(release, path)
            print(f"updates: put back {name} (sequence {pointer.sequence}), which the release had lost")


# --- Commands -----------------------------------------------------------------------------


def _decision(options: argparse.Namespace) -> tuple[str, publish.Live, publish.Decision]:
    commit = _commit(options.commit)
    transport = channels.HttpTransport()
    live = _live(transport)
    return commit, live, publish.decide(ROOT, commit, live, _state(), release=options.release, transport=transport,
                                        shell_platforms=_released_shell_platforms(options.release))


def _released_shell_platforms(release: str | None) -> set[str] | None:
    """For a feature release: the platforms it published an App for that replaces itself, from its own manifest."""
    if not release or not publish.feature_release(release):
        return None
    data = channels.HttpTransport(f"https://github.com/{OWNER}/{REPO}/releases/download/{release}").small(
        "release-manifest.json")
    if data is None:
        return None  # Not published yet: announcing it is refused for that anyway.
    return publish.released_shell_platforms(json.loads(data))


def _plan(options: argparse.Namespace) -> int:
    commit, live, decision = _decision(options)
    what = f"release {options.release}" if options.release else "latest.json only (no one is notified)"
    if options.release:
        what += " (feature release: announced once the Release workflow built installers and runtime layers)" \
            if publish.feature_release(options.release) else " (code-only: no workflow runs)"
    print(f"updates: would publish {what}, sequence {live.sequence + 1}\n  {decision.describe()}")
    if decision.refused:
        print(f"refused: {decision.refused}", file=sys.stderr)
        return EXIT_REFUSED
    return 0


def _run(options: argparse.Namespace) -> int:
    commit, live, decision = _decision(options)
    if decision.refused:
        print(f"refused: {decision.refused}", file=sys.stderr)
        return EXIT_REFUSED
    if live.latest is not None and live.latest.commit == commit and (
            not options.release or (live.announced is not None and live.announced.commit == commit)):
        print(f"updates: {commit[:12]} is already published")
        return 0
    notes = _notes(options.release) if options.release else ""
    github = GitHub()
    if options.release and publish.feature_release(options.release) and not github.published_release(options.release):
        print(f"refused: {options.release} is announced only after the Release workflow published its installers; "
              "run this again when it finished", file=sys.stderr)
        return EXIT_REFUSED
    release = github.release()
    transport = channels.HttpTransport()
    _repair(github, release, live)
    out = _state() / "out" / commit[:12]
    shutil.rmtree(out, ignore_errors=True)
    publication = publish.compose(
        ROOT, commit, _frontend(commit), live, decision, out, key_pem=_signing_key(),
        release=options.release, notes=notes, summary=_summary(commit),
    )
    upload(publication, github, release, live, transport)
    for layer in [*decision.runtime.values(), *decision.shell.values()]:
        if isinstance(layer, publish.LayerRecord):
            (_state() / "runtime" / f"{layer.name}.json").unlink(missing_ok=True)
            if layer.record in release["assets"]:
                github.delete(release, layer.record)
    shutil.rmtree(out, ignore_errors=True)
    print(f"updates: published {commit[:12]} as sequence {publication.sequence} "
          f"({', '.join(publication.pointers)}; {len(publication.new_packs)} new pack) with {github.calls} GitHub calls")
    return 0


def _announce(options: argparse.Namespace) -> int:
    if not publish.feature_release(options.tag):
        raise Failed(f"{options.tag} is code-only; publish-github.sh --tag {options.tag} publishes it")
    options.commit, options.release = f"refs/tags/{options.tag}", options.tag
    return _run(options)


def _release_page(options: argparse.Namespace) -> int:
    tag = options.tag
    if publish.feature_release(tag):
        raise Failed(f"{tag} is a feature release; the Release workflow makes its page with the installers")
    parts = tag[1:].split(".")
    installers = f"https://github.com/{OWNER}/{REPO}/releases/tag/v{parts[0]}.{parts[1]}.0"
    body = (f"{_notes(tag)}\n\n---\nInstalled apps update to this release by themselves. "
            f"New installs: the installers are on [v{parts[0]}.{parts[1]}.0]({installers}), which then updates in the app.")
    github = GitHub()
    github.api("-X", "POST", f"repos/{OWNER}/{REPO}/releases", "-f", f"tag_name={tag}",
               "-f", f"name=OpenProgram {tag[1:]} Release", "-f", f"body={body}", "-f", "make_latest=false")
    print(f"updates: created the release page for {tag} ({github.calls} GitHub call)")
    return 0


def _build_runtime_layer(options: argparse.Namespace) -> int:
    state = _state()
    live = _live(channels.HttpTransport())
    built = publish.build_runtime_layer(
        options.base.resolve(), platform=options.platform or current_platform(), packs=state / "packs",
        state=state, live=live, base_name=options.base_id, tailnet=options.tailnet,
    )
    print(f"updates: {built.name} for base {built.key} is ready; the next run publishes it "
          f"(new packs: {', '.join(pack for pack in built.packs if pack not in live.packs()) or 'none'})")
    return 0


def _upload_runtime_layer(options: argparse.Namespace) -> int:
    """In the Release workflow: a runtime layer for one platform, uploaded beside the packs; no pointer moves."""
    transport = channels.HttpTransport()
    live = publish.read_live(transport)
    with tempfile.TemporaryDirectory(prefix="runtime-layer-") as scratch:
        state = Path(scratch)
        built = publish.build_runtime_layer(
            options.base.resolve(), platform=options.platform, packs=state / "packs", state=state, live=live,
            base_name=options.base_id, tailnet=options.tailnet,
        )
        _upload_record(built, state)
    return 0


def _upload_record(built: publish.LayerRecord, state: Path) -> None:
    """A built layer's new packs, then its record: the record names only packs that are there."""
    github = GitHub()
    release = github.release()
    for pack in built.packs:
        if (state / "packs" / pack).is_file():
            github.add(release, state / "packs" / pack)
    record = state / built.record
    record.write_text(built.encode(), encoding="utf-8")
    github.upload(release, record)
    print(f"updates: uploaded {built.name} for {built.key} ({github.calls} GitHub calls)")


def _upload_shell_layer(options: argparse.Namespace) -> int:
    """In the Release workflow, after an App is signed and checked: its shell layer, uploaded; no pointer moves."""
    desktop = json.loads((ROOT / "apps" / "desktop" / "package.json").read_text(encoding="utf-8"))
    compat = json.loads((ROOT / "apps" / "desktop" / "shell" / "compat.json").read_text(encoding="utf-8"))
    transport = channels.HttpTransport()
    live = publish.read_live(transport)
    with tempfile.TemporaryDirectory(prefix="shell-layer-") as scratch:
        state = Path(scratch)
        built = publish.build_shell_layer(
            options.app.resolve(), platform=options.platform, version=str(desktop["version"]), compat=compat,
            packs=state / "packs", live=live,
            python_minor=shells.seed_python_minor(options.seed) if options.seed else None,
        )
        if options.no_upload:
            size = sum((state / "packs" / pack).stat().st_size for pack in built.packs
                       if (state / "packs" / pack).is_file())
            print(f"updates: built {built.name} for {built.key}, {size / 1024 ** 2:.1f} MB of new packs; not uploaded")
        else:
            _upload_record(built, state)
    return 0


def _gc(options: argparse.Namespace) -> int:
    state = _state()
    github = GitHub()
    release = github.release()
    transport = channels.HttpTransport()
    live = _live(transport)
    _repair(github, release, live)
    dead = publish.dead_fraction(live)
    if dead > options.dead:
        out = state / "out" / "repack"
        shutil.rmtree(out, ignore_errors=True)
        publication = publish.repack(transport, live, out, key_pem=_signing_key(), local=state / "packs")
        upload(publication, github, release, live, transport, keep=options.keep)
        shutil.rmtree(out, ignore_errors=True)
        print(f"updates: {dead:.0%} of the live packs was dead; repacked as sequence {publication.sequence}")
    else:
        _drop_dead_packs(github, release, [live.latest, live.announced], transport, options.keep)
        print(f"updates: {dead:.0%} of the live packs is dead; nothing repacked")
    print(f"updates: gc made {github.calls} GitHub calls")
    return 0


def _public_key(options: argparse.Namespace) -> int:
    print(signing.public_key_hex(_signing_key(options.signing_key)))
    return 0


def _generate_key(options: argparse.Namespace) -> int:
    print(signing.generate_signing_key(options.path))
    return 0


def _sign_file(options: argparse.Namespace) -> int:
    print(signing.sign_file(options.file, _signing_key(options.signing_key)))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, run in (("plan", _plan), ("run", _run)):
        command = commands.add_parser(name)
        command.add_argument("--commit", default="refs/heads/main")
        command.add_argument("--release", metavar="vX.Y.Z", help="also announce it in channel.json")
        command.set_defaults(run=run)
    announce = commands.add_parser("announce")
    announce.add_argument("tag", metavar="vX.Y.0")
    announce.set_defaults(run=_announce)
    page = commands.add_parser("release-page")
    page.add_argument("--tag", required=True)
    page.set_defaults(run=_release_page)
    runtime = commands.add_parser("build-runtime-layer")
    runtime.add_argument("--base", type=Path, required=True, help="a base directory, e.g. <runtime>/bases/<id>")
    runtime.add_argument("--base-id", help="its id, when the directory is not named by it")
    runtime.add_argument("--platform", help="<macos|linux|windows>-<arm64|x86_64>; default: this machine")
    runtime.add_argument("--tailnet", type=Path, help="the Tailscale sidecar binary, when the base lacks bin/openprogram-tailnet")
    runtime.set_defaults(run=_build_runtime_layer)
    uploaded = commands.add_parser("upload-runtime-layer")
    uploaded.add_argument("--base", type=Path, required=True)
    uploaded.add_argument("--base-id")
    uploaded.add_argument("--platform", required=True, help="<macos|linux|windows>-<arm64|x86_64>")
    uploaded.add_argument("--tailnet", type=Path)
    uploaded.set_defaults(run=_upload_runtime_layer)
    shell = commands.add_parser("upload-shell-layer")
    shell.add_argument("--app", type=Path, required=True,
                       help="the App: OpenProgram.app (unpacked from the notarized .zip), the Windows install "
                            "folder or the AppImage")
    shell.add_argument("--seed", type=Path,
                       help="the seed built into an AppImage, which says the Python line its launcher is built for")
    shell.add_argument("--platform", required=True, choices=publish.SHELL_PLATFORMS)
    shell.add_argument("--no-upload", action="store_true", help="build and check it only, as a rehearsal does")
    shell.set_defaults(run=_upload_shell_layer)
    gc = commands.add_parser("gc")
    gc.add_argument("--keep", type=int, default=publish.KEEP_POINTERS, help="pointers whose packs are kept")
    gc.add_argument("--dead", type=float, default=0.5, help="repack when more than this share of the live packs is dead")
    gc.set_defaults(run=_gc)
    key = commands.add_parser("public-key")
    key.add_argument("--signing-key")
    key.set_defaults(run=_public_key)
    generate = commands.add_parser("generate-key")
    generate.add_argument("path", type=Path)
    generate.set_defaults(run=_generate_key)
    sign = commands.add_parser("sign-file")
    sign.add_argument("file", type=Path)
    sign.add_argument("--signing-key")
    sign.set_defaults(run=_sign_file)
    options = parser.parse_args()
    try:
        return options.run(options)
    except publish.Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except (Failed, ReleaseError, builder.BuildError, store.SealError, OSError) as exc:
        print(f"updates failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
