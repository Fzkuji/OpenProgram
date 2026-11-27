"""Official updates end to end: a publisher writes packs and pointers, an installation takes them.

The official line is a real Git repository; versions are really built, with
the build steps stubbed as the version build tests stub them. The published
files are served by a local HTTP server that honours Range, so the bytes an
update downloads can be counted. The trial and the switch are the update
controller's and are passed in.
"""
from __future__ import annotations

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import plistlib
from pathlib import Path
import re
import shutil
import subprocess
import threading

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
import pytest

from openprogram.host._platform import runtime_layout as paths
from openprogram.host._platform.runtime_layout import Layout
from openprogram.operations.self_update.official import assemble, fetch, objects, publish, replay, signing
from openprogram.operations.self_update.official import channel as channels
from openprogram.operations.self_update.official import shell as shells
from openprogram.operations.self_update.official.replay import Lifecycle, Verdict
from openprogram.operations.self_update.official.repository import ReleaseError
from openprogram.operations.self_update.versions import build as builder
from openprogram.operations.self_update.versions import pointer as pointers
from openprogram.operations.self_update.versions import store
from tests.component.self_update.versions.test_build import (  # noqa: F401
    commit_change, git, log, repo,
)

pytestmark = pytest.mark.skipif(os.name == "nt", reason="build stubs are POSIX shell")
PLATFORM = "macos-arm64"
RUNTIME_PLATFORMS = publish.RUNTIME_PLATFORMS
PEM = Ed25519PrivateKey.generate().private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
TRUSTED = [signing.public_key_hex(PEM)]


class RangeHandler(SimpleHTTPRequestHandler):
    """A static server that answers ``Range: bytes=a-b`` with 206, and counts what it sends."""

    sent: list[int]

    def log_message(self, *_args) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        path = Path(self.translate_path(self.path))
        if not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        wanted = re.fullmatch(r"bytes=(\d+)-(\d+)", self.headers.get("Range", ""))
        if wanted:
            start, end = int(wanted[1]), int(wanted[2]) + 1
            body = data[start:end]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end - 1}/{len(data)}")
        else:
            body = data
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.sent.append(len(body))


@pytest.fixture
def server(tmp_path):
    root = tmp_path / "server"
    root.mkdir()
    handler = type("Handler", (RangeHandler,), {"sent": []})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(handler, directory=str(root)))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield root, f"http://127.0.0.1:{httpd.server_port}", handler.sent
    httpd.shutdown()


class World:
    """The official line, a publisher, one installation on the line, and the owner's own commits."""

    def __init__(self, tmp_path: Path, source: Path, server) -> None:
        self.tmp = tmp_path
        self.source = source
        self.served, self.url, self.sent = server
        commit_change(source, "apps/desktop/shell/compat.json", '{"shell_compat": 1, "min": 1, "max": 1}\n',
                      "declare the shell")
        self.first = git(source, "rev-parse", "HEAD")
        self.ci = Layout(tmp_path / "ci" / "runtime")
        self.ci.root.mkdir(parents=True)
        self.state = tmp_path / "publisher"
        self.layout = Layout(tmp_path / "state" / "runtime")
        self.layout.root.mkdir(parents=True)
        repository = self.layout.repo
        subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=local", str(repository)], check=True)
        git(repository, "fetch", "-q", str(source), f"{self.first}:refs/heads/local")
        git(repository, "update-ref", "refs/heads/release", self.first)
        self.tree = tmp_path / "owner-tree"
        git(repository, "worktree", "add", "-q", str(self.tree), "local")
        self.trials: list[str] = []
        self.activated: list[str] = []
        self.fail_trial = lambda version: False
        self.build_calls: list[builder.BuildRequest] = []
        self.real_build = builder.build_version
        self.base = ""

    # The publisher.

    def publish(self, files=None, *, release=None, message="publish", runtime=True,
                shell_platforms=None) -> publish.Publication:
        for name, content in (files or {"openprogram/module.py": "number = 2\n"}).items():
            commit_change(self.source, name, content, message)
        head = git(self.source, "rev-parse", "HEAD")
        built = self.real_build(self.ci, builder.BuildRequest(f"ci-{head[:8]}", head, worktree=self.source))
        self.base = built.base
        frontend = paths.frontend_bundle(paths.site_packages(self.ci.version(built.version), built.base))
        live = self.live()
        if runtime:
            self.runtime_layers(self.fake_base(), built.base, live)
        decision = publish.decide(self.source, head, live, self.state, release=release, shell_platforms=shell_platforms,
                                  transport=channels.DirectoryTransport(self.served))
        return publish.compose(self.source, head, frontend, live, decision, self.served, key_pem=PEM,
                               release=release, notes="Notes." if release else "", summary=message, level=3,
                               trusted_keys=TRUSTED)

    def fake_base(self) -> Path:
        """A small base a runtime layer is made from (the build stubs' own base links outside itself)."""
        base = self.tmp / "fake-base"
        if not base.is_dir():
            (base / "python/bin").mkdir(parents=True)
            (base / "python/bin/python3").write_text("#!/bin/sh\n")
            (base / "python/bin/python3").chmod(0o755)
            (base / "bin").mkdir()
            (base / "bin/python").symlink_to("../python/bin/python3")
            (base / "lib.py").write_text("x = 1\n" * 1000)
        return base

    def runtime_layers(self, directory: Path, base: str, live: publish.Live, *, tailnet=None,
                       platforms=None) -> None:
        """What the Release workflow uploads: a runtime layer per platform for ``base``, unless one is live."""
        for platform in platforms or RUNTIME_PLATFORMS:
            name = f"runtime-{platform}"
            if name in live.layers and live.layers[name][1].meta.get("base") == base:
                continue
            publish.build_runtime_layer(directory, platform=platform, packs=self.served, state=self.state,
                                        live=live, base_name=base, tailnet=tailnet, level=3)

    def live(self) -> publish.Live:
        return publish.read_live(channels.DirectoryTransport(self.served), trusted_keys=TRUSTED)

    def give_base(self) -> None:
        if not self.layout.base(self.base).exists():
            self.layout.bases.mkdir(exist_ok=True)
            shutil.copytree(self.ci.base(self.base), self.layout.base(self.base), symlinks=True)

    # The installation.

    def trial(self, version):
        self.trials.append(version)
        return Verdict(False, "the trial failed") if self.fail_trial(version) else Verdict(True)

    def activate(self, version):
        self.activated.append(version)
        manifest = store.read_manifest(self.layout, version)
        git(self.layout.repo, "update-ref", "refs/heads/local", manifest["commit"])
        self.layout.pointer.write_text(pointers.render(pointers.Pointer(1, version, manifest["base"])), encoding="ascii")
        return Verdict(True)

    def build(self, layout, request):
        self.build_calls.append(request)
        return self.real_build(layout, request)

    def lifecycle(self, **changes):
        return Lifecycle(**{"trial": self.trial, "activate": self.activate, "build": self.build, **changes})

    def transport(self) -> channels.HttpTransport:
        return channels.HttpTransport(self.url)

    def run(self, *, transport=None, lifecycle=None, platform=PLATFORM, **options) -> replay.ReplayResult:
        lifecycle = lifecycle or self.lifecycle()
        self.transport_used = transport or self.transport()
        return fetch.update(
            layout=self.layout, transport=self.transport_used, platform=platform, trusted_keys=TRUSTED,
            apply=lambda plan, _marks: replay.replay_update(self.layout, plan, lifecycle, update_id="u1", parent=None),
            **options,
        )

    def site_file(self, version, name):
        manifest = store.read_manifest(self.layout, version)
        return paths.site_packages(self.layout.version(version), manifest["base"]) / "openprogram" / name

    def ref(self, name):
        return git(self.layout.repo, "rev-parse", name)


@pytest.fixture
def world(tmp_path, repo, log, server, monkeypatch):  # noqa: F811
    monkeypatch.setenv(channels.POINTER_VARIABLE, "latest")
    return World(tmp_path, repo, server)


def test_an_update_is_built_here_from_its_source_and_prebuilt_frontend(world, log):
    world.publish()
    world.give_base()
    head = git(world.source, "rev-parse", "HEAD")
    log.write_text("")
    result = world.run()

    assert result.outcome == replay.ACTIVATED, result.message
    [version] = world.activated
    manifest = store.read_manifest(world.layout, version)
    assert manifest["official"]["commit"] == head and manifest["official"]["sequence"] == 1
    assert manifest["built_by"] == f"official 9.9.9-{head[:12]}"
    assert "stage" not in log.read_text(), "the frontend comes from the code layer, not a build"
    assert "number = 2" in world.site_file(version, "module.py").read_text()
    assert (world.site_file(version, "interfaces/server/_frontend/index.html").read_text()
            == f"built from {head}\n")
    release = world.ref("refs/heads/release")
    assert git(world.layout.repo, "rev-parse", f"{release}^{{tree}}") == git(world.source, "rev-parse", "HEAD^{tree}")
    assert git(world.layout.repo, "rev-parse", f"{release}^") == world.first
    assert world.ref(f"refs/openprogram/official/{head}") == release
    assert store.changed_files(world.layout.version(version), manifest["base"]) == []


def test_a_one_line_change_downloads_only_that_file(world):
    world.publish()
    world.give_base()
    assert world.run().outcome == replay.ACTIVATED

    publication = world.publish({"openprogram/module.py": "number = 3\n"})
    assert publication.new_packs, "a change makes one new pack"
    transport = world.transport()
    asked = []
    spans = transport.spans
    transport.spans = lambda pack, wanted, directory: asked.append(pack) or spans(pack, wanted, directory)
    result = world.run(transport=transport)
    assert result.outcome == replay.ACTIVATED, result.message
    second = world.transport_used.fetched_bytes
    pack = publication.new_packs[0]
    layer = world.live().layers["code"][1]
    new_objects = [where for where in layer.objects.values() if where.pack == pack]
    pointer = len((world.served / channels.LATEST).read_bytes())
    # The pointer, the changed trees, the changed file and the one frontend file
    # that names the commit; unchanged directories' trees, the rest of the
    # frontend the running version has, and every unchanged source are not fetched.
    changed = {entry.path for entry in layer.files() if layer.objects[entry.sha256].pack == pack}
    assert changed == {"source/openprogram/module.py", "frontend/index.html"}
    assert len(new_objects) == 2
    assert set(asked) == {pack}  # Nothing from the first pack.
    # One request per tree level, each within the new pack.
    assert second <= pointer + len(asked) * (world.served / pack).stat().st_size
    assert "number = 3" in world.site_file(world.activated[-1], "module.py").read_text()


def test_own_commits_are_replayed_and_a_conflict_is_saved(world):
    clash = world.own_commit("module.py", "number = 'mine'\n", "clashes")
    fine = world.own_commit("own_b.py", "from openprogram.module import number\n", "applies")
    world.publish()
    world.give_base()
    result = world.run()

    assert result.outcome == replay.ACTIVATED, result.message
    assert [item.sha for item in result.replayed] == [fine]
    assert [(item.sha, item.reason) for item in result.saved] == [(clash, replay.CONFLICT)]
    name = result.release
    assert (world.layout.root / "unreplayable" / name / f"{clash}.patch").is_file()
    version = world.activated[0]
    assert world.site_file(version, "own_b.py").is_file()
    assert "number = 2" in world.site_file(version, "module.py").read_text()
    tip = world.ref("refs/heads/local")
    assert git(world.layout.repo, "merge-base", "--is-ancestor", world.ref("refs/heads/release"), tip) == ""


def test_a_failing_trial_drops_the_newest_commit(world):
    oldest = world.own_commit("own_a.py", "a = 1\n", "a")
    newest = world.own_commit("own_c.py", "c = 1\n", "c")
    world.publish()
    world.give_base()
    world.fail_trial = lambda version: world.site_file(version, "own_c.py").exists()
    result = world.run()

    assert result.outcome == replay.ACTIVATED, result.message
    assert [item.sha for item in result.replayed] == [oldest]
    assert [(item.sha, item.reason) for item in result.saved] == [(newest, replay.FAILED_TRIAL)]


def test_an_installation_already_on_the_update_is_up_to_date_and_downloads_no_objects(world):
    world.publish()
    world.give_base()
    assert world.run().outcome == replay.ACTIVATED
    trials = len(world.trials)
    again = world.run()
    assert again.outcome == replay.UP_TO_DATE and len(world.trials) == trials
    status = fetch.check(layout=world.layout, transport=world.transport(), platform=PLATFORM, trusted_keys=TRUSTED)
    assert status["update_available"] is False and status["announce"] is False and status["channel"] == "daily"


def test_a_failed_trial_is_retried_from_what_is_already_here(world):
    world.publish()
    world.give_base()
    world.fail_trial = lambda version: True
    assert world.run().outcome == replay.ABORTED
    world.fail_trial = lambda version: False
    retry = world.run()
    assert retry.outcome == replay.ACTIVATED, retry.message
    assert world.transport_used.fetched_bytes < 4096, "the objects were kept from the first attempt"


def test_an_update_for_a_newer_shell_waits_and_changes_nothing(world):
    world.publish({"apps/desktop/shell/compat.json": '{"shell_compat": 9, "min": 9, "max": 9}\n'})
    world.give_base()
    identity = world.layout.root / "identity"
    identity.mkdir()
    (identity / "shell-compat.json").write_text('{"min": 1, "max": 2}\n')
    before = world.ref("refs/heads/release")
    result = world.run()
    assert result.outcome == replay.WAITING_FOR_SHELL and "shell" in result.message
    assert world.ref("refs/heads/release") == before and world.trials == []


def fake_app(root: Path, version: str, executable: str = "the shell") -> Path:
    """An App bundle as a signed one is laid out: a framework of version links, modes, an empty folder."""
    app = root / "OpenProgram.app"
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Info.plist").write_bytes(plistlib.dumps(
        {"CFBundleExecutable": "OpenProgram", "CFBundleShortVersionString": version}))
    (contents / "MacOS" / "OpenProgram").write_text(executable)
    (contents / "MacOS" / "OpenProgram").chmod(0o755)
    framework = contents / "Frameworks" / "Electron Framework.framework"
    (framework / "Versions" / "A" / "Resources").mkdir(parents=True)
    (framework / "Versions" / "A" / "Electron Framework").write_bytes(b"\xcf\xfa\xed\xfe" + b"x" * 50_000)
    (framework / "Versions" / "A" / "Electron Framework").chmod(0o755)
    (framework / "Versions" / "A" / "Resources" / "Info.plist").write_text("framework\n")
    (framework / "Versions" / "Current").symlink_to("A")
    (framework / "Electron Framework").symlink_to("Versions/Current/Electron Framework")
    (framework / "Resources").symlink_to("Versions/Current/Resources")
    (contents / "Resources" / "empty").mkdir(parents=True)
    (contents / "_CodeSignature").mkdir()
    (contents / "_CodeSignature" / "CodeResources").write_text(f"signed {version}\n")
    return app


SHELL_COMPAT = {"shell_compat": 2, "min": 1, "max": 2}


def fake_appimage(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x7fELF" + text.encode() + b"\0" * 20_000)
    path.chmod(0o755)
    return path


def upload_shell_layers(world, app: Path, version: str) -> None:
    """What the Release workflow's desktop legs upload: a shell layer and its record per platform."""
    windows = app.parent / "win-unpacked"
    (windows / "resources").mkdir(parents=True, exist_ok=True)
    (windows / "OpenProgram.exe").write_text(f"MZ {version}")
    (windows / "resources" / "app.asar").write_text("shell")
    image = fake_appimage(app.parent / f"OpenProgram-{version}-linux.AppImage", f"AppImage {version}")
    for platform in publish.SHELL_PLATFORMS:
        source = {"windows": windows, "linux": image}.get(platform.split("-")[0], app)
        built = publish.build_shell_layer(source, platform=platform, version=version, compat=SHELL_COMPAT,
                                          packs=world.served, live=world.live(), level=3)
        (world.served / built.record).write_text(built.encode())


def feature_release(world, number: int = 5, **options):
    files = {"pyproject.toml": (world.source / "pyproject.toml").read_text().replace("9.9.9", "9.9.0"),
             "openprogram/module.py": f"number = {number}\n",
             "apps/desktop/shell/compat.json": json.dumps(SHELL_COMPAT) + "\n"}
    changed = {name: text for name, text in files.items()
               if not (world.source / name).is_file() or (world.source / name).read_text() != text}
    return world.publish(changed, release="v9.9.0", **options)


def test_a_feature_release_is_refused_without_every_shell_layer(world, tmp_path):
    with pytest.raises(publish.Refused, match="has no shell layer of 9.9.0"):
        feature_release(world)
    app = fake_app(tmp_path / "built", "9.9.0")
    built = publish.build_shell_layer(app, platform=PLATFORM, version="9.9.0", compat=SHELL_COMPAT,
                                      packs=world.served, live=world.live(), level=3)
    (world.served / built.record).write_text(built.encode())
    with pytest.raises(publish.Refused,
                       match=r"macos-x86_64, windows-x86_64, linux-x86_64, linux-arm64 has no shell layer"):
        feature_release(world)
    with pytest.raises(ReleaseError, match="not 9.9.1"):
        publish.build_shell_layer(app, platform=PLATFORM, version="9.9.1", compat=SHELL_COMPAT,
                                  packs=world.served, live=world.live(), level=3)
    with pytest.raises(ReleaseError, match="keeps its installer"):
        publish.build_shell_layer(app, platform="windows-arm64", version="9.9.0", compat=SHELL_COMPAT,
                                  packs=world.served, live=world.live(), level=3)
    seeded = publish.build_shell_layer(fake_appimage(tmp_path / "x.AppImage", "x"), platform="linux-arm64",
                                       version="9.9.0", compat=SHELL_COMPAT, packs=world.served, live=world.live(),
                                       level=3, python_minor="3.12")
    assert seeded.name == "shell-linux-arm64"
    with pytest.raises(ReleaseError, match="not an executable AppImage"):
        publish.build_shell_layer(app, platform="linux-x86_64", version="9.9.0", compat=SHELL_COMPAT,
                                  packs=world.served, live=world.live(), level=3)


def test_a_feature_release_needs_shell_layers_only_for_the_apps_it_published(world, tmp_path):
    built = publish.build_shell_layer(fake_app(tmp_path / "built", "9.9.0"), platform=PLATFORM, version="9.9.0",
                                      compat=SHELL_COMPAT, packs=world.served, live=world.live(), level=3)
    (world.served / built.record).write_text(built.encode())
    feature_release(world, shell_platforms={PLATFORM})
    layers = channels.read(world.transport(), name=channels.LATEST, trusted_keys=TRUSTED).layers
    assert {name for name in layers if name.startswith("shell-")} == {f"shell-{PLATFORM}"}, \
        "the other platforms keep their installers"


def test_layers_in_another_format_are_written_again_or_refused(world, monkeypatch):
    world.publish()
    before = world.live().layers["code"][1]
    monkeypatch.setattr(objects, "SCHEMA", objects.SCHEMA + 1)
    live = world.live()
    assert not live.layers and live.other_schema == {"code", *(f"runtime-{name}" for name in RUNTIME_PLATFORMS)}
    assert publish.dead_fraction(live) == 0.0, "nothing to repack: the next publish replaces them"
    with pytest.raises(ReleaseError, match="older layer format; publish first"):
        publish.repack(world.transport(), live, world.tmp / "repack", key_pem=PEM)
    shutil.rmtree(world.state / "runtime")
    with pytest.raises(publish.Refused, match="older layer format.*runtime_layers_only=true"):
        world.publish({"openprogram/module.py": "number = 4\n"}, runtime=False)
    world.publish({"openprogram/module.py": "number = 5\n"})
    code = world.live().layers["code"][1]
    assert not code.packs() & before.packs(), "the code layer is written whole again, nothing read from the old one"
    world.give_base()
    assert world.run().outcome == replay.ACTIVATED


def test_a_new_shell_is_assembled_beside_the_app_from_its_unchanged_files(world, tmp_path, monkeypatch):
    built = fake_app(tmp_path / "built", "9.9.0", "the new shell")
    upload_shell_layers(world, built, "9.9.0")
    feature_release(world)
    published = channels.read(world.transport(), name=channels.LATEST, trusted_keys=TRUSTED)
    assert set(publish.SHELL_PLATFORMS) <= {name.removeprefix("shell-") for name in published.layers}
    pointer = json.loads(published.data)
    assert {pointer["layers"][f"shell-{platform}"]["version"] for platform in publish.SHELL_PLATFORMS} == {"9.9.0"}, \
        "an App sees a newer shell from the pointer alone"
    image = world.live().layers["shell-linux-x86_64"][1]
    assert [entry.path for entry in image.entries] == [shells.APPIMAGE] and image.meta["executable"] == shells.APPIMAGE
    layer = world.live().layers[f"shell-{PLATFORM}"][1]
    assert layer.meta["directories"] == ["Contents/Resources/empty"]
    links = {entry.path: entry.symlink for entry in layer.entries if entry.symlink}
    assert links["Contents/Frameworks/Electron Framework.framework/Versions/Current"] == "A"

    # The running App is 9.8.10 and its shell hosts only shell_compat 1, so the
    # update would wait for a shell; its shell layer brings one.
    running = fake_app(tmp_path / "Applications", "9.8.10", "old shell")
    monkeypatch.setenv("OPENPROGRAM_APP_PATH", str(running / "Contents" / "MacOS"))
    monkeypatch.setenv("OPENPROGRAM_SHELL_VERSION", "9.8.10")
    monkeypatch.setattr(shells, "mac_team", lambda bundle: "TEAM1234")
    checked = []
    monkeypatch.setattr(shells, "verify", lambda staged, app, meta: checked.append((staged.name, app.root, meta["version"])))
    identity = world.layout.root / "identity"
    identity.mkdir()
    (identity / "shell-compat.json").write_text('{"min": 1, "max": 1}\n')
    world.give_base()

    update = assemble.plan(world.layout, published, world.transport(), platform=PLATFORM)
    assert update.state == assemble.AVAILABLE, update.detail
    assert update.shell is not None and update.shell_app.root == running
    framework = running / "Contents/Frameworks/Electron Framework.framework/Versions/A/Electron Framework"
    assert framework in update.shell_local.values(), "an unchanged file is taken from the running App"
    shell_missing = {entry.path for entry in update.shell.files() if entry.sha256 in update.missing}
    assert shell_missing == {"Contents/Info.plist", "Contents/MacOS/OpenProgram",
                             "Contents/_CodeSignature/CodeResources"}

    result = world.run()
    assert result.outcome == fetch.SHELL_READY and not world.build_calls, \
        "a plain upgrade stops at the staged App: code that needs it runs only after the swap"
    staged = running.parent / ".OpenProgram.app.update" / "staged" / "OpenProgram.app"
    assert staged.is_dir(), "staged beside the App, on its drive, so the swap is a rename"
    assert checked == [("OpenProgram.app", running, "9.9.0")], "checked before it is recorded"
    record = shells.read_staged(world.layout)
    assert record["path"] == str(staged) and record["app"] == str(running) and record["version"] == "9.9.0"
    assert record["executable"] == "Contents/MacOS/OpenProgram"
    assert (staged / "Contents/MacOS/OpenProgram").read_text() == "the new shell"
    assert os.stat(staged / "Contents/MacOS/OpenProgram").st_mode & 0o111
    assert os.readlink(staged / "Contents/Frameworks/Electron Framework.framework/Electron Framework") \
        == "Versions/Current/Electron Framework"
    assert (staged / "Contents/Resources/empty").is_dir()
    assert framework.read_bytes() == (staged / framework.relative_to(running)).read_bytes()
    assert (running / "Contents/MacOS/OpenProgram").read_text() == "old shell", "the running App is untouched"
    # Prepared for Restart to update (upgrade --prepare), the staged App hosts the code it was staged for.
    assert world.run(hold=True).outcome == replay.ACTIVATED
    kept = {item.parent.name + item.name for item in (world.layout.root / "objects").rglob("*") if item.is_file()}
    assert not kept & update.shell.digests(), "the staged App's files are not kept twice"
    assert update.shell.tree_digests() <= kept, "its trees are, for the next check"

    # Not swapped yet (the App was not restarted): the code is here, the staged
    # shell still waits, and a later prepare reports it ready again.
    again = assemble.plan(world.layout, published, world.transport(), platform=PLATFORM)
    assert again.state == assemble.AVAILABLE and again.shell_only and again.shell_staged and again.shell is None
    assert world.run().outcome == fetch.SHELL_READY

    # Swapped in and restarted: the staged record is dropped at the next check.
    staged.rename(running.parent / "swapped.app")
    monkeypatch.setenv("OPENPROGRAM_SHELL_VERSION", "9.9.0")
    done = assemble.plan(world.layout, published, world.transport(), platform=PLATFORM)
    assert shells.read_staged(world.layout) is None and done.state == assemble.UP_TO_DATE


def test_a_newer_shell_is_taken_when_the_code_is_here_already(world, tmp_path, monkeypatch):
    upload_shell_layers(world, fake_app(tmp_path / "built", "9.9.0", "the new shell"), "9.9.0")
    feature_release(world)
    world.give_base()
    assert world.run().outcome == replay.ACTIVATED, "taken from the command line: no App, no shell"

    running = fake_app(tmp_path / "Applications", "9.8.10", "old shell")
    monkeypatch.setenv("OPENPROGRAM_APP_PATH", str(running / "Contents" / "MacOS"))
    monkeypatch.setenv("OPENPROGRAM_SHELL_VERSION", "9.8.10")
    monkeypatch.setattr(shells, "mac_team", lambda bundle: "TEAM1234")
    monkeypatch.setattr(shells, "verify", lambda staged, app, meta: None)
    published = channels.read(world.transport(), name=channels.LATEST, trusted_keys=TRUSTED)
    update = assemble.plan(world.layout, published, world.transport(), platform=PLATFORM)
    assert update.state == assemble.AVAILABLE and update.shell_only and update.shell is not None, \
        "the shell layer is compared by itself, not by the code"
    before = world.ref("refs/heads/release")
    result = world.run()
    assert result.outcome == fetch.SHELL_READY and "restart" in result.message
    assert world.ref("refs/heads/release") == before
    assert shells.read_staged(world.layout)["version"] == "9.9.0"


def test_an_appimage_is_assembled_beside_itself(world, tmp_path, monkeypatch):
    upload_shell_layers(world, fake_app(tmp_path / "built", "9.9.0", "the new shell"), "9.9.0")
    feature_release(world)
    world.give_base()
    image = fake_appimage(tmp_path / "Apps" / "OpenProgram.AppImage", "AppImage 9.9.1")  # Same size, other bytes.
    monkeypatch.setenv("OPENPROGRAM_APP_PATH", "/tmp/.mount_abc/usr/bin")
    monkeypatch.setenv("OPENPROGRAM_SHELL_VERSION", "9.8.10")
    monkeypatch.setenv("APPIMAGE", str(image))
    identity = world.layout.root / "identity"
    identity.mkdir()
    (identity / "shell-compat.json").write_text('{"min": 1, "max": 1}\n')
    published = channels.read(world.transport(), name=channels.LATEST, trusted_keys=TRUSTED)
    update = assemble.plan(world.layout, published, world.transport(), platform="linux-x86_64")
    assert list(update.shell_local.values()) == [image], "the running AppImage is the layer's one file"
    assert update.needs_swap, "the installed shell hosts only shell_compat 1"
    result = world.run(platform="linux-x86_64")
    assert result.outcome == fetch.SHELL_READY and not world.build_calls, "the code waits for the new AppImage"
    assert world.run(platform="linux-x86_64", hold=True).outcome == replay.ACTIVATED, \
        "prepared for Restart to update, it is built for the staged AppImage"
    staged = tmp_path / "Apps" / ".OpenProgram.AppImage.update" / "staged" / "OpenProgram.AppImage"
    assert staged.read_bytes().startswith(b"\x7fELFAppImage 9.9.0") and os.access(staged, os.X_OK)
    record = shells.read_staged(world.layout)
    assert record["path"] == str(staged) and record["app"] == str(image) and record["executable"] == ""
    assert image.read_bytes().startswith(b"\x7fELFAppImage 9.9.1"), "the running AppImage is untouched"
    shells.forget(world.layout)
    assert not (tmp_path / "Apps" / ".OpenProgram.AppImage.update").exists()


def test_a_re_signed_binary_downloads_only_its_changed_pieces(world, tmp_path, monkeypatch):
    body = os.urandom(5 * objects.CHUNK_SIZE)

    def signed(app: Path, header: bytes, signature: bytes) -> Path:
        binary = app / "Contents/Frameworks/Electron Framework.framework/Versions/A/Electron Framework"
        binary.write_bytes(header + body[len(header):] + signature)
        return binary

    built = fake_app(tmp_path / "built", "9.9.0", "the new shell")
    expected = signed(built, b"\xcf\xfa\xed\xfe new", b"signature 9.9.0" * 300).read_bytes()
    upload_shell_layers(world, built, "9.9.0")
    feature_release(world)
    running = fake_app(tmp_path / "Applications", "9.8.10", "old shell")
    binary = signed(running, b"\xcf\xfa\xed\xfe old", b"signature 9.8.10" * 100)
    monkeypatch.setenv("OPENPROGRAM_APP_PATH", str(running / "Contents" / "MacOS"))
    monkeypatch.setenv("OPENPROGRAM_SHELL_VERSION", "9.8.10")
    monkeypatch.setattr(shells, "mac_team", lambda bundle: "TEAM1234")
    monkeypatch.setattr(shells, "verify", lambda staged, app, meta: None)
    world.give_base()
    published = channels.read(world.transport(), name=channels.LATEST, trusted_keys=TRUSTED)
    update = assemble.plan(world.layout, published, world.transport(), platform=PLATFORM)
    entry = next(entry for entry in update.shell.files() if entry.path.endswith("A/Electron Framework"))
    assert len(entry.chunks) == 6 and update.local[entry.sha256] == binary, "its earlier version, at another size"
    fetched = []
    real = assemble._fetch_objects
    monkeypatch.setattr(assemble, "_fetch_objects",
                        lambda layout, transport, wanted, **kw: (fetched.extend(wanted), real(layout, transport, wanted, **kw)))
    assemble.download(world.layout, update, world.transport())
    pieces = [digest for digest, _size in entry.chunks]
    assert set(fetched) & set(pieces) == {pieces[0], pieces[-1]}, "the header and the signature's pieces only"
    store = assemble.object_store(world.layout)
    assert not any(store.has(digest) for digest in pieces), "pieces go once the file is joined"
    staged = assemble.stage_shell(world.layout, update, world.transport())
    assert (staged / binary.relative_to(running)).read_bytes() == expected


def test_a_running_file_that_changed_after_the_plan_is_fetched(world, tmp_path, monkeypatch):
    upload_shell_layers(world, fake_app(tmp_path / "built", "9.9.0", "the new shell"), "9.9.0")
    feature_release(world)
    running = fake_app(tmp_path / "Applications", "9.8.10", "old shell")
    monkeypatch.setenv("OPENPROGRAM_APP_PATH", str(running / "Contents" / "MacOS"))
    monkeypatch.setenv("OPENPROGRAM_SHELL_VERSION", "9.8.10")
    monkeypatch.setattr(shells, "mac_team", lambda bundle: "TEAM1234")
    monkeypatch.setattr(shells, "verify", lambda staged, app, meta: None)
    world.give_base()
    published = channels.read(world.transport(), name=channels.LATEST, trusted_keys=TRUSTED)
    update = assemble.plan(world.layout, published, world.transport(), platform=PLATFORM)
    framework = running / "Contents/Frameworks/Electron Framework.framework/Versions/A/Electron Framework"
    expected = framework.read_bytes()
    assemble.download(world.layout, update, world.transport())
    framework.write_bytes(b"\xcf\xfa\xed\xfe" + b"y" * 50_000)  # Same size, other bytes.
    staged = assemble.stage_shell(world.layout, update, world.transport())
    assert (staged / framework.relative_to(running)).read_bytes() == expected

    # A failed check leaves nothing staged.
    shells.forget(world.layout)
    monkeypatch.setattr(shells, "verify", lambda staged, app, meta: (_ for _ in ()).throw(ReleaseError("bad signature")))
    with pytest.raises(ReleaseError, match="bad signature"):
        assemble.stage_shell(world.layout, update, world.transport())
    assert shells.read_staged(world.layout) is None
    assert not list(shells.staging_root(update.shell_app).glob(".staging-*"))


def test_a_new_base_without_this_platforms_runtime_waits(world):
    world.publish()  # This installation has no base, and its platform is not one updates are published for.
    result = world.run(platform="windows-arm64")
    assert result.outcome == replay.WAITING_FOR_RUNTIME and "windows-arm64" in result.message
    assert world.trials == []


def test_a_new_base_comes_from_this_platforms_runtime_layer(world):
    world.publish()  # This installation has no base: it is assembled from the runtime layer.
    published = channels.read(world.transport(), trusted_keys=TRUSTED)
    update = assemble.plan(world.layout, published, world.transport(), platform=PLATFORM)
    assert update.state == assemble.AVAILABLE and update.runtime.meta == {"base": world.base, "platform": PLATFORM}
    assemble.download(world.layout, update, world.transport())
    assert assemble.assemble_base(world.layout, update)
    assert os.readlink(world.layout.base(world.base) / "bin/python") == "../python/bin/python3"


def test_nothing_is_published_without_every_platforms_runtime_layer(world):
    head_files = {"openprogram/module.py": "number = 2\n"}
    with pytest.raises(publish.Refused, match="has no runtime layer for macos-arm64.*windows-x86_64"):
        world.publish(head_files, runtime=False)
    assert not (world.served / channels.LATEST).exists()


def test_an_older_pointer_than_one_seen_is_refused(world):
    world.publish()
    older = {channels.LATEST: (world.served / channels.LATEST).read_bytes()}
    world.give_base()
    world.publish({"openprogram/module.py": "number = 3\n"})
    assert world.run().outcome == replay.ACTIVATED
    for name, data in older.items():
        (world.served / name).write_bytes(data)
    with pytest.raises(ReleaseError, match="older than 2"):
        world.run()


def test_a_damaged_pack_or_pointer_changes_nothing(world):
    world.publish()
    world.give_base()
    layer = world.live().layers["code"][1]
    [entry] = [entry for entry in layer.files() if entry.path == "source/openprogram/module.py"]
    where = layer.objects[entry.sha256]
    pack = world.served / where.pack
    data = bytearray(pack.read_bytes())
    data[where.offset + where.length - 2] ^= 0xFF
    pack.write_bytes(bytes(data))
    before = world.ref("refs/heads/release")
    with pytest.raises(ReleaseError):
        world.run()
    assert world.ref("refs/heads/release") == before and world.trials == []
    pointer = world.served / channels.LATEST
    pointer.write_bytes(pointer.read_bytes().replace(b'\\"sequence\\": 1', b'\\"sequence\\": 9'))
    assert b'\\"sequence\\": 9' in pointer.read_bytes()
    with pytest.raises(ReleaseError, match="signature"):
        world.run()


def test_announced_releases_and_quiet_publishes_use_their_own_pointers(world, monkeypatch):
    world.publish()
    monkeypatch.delenv(channels.POINTER_VARIABLE)
    assert channels.read(world.transport(), trusted_keys=TRUSTED) is None  # Nothing announced yet.
    with pytest.raises(publish.Refused, match="does not match the version"):
        world.publish({"openprogram/module.py": "number = 3\n"}, release="v9.9.8")
    world.publish({"openprogram/module.py": "number = 4\n"}, release="v9.9.9")
    announced = channels.read(world.transport(), trusted_keys=TRUSTED)
    latest = channels.read(world.transport(), name=channels.LATEST, trusted_keys=TRUSTED)
    assert announced.announce and announced.notes == "Notes." and announced.sequence == latest.sequence == 2
    world.give_base()
    found = fetch.discover(layout=world.layout, transport=world.transport(), platform=PLATFORM, trusted_keys=TRUSTED)
    assert found.about() == {"channel": "stable", "latest_version": "9.9.9", "announce": True}


def test_a_code_only_release_refuses_a_runtime_or_shell_change(world):
    world.publish(release="v9.9.9")
    with pytest.raises(publish.Refused, match=r"feature release v9\.9\.0 \(shell changed"):
        world.publish({"pyproject.toml": (world.source / "pyproject.toml").read_text().replace("9.9.9", "9.9.10"),
                       "apps/desktop/shell/compat.json": '{"shell_compat": 2, "min": 1, "max": 2}\n'},
                      release="v9.9.10")


def test_changed_runtime_inputs_need_a_runtime_layer_which_installs_reuse(world, tmp_path):
    world.publish()
    old_base = world.base
    head = git(world.source, "rev-parse", "HEAD")
    commit_change(world.source, "uv.lock", "lock two\n", "move a dependency")
    moved = git(world.source, "rev-parse", "HEAD")
    decision = publish.decide(world.source, moved, world.live(), world.state)
    assert decision.runtime_changed and "has no runtime layer for" in decision.refused

    base = tmp_path / "built-base"
    (base / "python/bin").mkdir(parents=True)
    (base / "python/bin/python3").write_text("#!/bin/sh\n")
    (base / "python/bin/python3").chmod(0o755)
    (base / "bin").mkdir()
    (base / "bin/python").symlink_to("../python/bin/python3")
    (base / "lib.py").write_text("x = 1\n" * 1000)
    sidecar = tmp_path / "openprogram-tailnet"
    sidecar.write_text("sidecar\n")
    new_base = publish.base_id(world.source, moved)
    built = publish.build_runtime_layer(base, platform=PLATFORM, packs=world.served, state=world.state,
                                        live=world.live(), base_name=new_base, tailnet=sidecar, level=3)
    assert built.key == new_base and new_base != old_base
    decision = publish.decide(world.source, moved, world.live(), world.state)
    assert "has no runtime layer for macos-x86_64" in decision.refused
    world.runtime_layers(base, new_base, world.live(),
                         platforms=[platform for platform in publish.RUNTIME_PLATFORMS if platform != PLATFORM])
    decision = publish.decide(world.source, moved, world.live(), world.state)
    assert not decision.refused and f"runtime-{PLATFORM}" in decision.runtime
    frontend = paths.frontend_bundle(paths.site_packages(world.ci.version(store.list_versions(world.ci)[0]["id"]), old_base))
    publish.compose(world.source, moved, frontend, world.live(), decision, world.served, key_pem=PEM, level=3,
                    trusted_keys=TRUSTED)
    published = channels.read(world.transport(), trusted_keys=TRUSTED)
    update = assemble.plan(world.layout, published, world.transport(), platform=PLATFORM)
    assert update.state == assemble.AVAILABLE and update.runtime is not None and update.base == new_base
    assemble.download(world.layout, update, world.transport())
    assert assemble.assemble_base(world.layout, update)
    target = world.layout.base(new_base)
    assert os.readlink(target / "bin/python") == "../python/bin/python3"
    assert (target / "bin/openprogram-tailnet").read_text() == "sidecar\n"
    assert os.stat(target / "python/bin/python3").st_mode & 0o111
    assert head != moved


def test_packs_nothing_recent_names_are_dropped_and_a_repack_still_installs(world, tmp_path):
    names = [world.publish({"openprogram/module.py": f"number = {index}\n"}).new_packs[0] for index in range(4)]
    live = world.live()
    assert set(publish.unreferenced(names, [], [live.latest])) == set(names) - set(live.latest.packs)
    assert names[-1] in live.latest.packs and names[1] not in live.latest.packs
    assert 0 < publish.dead_fraction(live) < 1
    out = tmp_path / "repacked"
    repacked = publish.repack(channels.DirectoryTransport(world.served), live, out, key_pem=PEM, level=3,
                              trusted_keys=TRUSTED)
    [pack] = repacked.new_packs
    for name in (pack, channels.LATEST):
        shutil.copy2(out / name, world.served / name)
    for stale in names:
        (world.served / stale).unlink(missing_ok=True)
    world.give_base()
    result = world.run()
    assert result.outcome == replay.ACTIVATED, result.message
    assert "number = 3" in world.site_file(world.activated[-1], "module.py").read_text()


def _own_commit(self, name, content, message):
    path = self.tree / "openprogram" / name
    path.write_text(content)
    git(self.tree, "add", "-A")
    git(self.tree, "commit", "-qm", message)
    return git(self.tree, "rev-parse", "HEAD")


World.own_commit = _own_commit


def test_a_pointer_lost_from_the_release_is_taken_from_history(world):
    world.publish()
    blob = (world.served / channels.LATEST).read_bytes()
    history = [channels.open_pointer(blob, name=channels.LATEST, trusted_keys=TRUSTED)]
    (world.served / channels.LATEST).unlink()  # An upload that failed after its delete.
    assert channels.read(world.transport(), name=channels.LATEST, trusted_keys=TRUSTED) is None
    live = publish.read_live(channels.DirectoryTransport(world.served), trusted_keys=TRUSTED, history=history)
    assert live.repair == [channels.LATEST] and live.latest.sequence == 1 and "code" in live.layers
    assert publish.dead_fraction(live) < 0.5


class WholeFileHandler(RangeHandler):
    """A server that ignores Range and sends the whole file."""

    def do_GET(self) -> None:  # noqa: N802
        del self.headers["Range"]
        super().do_GET()


def test_a_server_that_ignores_the_range_is_not_asked_again(tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    (root / "pack-0123456789ab.pack").write_bytes(b"x" * 1000)
    handler = type("Plain", (WholeFileHandler,), {"sent": []})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(handler, directory=str(root)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        transport = channels.HttpTransport(f"http://127.0.0.1:{httpd.server_port}")
        with pytest.raises(ReleaseError, match="could not be downloaded"):
            transport.spans("pack-0123456789ab.pack", [objects.Span(0, 10, (("a", 0, 10),))], tmp_path)
        assert len(handler.sent) <= 1
    finally:
        httpd.shutdown()


def test_a_pointer_lists_the_older_packs_its_reused_trees_are_in(world):
    first = world.publish()
    world.publish({"openprogram/module.py": "number = 3\n"})
    live = world.live()
    code = live.layers["code"][1]
    assert code.packs() <= set(live.latest.packs)
    older = {where.pack for _digest, where in code.trees.values()} & set(first.new_packs)
    assert older, "an unchanged directory keeps its tree in the first pack"
    assert older <= set(live.latest.packs)
    assert not set(publish.unreferenced([*first.new_packs], [], [live.latest])) & older


def test_a_layer_in_an_unlisted_pack_is_refused_by_the_client(world):
    world.publish()
    world.publish({"openprogram/module.py": "number = 3\n"})
    blob = (world.served / channels.LATEST).read_bytes()
    published = channels.open_pointer(blob, name=channels.LATEST, trusted_keys=TRUSTED)
    code = world.live().layers["code"][1]
    # A pack only a reused tree is in: what a pointer that forgot older packs would leave out.
    [hidden] = code.packs() - {published.layers["code"].where.pack} - {
        ref.where.pack for name, ref in published.layers.items() if name != "code"}
    fields = {key: value for key, value in json.loads(published.data).items() if key not in ("schema", "pointer")}
    fields["packs"] = {name: info for name, info in fields["packs"].items() if name != hidden}
    (world.served / channels.LATEST).write_bytes(channels.seal(channels.encode(fields, name=channels.LATEST), PEM))
    world.give_base()
    with pytest.raises(ReleaseError, match="does not list"):
        world.run()


def test_a_runtime_layer_kept_by_the_announced_pointer_is_read_from_it(world):
    world.publish(release="v9.9.9")
    original = (world.source / "uv.lock").read_text() if (world.source / "uv.lock").exists() else ""
    world.publish({"uv.lock": "lock two\n"})  # latest.json moves to a new base, with its runtime layers.
    live = world.live()
    assert live.layers[f"runtime-{PLATFORM}"][1].meta["base"] != live.announced_layers[f"runtime-{PLATFORM}"][1].meta["base"]
    world.publish({"uv.lock": original}, runtime=False)  # Back on the announced base: its layers are reused.
    published = channels.read(world.transport(), name=channels.LATEST, trusted_keys=TRUSTED)
    announced = live.announced_layers[f"runtime-{PLATFORM}"]
    assert published.layers[f"runtime-{PLATFORM}"] == announced[0]
    assert announced[1].packs() <= set(published.packs)


class BrokenHandler(RangeHandler):
    """A server that fails every request that is not for a missing file."""

    def do_GET(self) -> None:  # noqa: N802
        path = Path(self.translate_path(self.path))
        self.send_error(500 if path.is_file() else 404)


def test_only_a_404_reads_as_missing(tmp_path):
    root = tmp_path / "broken"
    root.mkdir()
    (root / channels.LATEST).write_bytes(b"{}")
    handler = type("Broken", (BrokenHandler,), {"sent": []})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(handler, directory=str(root)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        transport = channels.HttpTransport(f"http://127.0.0.1:{httpd.server_port}")
        assert transport.small(channels.ANNOUNCED) is None
        with pytest.raises(channels.Unreachable):
            transport.small(channels.LATEST)
        with pytest.raises(channels.Unreachable):
            publish.read_live(transport, trusted_keys=TRUSTED)
    finally:
        httpd.shutdown()
