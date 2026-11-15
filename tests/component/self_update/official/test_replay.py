"""Official updates against a local directory as the release source.

The builds are real, with the build steps stubbed the same way the version
build tests stub them; the trial and the switch are the update controller's and
are passed in.
"""
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
from unittest import mock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
import pytest

from openprogram.operations.self_update.official import assets, replay
from openprogram.operations.self_update.official.replay import Lifecycle, Verdict
from openprogram.operations.self_update.versions import build as builder
from openprogram.operations.self_update.versions import layout as paths
from openprogram.operations.self_update.versions import store
from openprogram.operations.self_update.versions.layout import Layout
from tests.component.self_update.versions.test_build import (  # noqa: F401
    commit_change, git, log, repo,
)
from tests.support.sealed_versions import BASE, REPOSITORY, make_layout, make_version

pytestmark = pytest.mark.skipif(os.name == "nt", reason="build stubs are POSIX shell")
PLATFORM = "macos-arm64"
KEY = Ed25519PrivateKey.generate()
PEM = KEY.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
TRUSTED = [assets.public_key_hex(PEM)]


class World:
    """The official line, one installation on it, and the owner's own commits."""

    def __init__(self, tmp_path, source):
        self.tmp = tmp_path
        self.source = source
        self.first = git(source, "rev-parse", "HEAD")
        self.layout = Layout(tmp_path / "state" / "runtime")
        self.ci = Layout(tmp_path / "ci" / "runtime")
        self.ci.root.mkdir(parents=True)
        self.layout.root.mkdir(parents=True)
        repository = self.layout.repo
        subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=local", str(repository)], check=True)
        git(repository, "fetch", "-q", str(source), f"{self.first}:refs/heads/local")
        git(repository, "update-ref", "refs/heads/release", self.first)
        self.tree = tmp_path / "owner-tree"
        git(repository, "worktree", "add", "-q", str(self.tree), "local")
        self.trials = []
        self.activated = []
        self.fail_trial = lambda version: False
        self.build_calls = []
        self.real_build = builder.build_version

    def own(self, name, content="owned\n", message=None):
        path = self.tree / "openprogram" / name
        path.write_text(content)
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "-qm", message or f"own {name}")
        return git(self.tree, "rev-parse", "HEAD")

    def publish(self, release="0.2.0", files=None, *, base_asset=False):
        """Build a new official commit and write its release directory.

        This fixture's base links to the host's Python, which a real base never
        does, so it cannot be packed. The release carries no base, or with
        ``base_asset`` a placeholder, which only a machine lacking it would open.
        """
        for name, content in (files or {"openprogram/module.py": "number = 2\n"}).items():
            commit_change(self.source, name, content, f"release {release}")
        head = git(self.source, "rev-parse", "HEAD")
        built = self.real_build(self.ci, builder.BuildRequest(
            f"ci-{release}".replace(".", "-"), head, worktree=self.source,
        ))
        output = self.tmp / f"release-{release}"
        real_pack = assets._pack

        def pack(source, target, **options):
            if source.parent != self.ci.bases:
                return real_pack(source, target, **options)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"placeholder base")

        with mock.patch.object(assets, "_pack", pack):
            assets.package_platform(self.ci, built.version, output, repository=self.source, platform=PLATFORM)
        if not base_asset:
            note = output / f"{assets._SIDECAR_PREFIX}{PLATFORM}.json"
            (output / f"base-{PLATFORM}.tar.zst").unlink()
            note.write_text(json.dumps({**json.loads(note.read_text()), "base_asset": None}))
        assets.finalize_release(output, release=release, commit=head, repository=self.source, private_key_pem=PEM)
        self.base = built.base
        return output, built

    def give_base(self):
        if not self.layout.base(self.base).exists():
            self.layout.bases.mkdir(exist_ok=True)
            shutil.copytree(self.ci.base(self.base), self.layout.base(self.base), symlinks=True)

    def trial(self, version):
        self.trials.append(version)
        if self.fail_trial(version):
            return Verdict(False, "the trial failed")
        return Verdict(True)

    def activate(self, version):
        self.activated.append(version)
        commit = store.read_manifest(self.layout, version)["commit"]
        git(self.layout.repo, "update-ref", "refs/heads/local", commit)
        return Verdict(True)

    def build(self, layout, request):
        self.build_calls.append(request)
        return self.real_build(layout, request)

    def lifecycle(self, **changes):
        return Lifecycle(**{"trial": self.trial, "activate": self.activate, "build": self.build, **changes})

    def run(self, directory, **options):
        arguments = {"update_id": "u1", "platform": PLATFORM, "trusted_keys": TRUSTED, "parent": None}
        arguments.update(options)
        lifecycle = arguments.pop("lifecycle", None) or self.lifecycle()
        return replay.replay_release(self.layout, directory, lifecycle, **arguments)

    def site_file(self, version, name):
        manifest = store.read_manifest(self.layout, version)
        return paths.site_packages(self.layout.version(version), manifest["base"]) / "openprogram" / name

    def ref(self, name):
        return git(self.layout.repo, "rev-parse", name)


@pytest.fixture
def world(tmp_path, repo, log):  # noqa: F811
    return World(tmp_path, repo)


def test_no_own_commits_unpacks_the_prebuilt_version_and_skips_building(world):
    directory, built = world.publish()
    world.give_base()
    result = world.run(directory, parent="running-version")

    assert result.outcome == replay.ACTIVATED, result.message
    assert world.build_calls == [], "a release with no own commits is not built here"
    assert world.trials == [built.version] == world.activated
    manifest = store.read_manifest(world.layout, built.version)
    assert manifest["parent"] == "running-version"
    assert world.ref("refs/heads/release") == git(world.source, "rev-parse", "HEAD")
    assert store.changed_files(world.layout.version(built.version), built.base) == []


def test_the_unpacked_version_runs_against_this_machines_base(world):
    directory, built = world.publish()
    world.give_base()
    world.run(directory)

    version = world.layout.version(built.version)
    config = (version / "venv/pyvenv.cfg").read_text()
    assert str(world.ci.root) not in config
    assert str(world.layout.base(built.base)) in config
    assert str(world.ci.root) not in os.readlink(version / "venv/bin/python")
    done = subprocess.run(
        [str(version / "venv/bin/python"), "-I", "-B", "-c",
         "import sys, openprogram.module as m; print(sys.prefix); print(m.number)"],
        capture_output=True, text=True, timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines() == [str(version / "venv"), "2"]
    frontend = world.layout.frontends / f"{store.read_manifest(world.layout, built.version)['frontend_sha']}.zip"
    assert frontend.is_file()


def test_own_commits_are_replayed_oldest_first_and_keep_their_author(world):
    first = world.own("own_a.py", "a = 1\n", "first own")
    second = world.own("own_b.py", "from openprogram.own_a import a\n", "second own")
    directory, built = world.publish()
    world.give_base()
    result = world.run(directory)

    assert result.outcome == replay.ACTIVATED, result.message
    assert [item.sha for item in result.replayed] == [first, second]
    assert [item.subject for item in result.replayed] == ["first own", "second own"]
    assert result.saved == [] and result.attempts == 1
    version = world.activated[0]
    assert version != built.version
    assert world.site_file(version, "own_a.py").is_file() and world.site_file(version, "own_b.py").is_file()
    assert "number = 2" in world.site_file(version, "module.py").read_text()
    assert len(world.build_calls) == 1
    assert world.build_calls[0].parent == built.version, "the build starts from the release's own version"

    tip = world.ref("refs/heads/local")
    assert git(world.layout.repo, "log", "-1", "--format=%an %s", tip) == "t second own"
    assert git(world.layout.repo, "log", "-1", "--format=%cn", tip) == "OpenProgram"
    assert git(world.layout.repo, "merge-base", "--is-ancestor", world.ref("refs/heads/release"), tip) == ""
    assert not (world.layout.work / "official-0.2.0").exists()


def test_a_conflict_is_saved_and_the_rest_carry_on(world):
    clash = world.own("module.py", "number = 'mine'\n", "clashes with the release")
    fine = world.own("own_b.py", "b = 2\n", "applies")
    directory, _ = world.publish()
    world.give_base()
    result = world.run(directory)

    assert result.outcome == replay.ACTIVATED, result.message
    assert [item.sha for item in result.replayed] == [fine]
    assert [(item.sha, item.reason) for item in result.saved] == [(clash, replay.CONFLICT)]
    patch = world.layout.root / "unreplayable" / "0.2.0" / f"{clash}.patch"
    assert patch.is_file() and result.saved[0].patch == str(patch)
    assert "number = 'mine'" in patch.read_text()
    version = world.activated[0]
    assert "number = 2" in world.site_file(version, "module.py").read_text()
    assert world.site_file(version, "own_b.py").is_file()


def test_when_every_commit_conflicts_the_prebuilt_release_is_used(world):
    world.own("module.py", "number = 'mine'\n", "clash")
    directory, built = world.publish()
    world.give_base()
    result = world.run(directory)

    assert result.outcome == replay.ACTIVATED
    assert world.activated == [built.version] and world.build_calls == []
    assert len(result.saved) == 1 and result.replayed == []


def test_a_commit_the_release_already_has_is_neither_replayed_nor_saved(world):
    same = world.own("module.py", "number = 2\n", "same as the release will do")
    directory, built = world.publish()
    world.give_base()
    result = world.run(directory)

    assert result.outcome == replay.ACTIVATED, result.message
    assert [item.sha for item in result.already_in_release] == [same]
    assert result.saved == [] and result.replayed == []
    assert world.activated == [built.version]


def test_a_failing_trial_drops_commits_newest_first_until_one_passes(world):
    oldest = world.own("own_a.py", "a = 1\n", "a")
    middle = world.own("own_b.py", "b = 1\n", "b")
    newest = world.own("own_c.py", "c = 1\n", "c")
    directory, _ = world.publish()
    world.give_base()
    world.fail_trial = lambda version: world.site_file(version, "own_b.py").exists()
    result = world.run(directory)

    assert result.outcome == replay.ACTIVATED, result.message
    assert [item.sha for item in result.replayed] == [oldest]
    assert [(item.sha, item.reason) for item in result.saved] == [
        (newest, replay.FAILED_TRIAL), (middle, replay.FAILED_TRIAL),
    ]
    assert result.attempts == 3
    version = world.activated[-1]
    assert world.site_file(version, "own_a.py").exists() and not world.site_file(version, "own_c.py").exists()
    assert len(world.activated) == 1
    assert (world.layout.root / "unreplayable/0.2.0" / f"{newest}.patch").is_file()


def test_dropping_ends_at_the_unmodified_release(world):
    first = world.own("own_a.py", "a = 1\n", "a")
    directory, built = world.publish()
    world.give_base()
    world.fail_trial = lambda version: version != built.version
    result = world.run(directory)

    assert result.outcome == replay.ACTIVATED
    assert world.activated == [built.version]
    assert result.replayed == [] and [item.sha for item in result.saved] == [first]
    assert len(world.build_calls) == 1, "the unmodified release comes from the prebuilt asset"


def test_after_five_retries_the_rest_go_at_once(world):
    commits = [world.own(f"own_{index}.py", f"v = {index}\n", f"own {index}") for index in range(8)]
    directory, built = world.publish()
    world.give_base()
    world.fail_trial = lambda version: version != built.version
    result = world.run(directory)

    assert result.outcome == replay.ACTIVATED
    assert world.activated == [built.version]
    assert result.attempts == 7, "the first try, 5 retries, then the unmodified release"
    assert len(world.build_calls) == 6
    assert [item.sha for item in result.saved] == list(reversed(commits))


def test_a_commit_that_does_not_build_is_dropped_like_a_failed_trial(world):
    good = world.own("own_a.py", "a = 1\n", "a")
    bad = world.own("own_b.py", "b = 1\n", "b")
    directory, _ = world.publish()
    world.give_base()

    def build(layout, request):
        world.build_calls.append(request)
        if request.commit != builder._git(["rev-parse", "HEAD"], request.worktree).strip():
            raise AssertionError("the worktree must be at the candidate")
        if (Path(request.worktree) / "openprogram" / "own_b.py").exists():
            raise builder.BuildError("the commit does not build")
        return world.real_build(layout, request)

    result = world.run(directory, lifecycle=world.lifecycle(build=build))
    assert result.outcome == replay.ACTIVATED, result.message
    assert [item.sha for item in result.replayed] == [good]
    assert [item.sha for item in result.saved] == [bad]


def test_a_full_disk_drops_nothing(world):
    world.own("own_a.py", "a = 1\n", "a")
    directory, _ = world.publish()
    world.give_base()

    def build(layout, request):
        raise builder.NotEnoughSpace("not enough disk space")

    before = world.ref("refs/heads/local")
    result = world.run(directory, lifecycle=world.lifecycle(build=build))
    assert result.outcome == replay.ABORTED and "not enough disk" in result.message
    assert result.saved == [] and world.ref("refs/heads/local") == before
    assert not (world.layout.work / "official-0.2.0").exists()


def test_when_the_release_itself_fails_nothing_is_activated(world):
    world.own("own_a.py", "a = 1\n", "a")
    directory, _ = world.publish()
    world.give_base()
    world.fail_trial = lambda version: True
    before = world.ref("refs/heads/local")
    result = world.run(directory)

    assert result.outcome == replay.ABORTED
    assert "unchanged" in result.message and "the trial failed" in result.message
    assert world.activated == [] and result.replayed == []
    assert world.ref("refs/heads/local") == before


def test_a_rolled_back_activation_is_reported_and_drops_nothing(world):
    world.own("own_a.py", "a = 1\n", "a")
    directory, _ = world.publish()
    world.give_base()
    lifecycle = world.lifecycle(activate=lambda version: Verdict(False, "the probe failed"))
    result = world.run(directory, lifecycle=lifecycle)

    assert result.outcome == replay.ROLLED_BACK and "the probe failed" in result.message
    assert result.saved == [] and result.attempts == 1 and len(result.replayed) == 1


def test_running_again_after_a_failure_reuses_what_is_there(world):
    directory, built = world.publish()
    world.give_base()
    world.fail_trial = lambda version: True
    assert world.run(directory).outcome == replay.ABORTED
    world.fail_trial = lambda version: False
    second = world.run(directory)

    assert second.outcome == replay.ACTIVATED, second.message
    assert world.activated == [built.version]


def test_an_installation_already_on_the_release_is_up_to_date(world):
    directory, _ = world.publish()
    world.give_base()
    assert world.run(directory).outcome == replay.ACTIVATED
    trials = len(world.trials)
    again = world.run(directory)
    assert again.outcome == replay.UP_TO_DATE and len(world.trials) == trials


def test_a_release_without_a_base_is_built_from_its_commit(world):
    directory, built = world.publish()
    result = world.run(directory)  # This installation has no base, and the release carries none.

    assert result.outcome == replay.ACTIVATED, result.message
    assert len(world.build_calls) == 1
    assert world.build_calls[0].commit == git(world.source, "rev-parse", "HEAD")
    assert world.build_calls[0].worktree is None
    assert store.read_manifest(world.layout, world.activated[0])["commit"] == world.build_calls[0].commit
    assert world.layout.base(built.base).is_dir()


def test_a_release_for_a_newer_shell_waits_and_changes_nothing(world):
    world.own("own_a.py", "a = 1\n", "a")
    (world.source / "apps/desktop/shell").mkdir(parents=True)
    (world.source / "apps/desktop/shell/compat.json").write_text('{"shell_compat": 9}\n')
    directory, _ = world.publish()
    world.give_base()
    identity = world.layout.root / "identity"
    identity.mkdir()
    (identity / "shell-compat.json").write_text('{"min": 1, "max": 2}\n')
    release_before = world.ref("refs/heads/release")
    result = world.run(directory)

    assert result.outcome == replay.WAITING_FOR_SHELL and "shell" in result.message
    assert world.ref("refs/heads/release") == release_before and world.trials == []


def test_a_release_moving_python_waits_for_the_launcher(world):
    directory, built = world.publish()
    identity = world.layout.root / "identity"
    identity.mkdir()
    (identity / "python-minor").write_text("3.1\n")
    result = world.run(directory)
    assert result.outcome == replay.WAITING_FOR_SHELL
    assert paths.base_minor(built.base) in result.message


def test_an_older_release_cannot_replace_a_newer_one(world):
    older, _ = world.publish("0.2.0")
    world.give_base()
    world.run(older)
    newer, _ = world.publish("0.3.0", {"openprogram/module.py": "number = 3\n"})
    assert world.run(newer).outcome == replay.ACTIVATED
    tip = world.ref("refs/heads/release")

    result = world.run(older)
    assert result.outcome == replay.ABORTED and "does not follow" in result.message
    assert world.ref("refs/heads/release") == tip


@pytest.mark.parametrize("damage, expected", [
    (lambda d: (d / assets.MANIFEST_FILE).write_text((d / assets.MANIFEST_FILE).read_text().replace("0.2.0", "0.2.1")), "signature"),
    (lambda d: (d / assets.SIGNATURE_FILE).write_text("zz\n"), "malformed"),
    (lambda d: (d / assets.SIGNATURE_FILE).unlink(), "incomplete"),
    (lambda d: (d / assets.SUMS_FILE).write_text("0" * 64 + "  repo.bundle\n"), "does not match the signed manifest"),
    (lambda d: (d / assets.BUNDLE_FILE).write_bytes(b"swapped"), "does not match its checksum"),
    (lambda d: (d / f"version-{PLATFORM}.tar.zst").write_bytes(b"swapped"), "does not match its checksum"),
    (lambda d: (d / f"version-{PLATFORM}.tar.zst").unlink(), "incomplete"),
])
def test_an_altered_release_changes_nothing(world, damage, expected):
    directory, _ = world.publish()
    world.give_base()
    damage(directory)
    before = world.ref("refs/heads/release")
    result = world.run(directory)

    assert result.outcome == replay.ABORTED and expected in result.message
    assert world.ref("refs/heads/release") == before
    assert world.trials == [] and store.list_versions(world.layout) == []


def test_a_build_without_a_release_key_accepts_nothing(world, monkeypatch):
    monkeypatch.setattr(assets, "RELEASE_PUBLIC_KEYS", ())
    directory, _ = world.publish()
    result = world.run(directory, trusted_keys=None)
    assert result.outcome == replay.ABORTED and "no release signing key" in result.message


def test_this_build_trusts_a_release_key_and_it_is_not_the_test_key(world):
    assert assets.RELEASE_PUBLIC_KEYS
    assert all(re.fullmatch(r"[0-9a-f]{64}", key) for key in assets.RELEASE_PUBLIC_KEYS)
    assert not set(TRUSTED) & set(assets.RELEASE_PUBLIC_KEYS)
    directory, _ = world.publish()
    result = world.run(directory, trusted_keys=None)
    assert result.outcome == replay.ABORTED and "signature" in result.message


def test_a_key_that_did_not_sign_it_is_refused(world):
    directory, _ = world.publish()
    other = Ed25519PrivateKey.generate().private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    result = world.run(directory, trusted_keys=[assets.public_key_hex(other)])
    assert result.outcome == replay.ABORTED and "signature" in result.message


def test_a_release_without_this_platform_is_refused(world):
    directory, _ = world.publish()
    result = world.run(directory, platform="linux-arm64")
    assert result.outcome == replay.ABORTED and "linux-arm64" in result.message


def test_an_archive_with_the_wrong_version_inside_is_refused(world):
    directory, _ = world.publish()
    world.give_base()
    release = assets.read_release(directory, platform=PLATFORM, trusted_keys=TRUSTED)
    forged = assets.Release(**{**release.__dict__, "commit": "0" * 40})
    with pytest.raises(assets.ReleaseError, match="does not hold what the manifest says"):
        assets.install_version(world.layout, forged, update_id="u1", parent=None)
    assert store.list_versions(world.layout) == []
    assert not world.layout.staging("u1").exists()


# --- Assets --------------------------------------------------------------------


def _small_store(tmp_path, repo):  # noqa: F811
    layout = make_layout(tmp_path / "small" / "runtime")
    head = git(repo, "rev-parse", "HEAD")
    make_version(layout, "20261101T000000Z-aaaaaaaaaaaa", commit=head)
    base = layout.base(BASE)
    (base / "python/bin/python3").unlink()
    (base / "python/bin/python3").write_text("#!/bin/sh\n")
    (base / "bin").mkdir()
    (base / "bin/python").symlink_to("../python/bin/python3")
    return layout, head


def test_the_base_and_toolchain_travel_with_every_release(tmp_path, repo):  # noqa: F811
    # A machine that skipped releases has only this one to get them from.
    layout, head = _small_store(tmp_path, repo)
    lock = builder.toolchain_id((repo / "package-lock.json").read_bytes())
    (layout.toolchain / lock / "node_modules").mkdir(parents=True)
    (layout.toolchain / lock / "node_modules/m").write_text("m\n")
    output = tmp_path / "out"
    entry = assets.package_platform(layout, "20261101T000000Z-aaaaaaaaaaaa", output, repository=repo, platform=PLATFORM)
    assert entry["base_asset"] == f"base-{PLATFORM}.tar.zst" and entry["toolchain_asset"] == f"toolchain-{PLATFORM}.tar.zst"
    manifest = assets.finalize_release(output, release="0.2.0", commit=head, repository=repo, private_key_pem=PEM)
    assert not list(output.glob("platform-*.json")), "the per-platform notes are not published"
    assert set(manifest["platforms"]) == {PLATFORM}
    assert (output / assets.BUNDLE_FILE).is_file()
    release = assets.read_release(output, platform=PLATFORM, trusted_keys=TRUSTED)

    target = Layout(tmp_path / "other" / "runtime")
    target.root.mkdir(parents=True)
    assert assets.install_carried_base(target, release) is True
    assert assets.install_carried_toolchain(target, release) is True
    assert os.readlink(target.base(BASE) / "bin/python") == "../python/bin/python3"
    assert (target.toolchain / lock / "node_modules/m").read_text() == "m\n"
    assert assets.install_carried_base(target, release) is False
    assert not [item.name for item in target.bases.iterdir() if item.name.startswith(".")]


def test_a_machine_with_the_base_needs_only_the_rest_of_the_release(world):
    directory, built = world.publish(base_asset=True)
    release = assets.read_release(directory, platform=PLATFORM, trusted_keys=TRUSTED)
    assert release.base_asset in assets.assets_to_download(world.layout, release)

    world.give_base()
    wanted = assets.assets_to_download(world.layout, release)
    assert release.base_asset not in wanted
    assert release.version_asset in wanted

    (directory / release.base_asset).unlink()
    assert assets.read_release(directory, platform=PLATFORM, trusted_keys=TRUSTED, layout=world.layout).base == built.base
    with pytest.raises(assets.ReleaseError, match="incomplete"):
        assets.read_release(directory, platform=PLATFORM, trusted_keys=TRUSTED)


def test_the_base_is_still_required_when_the_machine_lacks_it(world):
    directory, _ = world.publish(base_asset=True)
    (directory / f"base-{PLATFORM}.tar.zst").unlink()
    with pytest.raises(assets.ReleaseError, match="incomplete"):
        assets.read_release(directory, platform=PLATFORM, trusted_keys=TRUSTED, layout=world.layout)


def test_a_present_file_the_machine_could_skip_is_still_checked(world):
    directory, _ = world.publish(base_asset=True)
    world.give_base()
    (directory / f"base-{PLATFORM}.tar.zst").write_bytes(b"altered")
    with pytest.raises(assets.ReleaseError, match="does not match"):
        assets.read_release(directory, platform=PLATFORM, trusted_keys=TRUSTED, layout=world.layout)


def test_the_toolchain_is_downloaded_only_for_an_owner_with_commits(world):
    directory, _ = world.publish()
    world.give_base()
    release = assets.read_release(directory, platform=PLATFORM, trusted_keys=TRUSTED)
    assert release.toolchain_asset is not None
    assert release.toolchain_asset not in assets.assets_to_download(world.layout, release)

    (world.tree / "mine.txt").write_text("mine\n")
    git(world.tree, "add", "mine.txt")
    git(world.tree, "-c", "user.name=o", "-c", "user.email=o@example.invalid", "commit", "-q", "-m", "mine")
    assert release.toolchain_asset in assets.assets_to_download(world.layout, release)


def test_a_toolchain_that_was_not_downloaded_is_left_to_the_build(world):
    directory, _ = world.publish()
    release = assets.read_release(directory, platform=PLATFORM, trusted_keys=TRUSTED)
    (directory / release.toolchain_asset).unlink()
    assert assets.read_release(directory, platform=PLATFORM, trusted_keys=TRUSTED, layout=world.layout).release == "0.2.0"
    assert assets.install_carried_toolchain(world.layout, release) is False


def test_a_link_that_leaves_the_tree_is_refused_when_packing(tmp_path, repo):  # noqa: F811
    layout, _ = _small_store(tmp_path, repo)
    (layout.base(BASE) / "bin/escape").symlink_to("/etc/passwd")
    with pytest.raises(assets.ReleaseError, match="leaves it"):
        assets.package_platform(layout, "20261101T000000Z-aaaaaaaaaaaa", tmp_path / "out", repository=repo, platform=PLATFORM)


def test_a_version_archive_carries_no_paths_from_the_build_machine(world):
    directory, _ = world.publish()
    import tarfile
    import zstandard

    with open(directory / f"version-{PLATFORM}.tar.zst", "rb") as raw, zstandard.ZstdDecompressor().stream_reader(raw) as stream:
        with tarfile.open(fileobj=stream, mode="r|") as archive:
            names = [member.name for member in archive]
    assert "version.json" in names and "desktop/main.js" in names
    assert not [name for name in names if name in {"files.tsv", "pristine.zip", "venv/pyvenv.cfg"}]
    assert not [name for name in names if name.startswith("venv/bin")]


def test_the_release_script_finalizes_and_prints_the_public_key(tmp_path, repo):  # noqa: F811
    layout, head = _small_store(tmp_path, repo)
    output = tmp_path / "out"
    assets.package_platform(layout, "20261101T000000Z-aaaaaaaaaaaa", output, repository=repo, platform=PLATFORM)
    key_file = tmp_path / "key.pem"
    key_file.write_bytes(PEM)
    script = str(REPOSITORY / "scripts/release/create-update-assets.py")
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    done = subprocess.run(
        [sys.executable, script, "finalize", "--directory", str(output), "--release", "v0.2.0",
         "--commit", head, "--repository", str(repo), "--signing-key", str(key_file)],
        capture_output=True, text=True, timeout=120, env=environment, cwd=tmp_path,
    )
    assert done.returncode == 0, done.stderr
    assert assets.read_release(output, platform=PLATFORM, trusted_keys=TRUSTED).release == "0.2.0"
    assert json.loads((output / assets.MANIFEST_FILE).read_text())["release"] == "0.2.0"
    printed = subprocess.run(
        [sys.executable, script, "public-key", "--signing-key", str(key_file)],
        capture_output=True, text=True, timeout=120, env=environment, cwd=tmp_path,
    )
    assert printed.stdout.strip() == TRUSTED[0]
