"""What a person sees from the POSIX installers: downloads, disk, PATH, re-runs."""
from __future__ import annotations

import re
import hashlib
import http.server
import os
from pathlib import Path
import shutil
import subprocess
import threading

from ._support import POSIX_SHELL_INTEGRATION, ROOT, _copied_public_installer, _fake_posix_cli_archive, pytest

pytestmark = POSIX_SHELL_INTEGRATION

INSTALLER = ROOT / "scripts" / "release" / "install-release.sh"
BOOTSTRAP = ROOT / "docs" / "_static_root" / "install.sh"
VERSION = "0.6.7"


class ReleaseServer:
    """GitHub's redirect and the release file host, with a connection that can drop."""

    def __init__(self, archive: Path, digest: str, *, drop_after: int | None = None) -> None:
        self.files = {
            archive.name: archive.read_bytes(),
            archive.name + ".sha256": f"{digest}  {archive.name}\n".encode("ascii"),
        }
        self.drop_after = drop_after
        self.requests: list[tuple[str, str, str | None]] = []
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *arguments) -> None:
                pass

            def _reply(self, send_body: bool) -> None:
                owner.requests.append((self.command, self.path, self.headers.get("Range")))
                name = self.path.rsplit("/", 1)[-1]
                if self.path.startswith("/github/"):
                    if name not in owner.files:
                        self.send_response(404)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    self.send_response(302)
                    self.send_header("Location", f"https://release-assets.githubusercontent.com/{name}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                data = owner.files[name]
                requested = self.headers.get("Range")
                start = int(requested.split("=")[1].split("-")[0]) if requested else 0
                self.send_response(206 if requested else 200)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(len(data) - start))
                if requested:
                    self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                self.end_headers()
                if not send_body:
                    return
                body = data[start:]
                if owner.drop_after is not None and not name.endswith(".sha256"):
                    self.wfile.write(body[: owner.drop_after])
                    self.wfile.flush()
                    owner.drop_after = None
                    self.close_connection = True
                    self.connection.shutdown(2)
                    return
                self.wfile.write(body)

            def do_HEAD(self) -> None:
                self._reply(False)

            def do_GET(self) -> None:
                self._reply(True)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def body_requests(self) -> list[tuple[str, str, str | None]]:
        return [
            request for request in self.requests
            if request[0] == "GET" and not request[1].endswith(".sha256") and request[1].startswith("/assets/")
        ]


def _script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _tools(tmp_path: Path, port: int | None = None) -> Path:
    """A PATH front that sends the installer's HTTPS requests to the local server."""
    tools = tmp_path / "tools"
    tools.mkdir()
    _script(tools / "sleep", "exit 0\n")
    if port is not None:
        _script(
            tools / "curl",
            f'''count=$#
while [ "$count" -gt 0 ]; do
  argument="$1"; shift; count=$((count - 1))
  case "$argument" in
    --proto) shift; count=$((count - 1)); continue ;;
    --tlsv1.2|--disable) continue ;;
    https://github.com/*) argument="http://127.0.0.1:{port}/github/${{argument#https://github.com/}}" ;;
    https://release-assets.githubusercontent.com/*) argument="http://127.0.0.1:{port}/assets/${{argument#https://release-assets.githubusercontent.com/}}" ;;
  esac
  set -- "$@" "$argument"
done
exec {shutil.which("curl")} "$@"
''',
        )
    return tools


def _environment(tmp_path: Path, tools: Path, **extra: str) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith("OPENPROGRAM_") and key.lower() not in {"https_proxy", "http_proxy", "all_proxy"}
    }
    environment.update(
        {
            "PATH": f"{tools}:{os.environ['PATH']}",
            "HOME": str(home),
            "SHELL": "/bin/zsh",
            "TMPDIR": str(tmp_path),
            "OPENPROGRAM_VERSION": VERSION,
            "OPENPROGRAM_STATE_DIR": str(tmp_path / "state"),
            "OPENPROGRAM_BIN_DIR": str(tmp_path / "bin"),
        }
    )
    environment.pop("ZDOTDIR", None)
    environment.update(extra)
    return environment


def _run(environment: dict[str, str], script: Path = INSTALLER) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(script)], check=False, env=environment, capture_output=True, text=True, timeout=120
    )


def _archive_for_this_computer(tmp_path: Path) -> tuple[Path, str]:
    built, digest = _fake_posix_cli_archive(tmp_path / "fixture", manifest_version=VERSION)
    system = "macos" if os.uname().sysname == "Darwin" else "linux"
    machine = "arm64" if os.uname().machine in {"arm64", "aarch64"} else "x86_64"
    translated = subprocess.run(
        ["sysctl", "-n", "sysctl.proc_translated"], check=False, capture_output=True, text=True
    ).stdout.strip() if system == "macos" else ""
    if translated == "1":
        machine = "arm64"
    named = built.with_name(f"OpenProgram-{VERSION}-runtime-{system}-{machine}.tar.gz")
    built.rename(named)
    return named, digest


@pytest.fixture
def release(tmp_path: Path):
    archive, digest = _archive_for_this_computer(tmp_path)
    servers: list[ReleaseServer] = []

    def start(*, drop_after: int | None = None) -> ReleaseServer:
        server = ReleaseServer(archive, digest, drop_after=drop_after)
        servers.append(server)
        return server

    yield archive, digest, start
    for server in servers:
        server.close()


def test_interrupted_download_continues_from_where_it_stopped(tmp_path: Path, release) -> None:
    archive, _digest, start = release
    half = archive.stat().st_size // 2
    server = start(drop_after=half)

    result = _run(_environment(tmp_path, _tools(tmp_path, server.port)))

    assert result.returncode == 0, result.stderr
    bodies = server.body_requests()
    assert len(bodies) == 2
    assert bodies[0][2] is None
    assert bodies[1][2] == f"bytes={half}-"
    assert "Trying again from there" in result.stderr
    assert f"OpenProgram {VERSION} installed." in result.stdout
    assert not (tmp_path / "state" / ".install-downloads").exists()


def test_a_later_run_continues_an_earlier_partial_download(tmp_path: Path, release) -> None:
    archive, _digest, start = release
    server = start()
    downloads = tmp_path / "state" / ".install-downloads"
    downloads.mkdir(parents=True)
    kept = archive.stat().st_size // 3
    (downloads / archive.name).write_bytes(archive.read_bytes()[:kept])

    result = _run(_environment(tmp_path, _tools(tmp_path, server.port)))

    assert result.returncode == 0, result.stderr
    assert "Continuing the download" in result.stdout
    assert server.body_requests()[0][2] == f"bytes={kept}-"


def test_download_prints_its_size_and_shows_no_bar_without_a_terminal(tmp_path: Path, release) -> None:
    _archive, _digest, start = release
    server = start()
    tools = _tools(tmp_path, server.port)
    real_curl = (tools / "curl").read_text(encoding="utf-8")
    log = tmp_path / "curl-arguments"
    (tools / "curl").write_text(real_curl.replace("count=$#", f'printf "%s\\n" "$*" >> {log}\ncount=$#', 1), encoding="utf-8")

    result = _run(_environment(tmp_path, tools))

    assert result.returncode == 0, result.stderr
    assert f"Downloading OpenProgram {VERSION} for" in result.stdout
    assert " MB)." in result.stdout
    arguments = log.read_text(encoding="utf-8")
    assert "--retry 3" in arguments
    assert "--continue-at -" in arguments
    assert "--progress-bar" not in arguments


def test_only_a_terminal_gets_the_progress_bar() -> None:
    source = INSTALLER.read_text(encoding="utf-8")
    assert 'if [ -n "$label" ] && [ -t 2 ]; then\n    progress_option="--progress-bar"' in source


def test_too_little_disk_stops_before_any_of_the_archive_is_fetched(tmp_path: Path, release) -> None:
    archive, _digest, start = release
    server = start()
    tools = _tools(tmp_path, server.port)
    _script(tools / "df", "printf 'Filesystem 1024-blocks Used Available Capacity Mounted\\n/dev/x 1 1 0 100% /\\n'\n")

    result = _run(_environment(tmp_path, tools))

    assert result.returncode == 1
    assert server.body_requests() == []
    assert "Not enough free disk space" in result.stderr
    assert "four times" in result.stderr
    assert not (tmp_path / "state" / "runtime" / "cli" / "current").exists()
    assert archive.exists()


def test_too_little_disk_also_stops_a_local_archive(tmp_path: Path, release) -> None:
    archive, digest, _start = release
    tools = _tools(tmp_path)
    _script(tools / "df", "printf 'Filesystem 1024-blocks Used Available Capacity Mounted\\n/dev/x 1 1 0 100% /\\n'\n")

    result = _run(
        _environment(tmp_path, tools, OPENPROGRAM_RUNTIME_ARCHIVE=str(archive), OPENPROGRAM_RUNTIME_SHA256=digest)
    )

    assert result.returncode == 1
    assert "Not enough free disk space" in result.stderr


def test_the_disk_check_keeps_the_same_margin_as_the_app(tmp_path: Path, release) -> None:
    archive, digest, _start = release
    tools = _tools(tmp_path)
    _script(tools / "df", "printf 'Filesystem 1024-blocks Used Available Capacity Mounted\\n/dev/x 1 1 102400 1% /\\n'\n")

    result = _run(
        _environment(tmp_path, tools, OPENPROGRAM_RUNTIME_ARCHIVE=str(archive), OPENPROGRAM_RUNTIME_SHA256=digest)
    )

    assert result.returncode == 1
    assert "512 MB to spare" in result.stderr
    # The need and the free amount can print as the same figure; the
    # difference is always named.
    assert re.search(r"is free, (\d+ MB|[\d.]+ GB|less than 1 MB) short\.", result.stderr)


def test_a_damaged_download_is_removed_and_says_to_run_again(tmp_path: Path, release) -> None:
    archive, _digest, start = release
    server = start()
    server.files[archive.name + ".sha256"] = f"{'0' * 64}  {archive.name}\n".encode("ascii")

    result = _run(_environment(tmp_path, _tools(tmp_path, server.port)))

    assert result.returncode == 1
    assert "The damaged download was removed" in result.stderr
    assert not (tmp_path / "state" / ".install-downloads" / archive.name).exists()


def test_a_redirect_to_another_host_is_refused(tmp_path: Path, release) -> None:
    _archive, _digest, start = release
    server = start()
    tools = _tools(tmp_path, server.port)
    real_curl = (tools / "curl").read_text(encoding="utf-8")
    (tools / "curl").write_text(
        real_curl.replace(
            'exec ',
            'case "$*" in *--head*/assets/*) '
            'printf "HTTP/1.1 302 Found\\r\\nLocation: https://elsewhere.example/x\\r\\n" > "$DUMP"; printf 302; exit 0 ;; esac\nexec ',
            1,
        ).replace("count=$#", 'previous=""\nfor word in "$@"; do [ "$previous" = "--dump-header" ] && DUMP="$word"; previous="$word"; done\ncount=$#', 1),
        encoding="utf-8",
    )

    result = _run(_environment(tmp_path, tools))

    assert result.returncode == 1
    assert "release URL is not allowed: https://elsewhere.example/x" in result.stderr


def test_a_network_failure_names_the_proxy_variables(tmp_path: Path, release) -> None:
    _archive, _digest, _start = release
    tools = _tools(tmp_path)
    _script(tools / "curl", "printf 'curl: (7) Failed to connect\\n' >&2\nexit 7\n")

    result = _run(_environment(tmp_path, tools))

    assert result.returncode == 1
    assert "HTTPS_PROXY" in result.stderr
    assert "NO_PROXY" in result.stderr


def _local_install(tmp_path: Path, release, **extra: str) -> subprocess.CompletedProcess[str]:
    archive, digest, _start = release
    environment = _environment(
        tmp_path, _tools(tmp_path), OPENPROGRAM_RUNTIME_ARCHIVE=str(archive), OPENPROGRAM_RUNTIME_SHA256=digest, **extra
    )
    environment.pop("OPENPROGRAM_BIN_DIR")
    return _run(environment)


def test_install_adds_one_marked_line_to_the_profile_and_ends_with_the_next_command(tmp_path: Path, release) -> None:
    result = _local_install(tmp_path, release)

    assert result.returncode == 0, result.stderr
    profile = tmp_path / "home" / ".zshrc"
    assert profile.read_text(encoding="utf-8") == (
        'export PATH="$HOME/.local/bin:$PATH" # Added by the OpenProgram installer\n'
    )
    lines = result.stdout.strip().splitlines()
    assert lines[-2].startswith("PATH: added ")
    assert lines[-1] == "Next, open a new terminal and run: openprogram"


def test_running_the_installer_twice_keeps_one_profile_line(tmp_path: Path, release) -> None:
    profile = tmp_path / "home" / ".zshrc"
    profile.parent.mkdir()
    profile.write_text("alias ll='ls -l'", encoding="utf-8")

    assert _local_install(tmp_path, release).returncode == 0
    (tmp_path / "tools").rename(tmp_path / "tools-first")
    second = _local_install(tmp_path, release)

    assert second.returncode == 0, second.stderr
    contents = profile.read_text(encoding="utf-8")
    assert contents.startswith("alias ll='ls -l'\n")
    assert contents.count("# Added by the OpenProgram installer") == 1
    assert "already adds" in second.stdout


def test_the_profile_opt_out_is_honoured(tmp_path: Path, release) -> None:
    result = _local_install(tmp_path, release, OPENPROGRAM_NO_MODIFY_PATH="1")

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "home" / ".zshrc").exists()
    lines = result.stdout.strip().splitlines()
    assert "is not on your PATH. To add it, run: export PATH=" in lines[-2]
    assert lines[-1].startswith("Next, run: ") and lines[-1].endswith("/.local/bin/openprogram")


def test_a_launcher_directory_already_on_path_changes_no_profile(tmp_path: Path, release) -> None:
    archive, digest, _start = release
    bin_directory = (tmp_path / "bin").resolve()
    tools = _tools(tmp_path)
    environment = _environment(
        tmp_path, tools, OPENPROGRAM_RUNTIME_ARCHIVE=str(archive), OPENPROGRAM_RUNTIME_SHA256=digest
    )
    environment["PATH"] = f"{bin_directory}:{environment['PATH']}"

    result = _run(environment)

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "home" / ".zshrc").exists()
    assert result.stdout.strip().splitlines()[-1] == "Next, run: openprogram"


def _store(tmp_path: Path, upgrade_body: str, *, start_exit: int = 0, with_plain_runtime: bool = False) -> Path:
    store = tmp_path / "state" / "runtime"
    (store / "bin").mkdir(parents=True)
    (store / "current").write_text("schema=1\nversion=first\n", encoding="ascii")
    _script(
        store / "bin" / "openprogram",
        f'''root="{store}"
case "$1" in
  --version) printf 'openprogram %s\\n' "$(sed -n 's/^version=//p' "$root/current")" ;;
  upgrade) {upgrade_body} ;;
esac
''',
    )
    for version in ("first", "second"):
        python = store / "versions" / version / "venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        _script(
            python,
            f'if [ "$#" -eq 3 ]; then printf "23458\\n"; exit 0; fi\n'
            f'if [ "${{4:-}}" = start ]; then exit {start_exit}; fi\nexit 0\n',
        )
    if with_plain_runtime:
        (store / "cli" / "releases").mkdir(parents=True)
        (store / "cli" / "marker").write_text("working install\n", encoding="ascii")
    return store


def _store_run(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    seed = tmp_path / "unused-seed.tar.gz"
    seed.write_bytes(b"not read when a store exists")
    return _run(
        _environment(
            tmp_path, _tools(tmp_path), OPENPROGRAM_SEED_ARCHIVE=str(seed),
            OPENPROGRAM_SEED_SHA256=hashlib.sha256(seed.read_bytes()).hexdigest(),
        )
    )


def test_rerun_on_a_store_with_nothing_new_says_nothing_was_activated(tmp_path: Path) -> None:
    _store(tmp_path, "printf 'Already up to date.\\n'")

    result = _store_run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "Already up to date." in result.stdout
    assert "No new version was activated. The active version is still openprogram first." in result.stdout
    assert f"OpenProgram {VERSION} installed." not in result.stdout
    assert (tmp_path / "bin" / "openprogram").is_file()
    assert result.stdout.strip().splitlines()[-1].startswith("Next, run: ")


def test_rerun_on_a_store_reports_the_version_upgrade_activated(tmp_path: Path) -> None:
    store = _store(tmp_path, "")
    command = store / "bin" / "openprogram"
    command.write_text(
        command.read_text(encoding="utf-8").replace(
            "upgrade)  ;;", 'upgrade) printf "schema=1\\nversion=second\\n" > "$root/current" ;;'
        ),
        encoding="utf-8",
    )

    result = _store_run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "OpenProgram updated. The active version is now openprogram second." in result.stdout


def test_rerun_on_a_store_reports_a_refused_update_and_keeps_its_exit_code(tmp_path: Path) -> None:
    _store(
        tmp_path,
        "printf 'This install takes a release from files.\\n'; exit 3",
        with_plain_runtime=True,
    )

    result = _store_run(tmp_path)

    assert result.returncode == 3
    assert "This install takes a release from files." in result.stdout
    assert f"OpenProgram {VERSION} was not installed" in result.stderr
    assert "The active version is still openprogram first." in result.stderr
    assert f"OpenProgram {VERSION} installed." not in result.stdout
    assert (tmp_path / "state" / "runtime" / "cli" / "marker").is_file()


def test_the_plain_runtime_goes_only_after_the_store_answers(tmp_path: Path) -> None:
    store = _store(tmp_path, "printf 'Already up to date.\\n'", with_plain_runtime=True)

    result = _store_run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert not (store / "cli").exists()


def test_the_plain_runtime_stays_when_the_store_does_not_start(tmp_path: Path) -> None:
    store = _store(tmp_path, "printf 'Already up to date.\\n'", start_exit=9, with_plain_runtime=True)

    result = _store_run(tmp_path)

    assert result.returncode == 9
    assert (store / "cli" / "marker").is_file()
    assert "was kept and still works" in result.stderr


def _fake_system(tools: Path, system: str, machine: str) -> None:
    _script(tools / "uname", f'case "$1" in -s) printf "{system}\\n" ;; -m) printf "{machine}\\n" ;; esac\n')


def test_rosetta_terminal_installs_the_apple_silicon_build(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    _fake_system(tools, "Darwin", "x86_64")
    _script(tools / "sysctl", 'printf "1\\n"\n')
    log = tmp_path / "urls"
    _script(tools / "curl", f'for word in "$@"; do case "$word" in https://*) printf "%s\\n" "$word" >> {log} ;; esac; done\nexit 7\n')

    result = _run(_environment(tmp_path, tools))

    assert result.returncode == 1
    assert "Rosetta" in result.stdout
    urls = log.read_text(encoding="utf-8")
    assert "runtime-macos-arm64" in urls or "seed-macos-arm64" in urls
    assert "macos-x86_64" not in urls


def _preflight(tmp_path: Path, tools: Path) -> subprocess.CompletedProcess[str]:
    source = INSTALLER.read_text(encoding="utf-8")
    start = source.index("linux_preflight() {")
    function = source[start : source.index('\n\nif [ "$platform" = "linux" ]; then', start)]
    driver = tmp_path / "preflight.sh"
    driver.write_text(f"set -eu\n{function}\nlinux_preflight\n", encoding="utf-8")
    for tool in ("grep", "sed", "tr"):
        os.symlink(shutil.which(tool), tools / tool)
    return subprocess.run(
        [shutil.which("sh"), str(driver)], check=False, capture_output=True, text=True,
        env={"PATH": str(tools), "HOME": str(tmp_path)},
    )


def _linux_tools(tmp_path: Path, cache: str = "", *, apt: bool = True, time64: bool = False) -> Path:
    tools = _tools(tmp_path)
    _script(tools / "ldd", "printf 'ldd (GNU libc) 2.39\\n'\n")
    _script(tools / "ldconfig", f"printf '%s\\n' '{cache}'\n")
    if apt:
        _script(tools / "apt-get", "exit 0\n")
        _script(tools / "apt-cache", f"exit {0 if time64 else 1}\n")
    return tools


def test_musl_linux_is_refused_before_any_download(tmp_path: Path) -> None:
    tools = _linux_tools(tmp_path)
    _script(tools / "ldd", "printf 'musl libc (x86_64)\\n' >&2; exit 1\n")

    result = _preflight(tmp_path, tools)

    assert result.returncode == 1
    assert "uses musl" in result.stderr
    source = INSTALLER.read_text(encoding="utf-8")
    assert source.index('if [ "$platform" = "linux" ]; then\n  linux_preflight') < source.index("seed_available()")


def test_missing_browser_libraries_print_the_exact_apt_line_for_time64_systems(tmp_path: Path) -> None:
    tools = _linux_tools(tmp_path, cache="libnss3.so (libc6,x86-64) => /usr/lib/libnss3.so", time64=True)
    _script(tools / "git", "exit 0\n")

    result = _preflight(tmp_path, tools)

    assert result.returncode == 0
    # One line: what will not work, that the rest does, and how to add it later.
    line = next(item for item in result.stderr.splitlines() if item.startswith("The browser tool will not work"))
    assert "everything else does" in line
    assert "Add them any time with: sudo apt-get update && sudo apt-get install -y " in line
    assert " libasound2t64" in line and " libatspi2.0-0t64" in line
    assert " libnss3" not in line
    assert "Git was not found" not in result.stderr


def test_older_systems_get_the_plain_package_names(tmp_path: Path) -> None:
    result = _preflight(tmp_path, _linux_tools(tmp_path, cache="libnss3.so"))

    line = next(item for item in result.stderr.splitlines() if item.startswith("The browser tool will not work"))
    assert " libasound2" in line
    assert "t64" not in line


def test_absent_git_is_no_warning_because_the_install_carries_its_own(tmp_path: Path) -> None:
    result = _preflight(tmp_path, _linux_tools(tmp_path, cache="libnss3.so"))

    assert result.returncode == 0
    assert "Git was not found" not in result.stderr


@pytest.mark.parametrize(("time64", "package"), [(False, "libcurl4"), (True, "libcurl4t64")])
def test_absent_libcurl_names_the_package_for_https_git(tmp_path: Path, time64: bool, package: str) -> None:
    result = _preflight(tmp_path, _linux_tools(tmp_path, cache="libnss3.so", time64=time64))

    assert "Git over HTTPS needs libcurl" in result.stderr
    assert f"sudo apt-get install -y {package}\n" in result.stderr


@pytest.mark.parametrize(("cache", "warned"), [("libcurl-gnutls.so.4", True), ("libcurl.so.4", False)])
def test_only_the_libcurl_the_bundled_git_links_against_counts(tmp_path: Path, cache: str, warned: bool) -> None:
    result = _preflight(tmp_path, _linux_tools(tmp_path, cache=cache))

    assert ("Git over HTTPS needs libcurl" in result.stderr) is warned


def test_wrapper_without_a_version_says_what_to_set(tmp_path: Path) -> None:
    environment = _environment(tmp_path, _tools(tmp_path))
    environment.pop("OPENPROGRAM_VERSION")

    result = _run(environment, _copied_public_installer(tmp_path))

    assert result.returncode == 1
    assert "No OpenProgram version was given" in result.stderr
    assert "OPENPROGRAM_VERSION" in result.stderr


def _bootstrap_tools(tmp_path: Path, manifest: str, system: str, machine: str) -> tuple[Path, Path]:
    tools = _tools(tmp_path)
    _fake_system(tools, system, machine)
    _script(tools / "sysctl", 'printf "0\\n"\n')
    log = tmp_path / "bootstrap-urls"
    (tmp_path / "manifest.json").write_text(manifest, encoding="utf-8")
    _script(
        tools / "curl",
        f'''output=""; url=""
while [ "$#" -gt 0 ]; do
  case "$1" in -o) output="$2"; shift 2 ;; https://*) url="$1"; shift ;; *) shift ;; esac
done
printf '%s\\n' "$url" >> {log}
case "$url" in
  */release-manifest.json) cat {tmp_path / "manifest.json"} ;;
  */scripts/install-release.sh) printf 'exit 0\\n' > "$output" ;;
esac
''',
    )
    return tools, log


def test_bootstrap_names_the_platform_a_release_does_not_cover(tmp_path: Path) -> None:
    tools, log = _bootstrap_tools(
        tmp_path,
        '{"files":[{"path":"x/OpenProgram-1.2.3-runtime-macos-arm64.tar.gz"}]}',
        "Linux",
        "x86_64",
    )

    result = _run(_environment(tmp_path, tools, OPENPROGRAM_VERSION="1.2.3"), BOOTSTRAP)

    assert result.returncode == 1
    assert "OpenProgram 1.2.3 has no command-line package for Linux on x86_64." in result.stderr
    assert "https://github.com/FuzzyKuji/OpenProgram/releases/tag/v1.2.3" in result.stderr
    assert "install-release.sh" not in log.read_text(encoding="utf-8")


def test_bootstrap_continues_when_the_manifest_lists_this_platform(tmp_path: Path) -> None:
    tools, log = _bootstrap_tools(
        tmp_path,
        '{"files":[{"path":"x/OpenProgram-1.2.3-seed-linux-arm64.tar.gz"}]}',
        "Linux",
        "aarch64",
    )

    result = _run(_environment(tmp_path, tools, OPENPROGRAM_VERSION="1.2.3"), BOOTSTRAP)

    assert result.returncode == 0, result.stderr
    assert "scripts/install-release.sh" in log.read_text(encoding="utf-8")


def test_error_paths_name_the_proxy_in_the_bootstrap_too() -> None:
    assert "HTTPS_PROXY" in BOOTSTRAP.read_text(encoding="utf-8")


BASE_ID = "py3.12-0123456789abcdef"


def _seed(tmp_path: Path) -> tuple[Path, str]:
    """A seed whose base Python stands in for install_seed.py and makes the launcher."""
    work = tmp_path / "seed-fixture"
    python = work / "base" / "bases" / BASE_ID / "python" / "bin" / "python3"
    python.parent.mkdir(parents=True)
    _script(
        python,
        'root=""\n'
        'while [ "$#" -gt 0 ]; do [ "$1" = --root ] && root="$2"; shift; done\n'
        'mkdir -p "$root/bin"\n'
        'printf \'#!/bin/sh\\necho 1.0\\n\' > "$root/bin/openprogram"\n'
        'chmod +x "$root/bin/openprogram"\n'
        'rm -f "$root/.seeding"\n',
    )
    seed = work / "seed"
    (seed / "bootstrap").mkdir(parents=True)
    (seed / "bootstrap" / "install_seed.py").write_text("", encoding="utf-8")
    (seed / "seed.json").write_text(f'{{\n  "base": "{BASE_ID}",\n}}\n', encoding="utf-8")
    subprocess.run(["tar", "-C", str(work / "base"), "-czf", str(seed / "base.tar.gz"), "bases"], check=True)
    archive = tmp_path / "seed.tar.gz"
    subprocess.run(["tar", "-C", str(work), "-czf", str(archive), "seed"], check=True)
    return archive, hashlib.sha256(archive.read_bytes()).hexdigest()


def test_an_interrupted_seed_install_leaves_no_half_base_and_a_rerun_finishes(tmp_path: Path) -> None:
    archive, digest = _seed(tmp_path)
    real_tar = shutil.which("tar")
    tools = _tools(tmp_path)
    # Stops part way through the inner base archive, having written one file.
    _script(
        tools / "tar",
        f'''case "$*" in
  *base.tar.gz*)
    while [ "$#" -gt 0 ]; do [ "$1" = -C ] && destination="$2"; shift; done
    mkdir -p "$destination/bases/{BASE_ID}/python"
    : > "$destination/bases/{BASE_ID}/python/half"
    exit 1
    ;;
esac
exec {real_tar} "$@"
''',
    )
    environment = _environment(
        tmp_path, tools, OPENPROGRAM_SEED_ARCHIVE=str(archive), OPENPROGRAM_SEED_SHA256=digest
    )
    store = tmp_path / "state" / "runtime"

    interrupted = _run(environment)

    assert interrupted.returncode != 0
    assert not (store / "bases" / BASE_ID).exists(), "a base that is not whole never reaches its final name"

    (tools / "tar").unlink()
    finished = _run(environment)

    assert finished.returncode == 0, finished.stderr
    assert (store / "bases" / BASE_ID / "python" / "bin" / "python3").is_file()
    assert not (store / "bases" / BASE_ID / "python" / "half").exists()
    assert not (store / ".seed-work").exists()
