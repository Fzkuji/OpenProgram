"""release local refresh tests."""
from __future__ import annotations
import os
import re
import runpy
import shutil
import signal
import time
from ._support import (
    POSIX_SHELL_INTEGRATION,
    Path,
    ROOT,
    _desktop_package,
    _fake_desktop_app,
    json,
    pytest,
    subprocess,
    sys,
)
from tests.support.executables import ExecutablePool


# Each script run starts hundreds of processes and takes seconds alone, far
# longer when every xdist worker runs one. The limit only bounds a real hang.
SCRIPT_TIMEOUT = 90


@pytest.fixture(scope="session")
def executable_pool(tmp_path_factory) -> ExecutablePool:
    return ExecutablePool(tmp_path_factory.mktemp("executable-pool"))


@pytest.fixture
def executables(executable_pool: ExecutablePool):
    yield executable_pool
    executable_pool.reclaim()


def test_local_app_refresh_detaches_before_stopping_worker() -> None:
    refresh = (ROOT / "scripts" / "refresh-local-app.sh").read_text(
        encoding="utf-8"
    )
    detach_at = refresh.index('start_new_session=True')
    lock_at = refresh.index('fcntl.flock(lock, fcntl.LOCK_EX)')
    stop_at = refresh.index('step "stopping the worker"')
    assert "OPENPROGRAM_REFRESH_DETACHED" in refresh
    assert '["bash", sys.argv[1], *sys.argv[2:]]' in refresh
    assert "OPENPROGRAM_SESSION_ID" in refresh
    assert detach_at < lock_at < stop_at



def test_local_app_refresh_reuses_a_matching_dev_build() -> None:
    refresh = (ROOT / "scripts" / "refresh-local-app.sh").read_text(encoding="utf-8")
    dev = (ROOT / "scripts" / "refresh-dev-instance.sh").read_text(encoding="utf-8")
    # Reused only when the dev build recorded this exact revision.
    assert '"$(cat "$prebuilt_dir/revision" 2>/dev/null)" = "$build_revision"' in refresh
    reuse = refresh.index('if test -n "$prebuilt_wheel"; then')
    assert reuse < refresh.index('$build_nice "$repo_root/scripts/release/stage-release-assets.sh"')
    assert reuse < refresh.index('step "quitting the App"')
    assert 'printf \'%s\\n\' "$revision" > "$artifacts.incoming/revision"' in dev
    assert 'mv "$artifacts.incoming" "$artifacts"' in dev
    # Builds run beside the open App at a lower priority.
    assert "renice -n 10 -p $$" in dev


def test_local_app_refresh_reopens_app_after_quit() -> None:
    refresh = (ROOT / "scripts" / "refresh-local-app.sh").read_text(
        encoding="utf-8"
    )
    open_at = refresh.index('open -a "$app_path"')
    wait_at = refresh.index(
        'pgrep -f "^${app_path}/Contents/MacOS/OpenProgram( |$)"', open_at
    )
    fail_at = refresh.index("OpenProgram did not reopen after the refresh", wait_at)
    quit_at = refresh.index(
        'osascript -e \'tell application id "ai.openprogram.desktop" to quit\''
    )
    commit_at = refresh.index("committed=1\n", quit_at)
    assert open_at < wait_at < fail_at < quit_at
    # The new App reopens once committed; a restored one from finish_refresh.
    assert refresh.index("reopen_app\n", commit_at) > commit_at
    assert "app_ready_for_reopen=1" in refresh[refresh.index("finish_refresh() {"):quit_at]



def test_local_app_refresh_restarts_worker_after_runtime_install() -> None:
    refresh = (ROOT / "scripts" / "refresh-local-app.sh").read_text(
        encoding="utf-8"
    )
    install = refresh.index('"$staged_python" -I -m pip install')
    stop = refresh.index('"$app_python" -I -B -m openprogram worker stop >/dev/null\n')
    switch = refresh.index('mv "$staged_app" "$app_path"')
    start = refresh.index("start_worker\n", switch)
    health = refresh.index("wait_for_health\n", start)
    assert install < stop < switch < start < health
    assert "scripts/release/pack-desktop-archive.sh" in refresh
    starter = refresh[refresh.index("start_worker() {"):refresh.index("wait_for_health() {")]
    assert '"$app_python" -I -B -m openprogram worker stop >/dev/null 2>&1 || return\n' in starter
    assert '"$app_python" -I -B -m openprogram worker install\n' in starter
    assert "http://127.0.0.1:18100/healthz" in refresh[refresh.index("wait_for_health() {"):install]



def test_local_app_refresh_hydrates_embedded_runtime_dependencies() -> None:
    refresh = (ROOT / "scripts" / "refresh-local-app.sh").read_text(
        encoding="utf-8"
    )
    hydrate = refresh.index('hydrate_wheel_dependencies "$staged_python"')
    reinstall = refresh.index(
        '"$staged_python" -I -m pip install --disable-pip-version-check'
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



def test_local_app_refresh_bundles_no_programs() -> None:
    refresh = (ROOT / "scripts" / "refresh-local-app.sh").read_text(
        encoding="utf-8"
    )
    assert "stage-program-source.py" not in refresh
    assert "program_wheel" not in refresh
    assert "--checkout" not in refresh
    assert (
        "for retired_program in wiki-agent-harness gui-agent-harness research-agent-harness; do"
        in refresh
    )
    snapshot = refresh.index('cp "$product_runtime_config" "$product_runtime_stage"')
    validate = refresh.index('"$staged_runtime/bin/verify-product-runtime.py" "$staged_runtime"')
    install = refresh.index('cp "$product_runtime_stage" "$staged_runtime/product-runtime.json"')
    assert snapshot < install < validate


def test_local_app_refresh_installs_the_app_icon_before_signing() -> None:
    refresh = (ROOT / "scripts" / "refresh-local-app.sh").read_text(
        encoding="utf-8"
    )
    stage = refresh.index('cp "$repo_root/apps/desktop/build/icon.icns" "$runtime_assets_stage/icon.icns"')
    install = refresh.index('cp "$runtime_assets_stage/icon.icns" "$staged_app/Contents/Resources/icon.icns"')
    sign = refresh.index('"$repo_root/scripts/release/local-macos-signing.py" sign --app "$staged_app"')
    touch = refresh.index('touch "$app_path"')
    assert stage < install < sign < touch


def test_local_app_refresh_removes_stale_package_layout_before_install() -> None:
    refresh = (ROOT / "scripts" / "refresh-local-app.sh").read_text(
        encoding="utf-8"
    )

    for python, install_command in (
        ("$staged_python", '"$staged_python" -I -m pip install'),
        ("$local_python", '"$local_python" -m pip install'),
    ):
        check = refresh.index(f'validate_stale_package_tree "{python}"')
        cleanup = refresh.index(f'remove_stale_package_tree "{python}"')
        install = refresh.index(install_command)
        assert check < cleanup < install
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
    archive = refresh.index('"$repo_root/scripts/release/pack-desktop-archive.sh"')
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



# `pip wheel` builds a Program wheel into the attempt's temporary directory. It
# mutates nothing installed, so the fake Pythons satisfy it without logging it.
PIP_WHEEL_SHIM = (
    'case "$*" in "-m pip wheel "*) out=; while [ "$#" -gt 0 ]; do '
    'if [ "$1" = "--wheel-dir" ]; then out="$2"; shift 2; else shift; fi; done; '
    'mkdir -p "$out"; : > "$out/program-0.0.0-py3-none-any.whl"; exit 0 ;; esac\n'
)


def _prepare_local_refresh_fixture(
    tmp_path: Path, executables: ExecutablePool,
) -> tuple[Path, Path, dict[str, str]]:
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
    executables.write(stage_assets, "#!/bin/sh\nexit 0\n")
    # This fixture stops before installation, but must provide every artifact
    # normally produced by the release build before the version recheck.
    tui = repo / "apps/cli/dist/index-standalone.cjs"
    tui.parent.mkdir(parents=True)
    tui.write_text("// staged Ink bundle\n", encoding="utf-8")
    (repo / "uv.lock").write_text("", encoding="utf-8")
    for name in ("product-runtime.json", "verify-product-runtime.py", "build-macos-runtime-app.py", "mac-runtime-main.c", "restore-asar-permissions.py"):
        (release_scripts / name).write_bytes((ROOT / "scripts/release" / name).read_bytes())
    shutil.copy2(ROOT / "scripts/release/pack-desktop-archive.sh", release_scripts / "pack-desktop-archive.sh")
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
    executables.write(embedded_uv, "#!/bin/sh\nprintf 'uv 0.11.16\\n'\n")
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    mutation_log = tmp_path / "mutation.log"
    local_python = fake_bin / "local-python"
    executables.write(
        local_python,
        "#!/bin/sh\n"
        "set -eu\n"
        + PIP_WHEEL_SHIM +
        'case "${1:-}" in\n'
        '  *.py|-) exec "$REAL_PYTHON" "$@" ;;\n'
        "esac\n"
        'printf "unexpected local Python mutation: %s\\n" "$*" >> "$MUTATION_LOG"\n'
        "exit 90\n",
    )
    fake_git = fake_bin / "git"
    executables.write(fake_git, "#!/bin/sh\nprintf 'fixed-head\\n'\n")
    fake_node = fake_bin / "node"
    executables.write(
        fake_node,
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
    )
    fake_uv = fake_bin / "uv"
    executables.write(
        fake_uv,
        "#!/bin/sh\n"
        "set -eu\n"
        # Runtime sync: `uv export` writes the locked requirements and
        # `uv pip install` applies them; neither builds a wheel.
        'case "${1:-}" in export) while [ "$#" -gt 0 ]; do '
        'if [ "$1" = "--output-file" ]; then : > "$2"; fi; shift; done; exit 0 ;; '
        'pip) exit 0 ;; esac\n'
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
        "    archive.writestr('openprogram/interfaces/server/_frontend/chat.html', "
        "'<body><div id=\"sidebar\"></div></body>\\n')\n"
        "PY\n",
    )

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
    return repo, app, env


@POSIX_SHELL_INTEGRATION
@pytest.mark.timeout(6 * SCRIPT_TIMEOUT)  # Six script runs in one test.
def test_local_app_refresh_rejects_dirty_version_change_after_build(
    tmp_path: Path,
    executables: ExecutablePool,
) -> None:
    repo, app, env = _prepare_local_refresh_fixture(tmp_path, executables)
    scripts = repo / "scripts"
    desktop = repo / "apps/desktop"
    desktop_files = json.loads((desktop / "package.json").read_text())["build"]["files"]
    installed_asar = app / "Contents/Resources/app.asar"
    fake_bin = tmp_path / "fake-bin"
    fake_uv = Path(env["OPENPROGRAM_UV_BIN"])
    local_python = Path(env["OPENPROGRAM_LOCAL_PYTHON"])
    mutation_log = Path(env["MUTATION_LOG"])
    result = subprocess.run(
        ["bash", str(scripts / "refresh-local-app.sh")],
        check=False,
        env=env,
        capture_output=True,
        text=True,
        timeout=SCRIPT_TIMEOUT,
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
        capture_output=True, text=True, timeout=SCRIPT_TIMEOUT,
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
            timeout=SCRIPT_TIMEOUT,
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

    # Interrupted while the new App is signed beside the installed one.
    signal_ready = tmp_path / "signal-ready"
    fake_codesign = fake_bin / "codesign"
    executables.write(
        fake_codesign,
        "#!/bin/sh\n"
        'touch "$SIGNAL_READY"\n'
        "sleep 30\n"
        "exit 1\n",
    )
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
    deadline = time.monotonic() + SCRIPT_TIMEOUT
    while not signal_ready.exists() and interrupted.poll() is None:
        if time.monotonic() >= deadline:
            interrupted.kill()
            raise AssertionError("refresh did not reach signing")
        time.sleep(0.02)
    os.killpg(interrupted.pid, signal.SIGTERM)
    stdout, stderr = interrupted.communicate(timeout=SCRIPT_TIMEOUT)

    # The flock-owning Python parent reports a signal directly; Bash reports 128+signal.
    assert interrupted.returncode in {-signal.SIGTERM, 143}, (stdout, stderr)
    assert not mutation_log.exists()
    assert installed_asar.read_bytes() == b"original-asar"
    assert not lock_file.exists()
    assert not list(app.parent.glob(".openprogram-refresh.*"))

    local_python.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        + PIP_WHEEL_SHIM +
        'case "${1:-}" in\n'
        '  *.py|-) exec "$REAL_PYTHON" "$@" ;;\n'
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_codesign.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    fake_open = fake_bin / "open"
    executables.write(fake_open, "#!/bin/sh\nexit 0\n")
    fake_curl = fake_bin / "curl"
    executables.write(fake_curl, "#!/bin/sh\nexit 0\n")
    cleanup_ready = tmp_path / "cleanup-ready"
    fake_rm = fake_bin / "rm"
    executables.write(
        fake_rm,
        "#!/bin/sh\n"
        "set -eu\n"
        'case "$*" in\n'
        '  *".openprogram-app-install.lock"*)\n'
        '    /bin/rm "$@"\n'
        '    touch "$CLEANUP_READY"\n'
        "    sleep 30\n"
        "    ;;\n"
        '  *) exec /bin/rm "$@" ;;\n'
        "esac\n",
    )
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
    deadline = time.monotonic() + SCRIPT_TIMEOUT
    while not cleanup_ready.exists() and cleanup_interrupted.poll() is None:
        if time.monotonic() >= deadline:
            cleanup_interrupted.kill()
            raise AssertionError("refresh did not enter final cleanup")
        time.sleep(0.02)
    os.killpg(cleanup_interrupted.pid, signal.SIGTERM)
    stdout, stderr = cleanup_interrupted.communicate(timeout=SCRIPT_TIMEOUT)

    assert cleanup_interrupted.returncode in {-signal.SIGTERM, 143}, (stdout, stderr)
    assert not lock_file.exists()
    assert not list(Path(env["TMPDIR"]).glob("openprogram-local-wheel.*"))



@POSIX_SHELL_INTEGRATION
@pytest.mark.parametrize(("change", "target_alias"), [
    *[(change, "canonical") for change in (
        "tracked", "staged", "untracked", "stale",
        "during_build", "untracked_during_build", "main_during_build",
        "after_build", "head_after_build", "main_after_build",
        "clean", "committed_during_build",
    )],
    *[(change, alias) for alias in ("dot", "slash", "symlink")
      for change in ("tracked", "stale", "clean", "locked")],
    ("tracked", "custom"),
])
def test_default_local_refresh_requires_committed_source_before_mutation(
    tmp_path: Path, executables: ExecutablePool, change: str, target_alias: str,
) -> None:
    repo, app, env = _prepare_local_refresh_fixture(tmp_path, executables)
    script = repo / "scripts/refresh-local-app.sh"
    # Remap only the canonical App path in the copied public script. No real
    # Applications path or worker is used, and no production bypass is added.
    default_app = tmp_path / "default.app" if target_alias == "custom" else app
    script.write_text(script.read_text().replace(
        '"/Applications/OpenProgram.app"', f'"{default_app}"',
    ))
    if target_alias == "dot":
        env["OPENPROGRAM_APP_PATH"] += "/."
    elif target_alias == "slash":
        env["OPENPROGRAM_APP_PATH"] += "/"
    elif target_alias == "symlink":
        alias = tmp_path / "alias" / "OpenProgram.app"
        alias.parent.mkdir()
        alias.symlink_to(app, target_is_directory=True)
        env["OPENPROGRAM_APP_PATH"] = str(alias)
    fake_bin = tmp_path / "fake-bin"
    (fake_bin / "git").unlink()  # Git ancestry/status come from a real temp repo.
    for name in ("pgrep", "pkill", "osascript", "open"):
        tool = fake_bin / name
        executables.write(tool, "#!/bin/sh\nexit 1\n")
    source = repo / "openprogram/source.py"
    source.parent.mkdir()
    source.write_text("VALUE = 1\n")
    (repo / ".gitignore").write_text("tmp/\n")
    (repo / "tmp").mkdir()
    (repo / "tmp/ignored-evidence.txt").write_text("local evidence\n")
    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(repo), *args], text=True,
            env=os.environ | {"GIT_CONFIG_NOSYSTEM": "1"},
        ).strip()
    git("init", "-b", "main")
    git("config", "user.name", "Refresh Test")
    git("config", "user.email", "refresh@example.invalid")
    git("config", "commit.gpgsign", "false")
    git("config", "core.hooksPath", "/dev/null")
    git("add", ".")
    git("commit", "-qm", "committed refresh source")
    initial_head = git("rev-parse", "HEAD")
    git("checkout", "-qb", "candidate")

    fake_uv = Path(env["OPENPROGRAM_UV_BIN"])
    fake_uv.write_text(
        "#!/bin/sh\nset -eu\n"
        # Runtime sync: `uv export` writes the locked requirements and
        # `uv pip install` applies them; neither builds a wheel.
        'case "${1:-}" in export) while [ "$#" -gt 0 ]; do '
        'if [ "$1" = "--output-file" ]; then : > "$2"; fi; shift; done; exit 0 ;; '
        'pip) exit 0 ;; esac\n'
        'printf "build\n" >> "$BUILD_LOG"\n'
        'out=; while [ "$#" -gt 0 ]; do '
        'if [ "$1" = "--out-dir" ]; then out="$2"; shift 2; else shift; fi; done\n'
        '"$REAL_PYTHON" - "$out/openprogram-0.6.6-py3-none-any.whl" <<\'PYWHEEL\'\n'
        "import sys, zipfile\n"
        "with zipfile.ZipFile(sys.argv[1], 'w') as z:\n"
        " z.writestr('openprogram-0.6.6.dist-info/METADATA', "
        "'Metadata-Version: 2.1\\nName: openprogram\\nVersion: 0.6.6\\n')\n"
        " z.writestr('openprogram/interfaces/server/_frontend/chat.html', "
        "'<body><div id=\"sidebar\"></div></body>')\n"
        "PYWHEEL\n"
        'case "$SOURCE_CHANGE" in\n'
        ' during_build) printf "VALUE = 2\\n" > "$REPO_ROOT/openprogram/source.py" ;;\n'
        ' untracked_during_build) touch "$REPO_ROOT/openprogram/new.py" ;;\n'
        ' main_during_build) git -C "$REPO_ROOT" update-ref refs/heads/main "$NEW_COMMIT" ;;\n'
        ' committed_during_build) '
        'if [ "$(git -C "$REPO_ROOT" rev-parse HEAD)" != "$NEW_COMMIT" ]; then '
        'git -C "$REPO_ROOT" update-ref refs/heads/candidate "$NEW_COMMIT"; fi ;;\n'
        'esac\n'
    )
    # A second committed revision with the same tree models another writer
    # advancing a ref without unrelated fixture changes.
    new_commit = subprocess.check_output(
        ["git", "-C", str(repo), "commit-tree", "HEAD^{tree}", "-p", "HEAD"],
        input="concurrent commit\n", text=True,
    ).strip()
    if change in {"tracked", "staged"}:
        source.write_text("VALUE = 2\n")
        if change == "staged":
            git("add", str(source))
    elif change == "untracked":
        (repo / "openprogram/new.py").write_text("VALUE = 2\n")
    elif change == "stale":
        git("update-ref", "refs/heads/main", new_commit)

    local_python = Path(env["OPENPROGRAM_LOCAL_PYTHON"])
    original_python = local_python.read_text()
    local_python.write_text(original_python.replace(
        'case "${1:-}" in',
        'case "$*" in *verify-release-version.py*--wheel*)\n'
        ' case "$SOURCE_CHANGE" in\n'
        '  after_build) printf "VALUE = 2\\n" > "$REPO_ROOT/openprogram/source.py" ;;\n'
        '  head_after_build) git -C "$REPO_ROOT" update-ref refs/heads/candidate "$NEW_COMMIT" ;;\n'
        '  main_after_build) git -C "$REPO_ROOT" update-ref refs/heads/main "$NEW_COMMIT" ;;\n'
        ' esac ;; esac\ncase "${1:-}" in',
    ))
    build_log = tmp_path / "build.log"
    env |= {"SOURCE_CHANGE": change, "NEW_COMMIT": new_commit,
            "BUILD_LOG": str(build_log), "OPENPROGRAM_REFRESH_LOCK_HELD": "1"}
    lock_file = app.parent / ".openprogram-app-install.lock"
    if change == "locked":
        lock_file.write_text(f"{os.getpid()}\n")
    installed_before = {str(p.relative_to(app)): p.read_bytes()
                        for p in app.rglob("*") if p.is_file()}
    result = subprocess.run(
        ["bash", str(script)], env=env, capture_output=True, text=True, timeout=SCRIPT_TIMEOUT,
    )
    output = result.stdout + result.stderr
    mutation_log = Path(env["MUTATION_LOG"])
    assert result.returncode != 0  # Native runtime actions are deliberately fake.
    if change in {"clean", "committed_during_build"} or target_alias == "custom":
        # Past every gate the new App is assembled beside the installed one;
        # the fake App fails real signing there, before anything is stopped.
        assert "assembling the new App beside the installed one" in output, output
        assert "nothing was stopped or replaced" in output, output
        assert not mutation_log.exists(), output
        assert "refusing to refresh the default App" not in output
        assert len(build_log.read_text().splitlines()) == (2 if change == "committed_during_build" else 1)
    elif change == "locked":
        assert "another OpenProgram App installation is running" in output, output
        assert not mutation_log.exists(), output
        assert len(build_log.read_text().splitlines()) == 1
    else:
        assert "refusing to refresh the default App" in output, output
        assert not mutation_log.exists(), output
        if change in {"stale", "main_during_build", "main_after_build"}:
            assert "behind refs/heads/main" in output
        elif change == "head_after_build":
            assert "HEAD changed after packaging" in output
        else:
            assert "uncommitted source changes" in output
        if change in {"tracked", "staged", "untracked", "stale"}:
            assert not build_log.exists()
    assert {str(p.relative_to(app)): p.read_bytes()
            for p in app.rglob("*") if p.is_file()} == installed_before
    assert not list(app.parent.glob(".openprogram-refresh.*"))
    if change == "locked":
        assert lock_file.read_text() == f"{os.getpid()}\n"
    else:
        assert not lock_file.exists()
    assert git("rev-parse", "HEAD") == (new_commit if change in {
        "committed_during_build", "head_after_build",
    } else initial_head)


PREVIOUS_REVISION = "previous-revision-of-the-app"


def _prepare_completion_refresh_fixture(
    tmp_path: Path, executables: ExecutablePool, *, service: bool,
):
    repo, app, env = _prepare_local_refresh_fixture(tmp_path, executables)
    env["OPENPROGRAM_REFRESH_DETACHED"] = "1"
    fake_bin = tmp_path / "fake-bin"
    # Keep the public script/build/version gates; replace external build,
    # signing, launch and process tools, never touching a real App or worker.
    uv = Path(env["OPENPROGRAM_UV_BIN"])
    uv.write_text(uv.read_text().replace("0.6.1", "0.6.6"))
    local = Path(env["OPENPROGRAM_LOCAL_PYTHON"])
    local.write_text(
        '#!/bin/sh\nset -eu\n'
        + PIP_WHEEL_SHIM +
        'case "$*" in\n'
        ' *verify-release-version.py*) exec "$REAL_PYTHON" "$@" ;;\n'
        ' *.py*) exit 0 ;;\n'
        'esac\n'
        'case "${1:-}" in -) exec "$REAL_PYTHON" "$@" ;; esac\n'
        'printf "local %s\\n" "$*" >> "$MUTATION_LOG"\n'
    )
    (app / "Contents/Frameworks").mkdir()
    (app / "Contents/Resources/openprogram-source-revision").write_text(PREVIOUS_REVISION + "\n")
    # The embedded interpreter tells the previous App from the new one by the
    # source revision beside it, so a failure can be made the new version's.
    embedded = app / "Contents/Resources/runtime/python/bin/python3"
    executables.write(
        embedded,
        '#!/bin/sh\nset -eu\n'
        'case "$*" in *"m.version"*) echo 0.6.6; exit 0 ;; esac\n'
        'case "$*" in *verify-product-runtime.py*) exit "${VERIFY_RC:-0}" ;; esac\n'
        'revision=$(sed -n 1p "$(dirname "$0")/../../../openprogram-source-revision" 2>/dev/null || :)\n'
        'role=new; test "$revision" != "$PREVIOUS_REV" || role=previous\n'
        'printf "%s %s\\n" "$role" "$*" >> "$MUTATION_LOG"\n'
        'fail() { test "$role" = previous || exit "$1"; exit 0; }\n'
        'case "$*" in\n'
        ' *"worker install"*|*"worker start"*) echo "$role" > "$WORKER_ROLE"; '
        'echo "${OPENPROGRAM_PROMOTE_FROM-unset}" > "$WORKER_ROLE.promote"; fail "${INSTALL_RC:-0}" ;;\n'
        ' *"-I -B - "*) body=$(cat); '
        'case "$body" in *current_worker_pid*) fail "${OWNER_RC:-0}" ;; esac ;;\n'
        'esac\nexit 0\n'
,
    )
    for name, body in {
        # The health endpoint answers for the worker that was started last.
        "curl": 'printf "%s\\n" "$*" >> "$PROBE_ARGS"; role=$(cat "$WORKER_ROLE" 2>/dev/null || echo none); '
                'test "$role" != none || exit 7; '
                'test "$role" = new || exit "${PREVIOUS_HEALTH_RC:-0}"; '
                'n=0; test ! -f "$PROBE_COUNT" || n=$(cat "$PROBE_COUNT"); '
                'n=$((n+1)); echo "$n" > "$PROBE_COUNT"; '
                'test "$n" -ge "${HEALTH_AFTER:-1}" || exit 7\n',
        "sleep": 'exec /bin/sleep 0.01\n',
        "pgrep": 'test -f "$OPENED" && test "${REOPEN_VISIBLE:-1}" = 1\n',
        "open": 'echo "$*" >> "$OPEN_LOG"; touch "$OPENED"; exit "${OPEN_RC:-0}"\n',
        "codesign": 'case "$*" in *--verify*) exit "${SIGN_RC:-0}" ;; esac\n',
        "osascript": 'exit 0\n',
        "pkill": 'exit 0\n',
    }.items():
        p = fake_bin / name
        executables.write(p, "#!/bin/sh\n" + body)
    env.update(PROBE_COUNT=str(tmp_path / "probes"), PROBE_ARGS=str(tmp_path / "probe-args"), OPEN_LOG=str(tmp_path / "opens"),
               OPENED=str(tmp_path / "opened"), WORKER_ROLE=str(tmp_path / "worker-role"),
               PREVIOUS_REV=PREVIOUS_REVISION)
    # The previous worker is serving when the refresh begins.
    Path(env["WORKER_ROLE"]).write_text("previous\n")
    if service:
        plist = Path(env["HOME"]) / "Library/LaunchAgents/ai.openprogram.worker.plist"
        plist.parent.mkdir(parents=True)
        plist.write_text("fake managed service")
    return repo, app, env


def _app_files(app: Path) -> dict[str, bytes]:
    return {str(p.relative_to(app)): p.read_bytes() for p in app.rglob("*") if p.is_file()}


def _shorten_health_wait(script: Path) -> None:
    source = script.read_text()
    shortened, replaced = re.subn(r"^health_timeout=\d+$", "health_timeout=1", source, flags=re.M)
    assert replaced == 1
    script.write_text(shortened)


# What each case breaks, its exit status, and how the refresh ends:
# "staging" fails before anything is stopped, "restored" puts the previous
# App and worker back, "new" keeps the healthy new version.
COMPLETION_CASES = {
    "delayed": ({"HEALTH_AFTER": "56"}, 0, "new"),
    "timeout": ({"HEALTH_AFTER": "999999"}, 7, "restored"),
    "timeout_open_failure": ({"HEALTH_AFTER": "999999", "OPEN_RC": "42"}, 7, "restored"),
    "owner_failure": ({"OWNER_RC": "23"}, 23, "restored"),
    "install_failure": ({"INSTALL_RC": "29"}, 29, "restored"),
    "sign_failure": ({"SIGN_RC": "31"}, 31, "staging"),
    "verify_failure": ({"VERIFY_RC": "17"}, 17, "staging"),
    "open_failure": ({"OPEN_RC": "42"}, 42, "new"),
    "restore_failure": ({"HEALTH_AFTER": "999999", "PREVIOUS_HEALTH_RC": "7"}, 7, "broken"),
}


@POSIX_SHELL_INTEGRATION
@pytest.mark.parametrize(("case", "service"), [
    ("delayed", True), ("delayed", False), ("timeout", True), ("timeout", False),
    *[(case, True) for case in COMPLETION_CASES if case not in {"delayed", "timeout"}],
])
def test_local_refresh_completion_preserves_failure_and_restores_the_previous_version(
    tmp_path: Path, executables: ExecutablePool, case: str, service: bool,
) -> None:
    repo, app, env = _prepare_completion_refresh_fixture(tmp_path, executables, service=service)
    script = repo / "scripts/refresh-local-app.sh"
    breaks, expected, outcome = COMPLETION_CASES[case]
    env.update(breaks)
    if "HEALTH_AFTER" in breaks and breaks["HEALTH_AFTER"] == "999999":
        _shorten_health_wait(script)
    before = _app_files(app)
    result = subprocess.run(
        ["bash", str(script)], env=env, capture_output=True, text=True, timeout=SCRIPT_TIMEOUT,
    )
    output = result.stdout + result.stderr
    assert result.returncode == expected, output
    calls = Path(env["MUTATION_LOG"]).read_text().splitlines() if Path(env["MUTATION_LOG"]).exists() else []
    starts = [c for c in calls if c.endswith(("-I -B -m openprogram worker install",
                                               "-I -B -m openprogram worker start"))]
    start = "worker install" if service else "worker start"
    opens = Path(env["OPEN_LOG"])
    leftovers = list(app.parent.glob(".openprogram-refresh.*"))
    if outcome == "staging":
        # Signing and verification run on the copy beside the App: the old
        # worker was never stopped and the installed App is byte-identical.
        assert not any("worker" in call for call in calls), output
        assert _app_files(app) == before
        assert not opens.exists(), output
        assert "nothing was stopped or replaced, so the previous version (previous-r) is still running" in output
        assert not leftovers
    elif outcome == "restored":
        assert starts == [f"new -I -B -m openprogram {start}", f"previous -I -B -m openprogram {start}"], output
        assert _app_files(app) == before
        assert Path(env["WORKER_ROLE"]).read_text().strip() == "previous"
        assert "the previous version (previous-r) is running again" in output
        # The previous App reopens behind what the user is doing.
        assert opens.read_text().splitlines() == [f"-g -a {app}"], output
        assert not leftovers
    elif outcome == "broken":
        assert "restoring the previous version failed while waiting for the previous worker to answer" in output
        # The previous App is back in place; the files of the attempt are kept.
        assert _app_files(app) == before
        assert len(leftovers) == 1 and str(leftovers[0]) in output
        assert not opens.exists(), output
    else:
        assert starts == [f"new -I -B -m openprogram {start}"], output
        assert (app / "Contents/Resources/openprogram-source-revision").read_text().strip() != PREVIOUS_REVISION
        assert Path(env["WORKER_ROLE"]).read_text().strip() == "new"
        assert opens.read_text().splitlines() == [f"-g -a {app}"], output
        assert not leftovers
    args_file = Path(env["PROBE_ARGS"])
    if args_file.exists():
        for probe in args_file.read_text().splitlines():
            assert "--connect-timeout 1" in probe
            assert re.search(r"--max-time [12](?: |$)", probe)
    if case.startswith("timeout"):
        assert "worker health did not become ready" in output
        assert str(Path(env["HOME"]) / ".openprogram/worker.log") in output
    if "open_failure" in case:
        assert "OpenProgram did not reopen after the refresh" in output
    if case == "open_failure":
        assert "is installed and its worker is healthy" in output
    assert not (app.parent / ".openprogram-app-install.lock").exists()
    assert not list(Path(env["TMPDIR"]).glob("openprogram-local-wheel.*"))


def _prepare_promotion_refresh_fixture(tmp_path: Path, executables: ExecutablePool):
    """A refresh run by `openprogram dev promote`: a real Git repository whose
    `stable` is one commit behind the checked-out commit to promote."""
    repo, app, env = _prepare_completion_refresh_fixture(tmp_path, executables, service=True)
    script = repo / "scripts/refresh-local-app.sh"
    # Remap only the canonical App path in the copied public script.
    script.write_text(script.read_text().replace('"/Applications/OpenProgram.app"', f'"{app}"'))
    (tmp_path / "fake-bin/git").unlink()
    # Builds a wheel without touching the source, so the checkout stays clean.
    Path(env["OPENPROGRAM_UV_BIN"]).write_text(
        "#!/bin/sh\nset -eu\n"
        'case "${1:-}" in export) while [ "$#" -gt 0 ]; do '
        'if [ "$1" = "--output-file" ]; then : > "$2"; fi; shift; done; exit 0 ;; '
        'pip) exit 0 ;; esac\n'
        'out=; while [ "$#" -gt 0 ]; do '
        'if [ "$1" = "--out-dir" ]; then out="$2"; shift 2; else shift; fi; done\n'
        'mkdir -p "$out"\n'
        'exec "$REAL_PYTHON" - "$out/openprogram-0.6.6-py3-none-any.whl" <<\'PYWHEEL\'\n'
        "import sys, zipfile\n"
        "with zipfile.ZipFile(sys.argv[1], 'w') as z:\n"
        " z.writestr('openprogram-0.6.6.dist-info/METADATA', "
        "'Metadata-Version: 2.1\\nName: openprogram\\nVersion: 0.6.6\\n')\n"
        " z.writestr('openprogram/interfaces/server/_frontend/chat.html', "
        "'<body><div id=\"sidebar\"></div></body>')\n"
        "PYWHEEL\n"
    )

    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(repo), *args], text=True,
            env=os.environ | {"GIT_CONFIG_NOSYSTEM": "1"},
        ).strip()

    git("init", "-q", "-b", "dev")
    git("config", "user.name", "Refresh Test")
    git("config", "user.email", "refresh@example.invalid")
    git("config", "commit.gpgsign", "false")
    git("config", "core.hooksPath", "/dev/null")
    git("add", ".")
    git("commit", "-qm", "what stable runs")
    stable = git("rev-parse", "HEAD")
    git("branch", "stable")
    (repo / "promoted.txt").write_text("promoted\n")
    git("add", "promoted.txt")
    git("commit", "-qm", "the commit to promote")
    target = git("rev-parse", "HEAD")
    env["OPENPROGRAM_PROMOTE_FROM"] = stable
    return script, app, env, git, stable, target


@POSIX_SHELL_INTEGRATION
def test_a_promotion_whose_runtime_fails_verification_changes_nothing(
    tmp_path: Path, executables: ExecutablePool,
) -> None:
    script, app, env, git, stable, target = _prepare_promotion_refresh_fixture(tmp_path, executables)
    env["VERIFY_RC"] = "1"
    before = _app_files(app)
    result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True,
                            timeout=SCRIPT_TIMEOUT)
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert "verifying the new runtime" in output
    calls = Path(env["MUTATION_LOG"]).read_text().splitlines()
    assert not any("worker" in call for call in calls), output
    assert Path(env["WORKER_ROLE"]).read_text().strip() == "previous"
    assert _app_files(app) == before
    assert git("rev-parse", "stable") == stable
    assert "nothing was stopped or replaced, so the previous version (previous-r) is still running" in output
    assert not list(app.parent.glob(".openprogram-refresh.*"))


@POSIX_SHELL_INTEGRATION
def test_a_promotion_whose_worker_never_answers_restores_the_previous_version(
    tmp_path: Path, executables: ExecutablePool,
) -> None:
    script, app, env, git, stable, target = _prepare_promotion_refresh_fixture(tmp_path, executables)
    _shorten_health_wait(script)
    git("commit", "-qam", "a shorter health wait")
    target = git("rev-parse", "HEAD")
    env["HEALTH_AFTER"] = "999999"
    before = _app_files(app)
    result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True,
                            timeout=SCRIPT_TIMEOUT)
    output = result.stdout + result.stderr
    assert result.returncode == 7, output
    assert "the refresh failed while waiting for the worker to answer" in output
    assert "the previous version (previous-r) is running again" in output
    calls = Path(env["MUTATION_LOG"]).read_text().splitlines()
    assert "previous -I -B -m openprogram worker stop" in calls  # The outage happened.
    assert [c for c in calls if c.endswith("worker install")] == [
        "new -I -B -m openprogram worker install", "previous -I -B -m openprogram worker install"]
    assert Path(env["WORKER_ROLE"]).read_text().strip() == "previous"
    assert _app_files(app) == before
    assert git("rev-parse", "stable") == stable != target
    assert not list(app.parent.glob(".openprogram-refresh.*"))


@POSIX_SHELL_INTEGRATION
def test_a_promotion_moves_stable_once_the_new_version_is_healthy(
    tmp_path: Path, executables: ExecutablePool,
) -> None:
    script, app, env, git, stable, target = _prepare_promotion_refresh_fixture(tmp_path, executables)
    result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True,
                            timeout=SCRIPT_TIMEOUT)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert git("rev-parse", "stable") == target
    assert (app / "Contents/Resources/openprogram-source-revision").read_text().strip() == target
    assert Path(env["WORKER_ROLE"]).read_text().strip() == "new"
    assert output.index("worker healthy") < output.index(f"moving stable to {target[:10]}")
    assert git("reflog", "-1", "--format=%gs", "stable") == "promote from dev"
    assert not list(app.parent.glob(".openprogram-refresh.*"))
    # A worker started detached inherits the environment; a refresh run from
    # it later must not take itself for this promotion.
    assert Path(env["WORKER_ROLE"] + ".promote").read_text().strip() == "unset"


@POSIX_SHELL_INTEGRATION
def test_a_signal_between_the_app_renames_still_restores_the_previous_version(
    tmp_path: Path, executables: ExecutablePool,
) -> None:
    script, app, env, git, stable, target = _prepare_promotion_refresh_fixture(tmp_path, executables)
    # The signal lands right after the installed App is renamed aside, before
    # the script can record that it was.
    executables.write(
        tmp_path / "fake-bin/mv",
        '#!/bin/sh\n/bin/mv "$@" || exit\n'
        'case "$2" in */previous.app) kill -TERM "$PPID" ;; esac\n',
    )
    before = _app_files(app)
    result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True,
                            timeout=SCRIPT_TIMEOUT)
    output = result.stdout + result.stderr
    assert result.returncode == 143, output
    assert "the previous version (previous-r) is running again" in output
    assert _app_files(app) == before
    assert Path(env["WORKER_ROLE"]).read_text().strip() == "previous"
    assert git("rev-parse", "stable") == stable
    assert not list(app.parent.glob(".openprogram-refresh.*"))


@POSIX_SHELL_INTEGRATION
def test_a_promotion_refuses_when_stable_moved_since_it_began(
    tmp_path: Path, executables: ExecutablePool,
) -> None:
    script, app, env, git, stable, target = _prepare_promotion_refresh_fixture(tmp_path, executables)
    git("branch", "-f", "stable", target)
    before = _app_files(app)
    result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True,
                            timeout=SCRIPT_TIMEOUT)
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "not " + stable + " as when the promotion began" in output, output
    assert not Path(env["MUTATION_LOG"]).exists()
    assert _app_files(app) == before


@POSIX_SHELL_INTEGRATION
@pytest.mark.parametrize("primary", [0, 23])
def test_local_refresh_preserves_cleanup_error_without_masking_primary(
    tmp_path: Path, executables: ExecutablePool, primary: int,
) -> None:
    repo, app, env = _prepare_completion_refresh_fixture(tmp_path, executables, service=True)
    env["OWNER_RC"] = str(primary)
    fake_rm = tmp_path / "fake-bin/rm"
    executables.write(
        fake_rm,
        '#!/bin/sh\n'
        'case "${2:-}" in "$TMPDIR"/openprogram-local-wheel.*) '
        'test "${2%/*}" != "$TMPDIR" || exit 47 ;; esac\n'
        'exec /bin/rm "$@"\n',
    )
    result = subprocess.run(
        ["bash", str(repo / "scripts/refresh-local-app.sh")], env=env,
        capture_output=True, text=True, timeout=SCRIPT_TIMEOUT,
    )
    assert result.returncode == (primary or 47), result.stdout + result.stderr
    assert "refreshed " not in result.stdout + result.stderr
    assert "refresh cleanup failed (exit 47)" in result.stderr
    assert Path(env["OPEN_LOG"]).read_text().splitlines() == [f"-g -a {app}"]
    assert not (app.parent / ".openprogram-app-install.lock").exists()
    assert list(Path(env["TMPDIR"]).glob("openprogram-local-wheel.*"))


def test_stale_package_cleanup_removes_a_dist_info_left_without_metadata(
    tmp_path: Path,
) -> None:
    # An upgrade from 0.9.8 left openprogram-0.9.8.dist-info holding only its
    # LICENSE; pip read it as "openprogram None" and refused the 1.0.0 install.
    helper = runpy.run_path(
        str(ROOT / "scripts/release/remove-stale-openprogram-packages.py")
    )
    site_packages = tmp_path / "site-packages"
    leftover = site_packages / "openprogram-0.9.8.dist-info"
    leftover.mkdir(parents=True)
    (leftover / "LICENSE").write_text("license\n", encoding="utf-8")
    current = site_packages / "openprogram-1.0.0.dist-info"
    current.mkdir()
    (current / "METADATA").write_text("Name: openprogram\nVersion: 1.0.0\n", encoding="utf-8")

    helper["remove_stale_package_trees"](site_packages)

    assert not leftover.exists()
    assert (current / "METADATA").exists()
