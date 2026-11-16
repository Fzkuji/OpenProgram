"""Network updates against a local HTTP server and real curl.

The release is a real one from ``test_replay``'s world; only the addresses are
local, so everything from the first request to the update controller's hand-off
runs as it does for a user.
"""
from argparse import Namespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
import pytest

from openprogram.host.security.url_policy import URLPolicyError
from openprogram.operations.self_update.github_release import github
from openprogram.operations.self_update.official import assets, fetch, replay
from openprogram.operations.self_update.official.assets import ReleaseError
from openprogram.operations.self_update.versions import store
from tests.component.self_update.official.test_replay import PEM, PLATFORM, TRUSTED, World
from tests.component.self_update.versions.test_build import git, log, repo  # noqa: F401

pytestmark = pytest.mark.skipif(os.name == "nt", reason="build stubs are POSIX shell")
RELEASE = "0.2.0"
OWNER_REPOSITORY = f"{github.DEFAULT_OWNER}/{github.DEFAULT_REPO}"
DOWNLOAD_PATH = f"/web/{OWNER_REPOSITORY}/releases/download/v{RELEASE}"
OTHER_KEY = Ed25519PrivateKey.generate().private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())


class Releases(ThreadingHTTPServer):
    """A GitHub stand-in. Tests change what it answers."""

    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.directory: Path | None = None
        self.api_status = 200
        self.web_status = 302
        self.notes = "What changed."
        self.replaced: dict[str, bytes] = {}
        self.redirects: dict[str, str] = {}
        self.files: dict[str, bytes] = {}  # Other paths, whole.
        self.requests: list[str] = []
        self.ranges: list[str] = []

    @property
    def base(self):
        return f"http://127.0.0.1:{self.server_address[1]}"

    def downloaded(self):
        return {path.rsplit("/", 1)[1] for path in self.requests if path.startswith(DOWNLOAD_PATH)}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _send(self, status, body=b"", **headers):
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name.replace("_", "-"), value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _asset(self, name):
        server = self.server
        if name in server.redirects:
            return self._send(302, Location=server.redirects[name])
        if name in server.replaced:
            content = server.replaced[name]
        elif server.directory is not None and (server.directory / name).is_file():
            content = (server.directory / name).read_bytes()
        else:
            return self._send(404)
        requested = self.headers.get("Range")
        if requested:
            server.ranges.append(requested)
            start, _, end = requested.removeprefix("bytes=").partition("-")
            first, last = int(start), int(end) if end else len(content) - 1
            if first >= len(content):
                return self._send(416)
            return self._send(206, content[first:last + 1],
                              Content_Range=f"bytes {first}-{last}/{len(content)}")
        return self._send(200, content)

    def do_GET(self):  # noqa: N802
        server, path = self.server, self.path
        server.requests.append(path)
        if path.startswith("/api/"):
            if server.api_status != 200:
                return self._send(server.api_status, b"{}", Content_Type="application/json")
            body = json.dumps({"tag_name": f"v{RELEASE}", "draft": False, "prerelease": False,
                               "body": server.notes, "assets": []}).encode()
            return self._send(200, body, Content_Type="application/json")
        if path in server.files:
            return self._send(200, server.files[path])
        if path.endswith("/releases/latest") and path.startswith(("/web/", "/moved/")):
            if path.startswith("/web/") and server.web_status != 302:
                return self._send(server.web_status)
            root, _, _ = path.partition("/releases/latest")
            return self._send(302, Location=f"{server.base}{root}/releases/tag/v{RELEASE}")
        if "/releases/download/" in path:
            return self._asset(path.rsplit("/", 1)[1])
        return self._send(404)


@pytest.fixture
def server(monkeypatch):
    instance = Releases()
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    base = instance.base
    monkeypatch.setattr(github, "API_ROOT", f"{base}/api")
    monkeypatch.setattr(github, "WEB_ROOT", f"{base}/web")
    monkeypatch.setattr(github, "UPDATE_SOURCE_URL", f"{base}/update-source.json")
    monkeypatch.setattr(github, "_RELEASE_HOSTS", {"127.0.0.1"})
    monkeypatch.setattr(github, "_URL_SCHEMES", {"http"})
    monkeypatch.setattr(github, "_CURL_PROTOCOLS", "=http")
    monkeypatch.setattr(github, "DOWNLOAD_RETRY_SECONDS", 0)
    monkeypatch.setattr(github, "_web_root", lambda _host: f"{base}/moved")
    monkeypatch.setattr(github, "_signed_hosts", set())

    def no_direct_route(*_arguments, **_options):
        raise URLPolicyError("NON_GLOBAL_ADDRESS", "http://127.0.0.1/")  # As behind a proxy: curl answers.

    monkeypatch.setattr(github.safe_http, "safe_client", no_direct_route)
    for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    monkeypatch.setattr(github.urllib.request, "getproxies", dict)
    monkeypatch.setattr(fetch, "installed_version", lambda: "0.1.0")
    yield instance
    instance.shutdown()
    instance.server_close()


@pytest.fixture
def world(tmp_path, repo, log):  # noqa: F811
    return World(tmp_path, repo)


@pytest.fixture
def published(world, server):
    directory, built = world.publish(RELEASE)
    world.give_base()
    server.directory = directory
    (directory / "version-linux-arm64.tar.zst").write_bytes(b"for another computer")
    return directory, built


def update(world, **options):
    arguments = {"layout": world.layout, "apply": lambda files: world.run(files), "platform": PLATFORM,
                 "trusted_keys": TRUSTED}
    arguments.update(options)
    return fetch.update(**arguments)


def assert_nothing_changed(world, before):
    assert world.ref("refs/heads/release") == before
    assert world.trials == [] and world.activated == []
    assert store.list_versions(world.layout) == []
    assert not (world.layout.root / "downloads").exists()


def test_a_signed_release_downloads_only_this_platform_and_applies(world, server, published):
    directory, built = published
    result = update(world)

    assert result.outcome == replay.ACTIVATED, result.message
    assert world.activated == [built.version]
    assert world.ref("refs/heads/release") == git(world.source, "rev-parse", "HEAD")
    names = server.downloaded()
    assert f"version-{PLATFORM}.tar.zst" in names and assets.BUNDLE_FILE in names
    assert "version-linux-arm64.tar.zst" not in names
    assert not (world.layout.root / "downloads").exists()


def test_the_download_goes_through_a_redirect_to_an_allowed_host(world, server, published):
    directory, _ = published
    name = f"version-{PLATFORM}.tar.zst"
    server.redirects[name] = f"{server.base}/web/{OWNER_REPOSITORY}/releases/download/v{RELEASE}/cdn-copy"
    server.replaced["cdn-copy"] = (directory / name).read_bytes()
    assert update(world).outcome == replay.ACTIVATED


def test_a_redirect_to_another_host_is_refused(world, server, published):
    name = f"version-{PLATFORM}.tar.zst"
    server.redirects[name] = "http://files.example.invalid/asset"
    before = world.ref("refs/heads/release")
    with pytest.raises(ReleaseError, match="does not trust"):
        update(world)
    assert_nothing_changed(world, before)


def test_a_bad_signature_refuses_before_any_file_is_fetched(world, server, published):
    directory, _ = published
    manifest = (directory / assets.MANIFEST_FILE).read_bytes()
    server.replaced[assets.SIGNATURE_FILE] = (assets._sign(manifest, OTHER_KEY).hex() + "\n").encode()
    before = world.ref("refs/heads/release")
    with pytest.raises(ReleaseError, match="signature"):
        update(world)
    assert_nothing_changed(world, before)
    assert server.downloaded() <= {assets.MANIFEST_FILE, assets.SIGNATURE_FILE, assets.SUMS_FILE}


@pytest.mark.parametrize("change, expected", [
    (lambda content: content[:-10], "bytes, and the signed manifest says"),
    (lambda content: content + b"more", "larger than the signed manifest"),
    (lambda content: bytes([content[0] ^ 1]) + content[1:], "checksum"),
])
def test_a_file_that_is_not_what_was_signed_refuses(world, server, published, change, expected):
    directory, _ = published
    name = f"version-{PLATFORM}.tar.zst"
    server.replaced[name] = change((directory / name).read_bytes())
    before = world.ref("refs/heads/release")
    with pytest.raises(ReleaseError, match=expected):
        update(world)
    assert_nothing_changed(world, before)


def test_a_release_without_this_platform_refuses(world, server, published):
    before = world.ref("refs/heads/release")
    with pytest.raises(ReleaseError, match="has nothing for linux-x86_64"):
        update(world, platform="linux-x86_64")
    assert_nothing_changed(world, before)


def test_files_signed_for_an_older_release_refuse_under_a_newer_tag(world, server, published, monkeypatch):
    latest = fetch.Latest("9.9.9", {"owner": github.DEFAULT_OWNER, "repo": github.DEFAULT_REPO,
                                    "web_root": github.WEB_ROOT, "via": "github-api"}, "")
    monkeypatch.setattr(fetch, "find_latest", lambda: latest)
    monkeypatch.setattr(github, "release_file", lambda _s, _v, name: (published[0] / name).read_bytes())
    with pytest.raises(ReleaseError, match="describe release 0.2.0"):
        update(world)


def test_a_machine_that_has_the_base_does_not_download_it(world, server):
    directory, built = world.publish(RELEASE, base_asset=True)
    world.give_base()
    server.directory = directory
    base = f"base-{PLATFORM}.tar.zst"

    status = fetch.check(layout=world.layout, platform=PLATFORM, trusted_keys=TRUSTED)
    result = update(world)

    assert result.outcome == replay.ACTIVATED, result.message
    assert world.activated == [built.version]
    assert base not in server.downloaded()
    assert status["download_bytes"] == sum(
        (directory / name).stat().st_size for name in (assets.BUNDLE_FILE, f"version-{PLATFORM}.tar.zst")
    )


def test_a_machine_without_the_base_downloads_it(world, server):
    directory, _ = world.publish(RELEASE, base_asset=True)
    server.directory = directory
    latest = fetch.find_latest()

    fetched = fetch.download(latest, world.layout, platform=PLATFORM, trusted_keys=TRUSTED)

    assert (fetched / f"base-{PLATFORM}.tar.zst").read_bytes() == b"placeholder base"


def test_a_stopped_download_resumes_from_where_it_stopped(world, server, published):
    directory, _ = published
    name = f"version-{PLATFORM}.tar.zst"
    content = (directory / name).read_bytes()
    staging = world.layout.root / "downloads" / RELEASE
    staging.mkdir(parents=True)
    (staging / f"{name}.part").write_bytes(content[:100])

    assert update(world).outcome == replay.ACTIVATED
    assert "bytes=100-" in server.ranges


def test_a_failed_connection_keeps_the_partial_file_and_says_what_to_do(world, server, published, monkeypatch):
    monkeypatch.setattr(github, "_curl_download", lambda *_a, **_o: (56, "000"))
    staging = world.layout.root / "downloads" / RELEASE
    staging.mkdir(parents=True)
    (staging / f"{assets.BUNDLE_FILE}.part").write_bytes(b"partial")
    with pytest.raises(ReleaseError, match="HTTPS_PROXY.*resumes"):
        update(world)
    assert (staging / f"{assets.BUNDLE_FILE}.part").read_bytes() == b"partial"


def test_a_full_disk_stops_before_downloading(world, server, published, monkeypatch):
    monkeypatch.setattr(fetch, "_free_bytes", lambda _path: 1024)
    before = world.ref("refs/heads/release")
    with pytest.raises(ReleaseError, match=r"Not enough disk space.* is free, (less than 1 MB|[\d.]+ (MB|GB)) short\."):
        update(world)
    assert_nothing_changed(world, before)


def test_an_install_on_the_latest_release_downloads_nothing(world, server, published, monkeypatch):
    monkeypatch.setattr(fetch, "installed_version", lambda: RELEASE)
    result = update(world)
    assert result.outcome == replay.UP_TO_DATE
    assert server.downloaded() == set()


@pytest.mark.parametrize("status", [403, 429, 500, 503])
def test_a_refusing_api_falls_back_to_the_redirect_and_loses_only_the_notes(world, server, published, status):
    server.api_status = status
    found = fetch.find_latest()
    assert found.release == RELEASE and found.notes == ""
    assert found.source["via"] == github.VIA_REDIRECT
    assert update(world).outcome == replay.ACTIVATED


def test_the_api_gives_the_notes_when_it_answers(server, published):
    found = fetch.find_latest()
    assert found.notes == "What changed." and found.source["via"] == github.VIA_API


def test_the_fallback_reads_file_sizes_from_the_release_manifest(server, published):
    server.api_status = 403
    server.replaced["release-manifest.json"] = json.dumps({
        "schema": 1, "version": RELEASE, "files": [{"path": "dist/runtime.tar.gz", "bytes": 7, "sha256": "a" * 64}],
    }).encode()
    release = github.latest_release()
    assert {"name": "runtime.tar.gz", "size": 7} in release["assets"]
    assert "release-manifest.json" in {asset["name"] for asset in release["assets"]}


def _github_is_gone(server):
    server.api_status = 404
    server.web_status = 404


def _source_file(server, *, key=PEM, payload=None):
    data = json.dumps(payload or {"schema": 1, "repository": "Elsewhere/OpenProgram", "hosts": ["mirror.example"]}).encode()
    server.files["/update-source.json"] = data
    server.files["/update-source.json.sig"] = (assets._sign(data, key).hex() + "\n").encode()


def test_a_signed_update_source_is_used_when_github_is_gone(world, server, published, monkeypatch):
    _github_is_gone(server)
    _source_file(server)
    monkeypatch.setattr(assets, "RELEASE_PUBLIC_KEYS", tuple(TRUSTED))
    found = fetch.find_latest()
    assert (found.source["owner"], found.source["repo"]) == ("Elsewhere", "OpenProgram")
    assert found.source["via"] == github.VIA_UPDATE_SOURCE
    assert "mirror.example" in github._signed_hosts
    assert github.moved_repository(github.latest_release()) == ("Elsewhere", "OpenProgram")
    assert update(world).outcome == replay.ACTIVATED


def test_the_update_source_is_not_read_while_github_answers(server, published, monkeypatch):
    _source_file(server)
    monkeypatch.setattr(assets, "RELEASE_PUBLIC_KEYS", tuple(TRUSTED))
    fetch.find_latest()
    assert "/update-source.json" not in server.requests


@pytest.mark.parametrize("arrange", [
    lambda server: _source_file(server, key=OTHER_KEY),
    lambda server: server.files.update({"/update-source.json": b'{"schema": 1}'}),
    lambda server: _source_file(server, payload={"schema": 2, "repository": "A/B", "hosts": ["a.example"]}),
    lambda server: _source_file(server, payload={"schema": 1, "repository": "no slash", "hosts": ["a.example"]}),
    lambda server: _source_file(server, payload={"schema": 1, "repository": "A/B", "hosts": []}),
    lambda server: None,
], ids=["wrong-key", "unsigned", "unknown-schema", "bad-repository", "no-hosts", "absent"])
def test_an_unverified_update_source_is_ignored(server, published, monkeypatch, arrange):
    _github_is_gone(server)
    arrange(server)
    monkeypatch.setattr(assets, "RELEASE_PUBLIC_KEYS", tuple(TRUSTED))
    with pytest.raises(ReleaseError, match="HTTPS_PROXY"):
        fetch.find_latest()
    assert github._signed_hosts == set()


def test_a_build_with_no_key_never_asks_for_the_update_source(server, published, monkeypatch):
    monkeypatch.setattr(assets, "RELEASE_PUBLIC_KEYS", ())
    _github_is_gone(server)
    assert github.latest_release() is None
    assert "/update-source.json" not in server.requests


def test_check_reports_a_release_that_can_be_applied(world, server, published):
    status = fetch.check(layout=world.layout, platform=PLATFORM, trusted_keys=TRUSTED)
    assert status["update_available"] and status["can_apply"]
    assert status["current_version"] == "0.1.0" and status["latest_version"] == RELEASE
    assert status["download_bytes"] > 0
    assert server.downloaded() == {assets.MANIFEST_FILE, assets.SIGNATURE_FILE, assets.SUMS_FILE}


def test_check_says_why_a_newer_release_cannot_be_applied(world, server, published, monkeypatch):
    monkeypatch.setattr(assets, "RELEASE_PUBLIC_KEYS", ())
    status = fetch.check(layout=world.layout, platform=PLATFORM, trusted_keys=None)
    assert status["update_available"] and not status["can_apply"]
    assert "no release signing key" in status["detail"]


# --- The command --------------------------------------------------------------


def _arguments(**values):
    return Namespace(**{"channel": None, "json": False, "check": False, "dry_run": False, "release_directory": None,
                        "upgrade_verb": None, **values})


@pytest.fixture
def version_store(monkeypatch):
    from types import SimpleNamespace

    from openprogram.operations.self_update.versions import layout as layouts

    monkeypatch.setattr(layouts, "current_runtime", lambda: SimpleNamespace(layout=None, version="v"))


def test_a_bare_upgrade_on_a_version_store_goes_through_the_fetcher(version_store, monkeypatch, capsys):
    from openprogram.interfaces.cli.commands.updates import upgrade

    calls = []
    outcome = replay.ReplayResult(outcome=replay.ACTIVATED, message="Updated to 0.2.0.", release="0.2.0")
    monkeypatch.setattr(fetch, "update", lambda **options: calls.append(options) or outcome)
    assert upgrade._cmd_upgrade(_arguments()) == 0
    assert len(calls) == 1 and "Updated to 0.2.0." in capsys.readouterr().out


def test_upgrade_check_on_a_version_store_reports_without_applying(version_store, monkeypatch, capsys):
    from openprogram.interfaces.cli.commands.updates import upgrade

    monkeypatch.setattr(fetch, "update", lambda **_: pytest.fail("a check must not update"))
    monkeypatch.setattr(fetch, "check", lambda: {
        "current_version": "0.1.0", "latest_version": "0.2.0", "update_available": True, "can_apply": True,
        "detail": "", "download_bytes": 1024 ** 2,
    })
    assert upgrade._cmd_upgrade(_arguments(check=True)) == 0
    output = capsys.readouterr().out
    assert "0.2.0" in output and "available" in output and "1.0 MB" in output


def test_upgrade_from_a_directory_still_works(version_store, monkeypatch, tmp_path):
    from openprogram.interfaces.cli.commands.updates import upgrade
    from openprogram.operations.self_update.control import official_update

    outcome = replay.ReplayResult(outcome=replay.UP_TO_DATE, message="Already there.", release="0.2.0")
    seen = []
    monkeypatch.setattr(official_update, "apply_release", lambda directory: seen.append(directory) or outcome)
    monkeypatch.setattr(fetch, "update", lambda **_: pytest.fail("--from must not download"))
    assert upgrade._cmd_upgrade(_arguments(release_directory=str(tmp_path))) == 0
    assert seen == [tmp_path]


def test_a_refused_release_exits_non_zero_with_the_reason(version_store, monkeypatch, capsys):
    from openprogram.interfaces.cli.commands.updates import upgrade

    def refuse(**_):
        raise ReleaseError("the release signature does not match a trusted key")

    monkeypatch.setattr(fetch, "update", refuse)
    assert upgrade._cmd_upgrade(_arguments(json=True)) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "release-refused"


@pytest.fixture
def no_version_store(monkeypatch):
    from openprogram.operations.self_update.versions import layout as layouts

    monkeypatch.setattr(layouts, "current_runtime", lambda: None)


def test_a_copy_inside_the_app_is_sent_to_settings(no_version_store, monkeypatch, capsys):
    from openprogram.interfaces.cli.commands.updates import upgrade
    from openprogram.operations.self_update.github_release import detect

    root = Path("/Applications/OpenProgram.app/Contents/Resources/runtime")
    monkeypatch.setattr(detect, "managed_runtime_root", lambda: root)
    monkeypatch.setattr(detect, "_in_version_store", lambda: False)
    assert upgrade._cmd_upgrade(_arguments()) == 1
    assert "Check for updates" in capsys.readouterr().out


def test_a_pip_install_is_told_how_to_reinstall(no_version_store, monkeypatch, capsys):
    from openprogram.interfaces.cli.commands.updates import upgrade
    from openprogram.operations.self_update.github_release import detect

    monkeypatch.setattr(detect, "managed_runtime_root", lambda: None)
    monkeypatch.setattr(detect, "_in_version_store", lambda: False)
    monkeypatch.setattr(detect, "repo_root", lambda: None)
    monkeypatch.delenv("OPENPROGRAM_IMMUTABLE_RUNTIME", raising=False)
    assert upgrade._cmd_upgrade(_arguments()) == 1
    assert "docs/install/install.html" in capsys.readouterr().out


def test_a_command_line_runtime_is_not_mistaken_for_the_app(monkeypatch):
    from openprogram.operations.self_update.github_release import detect

    monkeypatch.setattr(detect, "managed_runtime_root", lambda: Path("/home/me/.openprogram/runtime/cli"))
    monkeypatch.setattr(detect, "_in_version_store", lambda: False)
    monkeypatch.setattr(detect, "detect_install_method", lambda: detect.InstallMethod.MANAGED_RELEASE)
    assert detect.update_route() is detect.UpdateRoute.COMMAND_LINE_RELEASE


# --- The signing key ----------------------------------------------------------

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "release" / "create-update-assets.py"


def test_generate_key_writes_a_private_file_and_prints_only_the_public_value(tmp_path):
    path = tmp_path / "update-key.pem"
    done = subprocess.run([sys.executable, str(SCRIPT), "generate-key", str(path)],
                          capture_output=True, text=True, check=True)

    public = done.stdout.strip()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert public == assets.public_key_hex(path.read_bytes()) and len(public) == 64
    assert "PRIVATE" not in done.stdout and "PRIVATE" not in done.stderr


def test_generate_key_never_replaces_a_key(tmp_path):
    path = tmp_path / "update-key.pem"
    path.write_text("the only copy")
    done = subprocess.run([sys.executable, str(SCRIPT), "generate-key", str(path)], capture_output=True, text=True)
    assert done.returncode == 1 and "never replaced" in done.stderr
    assert path.read_text() == "the only copy"


def test_sign_file_writes_a_signature_the_trusted_key_accepts(tmp_path):
    key_path = tmp_path / "key.pem"
    public = assets.generate_signing_key(key_path)
    source = tmp_path / "update-source.json"
    source.write_text('{"schema": 1}')
    subprocess.run([sys.executable, str(SCRIPT), "sign-file", str(source), "--signing-key", str(key_path)], check=True)
    assets.verify_signature(source.read_bytes(), (tmp_path / "update-source.json.sig").read_text(), [public])
