"""release local refresh tests."""
from __future__ import annotations
from ._support import (
    POSIX_SHELL_INTEGRATION,
    Path,
    ROOT,
    _desktop_package,
    _fake_desktop_app,
    json,
    os,
    pytest,
    re,
    runpy,
    signal,
    subprocess,
    sys,
    time,
)


def test_local_app_refresh_detaches_before_stopping_worker() -> None:
    refresh = (ROOT / "scripts" / "refresh-local-app.sh").read_text(
        encoding="utf-8"
    )
    detach_at = refresh.index('start_new_session=True')
    lock_at = refresh.index('fcntl.flock(lock, fcntl.LOCK_EX)')
    stop_at = refresh.index('"$local_python" -m openprogram worker stop')
    assert "OPENPROGRAM_REFRESH_DETACHED" in refresh
    assert '["bash", sys.argv[1], *sys.argv[2:]]' in refresh
    assert "OPENPROGRAM_SESSION_ID" in refresh
    assert detach_at < lock_at < stop_at



def test_local_app_refresh_reopens_app_after_quit() -> None:
    refresh = (ROOT / "scripts" / "refresh-local-app.sh").read_text(
        encoding="utf-8"
    )
    quit_at = refresh.index(
        'osascript -e \'tell application id "ai.openprogram.desktop" to quit\''
    )
    open_at = refresh.index('open -a "$app_path"')
    wait_at = refresh.index(
        'pgrep -f "^${app_path}/Contents/MacOS/OpenProgram( |$)"', open_at
    )
    fail_at = refresh.index("OpenProgram did not reopen after the refresh")
    assert quit_at < open_at < wait_at < fail_at



def test_local_app_refresh_restarts_worker_after_runtime_install() -> None:
    refresh = (ROOT / "scripts" / "refresh-local-app.sh").read_text(
        encoding="utf-8"
    )
    install = refresh.index('"$app_python" -I -m pip install')
    stops = [
        match.start()
        for match in re.finditer(
            r'"\$app_python" -I -B -m openprogram worker (?:install|stop)', refresh
        )
    ]
    health = refresh.index(
        'curl -fsS http://127.0.0.1:18100/healthz', install
    )
    assert any(install < stop < health for stop in stops)
    final_window = refresh[install:health]
    assert "build.files" in refresh
    assert (
        '"$app_python" -I -B -m openprogram worker stop >/dev/null 2>&1\n'
        in final_window
    )
    assert '"$app_python" -I -B -m openprogram worker install\n' in final_window
    assert "worker stop >/dev/null 2>&1 || true" not in final_window



def test_local_app_refresh_hydrates_embedded_runtime_dependencies() -> None:
    refresh = (ROOT / "scripts" / "refresh-local-app.sh").read_text(
        encoding="utf-8"
    )
    hydrate = refresh.index('hydrate_wheel_dependencies "$app_python"')
    reinstall = refresh.index(
        '"$app_python" -I -m pip install --disable-pip-version-check'
    )
    assert hydrate < reinstall
    assert '--force-reinstall "$wheel"' in refresh[reinstall:]



def test_local_app_refresh_probes_node_after_relocation() -> None:
    refresh = (ROOT / "scripts" / "refresh-local-app.sh").read_text(
        encoding="utf-8"
    )
    assert 'node_candidates+=("$runtime_root/bin/node")' in refresh
    assert 'node_candidates+=("$path_node")' in refresh
    assert '"$runtime_assets_stage/node" "$runtime_assets_stage/index.cjs" --probe' in refresh



def test_local_app_refresh_installs_committed_gui_harness_snapshot() -> None:
    refresh = (ROOT / "scripts" / "refresh-local-app.sh").read_text(
        encoding="utf-8"
    )
    assert 'git -C "$gui_harness_repo" archive' in refresh
    assert '"$gui_harness_revision"' in refresh
    assert '"$local_python" -m pip install' in refresh
    assert '"$app_python" -I -m pip install' in refresh
    assert '--force-reinstall "$gui_harness_stage"' in refresh
    assert "from gui_harness.adapters.mac_window import window_support" in refresh
    assert 'json.load(stream)["programs"]["gui"]["commit"]' in refresh
    assert 'test "$gui_harness_revision" = "$gui_harness_pin"' in refresh
    snapshot = refresh.index('cp "$product_runtime_config" "$product_runtime_stage"')
    validate = refresh.index('"$local_python" - "$product_runtime_stage"')
    install = refresh.index('cp "$product_runtime_stage" "$installed_product_runtime"')
    assert snapshot < validate < install



def test_local_app_refresh_removes_stale_package_layout_before_install() -> None:
    refresh = (ROOT / "scripts" / "refresh-local-app.sh").read_text(
        encoding="utf-8"
    )

    cleanup = refresh.index('remove_stale_package_tree "$local_python"')
    install = refresh.index('"$local_python" -m pip install')
    local_check = refresh.index('validate_stale_package_tree "$local_python"')
    app_check = refresh.index('validate_stale_package_tree "$app_python"')

    assert local_check < app_check < cleanup < install
    assert "remove-stale-openprogram-packages.py" in refresh



@pytest.mark.parametrize("outside_site_packages", [False, True])
def test_stale_package_cleanup_rejects_symlinks_before_deleting(
    tmp_path: Path,
    outside_site_packages: bool,
) -> None:
    helper = runpy.run_path(
        str(ROOT / "scripts/release/remove-stale-openprogram-packages.py")
    )
    remove = helper["remove_stale_package_trees"]
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    for name in ("openprogram", "openprogram_server"):
        package = site_packages / name
        package.mkdir()
        (package / "owned.py").write_text("owned\n", encoding="utf-8")
    unrelated = (
        tmp_path / "external_pkg"
        if outside_site_packages
        else site_packages / "unrelated_pkg"
    )
    unrelated.mkdir()
    sentinel = unrelated / "keep.txt"
    sentinel.write_text("keep\n", encoding="utf-8")
    (site_packages / "openprogram_cli").symlink_to(
        unrelated,
        target_is_directory=True,
    )

    with pytest.raises(RuntimeError, match="symlinked package"):
        remove(site_packages)

    assert sentinel.read_text(encoding="utf-8") == "keep\n"
    assert (site_packages / "openprogram" / "owned.py").is_file()
    assert (site_packages / "openprogram_server" / "owned.py").is_file()



def test_stale_package_cleanup_removes_only_three_owned_directories(
    tmp_path: Path,
) -> None:
    helper = runpy.run_path(
        str(ROOT / "scripts/release/remove-stale-openprogram-packages.py")
    )
    remove = helper["remove_stale_package_trees"]
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    for name in ("openprogram", "openprogram_server", "openprogram_cli"):
        package = site_packages / name
        package.mkdir()
        (package / "owned.py").write_text("owned\n", encoding="utf-8")
    unrelated = site_packages / "unrelated_pkg"
    unrelated.mkdir()
    sentinel = unrelated / "keep.txt"
    sentinel.write_text("keep\n", encoding="utf-8")

    remove(site_packages)

    assert not (site_packages / "openprogram").exists()
    assert not (site_packages / "openprogram_server").exists()
    assert not (site_packages / "openprogram_cli").exists()
    assert sentinel.read_text(encoding="utf-8") == "keep\n"



def test_stale_package_preflight_checks_both_runtimes_before_deleting(
    tmp_path: Path,
) -> None:
    helper = runpy.run_path(
        str(ROOT / "scripts/release/remove-stale-openprogram-packages.py")
    )
    validate = helper["validate_stale_package_trees"]
    local_site = tmp_path / "local-site"
    app_site = tmp_path / "app-site"
    local_site.mkdir()
    app_site.mkdir()
    for name in ("openprogram", "openprogram_server", "openprogram_cli"):
        package = local_site / name
        package.mkdir()
        (package / "owned.py").write_text("owned\n", encoding="utf-8")
    for name in ("openprogram", "openprogram_server"):
        (app_site / name).mkdir()
    external = tmp_path / "external-cli"
    external.mkdir()
    sentinel = external / "keep.txt"
    sentinel.write_text("keep\n", encoding="utf-8")
    (app_site / "openprogram_cli").symlink_to(external, target_is_directory=True)

    validate(local_site)
    with pytest.raises(RuntimeError, match="symlinked package"):
        validate(app_site)

    assert all((local_site / name / "owned.py").is_file() for name in (
        "openprogram", "openprogram_server", "openprogram_cli",
    ))
    assert sentinel.read_text(encoding="utf-8") == "keep\n"



@POSIX_SHELL_INTEGRATION
def test_local_app_refresh_rejects_a_different_product_version_before_build(
    tmp_path: Path,
) -> None:
    source_version = _desktop_package()["version"]
    major, minor, patch = (int(part) for part in source_version.split("."))
    installed_version = f"{major}.{minor}.{patch + 1}"
    installed = _fake_desktop_app(tmp_path, installed_version)
    verifier = ROOT / "scripts" / "release" / "verify-release-version.py"
    result = subprocess.run(
        [
            sys.executable,
            str(verifier),
            "--installed-app",
            str(installed),
            "--require-source-match",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert (
        f"source version {source_version} != installed App version "
        f"{installed_version}"
    ) in result.stderr

    refresh = (ROOT / "scripts" / "refresh-local-app.sh").read_text(
        encoding="utf-8"
    )
    gate = refresh.index("--require-source-match")
    assert gate < refresh.index('wheel_dir="$(mktemp')
    assert gate < refresh.index('"$repo_root/scripts/release/stage-release-assets.sh"')
    assert gate < refresh.index("openprogram worker stop")
    post_build_gate = refresh.index('--wheel "$wheel"')
    lock = refresh.index('acquire_pid_lock "$install_lock_file"')
    archive = refresh.index('node "$asar_cli" pack')
    first_worker_mutation = refresh.index('pgrep -f "^/Applications/OpenProgram[.]app/Contents/MacOS/OpenProgram( |$)"')
    first_pip_mutation = refresh.index('"$local_python" -m pip install')
    assert refresh.count("--require-source-match") == 2
    assert archive < lock < post_build_gate < first_worker_mutation
    assert post_build_gate < first_pip_mutation
    chat_gate = refresh.index("zipfile.ZipFile")
    wheel_found = refresh.index('openprogram-*.whl')
    assert wheel_found < chat_gate < first_pip_mutation
    assert 'aria-label="Authenticating"' in refresh[chat_gate:first_pip_mutation]
    assert 'id="sidebar"' in refresh[chat_gate:first_pip_mutation]
    assert '$(dirname -- "$app_path")/.openprogram-app-install.lock' in refresh



@POSIX_SHELL_INTEGRATION
def test_local_app_refresh_rejects_dirty_version_change_after_build(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    release_scripts = scripts / "release"
    desktop = repo / "apps" / "desktop"
    release_scripts.mkdir(parents=True)
    desktop.mkdir(parents=True)
    (desktop / "scripts").mkdir()
    (desktop / "scripts/install-app.sh").write_bytes(
        (ROOT / "apps/desktop/scripts/install-app.sh").read_bytes()
    )
    (scripts / "refresh-local-app.sh").write_text(
        (ROOT / "scripts" / "refresh-local-app.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    # This fixture exercises version/lock ordering without native signing.
    (release_scripts / "local-macos-signing.py").write_text("pass\n", encoding="utf-8")
    (release_scripts / "verify-release-version.py").write_text(
        (ROOT / "scripts" / "release" / "verify-release-version.py").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    stage_assets = release_scripts / "stage-release-assets.sh"
    stage_assets.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stage_assets.chmod(0o755)
    # This fixture stops before installation, but must provide every artifact
    # normally produced by the release build before the version recheck.
    tui = repo / "apps/cli/dist/index-standalone.cjs"
    tui.parent.mkdir(parents=True)
    tui.write_text("// staged Ink bundle\n", encoding="utf-8")
    (repo / "uv.lock").write_text("", encoding="utf-8")
    for name in ("product-runtime.json", "verify-product-runtime.py", "build-macos-runtime-app.py", "mac-runtime-main.c"):
        (release_scripts / name).write_bytes((ROOT / "scripts/release" / name).read_bytes())
    (desktop / "build").mkdir(exist_ok=True)
    (desktop / "build/icon.icns").write_bytes(b"icns")
    (desktop / "build/office").mkdir(exist_ok=True)  # Staged artifact; this fixture stops before installation.
    (release_scripts / "office").mkdir()
    (release_scripts / "office/stage.py").write_text(
        "import pathlib, sys; pathlib.Path(sys.argv[sys.argv.index('--output') + 1]).mkdir(parents=True, exist_ok=True)\n"
    )
    (release_scripts / "install-release.sh").write_text(
        'OPENPROGRAM_VERSION="${OPENPROGRAM_VERSION:-0.6.6}"\n',
        encoding="utf-8",
    )
    (release_scripts / "install-release.ps1").write_text(
        'if ($env:OPENPROGRAM_VERSION) { $env:OPENPROGRAM_VERSION } '
        'else { "0.6.6" }\n',
        encoding="utf-8",
    )
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "openprogram"\nversion = "0.6.6"\n',
        encoding="utf-8",
    )
    desktop_files = [
        "main.js",
        "menu-geometry.js",
        "worker-recovery-state.js",
        "tab-transfer-validation.js",
        "preload.js",
        "update-service.js",
        "packaged-runtime.js",
        "worker-start-url.js",
        "tab-transfer-store.js",
        "window-state.js",
        "window-lifecycle.js",
        "theme-chrome.js",
        "browsing-history-store.js",
        "browser-profile-import.js",
    ]
    (desktop / "package.json").write_text(
        json.dumps({"version": "0.6.6", "build": {"files": desktop_files}}),
        encoding="utf-8",
    )
    for desktop_file in desktop_files:
        (desktop / desktop_file).write_text("module.exports = {};\n", encoding="utf-8")
    asar_cli = repo / "node_modules" / "@electron" / "asar" / "bin" / "asar.js"
    asar_cli.parent.mkdir(parents=True)
    asar_cli.write_text("", encoding="utf-8")
    app = _fake_desktop_app(tmp_path / "installed", "0.6.6")
    installed_asar = app / "Contents" / "Resources" / "app.asar"
    installed_asar.write_bytes(b"original-asar")
    embedded_bin = app / "Contents/Resources/runtime/bin"
    embedded_bin.mkdir(exist_ok=True)
    embedded_uv = embedded_bin / "uv"
    embedded_uv.write_text("#!/bin/sh\nprintf 'uv 0.11.16\\n'\n", encoding="utf-8")
    embedded_uv.chmod(0o755)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    mutation_log = tmp_path / "mutation.log"
    local_python = fake_bin / "local-python"
    local_python.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'case "${1:-}" in\n'
        '  *.py|-) exec "$REAL_PYTHON" "$@" ;;\n'
        "esac\n"
        'printf "unexpected local Python mutation: %s\\n" "$*" >> "$MUTATION_LOG"\n'
        "exit 90\n",
        encoding="utf-8",
    )
    local_python.chmod(0o755)
    fake_git = fake_bin / "git"
    fake_git.write_text("#!/bin/sh\nprintf 'fixed-head\\n'\n", encoding="utf-8")
    fake_git.chmod(0o755)
    fake_node = fake_bin / "node"
    fake_node.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'case "$2" in\n'
        '  --probe) test -z "${NODE_PROBE_FAIL:-}" ;;\n'
        '  extract) mkdir -p "$4/node_modules" ;;\n'
        '  pack) test -f "$3/menu-geometry.js" || exit 92; '
        'test -f "$3/worker-recovery-state.js" || exit 93; '
        'test -f "$3/tab-transfer-validation.js" || exit 94; '
        'test -f "$3/window-state.js" || exit 95; '
        'test -f "$3/theme-chrome.js" || exit 96; : > "$4" ;;\n'
        # The real descriptor writer is covered by test_package_protocol;
        # this fixture isolates refresh version/lock/signal behavior.
        '  --resources) exit 0 ;;\n'
        '  *) printf "unexpected node call: %s\\n" "$*" >&2; exit 91 ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    fake_node.chmod(0o755)
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "out=\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        '  if [ "$1" = "--out-dir" ]; then out="$2"; shift 2; else shift; fi\n'
        "done\n"
        'printf \'[project]\\nname = "openprogram"\\nversion = "0.6.1"\\n\' > "$REPO_ROOT/pyproject.toml"\n'
        'printf \'{"version":"0.6.1","build":{"files":["main.js","menu-geometry.js","worker-recovery-state.js","tab-transfer-validation.js","preload.js","update-service.js","packaged-runtime.js","worker-start-url.js","tab-transfer-store.js","window-state.js","window-lifecycle.js","theme-chrome.js","browsing-history-store.js","browser-profile-import.js"]}}\\n\' > "$REPO_ROOT/apps/desktop/package.json"\n'
        'mkdir -p "$out"\n'
        'exec "$REAL_PYTHON" - "$out/openprogram-0.6.1-py3-none-any.whl" <<\'PY\'\n'
        "import sys, zipfile\n"
        "with zipfile.ZipFile(sys.argv[1], 'w') as archive:\n"
        "    archive.writestr('openprogram-0.6.1.dist-info/METADATA', "
        "'Metadata-Version: 2.1\\nName: openprogram\\nVersion: 0.6.1\\n')\n"
        "    archive.writestr('openprogram_server/_webui/_frontend/chat.html', "
        "'<body><div id=\"sidebar\"></div></body>\\n')\n"
        "PY\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    env = {
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "HOME": str(tmp_path / "home"),
        "TMPDIR": str(tmp_path / "tmp"),
        "OPENPROGRAM_APP_PATH": str(app),
        "OPENPROGRAM_LOCAL_PYTHON": str(local_python),
        "OPENPROGRAM_UV_BIN": str(fake_uv),
        "REAL_PYTHON": sys.executable,
        "REPO_ROOT": str(repo),
        "MUTATION_LOG": str(mutation_log),
    }
    Path(env["TMPDIR"]).mkdir()
    result = subprocess.run(
        ["bash", str(scripts / "refresh-local-app.sh")],
        check=False,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )

    detached_log = Path(env["TMPDIR"]) / f"openprogram-refresh-{os.getuid()}.log"
    refresh_output = result.stdout + result.stderr
    if detached_log.exists():
        refresh_output += detached_log.read_text(encoding="utf-8", errors="replace")

    assert result.returncode != 0
    assert "source version 0.6.1 != installed App version 0.6.6" in refresh_output
    assert not mutation_log.exists()
    assert installed_asar.read_bytes() == b"original-asar"
    assert not (app.parent / ".openprogram-app-install.lock").exists()

    detached_log.unlink()
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "openprogram"\nversion = "0.6.6"\n',
        encoding="utf-8",
    )
    (desktop / "package.json").write_text(
        json.dumps({"version": "0.6.6", "build": {"files": desktop_files}}),
        encoding="utf-8",
    )
    fake_uv.write_text(
        fake_uv.read_text(encoding="utf-8").replace("0.6.1", "0.6.6"),
        encoding="utf-8",
    )
    bad_node = subprocess.run(
        ["bash", str(scripts / "refresh-local-app.sh")],
        env=env | {"NODE_PROBE_FAIL": "1"},
        capture_output=True, text=True, timeout=15,
    )
    bad_node_output = bad_node.stdout + bad_node.stderr
    if detached_log.exists():
        bad_node_output += detached_log.read_text(encoding="utf-8", errors="replace")
    assert bad_node.returncode != 0
    assert "bundled Node cannot run after relocation" in bad_node_output
    assert not mutation_log.exists()
    assert installed_asar.read_bytes() == b"original-asar"

    detached_log.unlink()
    lock_file = app.parent / ".openprogram-app-install.lock"
    lock_file.write_text(f"{os.getpid()}\n", encoding="utf-8")
    try:
        blocked = subprocess.run(
            ["bash", str(scripts / "refresh-local-app.sh")],
            check=False,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
    finally:
        lock_file.unlink()

    blocked_output = blocked.stdout + blocked.stderr
    if detached_log.exists():
        blocked_output += detached_log.read_text(encoding="utf-8", errors="replace")
    assert blocked.returncode != 0
    assert "another OpenProgram App installation is running" in blocked_output
    assert not mutation_log.exists()
    assert installed_asar.read_bytes() == b"original-asar"

    signal_ready = tmp_path / "signal-ready"
    fake_pgrep = fake_bin / "pgrep"
    fake_pgrep.write_text(
        "#!/bin/sh\n"
        'touch "$SIGNAL_READY"\n'
        "sleep 10\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_pgrep.chmod(0o755)
    signal_env = env | {
        "OPENPROGRAM_REFRESH_DETACHED": "1",
        "SIGNAL_READY": str(signal_ready),
    }
    interrupted = subprocess.Popen(
        ["bash", str(scripts / "refresh-local-app.sh")],
        env=signal_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        text=True,
    )
    deadline = time.monotonic() + 10
    while not signal_ready.exists() and interrupted.poll() is None:
        if time.monotonic() >= deadline:
            interrupted.kill()
            raise AssertionError("refresh did not acquire the install lock")
        time.sleep(0.02)
    os.killpg(interrupted.pid, signal.SIGTERM)
    stdout, stderr = interrupted.communicate(timeout=5)

    # The flock-owning Python parent reports a signal directly; Bash reports 128+signal.
    assert interrupted.returncode in {-signal.SIGTERM, 143}, (stdout, stderr)
    assert not mutation_log.exists()
    assert installed_asar.read_bytes() == b"original-asar"
    assert not lock_file.exists()

    local_python.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'case "${1:-}" in\n'
        '  *.py|-) exec "$REAL_PYTHON" "$@" ;;\n'
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_pgrep.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    fake_open = fake_bin / "open"
    fake_open.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_open.chmod(0o755)
    fake_curl = fake_bin / "curl"
    fake_curl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_curl.chmod(0o755)
    cleanup_ready = tmp_path / "cleanup-ready"
    fake_rm = fake_bin / "rm"
    fake_rm.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'case "$*" in\n'
        '  *".openprogram-app-install.lock"*)\n'
        '    /bin/rm "$@"\n'
        '    touch "$CLEANUP_READY"\n'
        "    sleep 10\n"
        "    ;;\n"
        '  *) exec /bin/rm "$@" ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    fake_rm.chmod(0o755)
    cleanup_env = env | {
        "OPENPROGRAM_REFRESH_DETACHED": "1",
        "CLEANUP_READY": str(cleanup_ready),
    }
    cleanup_interrupted = subprocess.Popen(
        ["bash", str(scripts / "refresh-local-app.sh")],
        env=cleanup_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        text=True,
    )
    deadline = time.monotonic() + 10
    while not cleanup_ready.exists() and cleanup_interrupted.poll() is None:
        if time.monotonic() >= deadline:
            cleanup_interrupted.kill()
            raise AssertionError("refresh did not enter final cleanup")
        time.sleep(0.02)
    os.killpg(cleanup_interrupted.pid, signal.SIGTERM)
    stdout, stderr = cleanup_interrupted.communicate(timeout=5)

    assert cleanup_interrupted.returncode in {-signal.SIGTERM, 143}, (stdout, stderr)
    assert not lock_file.exists()
    assert not list(Path(env["TMPDIR"]).glob("openprogram-local-wheel.*"))

