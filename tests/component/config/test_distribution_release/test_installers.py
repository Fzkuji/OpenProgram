"""release installers tests."""
from __future__ import annotations
from ._support import (
    POSIX_SHELL_INTEGRATION,
    Path,
    ROOT,
    _copied_public_installer,
    _fake_posix_cli_archive,
    json,
    os,
    pytest,
    subprocess,
    sys,
)


def test_release_installer_is_versioned_and_source_free() -> None:
    installer = (ROOT / "scripts" / "release" / "install-release.sh").read_text(encoding="utf-8")
    assert "OPENPROGRAM_RUNTIME_ARCHIVE" in installer
    assert "runtime-${platform}-${arch}.tar.gz" in installer
    assert "runtime-manifest.json" in installer
    assert "verify-product-runtime.py" in installer
    assert "OPENPROGRAM_WHEEL" not in installer
    assert "pypi" not in installer.lower()
    assert "pip install" not in installer
    assert "git clone" not in installer
    assert "pip install -e" not in installer
    assert "npm" not in installer



def test_release_installer_cold_starts_before_switching_current() -> None:
    installer = (ROOT / "scripts" / "release" / "install-release.sh").read_text(encoding="utf-8")
    assert 'probe_home="$(mktemp -d "${TMPDIR:-/tmp}/openprogram-release-probe.XXXXXX")"' in installer
    assert '$release_dir/.probe-home' not in installer
    assert 'XDG_CONFIG_HOME="$probe_home/.config"' in installer
    assert 'OPENPROGRAM_STATE_DIR="$probe_home/.openprogram"' in installer
    assert "OPENPROGRAM_PROFILE=" in installer
    assert "OPENPROGRAM_NO_WEB=" in installer
    assert 'OPENPROGRAM_WORKDIR="$probe_home"' in installer
    assert "-m openprogram worker start" not in installer
    probe = installer.index("probe_active=1")
    start = installer.index(
        "spawn_detached(prefer_service=False, on_spawn=record_probe_pid)", probe
    )
    health = installer.index("/healthz", start)
    stop = installer.index("stop_probe_process_group", health)
    publish = installer.index("os.replace(candidate_path, release_path)", stop)
    switch = installer.index("os.replace(next_link, current_path)", publish)
    assert start < health < stop < publish < switch



@POSIX_SHELL_INTEGRATION
def test_public_release_installer_downloads_same_tag_implementation(
    tmp_path: Path,
) -> None:
    wrapper = _copied_public_installer(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        "#!/bin/sh\noutput=\nurl=\n"
        "while [ \"$#\" -gt 0 ]; do case \"$1\" in "
        "--output) output=\"$2\"; shift 2 ;; https://*) url=\"$1\"; shift ;; "
        "*) shift ;; esac; done\n"
        "printf '%s\\n' \"$url\" > \"$FAKE_CURL_LOG\"\n"
        "printf '#!/bin/sh\nprintf \"%%s|%%s\\\\n\" \"$OPENPROGRAM_VERSION\" \"$OPENPROGRAM_REPOSITORY\" > \"$FAKE_RESULT\"\\n' > \"$output\"\n",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    result = tmp_path / "result"
    curl_log = tmp_path / "curl.log"
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "OPENPROGRAM_VERSION": "1.2.3",
        "OPENPROGRAM_REPOSITORY": "Example/OpenProgram",
        "FAKE_CURL_LOG": str(curl_log),
        "FAKE_RESULT": str(result),
    }

    subprocess.run(["sh", str(wrapper)], check=True, env=env)

    assert curl_log.read_text(encoding="utf-8").strip() == (
        "https://raw.githubusercontent.com/Example/OpenProgram/"
        "v1.2.3/scripts/release/install-release.sh"
    )
    assert result.read_text(encoding="utf-8") == "1.2.3|Example/OpenProgram\n"



@POSIX_SHELL_INTEGRATION
@pytest.mark.parametrize("version", ["1", "1.2", "1.2.3.4", "1.2.x"])
def test_public_release_installer_rejects_non_release_versions(
    tmp_path: Path, version: str
) -> None:
    result = subprocess.run(
        ["sh", str(_copied_public_installer(tmp_path))],
        check=False,
        env=os.environ | {"OPENPROGRAM_VERSION": version},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert f"invalid OpenProgram version: {version}" in result.stderr



@POSIX_SHELL_INTEGRATION
@pytest.mark.parametrize(
    "installer",
    ["scripts/install-release.sh", "scripts/release/install-release.sh"],
)
@pytest.mark.parametrize(
    "repository", ["repo", "owner/repo/extra", "/repo", "owner/", "owner/re po"]
)
def test_posix_release_installers_require_one_owner_repo_pair(
    tmp_path: Path, installer: str, repository: str
) -> None:
    result = subprocess.run(
        ["sh", str(ROOT / installer)],
        check=False,
        env=os.environ
        | {
            "HOME": str(tmp_path / "home"),
            "OPENPROGRAM_VERSION": "1.2.3",
            "OPENPROGRAM_REPOSITORY": repository,
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert f"invalid OpenProgram repository: {repository}" in result.stderr



@POSIX_SHELL_INTEGRATION
def test_public_release_installer_dispatches_to_checkout_implementation(
    tmp_path: Path,
) -> None:
    scripts = tmp_path / "scripts"
    release_scripts = scripts / "release"
    release_scripts.mkdir(parents=True)
    wrapper = scripts / "install-release.sh"
    wrapper.write_text(
        (ROOT / "scripts" / "install-release.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (release_scripts / "install-release.sh").write_text(
        "#!/bin/sh\nprintf '%s|%s|%s\\n' \"$OPENPROGRAM_VERSION\" \"$1\" \"$2\" > \"$RESULT\"\nexit 23\n",
        encoding="utf-8",
    )
    output = tmp_path / "result"
    result = subprocess.run(
        ["sh", str(wrapper), "first", "second"],
        check=False,
        env=os.environ
        | {"OPENPROGRAM_VERSION": "1.2.3", "RESULT": str(output)},
    )
    assert result.returncode == 23
    assert output.read_text(encoding="utf-8") == "1.2.3|first|second\n"



@POSIX_SHELL_INTEGRATION
def test_public_release_installer_stops_on_term(tmp_path: Path) -> None:
    wrapper = _copied_public_installer(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        "#!/bin/sh\noutput=\n"
        "while [ \"$#\" -gt 0 ]; do case \"$1\" in "
        "--output) output=\"$2\"; shift 2 ;; *) shift ;; esac; done\n"
        "printf '#!/bin/sh\\ntouch \"$SHOULD_NOT_RUN\"\\n' > \"$output\"\n"
        "kill -TERM \"$PPID\"\n",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    marker = tmp_path / "installer-ran"
    result = subprocess.run(
        ["sh", str(wrapper)],
        check=False,
        env=os.environ
        | {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "OPENPROGRAM_VERSION": "1.2.3",
            "SHOULD_NOT_RUN": str(marker),
        },
    )
    assert result.returncode == 143
    assert not marker.exists()



@POSIX_SHELL_INTEGRATION
def test_release_installer_replaces_an_existing_current_symlink(tmp_path: Path) -> None:
    state_root = tmp_path / 'state with "quote"'
    runtime_root = state_root / "runtime" / "cli"
    old_release = runtime_root / "releases" / "0.6.6"
    old_release.mkdir(parents=True)
    (runtime_root / "current").symlink_to(old_release)

    archive_root = tmp_path / "archive" / "runtime"
    (archive_root / "python" / "bin").mkdir(parents=True)
    (archive_root / "bin").mkdir()
    fake_python = archive_root / "python" / "bin" / "python3"
    fake_python.write_text(
        "#!/bin/sh\n"
        "if [ \"$#\" -eq 3 ] && [ \"$3\" = - ]; then printf '23456\\n'; exit 0; fi\n"
        f"if [ \"$#\" -eq 4 ] && [ \"$3\" = - ]; then case \"$4\" in */runtime-manifest.json) exec {sys.executable!r} \"$@\" ;; esac; fi\n"
        f"if [ \"$#\" -ge 5 ] && [ \"$3\" = - ]; then exec {sys.executable!r} \"$@\"; fi\n"
        "case \"$*\" in\n"
        "  *'openprogram --version'*) printf 'openprogram 0.6.7\\n' ;;\n"
        "  *) : ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    (archive_root / "bin" / "verify-product-runtime.py").write_text(
        "# acceptance fixture\n", encoding="utf-8"
    )
    (archive_root / "bin" / "python").symlink_to("../python/bin/python3")
    (archive_root / "runtime-manifest.json").write_text(
        json.dumps(
            {"openprogram": "0.6.7", "python": "python/bin/python3"}, indent=2
        ),
        encoding="utf-8",
    )
    archive = tmp_path / "OpenProgram-0.6.7-runtime-macos-arm64.tar.gz"
    subprocess.run(
        ["tar", "-C", str(tmp_path / "archive"), "-czf", str(archive), "runtime"],
        check=True,
    )
    import hashlib

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    launcher_dir = tmp_path / 'bin with "quote"'
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path / "home"),
        "TMPDIR": str(tmp_path),
        "LC_ALL": "C",
        "OPENPROGRAM_VERSION": "0.6.7",
        "OPENPROGRAM_STATE_DIR": str(state_root),
        "OPENPROGRAM_BIN_DIR": str(launcher_dir),
        "OPENPROGRAM_RUNTIME_ARCHIVE": str(archive),
        # Installer comparisons are hexadecimal, not case-sensitive text.
        "OPENPROGRAM_RUNTIME_SHA256": digest.upper(),
    }

    launcher_dir.write_text("not a directory", encoding="utf-8")
    failed = subprocess.run(
        ["sh", str(ROOT / "scripts" / "install-release.sh")],
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )
    assert failed.returncode != 0
    assert (runtime_root / "current").resolve() == old_release
    launcher_dir.unlink()

    subprocess.run(
        ["sh", str(ROOT / "scripts" / "install-release.sh")],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )

    assert (runtime_root / "current").resolve() == runtime_root / "releases" / "0.6.7"
    assert (launcher_dir / "openprogram").is_file()
    launcher_text = (launcher_dir / "openprogram").read_text(encoding="utf-8")
    assert 'export OPENPROGRAM_STATE_DIR="$state_root"' in launcher_text
    assert 'export OPENPROGRAM_BIN_DIR="$launcher_dir"' in launcher_text
    assert str(state_root.resolve()) in launcher_text
    assert str(launcher_dir.resolve()) in launcher_text
    launched = subprocess.run(
        [str(launcher_dir / "openprogram"), "--version"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    assert launched.stdout == "openprogram 0.6.7\n"



@POSIX_SHELL_INTEGRATION
def test_release_installer_rejects_archive_version_mismatch_before_publish(
    tmp_path: Path,
) -> None:
    archive, digest = _fake_posix_cli_archive(
        tmp_path / "wrong-version", manifest_version="0.6.6"
    )
    state = tmp_path / "state"
    result = subprocess.run(
        ["sh", str(ROOT / "scripts" / "release" / "install-release.sh")],
        check=False,
        env=os.environ
        | {
            "HOME": str(tmp_path / "home"),
            "OPENPROGRAM_VERSION": "0.6.7",
            "OPENPROGRAM_STATE_DIR": str(state),
            "OPENPROGRAM_BIN_DIR": str(tmp_path / "bin"),
            "OPENPROGRAM_RUNTIME_ARCHIVE": str(archive),
            "OPENPROGRAM_RUNTIME_SHA256": digest,
        },
        capture_output=True,
        text=True,
    )

    runtime_root = state / "runtime" / "cli"
    assert result.returncode != 0
    assert (
        "runtime version 0.6.6 does not match requested OpenProgram 0.6.7"
        in result.stderr
    )
    assert not (runtime_root / "releases" / "0.6.7").exists()
    assert not (runtime_root / "current").exists()
    assert list(runtime_root.glob(".staging-*")) == []



@POSIX_SHELL_INTEGRATION
def test_release_installer_probe_ignores_caller_service_and_no_web(
    tmp_path: Path,
) -> None:
    archive, digest = _fake_posix_cli_archive(
        tmp_path / "archive-fixture", manifest_version="0.6.7"
    )
    caller_home = tmp_path / "caller-home"
    caller_xdg = tmp_path / "caller-xdg"
    caller_unit = caller_xdg / "systemd" / "user" / "openprogram-worker.service"
    caller_unit.parent.mkdir(parents=True)
    caller_unit.write_text("caller unit must remain untouched\n", encoding="utf-8")
    probe_log = tmp_path / "probe-environment"

    subprocess.run(
        ["sh", str(ROOT / "scripts" / "release" / "install-release.sh")],
        check=True,
        env=os.environ
        | {
            "HOME": str(caller_home),
            "XDG_CONFIG_HOME": str(caller_xdg),
            "OPENPROGRAM_STATE_DIR": str(tmp_path / "release-state"),
            "OPENPROGRAM_PROFILE": "caller-profile",
            "OPENPROGRAM_NO_WEB": "1",
            "OPENPROGRAM_WORKDIR": str(tmp_path / "caller-workdir"),
            "OPENPROGRAM_VERSION": "0.6.7",
            "OPENPROGRAM_BIN_DIR": str(tmp_path / "bin"),
            "OPENPROGRAM_RUNTIME_ARCHIVE": str(archive),
            "OPENPROGRAM_RUNTIME_SHA256": digest,
            "PROBE_ENV_LOG": str(probe_log),
        },
        capture_output=True,
        text=True,
    )

    values = probe_log.read_text(encoding="utf-8").splitlines()
    probe_home, xdg_config, state_dir, profile, no_web, workdir, cwd, unit = values
    assert probe_home != str(caller_home)
    assert Path(xdg_config) == Path(probe_home) / ".config"
    assert Path(state_dir) == Path(probe_home) / ".openprogram"
    assert profile == ""
    assert no_web == ""
    assert workdir == probe_home
    assert Path(cwd).resolve() == Path(probe_home).resolve()
    assert unit == "unit-hidden"
    assert caller_unit.read_text(encoding="utf-8") == (
        "caller unit must remain untouched\n"
    )



def test_posix_release_installer_has_one_atomic_install_transaction() -> None:
    installer = (ROOT / "scripts" / "release" / "install-release.sh").read_text(
        encoding="utf-8"
    )
    assert 'install_lock="$runtime_root/.install.lock"' in installer
    assert "fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)" in installer
    assert 'ln "$lock_token" "$install_lock"' not in installer
    assert 'probe_home="$(mktemp -d "${TMPDIR:-/tmp}/openprogram-release-probe.XXXXXX")"' in installer
    assert 'listener.bind(("127.0.0.1", 0))' in installer
    assert 'raise SystemExit("worker health probe never returned status=ok")' in installer
    assert "spawn_detached(prefer_service=False, on_spawn=record_probe_pid)" in installer
    assert "os.killpg(pgid, signal.SIGTERM)" in installer
    assert "os.killpg(pgid, signal.SIGKILL)" in installer
    publish = installer.index("os.replace(candidate_path, release_path)")
    switch = installer.index("os.replace(next_link, current_path)", publish)
    launcher = installer.index("os.replace(launcher_staging_path, launcher_path)", switch)
    assert publish < switch < launcher
    assert 'export OPENPROGRAM_STATE_DIR="$state_root"' in installer
    assert 'export OPENPROGRAM_BIN_DIR="$launcher_dir"' in installer
    assert 'exec "$runtime_root/current/bin/python" -I -B -m openprogram' in installer



@POSIX_SHELL_INTEGRATION
def test_posix_release_installer_ignores_a_stale_lock_file(tmp_path: Path) -> None:
    archive, digest = _fake_posix_cli_archive(
        tmp_path / "archive-fixture", manifest_version="0.6.7"
    )
    runtime_root = tmp_path / "state" / "runtime" / "cli"
    runtime_root.mkdir(parents=True)
    lock = runtime_root / ".install.lock"
    lock.write_text("999999999\n", encoding="ascii")

    result = subprocess.run(
        ["sh", str(ROOT / "scripts" / "release" / "install-release.sh")],
        check=True,
        env=os.environ
        | {
            "HOME": str(tmp_path / "home"),
            "OPENPROGRAM_VERSION": "0.6.7",
            "OPENPROGRAM_STATE_DIR": str(tmp_path / "state"),
            "OPENPROGRAM_BIN_DIR": str(tmp_path / "bin"),
            "OPENPROGRAM_RUNTIME_ARCHIVE": str(archive),
            "OPENPROGRAM_RUNTIME_SHA256": digest,
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert (runtime_root / "current").resolve() == (
        runtime_root / "releases" / "0.6.7"
    )
    # flock ownership is kernel state.  Arbitrary legacy contents never block
    # an install and need no unsafe stale-owner deletion protocol.
    assert lock.read_text(encoding="ascii") == "999999999\n"



@POSIX_SHELL_INTEGRATION
def test_posix_release_installer_serializes_concurrent_activation(
    tmp_path: Path,
) -> None:
    archive, digest = _fake_posix_cli_archive(
        tmp_path / "archive-fixture", manifest_version="0.6.7"
    )
    state = tmp_path / "state"
    env = os.environ | {
        "HOME": str(tmp_path / "home"),
        "OPENPROGRAM_VERSION": "0.6.7",
        "OPENPROGRAM_STATE_DIR": str(state),
        "OPENPROGRAM_BIN_DIR": str(tmp_path / "bin"),
        "OPENPROGRAM_RUNTIME_ARCHIVE": str(archive),
        "OPENPROGRAM_RUNTIME_SHA256": digest,
    }
    command = ["sh", str(ROOT / "scripts" / "release" / "install-release.sh")]
    installers = [
        subprocess.Popen(
            command,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    results = [installer.communicate(timeout=30) for installer in installers]

    assert [installer.returncode for installer in installers] == [0, 0], results
    runtime_root = state / "runtime" / "cli"
    assert (runtime_root / "current").resolve() == (
        runtime_root / "releases" / "0.6.7"
    )
    assert (tmp_path / "bin" / "openprogram").is_file()
    assert list(runtime_root.glob(".staging-*")) == []



@POSIX_SHELL_INTEGRATION
def test_posix_release_installer_rolls_back_current_on_activation_failure(
    tmp_path: Path,
) -> None:
    archive, digest = _fake_posix_cli_archive(
        tmp_path / "archive-fixture", manifest_version="0.6.7"
    )
    state = tmp_path / "state"
    runtime_root = state / "runtime" / "cli"
    old_release = runtime_root / "releases" / "0.6.6"
    old_release.mkdir(parents=True)
    (runtime_root / "current").symlink_to(old_release)
    launcher_dir = tmp_path / "bin"
    launcher_dir.mkdir()
    launcher = launcher_dir / "openprogram"
    launcher.write_text("old launcher\n", encoding="utf-8")
    env = os.environ | {
        "HOME": str(tmp_path / "home"),
        "OPENPROGRAM_VERSION": "0.6.7",
        "OPENPROGRAM_STATE_DIR": str(state),
        "OPENPROGRAM_BIN_DIR": str(launcher_dir),
        "OPENPROGRAM_RUNTIME_ARCHIVE": str(archive),
        "OPENPROGRAM_RUNTIME_SHA256": digest,
    }
    command = ["sh", str(ROOT / "scripts" / "release" / "install-release.sh")]

    failed = subprocess.run(
        command,
        check=False,
        env=env | {"OPENPROGRAM_INSTALL_TEST_FAULT": "after-current"},
        capture_output=True,
        text=True,
    )

    assert failed.returncode != 0
    assert "injected activation failure after current switch" in failed.stderr
    assert (runtime_root / "current").resolve() == old_release
    assert launcher.read_text(encoding="utf-8") == "old launcher\n"
    # A fully verified release may remain cached, but it is not selected until
    # the same atomic activation transaction succeeds on retry.
    assert (runtime_root / "releases" / "0.6.7").is_dir()

    subprocess.run(command, check=True, env=env, capture_output=True, text=True)
    assert (runtime_root / "current").resolve() == (
        runtime_root / "releases" / "0.6.7"
    )
    assert launcher.read_text(encoding="utf-8").startswith("#!/bin/sh\n")



@POSIX_SHELL_INTEGRATION
def test_posix_release_installer_rejects_a_directory_launcher_target(
    tmp_path: Path,
) -> None:
    archive, digest = _fake_posix_cli_archive(
        tmp_path / "archive-fixture", manifest_version="0.6.7"
    )
    state = tmp_path / "state"
    runtime_root = state / "runtime" / "cli"
    old_release = runtime_root / "releases" / "0.6.6"
    old_release.mkdir(parents=True)
    (runtime_root / "current").symlink_to(old_release)
    launcher_target = tmp_path / "bin" / "openprogram"
    launcher_target.mkdir(parents=True)

    result = subprocess.run(
        ["sh", str(ROOT / "scripts" / "release" / "install-release.sh")],
        check=False,
        env=os.environ
        | {
            "HOME": str(tmp_path / "home"),
            "OPENPROGRAM_VERSION": "0.6.7",
            "OPENPROGRAM_STATE_DIR": str(state),
            "OPENPROGRAM_BIN_DIR": str(launcher_target.parent),
            "OPENPROGRAM_RUNTIME_ARCHIVE": str(archive),
            "OPENPROGRAM_RUNTIME_SHA256": digest,
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "launcher target is a directory" in result.stderr
    assert launcher_target.is_dir()
    assert (runtime_root / "current").resolve() == old_release
    assert not (runtime_root / "releases" / "0.6.7").exists()



@POSIX_SHELL_INTEGRATION
def test_short_public_installer_resolves_latest_and_accepts_a_pin(
    tmp_path: Path,
) -> None:
    bootstrap = ROOT / "docs" / "_static_root" / "install.sh"
    assert bootstrap.is_file()

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_installer = tmp_path / "tagged-installer.sh"
    fake_installer.write_text(
        "#!/bin/sh\n"
        "printf '%s|%s\\n' \"$OPENPROGRAM_VERSION\" "
        '"$OPENPROGRAM_REPOSITORY" > "$FAKE_RESULT"\n',
        encoding="utf-8",
    )
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        """#!/bin/sh
set -eu
output=""
url=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o) output="$2"; shift 2 ;;
    -w) shift 2 ;;
    https://*) url="$1"; shift ;;
    *) shift ;;
  esac
done
printf '%s\n' "$url" >> "$FAKE_CURL_LOG"
case "$url" in
  */releases/latest)
    printf 'https://github.com/Fzkuji/OpenProgram/releases/tag/v0.6.1'
    ;;
  */v*/scripts/install-release.sh)
    cp "$FAKE_INSTALLER" "$output"
    ;;
  *)
    printf 'unexpected URL: %s\n' "$url" >&2
    exit 1
    ;;
esac
""",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)

    result = tmp_path / "result"
    curl_log = tmp_path / "curl.log"
    env = {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
        "TMPDIR": str(tmp_path),
        "LC_ALL": "C",
        "FAKE_INSTALLER": str(fake_installer),
        "FAKE_RESULT": str(result),
        "FAKE_CURL_LOG": str(curl_log),
    }
    subprocess.run(["sh", str(bootstrap)], check=True, env=env)
    assert result.read_text(encoding="utf-8") == "0.6.1|Fzkuji/OpenProgram\n"
    assert curl_log.read_text(encoding="utf-8").splitlines() == [
        "https://github.com/Fzkuji/OpenProgram/releases/latest",
        "https://raw.githubusercontent.com/Fzkuji/OpenProgram/v0.6.1/scripts/install-release.sh",
    ]

    result.unlink()
    curl_log.unlink()
    subprocess.run(
        ["sh", str(bootstrap)],
        check=True,
        env=env | {"OPENPROGRAM_VERSION": "1.2.3"},
    )
    assert result.read_text(encoding="utf-8") == "1.2.3|Fzkuji/OpenProgram\n"
    assert curl_log.read_text(encoding="utf-8").splitlines() == [
        "https://raw.githubusercontent.com/Fzkuji/OpenProgram/v1.2.3/scripts/install-release.sh"
    ]



def test_source_development_installer_adds_to_complete_product() -> None:
    installer = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert 'PIP install -e "$HOST_ROOT[all,search]"' in installer
    assert '"$PY" -m playwright install chromium' in installer
    assert '"$PY" -m openprogram programs install all' in installer
    assert 'bash "$gui_installer" --no-host --python "$PY"' in installer
    assert 'get_program("research").clone_dir()' in installer
    assert 'PIP install -e "$research_source[pdf]"' in installer
    assert "prompt_programs_menu" not in installer
    assert "--minimal was removed" in installer
    assert "WITH_STEALTH" in installer
    assert "WITH_AGENT_BROWSER" in installer

