from __future__ import annotations

import json
import os
import plistlib
import re
import runpy
import signal
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[3]
MACOS_DESKTOP_INSTALL = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="requires macOS app bundle and LaunchServices tools",
)


def _desktop_package() -> dict:
    return json.loads((ROOT / "apps" / "desktop" / "package.json").read_text(encoding="utf-8"))


def test_desktop_targets_and_embedded_runtime_are_declared() -> None:
    package = _desktop_package()
    build = package["build"]
    mac_targets = {
        target if isinstance(target, str) else target["target"]
        for target in build["mac"]["target"]
    }
    assert {"dmg", "zip"} <= mac_targets
    assert "linux" not in build
    assert "dist:linux" not in package["scripts"]
    assert {item["to"] for item in build["extraResources"]} >= {"runtime"}
    resources = {item["to"]: item["from"] for item in build["extraResources"]}
    assert resources["update/install-app.sh"] == "scripts/install-app.sh"
    assert "worker-recovery-state.js" in build["files"]
    assert "tab-transfer-validation.js" in build["files"]
    assert package["desktopName"] == "ai.openprogram.OpenProgram.desktop"


def _fake_desktop_app(root: Path, version: str, *, app_id: str = "ai.openprogram.desktop") -> Path:
    app = root / "OpenProgram.app"
    executable = app / "Contents" / "MacOS" / "OpenProgram"
    resources = app / "Contents" / "Resources"
    runtime = resources / "runtime"
    runtime_python = runtime / "python" / "bin" / "python3"
    executable.parent.mkdir(parents=True)
    runtime_python.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    (resources / "icon.icns").write_bytes(b"icns")
    runtime_python.write_text(
        f"#!/bin/sh\nprintf '%s\\n' '{version}'\n",
        encoding="utf-8",
    )
    runtime_python.chmod(0o755)
    metadata = (
        runtime
        / "python/lib/python3.12/site-packages"
        / f"openprogram-{version}.dist-info/METADATA"
    )
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        f"Metadata-Version: 2.4\nName: openprogram\nVersion: {version}\n",
        encoding="utf-8",
    )
    (runtime / "runtime-manifest.json").write_text(
        json.dumps(
            {
                "schema": 2,
                "openprogram": version,
                "python": "python/bin/python3",
            }
        ),
        encoding="utf-8",
    )
    with (app / "Contents" / "Info.plist").open("wb") as stream:
        plistlib.dump(
            {
                "CFBundleIdentifier": app_id,
                "CFBundleShortVersionString": version,
                "CFBundleExecutable": "OpenProgram",
                "CFBundleIconFile": "icon.icns",
            },
            stream,
        )
    return app


@pytest.mark.macos
@MACOS_DESKTOP_INSTALL
def test_local_desktop_build_installs_one_canonical_app(tmp_path: Path) -> None:
    package = _desktop_package()
    assert package["scripts"]["dist"] == "npm run app:install"
    assert package["scripts"]["app:install"] == "bash scripts/package-and-install-app.sh"
    assert "dist:dir" not in package["scripts"]

    installer = ROOT / "apps" / "desktop" / "scripts" / "install-app.sh"
    packager = (ROOT / "apps" / "desktop" / "scripts" / "package-and-install-app.sh").read_text(
        encoding="utf-8"
    )
    installer_text = installer.read_text(encoding="utf-8")
    assert 'target_app="$applications_dir/OpenProgram.app"' in installer_text
    assert 'open "$target_app"' in installer_text
    assert 'open "$source_app"' not in installer_text
    assert installer_text.count(
        '"$launch_services_register" -f "$target_app"'
    ) == 2
    rollback_start = installer_text.index(
        'if [[ "$old_moved" == 1 && "$activated" == 0'
    )
    rollback_end = installer_text.index(
        'if [[ "$status" != 0 && "$resume_after_failure"', rollback_start
    )
    rollback = installer_text[rollback_start:rollback_end]
    assert rollback.index('mv "$previous_app" "$target_app"') < rollback.index(
        '"$launch_services_register" -f "$target_app"'
    )
    assert (
        '"$launch_services_register" -f "$target_app" >/dev/null 2>&1 || :'
        in rollback
    )
    assert 'openprogram worker stop' in installer_text
    assert 'openprogram worker uninstall' in installer_text
    assert 'openprogram worker install' in installer_text
    assert 'wait_for_worker_health' in installer_text
    assert "process.stdout.write(python);\nNODE\n}\n\nwait_for_worker_health()" in installer_text
    assert installer_text.index('openprogram worker uninstall') < installer_text.index(
        'openprogram worker stop'
    )
    assert installer_text.index('openprogram worker stop') < installer_text.index(
        'mv "$target_app" "$previous_app"'
    )
    wait_index = installer_text.index('wait_for_worker_health ||')
    assert wait_index < installer_text.index('open "$target_app"', wait_index)
    assert 'mktemp -d "${TMPDIR:-/tmp}/openprogram-app-package.XXXXXX"' in packager
    assert "npm exec --workspace apps/desktop -- electron-builder" in packager
    smoke = 'bash "$repo_root/scripts/release/smoke-packaged-runtime.sh" mac "$package_dir"'
    assert smoke in packager
    assert 'env -u DESTDIR bash "$script_dir/install-app.sh" "$built_app"' in packager
    assert packager.index(smoke) < packager.index(
        'env -u DESTDIR bash "$script_dir/install-app.sh" "$built_app"'
    )
    assert 'lock_root="$HOME/Library/Caches/OpenProgram"' in packager
    assert 'acquire_pid_lock "$lock_file"' in packager
    assert '"$web_build_dir" "$web_output_dir" "$frontend_stage_dir"' in packager
    assert 'rm -rf "$repo_root/build"' in (
        ROOT / "scripts" / "release" / "build-product-runtime.sh"
    ).read_text(encoding="utf-8")
    env = {
        "DESTDIR": str(tmp_path / "root"),
        "HOME": str(tmp_path / "home"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": str(tmp_path / "tmp"),
    }
    Path(env["TMPDIR"]).mkdir()

    first = _fake_desktop_app(tmp_path / "first", "0.6.1")
    subprocess.run(["bash", str(installer), str(first)], check=True, env=env)
    target = Path(env["DESTDIR"]) / "Applications" / "OpenProgram.app"
    assert target.is_dir()

    second = _fake_desktop_app(tmp_path / "second", "0.6.2")
    subprocess.run(["bash", str(installer), str(second)], check=True, env=env)
    with (target / "Contents" / "Info.plist").open("rb") as stream:
        assert plistlib.load(stream)["CFBundleShortVersionString"] == "0.6.2"

    applications = target.parent
    assert sorted(path.name for path in applications.glob("*.app")) == ["OpenProgram.app"]
    assert not list(applications.glob(".openprogram-app-install.*"))

    invalid = _fake_desktop_app(
        tmp_path / "invalid", "0.6.3", app_id="example.invalid"
    )
    failed = subprocess.run(
        ["bash", str(installer), str(invalid)],
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )
    assert failed.returncode != 0
    with (target / "Contents" / "Info.plist").open("rb") as stream:
        assert plistlib.load(stream)["CFBundleShortVersionString"] == "0.6.2"

    downgrade = _fake_desktop_app(tmp_path / "downgrade", "0.6.1")
    rejected = subprocess.run(
        ["bash", str(installer), str(downgrade)],
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode != 0
    assert "refusing to replace OpenProgram 0.6.2 with older version 0.6.1" in (
        rejected.stderr
    )
    with (target / "Contents" / "Info.plist").open("rb") as stream:
        assert plistlib.load(stream)["CFBundleShortVersionString"] == "0.6.2"


@pytest.mark.macos
@MACOS_DESKTOP_INSTALL
def test_local_desktop_install_compares_numeric_versions_as_decimal(
    tmp_path: Path,
) -> None:
    installer = ROOT / "apps" / "desktop" / "scripts" / "install-app.sh"
    env = {
        "DESTDIR": str(tmp_path / "root"),
        "HOME": str(tmp_path / "home"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": str(tmp_path / "tmp"),
    }
    Path(env["TMPDIR"]).mkdir()
    installed = _fake_desktop_app(tmp_path / "installed", "0.9.0")
    subprocess.run(["bash", str(installer), str(installed)], check=True, env=env)
    leading_zero = _fake_desktop_app(tmp_path / "leading-zero", "0.08.0")

    rejected = subprocess.run(
        ["bash", str(installer), str(leading_zero)],
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )

    assert rejected.returncode != 0
    assert "refusing to replace OpenProgram 0.9.0 with older version 0.08.0" in (
        rejected.stderr
    )
    target = Path(env["DESTDIR"]) / "Applications" / "OpenProgram.app"
    with (target / "Contents" / "Info.plist").open("rb") as stream:
        assert plistlib.load(stream)["CFBundleShortVersionString"] == "0.9.0"


@pytest.mark.macos
@MACOS_DESKTOP_INSTALL
def test_local_desktop_install_preserves_an_invalid_existing_app(
    tmp_path: Path,
) -> None:
    installer = ROOT / "apps" / "desktop" / "scripts" / "install-app.sh"
    env = {
        "DESTDIR": str(tmp_path / "root"),
        "HOME": str(tmp_path / "home"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": str(tmp_path / "tmp"),
    }
    Path(env["TMPDIR"]).mkdir()
    installed = _fake_desktop_app(tmp_path / "installed", "0.6.4")
    subprocess.run(["bash", str(installer), str(installed)], check=True, env=env)
    target = Path(env["DESTDIR"]) / "Applications" / "OpenProgram.app"
    runtime = target / "Contents" / "Resources" / "runtime"
    manifest = json.loads(
        (runtime / "runtime-manifest.json").read_text(encoding="utf-8")
    )
    runtime_python = runtime / manifest["python"]
    runtime_python.write_text(
        "#!/bin/sh\nprintf '%s\\n' '0.6.1'\n",
        encoding="utf-8",
    )
    runtime_python.chmod(0o755)
    candidate = _fake_desktop_app(tmp_path / "candidate", "0.6.2")

    rejected = subprocess.run(
        ["bash", str(installer), str(candidate)],
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )

    assert rejected.returncode != 0
    assert "existing OpenProgram app failed validation" in rejected.stderr
    with (target / "Contents" / "Info.plist").open("rb") as stream:
        assert plistlib.load(stream)["CFBundleShortVersionString"] == "0.6.4"
    assert runtime_python.read_text(encoding="utf-8").endswith("'0.6.1'\n")

@pytest.mark.macos
@MACOS_DESKTOP_INSTALL
def test_local_desktop_install_preserves_recovery_copy_when_restore_fails(
    tmp_path: Path,
) -> None:
    installer = ROOT / "apps" / "desktop" / "scripts" / "install-app.sh"
    env = {
        "DESTDIR": str(tmp_path / "root"),
        "HOME": str(tmp_path / "home"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": str(tmp_path / "tmp"),
    }
    Path(env["TMPDIR"]).mkdir()
    original = _fake_desktop_app(tmp_path / "original", "0.6.2")
    subprocess.run(["bash", str(installer), str(original)], check=True, env=env)
    target = Path(env["DESTDIR"]) / "Applications" / "OpenProgram.app"

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    move_count = tmp_path / "move-count"
    fake_mv = fake_bin / "mv"
    fake_mv.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'count="$(cat "$FAKE_MV_COUNT" 2>/dev/null || printf 0)"\n'
        'count="$((count + 1))"\n'
        'printf "%s\\n" "$count" >"$FAKE_MV_COUNT"\n'
        'if [ "$count" -ge 2 ]; then exit 73; fi\n'
        'exec /bin/mv "$@"\n',
        encoding="utf-8",
    )
    fake_mv.chmod(0o755)
    replacement = _fake_desktop_app(tmp_path / "replacement", "0.6.4")
    failed_restore_env = env | {
        "PATH": f"{fake_bin}:{env['PATH']}",
        "FAKE_MV_COUNT": str(move_count),
    }
    failed_restore = subprocess.run(
        ["bash", str(installer), str(replacement)],
        check=False,
        env=failed_restore_env,
        capture_output=True,
        text=True,
    )

    assert failed_restore.returncode != 0
    assert not target.exists()
    recovery_dirs = list(target.parent.glob(".openprogram-app-install.*"))
    assert len(recovery_dirs) == 1
    recovered_app = recovery_dirs[0] / "previous.app"
    with (recovered_app / "Contents" / "Info.plist").open("rb") as stream:
        assert plistlib.load(stream)["CFBundleShortVersionString"] == "0.6.2"
    assert str(recovered_app) in failed_restore.stderr


@pytest.mark.macos
@MACOS_DESKTOP_INSTALL
def test_concurrent_local_desktop_install_cannot_nest_the_canonical_app(
    tmp_path: Path,
) -> None:
    installer = ROOT / "apps" / "desktop" / "scripts" / "install-app.sh"
    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()
    env = {
        "DESTDIR": str(tmp_path / "root"),
        "HOME": str(tmp_path / "home"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": str(temp_dir),
    }
    original = _fake_desktop_app(tmp_path / "original", "0.6.0")
    subprocess.run(["bash", str(installer), str(original)], check=True, env=env)

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    entered = tmp_path / "ditto-entered"
    release = tmp_path / "ditto-release"
    fake_ditto = fake_bin / "ditto"
    fake_ditto.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'touch "$DITTO_ENTERED"\n'
        'while [ ! -f "$DITTO_RELEASE" ]; do sleep 0.01; done\n'
        'exec /usr/bin/ditto "$@"\n',
        encoding="utf-8",
    )
    fake_ditto.chmod(0o755)
    concurrent_env = env | {
        "PATH": f"{fake_bin}:{env['PATH']}",
        "DITTO_ENTERED": str(entered),
        "DITTO_RELEASE": str(release),
    }
    first_source = _fake_desktop_app(tmp_path / "first-concurrent", "0.6.1")
    second_source = _fake_desktop_app(tmp_path / "second-concurrent", "0.6.2")

    first = subprocess.Popen(
        ["bash", str(installer), str(first_source)],
        env=concurrent_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not entered.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert entered.exists()
        second = subprocess.run(
            ["bash", str(installer), str(second_source)],
            check=False,
            env=concurrent_env,
            capture_output=True,
            text=True,
            timeout=5,
        )
    finally:
        release.touch()
        first_stdout, first_stderr = first.communicate(timeout=10)

    assert first.returncode == 0, (first_stdout, first_stderr)
    assert second.returncode != 0
    assert "another OpenProgram App installation is running" in second.stderr
    target = Path(env["DESTDIR"]) / "Applications" / "OpenProgram.app"
    with (target / "Contents" / "Info.plist").open("rb") as stream:
        assert plistlib.load(stream)["CFBundleShortVersionString"] == "0.6.1"
    assert not (target / "OpenProgram.app").exists()
    assert sorted(path.name for path in target.parent.glob("*.app")) == [
        "OpenProgram.app"
    ]
    assert not (target.parent / ".openprogram-app-install.lock").exists()


@pytest.mark.macos
@MACOS_DESKTOP_INSTALL
def test_local_desktop_install_rechecks_downgrade_after_lock(
    tmp_path: Path,
) -> None:
    installer = ROOT / "apps" / "desktop" / "scripts" / "install-app.sh"
    instrumented = tmp_path / "install-app-with-barrier.sh"
    installer_text = installer.read_text(encoding="utf-8")
    marker = 'reject_downgrade "$source_app"\n\nmkdir -p'
    assert installer_text.count(marker) == 1
    instrumented.write_text(
        installer_text.replace(
            marker,
            'reject_downgrade\n'
            'touch "$TOCTOU_CHECKED"\n'
            'while [[ ! -f "$TOCTOU_RELEASE" ]]; do sleep 0.01; done\n\n'
            "mkdir -p",
        ),
        encoding="utf-8",
    )
    env = {
        "DESTDIR": str(tmp_path / "root"),
        "HOME": str(tmp_path / "home"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": str(tmp_path / "tmp"),
    }
    Path(env["TMPDIR"]).mkdir()
    installed = _fake_desktop_app(tmp_path / "installed", "0.6.1")
    subprocess.run(["bash", str(installer), str(installed)], check=True, env=env)

    checked = tmp_path / "checked"
    release = tmp_path / "release"
    stale_candidate = _fake_desktop_app(tmp_path / "stale", "0.6.2")
    stale = subprocess.Popen(
        ["bash", str(instrumented), str(stale_candidate)],
        env=env | {"TOCTOU_CHECKED": str(checked), "TOCTOU_RELEASE": str(release)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not checked.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert checked.exists()
        newer = _fake_desktop_app(tmp_path / "newer", "0.6.3")
        subprocess.run(["bash", str(installer), str(newer)], check=True, env=env)
    finally:
        release.touch()
        stale_stdout, stale_stderr = stale.communicate(timeout=10)

    assert stale.returncode != 0, stale_stdout
    assert "refusing to replace OpenProgram 0.6.3 with older version 0.6.2" in (
        stale_stderr
    )
    target = Path(env["DESTDIR"]) / "Applications" / "OpenProgram.app"
    with (target / "Contents" / "Info.plist").open("rb") as stream:
        assert plistlib.load(stream)["CFBundleShortVersionString"] == "0.6.3"


@pytest.mark.macos
@MACOS_DESKTOP_INSTALL
def test_local_desktop_install_compares_the_staged_candidate(
    tmp_path: Path,
) -> None:
    installer = ROOT / "apps" / "desktop" / "scripts" / "install-app.sh"
    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()
    env = {
        "DESTDIR": str(tmp_path / "root"),
        "HOME": str(tmp_path / "home"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": str(temp_dir),
    }
    installed = _fake_desktop_app(tmp_path / "installed", "0.6.3")
    subprocess.run(["bash", str(installer), str(installed)], check=True, env=env)

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    entered = tmp_path / "ditto-entered"
    release = tmp_path / "ditto-release"
    fake_ditto = fake_bin / "ditto"
    fake_ditto.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'touch "$DITTO_ENTERED"\n'
        'while [ ! -f "$DITTO_RELEASE" ]; do sleep 0.01; done\n'
        'exec /usr/bin/ditto "$@"\n',
        encoding="utf-8",
    )
    fake_ditto.chmod(0o755)
    candidate = _fake_desktop_app(tmp_path / "candidate", "0.6.4")
    candidate_env = env | {
        "PATH": f"{fake_bin}:{env['PATH']}",
        "DITTO_ENTERED": str(entered),
        "DITTO_RELEASE": str(release),
    }
    installing = subprocess.Popen(
        ["bash", str(installer), str(candidate)],
        env=candidate_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not entered.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert entered.exists()
        plist_path = candidate / "Contents" / "Info.plist"
        with plist_path.open("rb") as stream:
            plist = plistlib.load(stream)
        plist["CFBundleShortVersionString"] = "0.6.2"
        with plist_path.open("wb") as stream:
            plistlib.dump(plist, stream)
        runtime = candidate / "Contents" / "Resources" / "runtime"
        manifest_path = runtime / "runtime-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["openprogram"] = "0.6.2"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        runtime_python = runtime / manifest["python"]
        runtime_python.write_text(
            "#!/bin/sh\nprintf '%s\\n' '0.6.2'\n",
            encoding="utf-8",
        )
        runtime_python.chmod(0o755)
        metadata_path = next(runtime.rglob("openprogram-*.dist-info/METADATA"))
        metadata_path.write_text(
            "Metadata-Version: 2.4\nName: openprogram\nVersion: 0.6.2\n",
            encoding="utf-8",
        )
    finally:
        release.touch()
        install_stdout, install_stderr = installing.communicate(timeout=10)

    assert installing.returncode != 0, install_stdout
    assert "refusing to replace OpenProgram 0.6.3 with older version 0.6.2" in (
        install_stderr
    )
    target = Path(env["DESTDIR"]) / "Applications" / "OpenProgram.app"
    with (target / "Contents" / "Info.plist").open("rb") as stream:
        assert plistlib.load(stream)["CFBundleShortVersionString"] == "0.6.3"


@pytest.mark.macos
@MACOS_DESKTOP_INSTALL
def test_packager_honors_one_stable_user_lock_across_worktrees(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    lock_file = home / "Library" / "Caches" / "OpenProgram" / "app-package.lock"
    lock_file.parent.mkdir(parents=True)
    lock_file.write_text(f"{os.getpid()}\n", encoding="utf-8")
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": str(tmp_path),
    }

    competing = subprocess.run(
        ["bash", str(ROOT / "apps" / "desktop" / "scripts" / "package-and-install-app.sh")],
        check=False,
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert competing.returncode != 0
    assert "another OpenProgram App package is running" in competing.stderr
    assert lock_file.read_text(encoding="utf-8").strip() == str(os.getpid())


@pytest.mark.macos
@MACOS_DESKTOP_INSTALL
def test_packager_build_only_writes_artifact_without_installing(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    source = _fake_desktop_app(tmp_path / "built", "0.6.4")
    installed_marker = tmp_path / "installer-called"

    fake_npm = fake_bin / "npm"
    fake_npm.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "for arg in \"$@\"; do\n"
        "  case \"$arg\" in\n"
        "    --config.directories.output=*)\n"
        "      output=${arg#*=}\n"
        "      mkdir -p \"$output/mac\"\n"
        "      /usr/bin/ditto \"$FAKE_BUILT_APP\" \"$output/mac/OpenProgram.app\"\n"
        "      ;;\n"
        "  esac\n"
        "done\n",
        encoding="utf-8",
    )
    fake_npm.chmod(0o755)
    fake_bash = fake_bin / "bash"
    fake_bash.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  */smoke-packaged-runtime.sh) exit 0 ;;\n"
        "  */install-app.sh) touch \"$INSTALL_CALLED\"; exit 99 ;;\n"
        "esac\n"
        "exec /bin/bash \"$@\"\n",
        encoding="utf-8",
    )
    fake_bash.chmod(0o755)

    output = tmp_path / "artifact" / "OpenProgram.app"
    completed = subprocess.run(
        [
            "/bin/bash",
            str(ROOT / "apps" / "desktop" / "scripts" / "package-and-install-app.sh"),
            "--output",
            str(output),
        ],
        check=False,
        env={
            "FAKE_BUILT_APP": str(source),
            "HOME": str(tmp_path / "home"),
            "INSTALL_CALLED": str(installed_marker),
            "PATH": f"{fake_bin}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            "TMPDIR": str(tmp_path),
        },
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert output.is_dir()
    assert not installed_marker.exists()
    assert f"OpenProgram App artifact written to {output}" in completed.stdout


@pytest.mark.macos
@MACOS_DESKTOP_INSTALL
def test_deferred_install_can_rollback_or_commit(tmp_path: Path) -> None:
    installer = ROOT / "apps" / "desktop" / "scripts" / "install-app.sh"
    env = {
        "DESTDIR": str(tmp_path / "root"),
        "HOME": str(tmp_path / "home"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": str(tmp_path / "tmp"),
    }
    Path(env["TMPDIR"]).mkdir()
    original = _fake_desktop_app(tmp_path / "original", "0.6.1")
    subprocess.run(["bash", str(installer), str(original)], check=True, env=env)

    candidate = _fake_desktop_app(tmp_path / "candidate", "0.6.2")
    activated = subprocess.run(
        ["bash", str(installer), "--defer-commit", str(candidate)],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    transaction = Path(
        next(
            line.removeprefix("OPENPROGRAM_TRANSACTION_DIR=")
            for line in activated.stdout.splitlines()
            if line.startswith("OPENPROGRAM_TRANSACTION_DIR=")
        )
    )
    target = Path(env["DESTDIR"]) / "Applications" / "OpenProgram.app"
    assert transaction.parent == target.parent
    assert (transaction / "previous.app").is_dir()
    with (target / "Contents" / "Info.plist").open("rb") as stream:
        assert plistlib.load(stream)["CFBundleShortVersionString"] == "0.6.2"

    subprocess.run(
        ["bash", str(installer), "--rollback", str(transaction)],
        check=True,
        env=env,
    )
    assert not transaction.exists()
    with (target / "Contents" / "Info.plist").open("rb") as stream:
        assert plistlib.load(stream)["CFBundleShortVersionString"] == "0.6.1"

    replacement = _fake_desktop_app(tmp_path / "replacement", "0.6.3")
    activated = subprocess.run(
        ["bash", str(installer), "--defer-commit", str(replacement)],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    transaction = Path(
        next(
            line.removeprefix("OPENPROGRAM_TRANSACTION_DIR=")
            for line in activated.stdout.splitlines()
            if line.startswith("OPENPROGRAM_TRANSACTION_DIR=")
        )
    )
    subprocess.run(
        ["bash", str(installer), "--commit", str(transaction)],
        check=True,
        env=env,
    )
    assert not transaction.exists()
    with (target / "Contents" / "Info.plist").open("rb") as stream:
        assert plistlib.load(stream)["CFBundleShortVersionString"] == "0.6.3"


@pytest.mark.macos
@MACOS_DESKTOP_INSTALL
def test_deferred_install_rejects_commit_after_active_app_changes(
    tmp_path: Path,
) -> None:
    installer = ROOT / "apps" / "desktop" / "scripts" / "install-app.sh"
    env = {
        "DESTDIR": str(tmp_path / "root"),
        "HOME": str(tmp_path / "home"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": str(tmp_path / "tmp"),
    }
    Path(env["TMPDIR"]).mkdir()
    original = _fake_desktop_app(tmp_path / "original", "0.6.1")
    subprocess.run(["bash", str(installer), str(original)], check=True, env=env)
    candidate = _fake_desktop_app(tmp_path / "candidate", "0.6.2")
    activated = subprocess.run(
        ["bash", str(installer), "--defer-commit", str(candidate)],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    transaction = Path(
        next(
            line.removeprefix("OPENPROGRAM_TRANSACTION_DIR=")
            for line in activated.stdout.splitlines()
            if line.startswith("OPENPROGRAM_TRANSACTION_DIR=")
        )
    )
    target = Path(env["DESTDIR"]) / "Applications" / "OpenProgram.app"
    (target / ".unexpected-change").write_text("changed", encoding="utf-8")

    rejected = subprocess.run(
        ["bash", str(installer), "--commit", str(transaction)],
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )

    assert rejected.returncode != 0
    assert "active OpenProgram app does not match the deferred transaction" in rejected.stderr
    assert (transaction / "previous.app").is_dir()


@pytest.mark.macos
@MACOS_DESKTOP_INSTALL
@pytest.mark.parametrize("terminal_action", ["commit", "rollback"])
def test_deferred_actions_do_not_execute_candidate_runtime(
    tmp_path: Path,
    terminal_action: str,
) -> None:
    installer = ROOT / "apps" / "desktop" / "scripts" / "install-app.sh"
    env = {
        "DESTDIR": str(tmp_path / "root"),
        "HOME": str(tmp_path / "home"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": str(tmp_path / "tmp"),
    }
    Path(env["TMPDIR"]).mkdir()
    original = _fake_desktop_app(tmp_path / "original", "0.6.1")
    subprocess.run(["bash", str(installer), str(original)], check=True, env=env)
    candidate = _fake_desktop_app(tmp_path / "candidate", "0.6.2")
    marker = tmp_path / "candidate-executed"
    runtime_python = candidate / "Contents/Resources/runtime/python/bin/python3"
    runtime_python.write_text(
        "#!/bin/sh\n"
        f"rm -rf {env['DESTDIR']}/Applications/.openprogram-app-install.*/previous.app\n"
        f"touch {marker!s}\n"
        "printf '%s\\n' '0.6.2'\n",
        encoding="utf-8",
    )
    runtime_python.chmod(0o755)
    activated = subprocess.run(
        ["bash", str(installer), "--defer-commit", str(candidate)],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    transaction = Path(
        next(
            line.removeprefix("OPENPROGRAM_TRANSACTION_DIR=")
            for line in activated.stdout.splitlines()
            if line.startswith("OPENPROGRAM_TRANSACTION_DIR=")
        )
    )
    assert not marker.exists()
    assert (transaction / "previous.app").is_dir()

    subprocess.run(
        ["bash", str(installer), f"--{terminal_action}", str(transaction)],
        check=True,
        env=env,
    )

    assert not marker.exists()


@pytest.mark.macos
@MACOS_DESKTOP_INSTALL
def test_installer_rejects_candidate_package_metadata_version_mismatch(
    tmp_path: Path,
) -> None:
    installer = ROOT / "apps" / "desktop" / "scripts" / "install-app.sh"
    env = {
        "DESTDIR": str(tmp_path / "root"),
        "HOME": str(tmp_path / "home"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": str(tmp_path / "tmp"),
    }
    Path(env["TMPDIR"]).mkdir()
    original = _fake_desktop_app(tmp_path / "original", "0.6.1")
    subprocess.run(["bash", str(installer), str(original)], check=True, env=env)
    candidate = _fake_desktop_app(tmp_path / "candidate", "0.6.2")
    metadata = next(candidate.rglob("openprogram-*.dist-info/METADATA"))
    metadata.write_text(
        "Metadata-Version: 2.4\nName: openprogram\nVersion: 0.6.1\n",
        encoding="utf-8",
    )

    rejected = subprocess.run(
        ["bash", str(installer), str(candidate)],
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )

    assert rejected.returncode != 0
    assert "invalid OpenProgram app bundle" in rejected.stderr
    target = Path(env["DESTDIR"]) / "Applications" / "OpenProgram.app"
    with (target / "Contents" / "Info.plist").open("rb") as stream:
        assert plistlib.load(stream)["CFBundleShortVersionString"] == "0.6.1"


@pytest.mark.macos
@MACOS_DESKTOP_INSTALL
def test_deferred_rollback_preserves_candidate_when_previous_is_missing(
    tmp_path: Path,
) -> None:
    installer = ROOT / "apps" / "desktop" / "scripts" / "install-app.sh"
    env = {
        "DESTDIR": str(tmp_path / "root"),
        "HOME": str(tmp_path / "home"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": str(tmp_path / "tmp"),
    }
    Path(env["TMPDIR"]).mkdir()
    original = _fake_desktop_app(tmp_path / "original", "0.6.1")
    subprocess.run(["bash", str(installer), str(original)], check=True, env=env)
    candidate = _fake_desktop_app(tmp_path / "candidate", "0.6.2")
    activated = subprocess.run(
        ["bash", str(installer), "--defer-commit", str(candidate)],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    transaction = Path(
        next(
            line.removeprefix("OPENPROGRAM_TRANSACTION_DIR=")
            for line in activated.stdout.splitlines()
            if line.startswith("OPENPROGRAM_TRANSACTION_DIR=")
        )
    )
    target = Path(env["DESTDIR"]) / "Applications" / "OpenProgram.app"
    previous = transaction / "previous.app"
    subprocess.run(["/bin/rm", "-rf", str(previous)], check=True)

    rejected = subprocess.run(
        ["bash", str(installer), "--rollback", str(transaction)],
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )

    assert rejected.returncode != 0
    assert "invalid OpenProgram App transaction" in rejected.stderr
    assert transaction.is_dir()
    assert target.is_dir()
    with (target / "Contents" / "Info.plist").open("rb") as stream:
        assert plistlib.load(stream)["CFBundleShortVersionString"] == "0.6.2"


@pytest.mark.macos
@MACOS_DESKTOP_INSTALL
@pytest.mark.parametrize("marker_kind", ["missing", "symlink"])
def test_deferred_rollback_rejects_invalid_previous_marker(
    tmp_path: Path,
    marker_kind: str,
) -> None:
    installer = ROOT / "apps" / "desktop" / "scripts" / "install-app.sh"
    env = {
        "DESTDIR": str(tmp_path / "root"),
        "HOME": str(tmp_path / "home"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": str(tmp_path / "tmp"),
    }
    Path(env["TMPDIR"]).mkdir()
    original = _fake_desktop_app(tmp_path / "original", "0.6.1")
    subprocess.run(["bash", str(installer), str(original)], check=True, env=env)
    candidate = _fake_desktop_app(tmp_path / "candidate", "0.6.2")
    activated = subprocess.run(
        ["bash", str(installer), "--defer-commit", str(candidate)],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    transaction = Path(
        next(
            line.removeprefix("OPENPROGRAM_TRANSACTION_DIR=")
            for line in activated.stdout.splitlines()
            if line.startswith("OPENPROGRAM_TRANSACTION_DIR=")
        )
    )
    target = Path(env["DESTDIR"]) / "Applications" / "OpenProgram.app"
    marker = transaction / "had-previous"
    marker.unlink()
    if marker_kind == "symlink":
        subprocess.run(["/bin/rm", "-rf", str(transaction / "previous.app")], check=True)
        (transaction / "previous.sha256").unlink()
        marker.symlink_to(target)

    rejected = subprocess.run(
        ["bash", str(installer), "--rollback", str(transaction)],
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )

    assert rejected.returncode != 0
    assert "invalid OpenProgram App transaction" in rejected.stderr
    assert transaction.is_dir()
    assert target.is_dir()


@pytest.mark.macos
@MACOS_DESKTOP_INSTALL
def test_packager_rejects_canonical_aliases_and_cleanup_owned_outputs(
    tmp_path: Path,
) -> None:
    packager = ROOT / "apps" / "desktop" / "scripts" / "package-and-install-app.sh"
    applications_alias = tmp_path / "applications"
    applications_alias.symlink_to("/Applications", target_is_directory=True)
    outputs = [
        "/Applications/./OpenProgram.app",
        str(applications_alias / "OpenProgram.app"),
        str(ROOT / "build" / "OpenProgram.app"),
        str(ROOT / "apps" / "desktop" / "build" / "runtime" / "OpenProgram.app"),
    ]
    env = {
        "HOME": str(tmp_path / "home"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": str(tmp_path),
    }

    for output in outputs:
        rejected = subprocess.run(
            ["/bin/bash", str(packager), "--output", output],
            check=False,
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert rejected.returncode != 0, output
        assert "build output" in rejected.stderr, output


def test_launchd_replacement_unloads_keepalive_before_stopping_worker(
    tmp_path: Path, monkeypatch
) -> None:
    from openprogram.worker.services import launchd
    from openprogram.worker import lifecycle

    plist_path = tmp_path / "ai.openprogram.worker.plist"
    plist_path.write_bytes(plistlib.dumps({"Label": launchd.LABEL}))
    events: list[str] = []

    monkeypatch.setattr(launchd, "_plist_path", lambda: plist_path)
    monkeypatch.setattr(
        launchd,
        "_launchctl",
        lambda *args: (events.append(f"launchctl:{args[0]}") or (0, "")),
    )
    monkeypatch.setattr(lifecycle, "current_worker_pid", lambda: 12345)
    monkeypatch.setattr(
        lifecycle, "stop_worker", lambda: (events.append("stop") or 0)
    )

    assert launchd.install() == 0
    assert events == ["launchctl:list", "launchctl:unload", "stop", "launchctl:load"]


def test_launchd_replaces_an_unloaded_stale_plist(tmp_path: Path, monkeypatch) -> None:
    from openprogram.worker.services import launchd
    from openprogram.worker import lifecycle

    plist_path = tmp_path / "ai.openprogram.worker.plist"
    plist_path.write_bytes(plistlib.dumps({"Label": launchd.LABEL}))
    events: list[str] = []

    def fake_launchctl(*args: str) -> tuple[int, str]:
        events.append(f"launchctl:{args[0]}")
        if args[0] == "list":
            return 113, f'Could not find service "{launchd.LABEL}"'
        return 0, ""

    monkeypatch.setattr(launchd, "_plist_path", lambda: plist_path)
    monkeypatch.setattr(launchd, "_launchctl", fake_launchctl)
    monkeypatch.setattr(lifecycle, "current_worker_pid", lambda: None)

    assert launchd.install() == 0
    assert events == ["launchctl:list", "launchctl:load"]
    assert plist_path.is_file()


def test_launchd_unload_failure_preserves_the_service_plist(
    tmp_path: Path, monkeypatch
) -> None:
    from openprogram.worker.services import launchd

    plist_path = tmp_path / "ai.openprogram.worker.plist"
    original = plistlib.dumps({"Label": launchd.LABEL})
    plist_path.write_bytes(original)

    def fake_launchctl(*args: str) -> tuple[int, str]:
        if args[0] == "list":
            return 0, "loaded"
        return 5, "synthetic unload failure"

    monkeypatch.setattr(launchd, "_plist_path", lambda: plist_path)
    monkeypatch.setattr(launchd, "_launchctl", fake_launchctl)

    assert launchd.uninstall() == 5
    assert plist_path.read_bytes() == original


def test_packaged_runtime_smoke_rejects_an_incomplete_app_before_install(
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "installed"
    current = _fake_desktop_app(install_root / "Applications", "0.6.2")
    package_dir = tmp_path / "package"
    _fake_desktop_app(package_dir, "0.6.3")

    failed = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts" / "release" / "smoke-packaged-runtime.sh"),
            "mac",
            str(package_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert failed.returncode != 0
    with (current / "Contents" / "Info.plist").open("rb") as stream:
        assert plistlib.load(stream)["CFBundleShortVersionString"] == "0.6.2"


def test_macos_icon_uses_the_apple_icon_source_format() -> None:
    desktop = ROOT / "apps" / "desktop"
    package = json.loads((desktop / "package.json").read_text(encoding="utf-8"))
    icon_source = desktop / "build" / "AppIcon.icon"
    packaged_icon = desktop / "build" / "icon.icns"
    assert package["build"]["mac"]["icon"] == "build/icon.icns"
    assert (icon_source / "icon.json").is_file()
    assert packaged_icon.is_file()
    assert packaged_icon.read_bytes().startswith(b"icns")
    assert not (desktop / "build" / "icon.svg").exists()
    assert not (desktop / "build" / "icon.iconset").exists()


def test_launchd_worker_preserves_packaged_python_flags(monkeypatch) -> None:
    from openprogram.worker import lifecycle
    from openprogram.worker.services import launchd

    flags = SimpleNamespace(isolated=1, dont_write_bytecode=1)
    monkeypatch.setattr(lifecycle.sys, "flags", flags)

    assert launchd._build_plist()["ProgramArguments"] == [
        sys.executable,
        "-I",
        "-B",
        "-u",
        "-m",
        "openprogram",
        "worker",
        "run",
    ]
    assert "ProcessType" not in launchd._build_plist()


def test_core_agentic_functions_are_not_excluded_from_wheel() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'exclude = ["openprogram.programs.workflow.*"]' not in pyproject


def test_packaged_worker_uses_isolated_embedded_python() -> None:
    helper = (ROOT / "apps" / "desktop" / "packaged-runtime.js").read_text(encoding="utf-8")
    main = (ROOT / "apps" / "desktop" / "main.js").read_text(encoding="utf-8")
    assert '"-I", "-B", "-m", "openprogram", "worker", "start"' in helper
    assert "process.resourcesPath" in main
    assert "app.getVersion()" in main
    packaged_branch = re.search(
        r"if \(app\.isPackaged\)(.*?)(?:\n\s*else|\n\s*})",
        main,
        re.DOTALL,
    )
    assert packaged_branch is not None
    assert 'start("openprogram"' not in packaged_branch.group(1)
    assert "/opt/miniconda3" not in main
    assert 'env.OPENPROGRAM_IMMUTABLE_RUNTIME = "1"' in main


def test_detached_worker_preserves_packaged_python_flags() -> None:
    from openprogram.worker.lifecycle import _detached_worker_command

    command = _detached_worker_command(
        SimpleNamespace(isolated=1, dont_write_bytecode=1)
    )
    assert command[1:] == [
        "-I",
        "-B",
        "-u",
        "-m",
        "openprogram",
        "worker",
        "run",
    ]


def test_linux_worker_process_probe_treats_zombie_as_stopped(monkeypatch) -> None:
    from openprogram.worker import lifecycle

    monkeypatch.setattr(lifecycle.sys, "platform", "linux")
    monkeypatch.setattr(
        lifecycle.Path,
        "read_text",
        lambda self, **kwargs: "123 (openprogram) Z 1 2 3",
    )
    assert lifecycle._process_alive(123) is False


def test_packaged_runtime_rejects_program_mutation(monkeypatch, capsys) -> None:
    from openprogram.cli.commands.programs import _cmd_install, _cmd_uninstall

    monkeypatch.setenv("OPENPROGRAM_IMMUTABLE_RUNTIME", "1")
    with pytest.raises(SystemExit) as install_exit:
        _cmd_install("research")
    assert install_exit.value.code == 1
    assert "disabled in the packaged desktop runtime" in capsys.readouterr().out

    with pytest.raises(SystemExit) as uninstall_exit:
        _cmd_uninstall("research")
    assert uninstall_exit.value.code == 1
    assert "disabled in the packaged desktop runtime" in capsys.readouterr().out


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
    assert 'probe_home="$release_dir/.probe-home-$$"' in installer
    assert 'HOME="$probe_home" OPENPROGRAM_WEB_PORT="$probe_port"' in installer
    start = installer.index('"$python_bin" -I -B -m openprogram worker start')
    health = installer.index("/healthz", start)
    stop = installer.index('"$python_bin" -I -B -m openprogram worker stop', health)
    switch = installer.index("os.replace(sys.argv[1], sys.argv[2])", stop)
    assert start < health < stop < switch


def _copied_public_installer(tmp_path: Path) -> Path:
    wrapper = tmp_path / "install-release.sh"
    wrapper.write_text(
        (ROOT / "scripts" / "install-release.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return wrapper


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


def test_release_installer_replaces_an_existing_current_symlink(tmp_path: Path) -> None:
    runtime_root = tmp_path / "state" / "runtime" / "cli"
    old_release = runtime_root / "releases" / "0.6.6"
    old_release.mkdir(parents=True)
    (runtime_root / "current").symlink_to(old_release)

    archive_root = tmp_path / "archive" / "runtime"
    (archive_root / "python" / "bin").mkdir(parents=True)
    (archive_root / "bin").mkdir()
    fake_python = archive_root / "python" / "bin" / "python3"
    fake_python.write_text(
        "#!/bin/sh\n"
        f"if [ \"$#\" -eq 5 ] && [ \"$3\" = - ]; then exec {sys.executable!r} \"$@\"; fi\n"
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
    (archive_root / "runtime-manifest.json").write_text(
        json.dumps({"python": "python/bin/python3"}, indent=2), encoding="utf-8"
    )
    archive = tmp_path / "OpenProgram-0.6.7-runtime-macos-arm64.tar.gz"
    subprocess.run(
        ["tar", "-C", str(tmp_path / "archive"), "-czf", str(archive), "runtime"],
        check=True,
    )
    import hashlib

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    launcher_dir = tmp_path / "bin"
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path / "home"),
        "TMPDIR": str(tmp_path),
        "LC_ALL": "C",
        "OPENPROGRAM_VERSION": "0.6.7",
        "OPENPROGRAM_STATE_DIR": str(tmp_path / "state"),
        "OPENPROGRAM_BIN_DIR": str(launcher_dir),
        "OPENPROGRAM_RUNTIME_ARCHIVE": str(archive),
        "OPENPROGRAM_RUNTIME_SHA256": digest,
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


def test_docs_publish_short_installer_at_the_domain_root() -> None:
    workflow = (ROOT / ".github" / "workflows" / "docs-pages.yml").read_text(
        encoding="utf-8"
    )
    assert "mv _publish/docs/install.sh _publish/install" in workflow


def test_normal_user_docs_use_the_short_release_installer() -> None:
    short_command = "curl -fsSL https://openprogram.io/install | sh"
    for relative in (
        "README.md",
        "docs/README.md",
        "docs/README.zh.md",
        "docs/install/install.md",
        "docs/install/install.zh.md",
        "docs/install/upgrade.md",
        "docs/install/upgrade.zh.md",
        "docs/start/GETTING_STARTED.md",
        "docs/start/GETTING_STARTED.zh.md",
        "website/index.html",
    ):
        contents = (ROOT / relative).read_text(encoding="utf-8")
        assert short_command in contents, relative
        assert "v0.6.1/scripts/install-release.sh" not in contents, relative


def test_cli_exposes_distribution_version(capsys) -> None:
    from openprogram.cli import build_parser

    with pytest.raises(SystemExit) as version_exit:
        build_parser().parse_args(["--version"])
    assert version_exit.value.code == 0
    assert capsys.readouterr().out.startswith("openprogram ")


def test_desktop_runtime_removes_absolute_python_aliases() -> None:
    staging = (ROOT / "scripts" / "release" / "build-product-runtime.sh").read_text(
        encoding="utf-8"
    )
    assert 'readlink "$python_alias"' in staging
    assert 'unlink "$python_alias"' in staging


def test_local_app_refresh_restarts_worker_after_runtime_install() -> None:
    refresh = (ROOT / "scripts" / "refresh-local-app.sh").read_text(
        encoding="utf-8"
    )
    install = refresh.index('"$app_python" -I -m pip install')
    stops = [
        match.start()
        for match in re.finditer(
            r'"\$local_python" -m openprogram worker stop', refresh
        )
    ]
    health = refresh.index(
        'curl -fsS http://127.0.0.1:18100/healthz', install
    )
    assert any(install < stop < health for stop in stops)
    final_window = refresh[install:health]
    assert "build.files" in refresh
    assert (
        '"$local_python" -m openprogram worker stop >/dev/null 2>&1\n'
        in final_window
    )
    assert "worker stop >/dev/null 2>&1 || true" not in final_window


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
    first_worker_mutation = refresh.index("pgrep -x OpenProgram")
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


def test_release_version_verifier_rejects_a_mismatched_built_wheel(
    tmp_path: Path,
) -> None:
    source_version = _desktop_package()["version"]
    installed = _fake_desktop_app(tmp_path / "installed", source_version)
    wheel = tmp_path / "openprogram-0.6.1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "openprogram-0.6.1.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: openprogram\nVersion: 0.6.1\n",
        )
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "release" / "verify-release-version.py"),
            "--installed-app",
            str(installed),
            "--require-source-match",
            "--wheel",
            str(wheel),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert f"wheel version 0.6.1 != source version {source_version}" in result.stderr


def test_local_app_refresh_rejects_dirty_version_change_after_build(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    release_scripts = scripts / "release"
    desktop = repo / "apps" / "desktop"
    release_scripts.mkdir(parents=True)
    desktop.mkdir(parents=True)
    (scripts / "refresh-local-app.sh").write_text(
        (ROOT / "scripts" / "refresh-local-app.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (release_scripts / "verify-release-version.py").write_text(
        (ROOT / "scripts" / "release" / "verify-release-version.py").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    stage_assets = release_scripts / "stage-release-assets.sh"
    stage_assets.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stage_assets.chmod(0o755)
    (release_scripts / "install-release.sh").write_text(
        'OPENPROGRAM_VERSION="${OPENPROGRAM_VERSION:-0.6.6}"\n',
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
        '  extract) mkdir -p "$4/node_modules" ;;\n'
        '  pack) test -f "$3/menu-geometry.js" || exit 92; '
        'test -f "$3/worker-recovery-state.js" || exit 93; '
        'test -f "$3/tab-transfer-validation.js" || exit 94; '
        'test -f "$3/window-state.js" || exit 95; '
        'test -f "$3/theme-chrome.js" || exit 96; : > "$4" ;;\n'
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

    assert result.returncode != 0
    assert "source version 0.6.1 != installed App version 0.6.6" in result.stderr
    assert not mutation_log.exists()
    assert installed_asar.read_bytes() == b"original-asar"
    assert not (app.parent / ".openprogram-app-install.lock").exists()

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

    assert blocked.returncode != 0
    assert "another OpenProgram App installation is running" in blocked.stderr
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
    signal_env = env | {"SIGNAL_READY": str(signal_ready)}
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

    assert interrupted.returncode == 143, (stdout, stderr)
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
    cleanup_env = env | {"CLEANUP_READY": str(cleanup_ready)}
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

    assert cleanup_interrupted.returncode == 143, (stdout, stderr)
    assert not lock_file.exists()
    assert not list(Path(env["TMPDIR"]).glob("openprogram-local-wheel.*"))


def test_release_frontend_staging_removes_stale_export_before_build() -> None:
    staging = (ROOT / "scripts" / "release" / "stage-release-assets.sh").read_text(
        encoding="utf-8"
    )
    cleanup = staging.index('rm -rf "$source_dir"')
    build = staging.index("npm run build --workspace apps/web")
    assert cleanup < build


def test_release_frontend_staging_removes_legacy_package_assets() -> None:
    staging = (ROOT / "scripts" / "release" / "stage-release-assets.sh").read_text(
        encoding="utf-8"
    )
    assert 'legacy_target_dir="$repo_root/openprogram/webui/_frontend"' in staging
    assert 'rm -rf "$target_dir" "$legacy_target_dir"' in staging


def test_release_frontend_staging_directory_is_ignored() -> None:
    generated = "apps/server/openprogram_server/_webui/_frontend/index.html"
    result = subprocess.run(
        ["git", "check-ignore", "-q", generated],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_release_frontend_staging_includes_prebuilt_docs() -> None:
    staging = (ROOT / "scripts" / "release" / "stage-release-assets.sh").read_text(
        encoding="utf-8"
    )
    assert "scripts.docs_site.build" in staging
    assert 'docs_target_dir="$target_dir/docs"' in staging
    assert 'cp -R "$docs_source_dir/." "$docs_target_dir/"' in staging
    copy = staging.index('cp -R "$source_dir/." "$target_dir/"')
    gate = staging[copy:]
    assert "chat.html" in gate
    assert 'aria-label="Authenticating"' in gate
    assert 'id="sidebar"' in gate


def test_release_asset_staging_invokes_locked_docs_builder(tmp_path) -> None:
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    release_scripts = scripts / "release"
    fake_bin = tmp_path / "bin"
    release_scripts.mkdir(parents=True)
    fake_bin.mkdir()
    (repo / "apps" / "web").mkdir(parents=True)
    script = release_scripts / "stage-release-assets.sh"
    script.write_text(
        (ROOT / "scripts" / "release" / "stage-release-assets.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    script.chmod(0o755)

    fake_npm = fake_bin / "npm"
    fake_npm.write_text(
        """#!/bin/sh
if [ "$1" = "run" ] && [ "$2" = "build" ]; then
  mkdir -p "$PWD/apps/web/out"
  printf '<html>web</html>\\n' > "$PWD/apps/web/out/index.html"
  printf '<body><div id="sidebar"></div></body>\\n' > "$PWD/apps/web/out/chat.html"
fi
""",
        encoding="utf-8",
    )
    fake_npm.chmod(0o755)

    uv_log = tmp_path / "uv.log"
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        """#!/bin/sh
printf '%s\\n' "$*" > "$UV_LOG"
mkdir -p "$PWD/docs/_site"
printf '<html>docs</html>\\n' > "$PWD/docs/_site/index.html"
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    subprocess.run(
        ["bash", str(script)],
        cwd=repo,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "UV_LOG": str(uv_log),
        },
        check=True,
        capture_output=True,
        text=True,
    )
    assert uv_log.read_text(encoding="utf-8").strip() == (
        "run --isolated --locked --python 3.12 --with markdown-it-py "
        "--with mdit-py-plugins --with pygments python -m scripts.docs_site.build"
    )
    staged_chat = (
        repo / "apps" / "server" / "openprogram_server" / "_webui" / "_frontend" / "chat.html"
    )
    assert 'id="sidebar"' in staged_chat.read_text(encoding="utf-8")

    fake_npm.write_text(
        """#!/bin/sh
if [ "$1" = "run" ] && [ "$2" = "build" ]; then
  mkdir -p "$PWD/apps/web/out"
  printf '<html>web</html>\\n' > "$PWD/apps/web/out/index.html"
  printf '<main aria-label="Authenticating"></main>\\n' > "$PWD/apps/web/out/chat.html"
fi
""",
        encoding="utf-8",
    )
    rejected = subprocess.run(
        ["bash", str(script)],
        cwd=repo,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "UV_LOG": str(uv_log),
        },
        check=False,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode != 0
    assert "Authenticating" in rejected.stderr


def test_product_runtime_installs_complete_default_capabilities() -> None:
    staging = (ROOT / "scripts" / "release" / "build-product-runtime.sh").read_text(
        encoding="utf-8"
    )
    verifier = (ROOT / "scripts" / "release" / "verify-product-runtime.py").read_text(
        encoding="utf-8"
    )
    product_config = (ROOT / "scripts" / "release" / "product-runtime.json").read_text(
        encoding="utf-8"
    )
    assert "--frozen --no-dev" in staging
    assert "--extra all --extra search" in staging
    assert "--extra embedding" not in staging
    assert "--require-hashes" in staging
    assert '--no-deps "$wheel"' in staging
    assert '--no-deps "$program_dir"' in staging
    assert '"${program_dir}[ocr]"' not in staging
    assert "playwright.sync_api" in verifier
    assert "playwright install chromium" in staging
    assert "easyocr.Reader" not in staging
    assert '"${program_dir}[pdf]"' in staging
    assert "https://download.pytorch.org/whl/cpu" not in staging
    assert "torch==$torch_version" not in staging
    assert "2147483648" in (
        ROOT / "scripts" / "release" / "archive-product-runtime.sh"
    ).read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    main_deps = pyproject.split("[project.optional-dependencies]")[0]
    assert "sentence-transformers" not in main_deps
    assert 'embedding = ["sentence-transformers>=3.4,<4"]' in pyproject
    assert '"pypdf>=5.0"' in pyproject
    assert '"rich>=13.0"' in pyproject
    assert '"sentence_transformers"' not in verifier
    assert "_reject_torch_wheels()" in verifier
    assert "product runtime must not ship torch or CUDA wheels" in verifier
    assert '"pypdf",' in verifier
    assert "_probe_pdf_tools()" in verifier
    assert "_probe_rich_terminal()" in verifier
    assert "Salesforce/GPA-GUI-Detector" in product_config
    assert "GUI-Agent-Harness" in product_config
    assert "Research-Agent-Harness" in product_config
    assert "Wiki-Agent-Harness" in product_config


def test_product_runtime_pdf_tool_probe() -> None:
    verifier = runpy.run_path(str(ROOT / "scripts" / "release" / "verify-product-runtime.py"))
    verifier["_probe_pdf_tools"]()


def test_product_runtime_rich_terminal_probe() -> None:
    verifier = runpy.run_path(str(ROOT / "scripts" / "release" / "verify-product-runtime.py"))
    verifier["_probe_rich_terminal"]()


def test_packaged_cli_falls_back_when_ink_runtime_is_absent(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from types import SimpleNamespace

    from openprogram.cli import chat as cli_chat
    from openprogram.cli import ink as cli_ink
    from openprogram.agent.management import manager

    monkeypatch.setenv("OPENPROGRAM_IMMUTABLE_RUNTIME", "1")
    monkeypatch.setattr(
        cli_ink,
        "_resolve_node",
        lambda: (_ for _ in ()).throw(RuntimeError("node unavailable")),
    )
    with pytest.raises(RuntimeError, match="node unavailable"):
        cli_ink.run_ink_tui()

    monkeypatch.setattr(
        cli_chat, "_get_chat_runtime", lambda: ("test", SimpleNamespace(model="test"))
    )
    monkeypatch.setattr(manager, "get_default", lambda: SimpleNamespace(id="main"))
    monkeypatch.setattr(
        cli_ink,
        "run_ink_tui",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("Ink unavailable")),
    )
    monkeypatch.setattr(cli_chat, "_print_banner", lambda *_args, **_kwargs: None)

    from rich.console import Console

    monkeypatch.setattr(
        Console,
        "input",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(EOFError()),
    )
    cli_chat.run_cli_chat(tui=True)
    output = capsys.readouterr()
    assert "falling back to REPL" in output.out
    assert "Goodbye" in output.out


def test_missing_bundled_pdf_dependency_requires_complete_reinstall(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import sys

    from openprogram.programs.tools.web.pdf import execute as pdf_extract
    from openprogram.programs.tools.files.read import _read_pdf

    pdf_path = tmp_path / "probe.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    monkeypatch.setitem(sys.modules, "pypdf", None)

    for result in (
        pdf_extract(file_path=str(pdf_path)),
        _read_pdf(str(pdf_path), offset=1, limit=1),
    ):
        assert "reinstall the complete OpenProgram release" in result
        assert "pip install" not in result


def test_product_runtime_rejects_torch_wheels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = runpy.run_path(str(ROOT / "scripts" / "release" / "verify-product-runtime.py"))

    class _Dist:
        def __init__(self, name: str) -> None:
            self.metadata = {"Name": name}

    monkeypatch.setattr(
        verifier["importlib"].metadata,
        "distributions",
        lambda: [_Dist("torch"), _Dist("pypdf")],
    )
    with pytest.raises(RuntimeError, match="must not ship torch"):
        verifier["_reject_torch_wheels"]()

    monkeypatch.setattr(
        verifier["importlib"].metadata,
        "distributions",
        lambda: [_Dist("nvidia-cublas"), _Dist("pypdf")],
    )
    with pytest.raises(RuntimeError, match="must not ship torch"):
        verifier["_reject_torch_wheels"]()

    monkeypatch.setattr(
        verifier["importlib"].metadata,
        "distributions",
        lambda: [_Dist("pypdf")],
    )
    verifier["_reject_torch_wheels"]()


def test_product_runtime_rejects_installed_openprogram_version_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = runpy.run_path(str(ROOT / "scripts" / "release" / "verify-product-runtime.py"))
    verify_version = verifier["_verify_openprogram_version"]

    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.6.1")
    with pytest.raises(
        RuntimeError,
        match=r"OpenProgram version mismatch: expected 0\.6\.6, got 0\.6\.1",
    ):
        verify_version("0.6.6")


def test_search_runtime_dependency_supports_macos_x64() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert 'search = ["semble>=0.5.3"]' in pyproject
    assert "tree-sitter-language-pack" not in lock
    assert re.search(
        r"semble_grammars-[^-]+-py3-none-macosx_[^-]+_x86_64\.whl",
        lock,
    )


def test_memory_runtime_dependency_supports_macos_x64() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert "sys_platform == 'darwin' and platform_machine == 'x86_64'" in pyproject
    assert re.search(r"torch-[^-]+-.*macosx_[^-]+_x86_64\.whl", lock)


def test_product_manifest_requires_one_complete_capability_set() -> None:
    manifest = json.loads(
        (ROOT / "scripts" / "release" / "product-runtime.json").read_text(encoding="utf-8")
    )
    assert manifest["schema"] == 1
    assert set(manifest["capabilities"]) == {
        "web",
        "providers",
        "mcp",
        "memory",
        "channels",
        "search",
        "browser.playwright",
        "model.gpa_detector",
        "program.gui",
        "program.research",
        "program.wiki",
    }
    assert set(manifest["programs"]) == {"gui", "research", "wiki"}
    assert "torch" not in manifest["programs"]["gui"]
    assert "torchvision" not in manifest["programs"]["gui"]
    assert "numpy" not in manifest["programs"]["gui"]
    assert "opencv" not in manifest["programs"]["gui"]
    for program in manifest["programs"].values():
        assert re.fullmatch(r"[0-9a-f]{40}", program["commit"])


def test_source_development_installer_adds_to_complete_product() -> None:
    installer = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert 'PIP install -e "$HOST_ROOT[all,search]"' in installer
    assert '"$PY" -m playwright install chromium' in installer
    assert '"$PY" -m openprogram programs install all' in installer
    assert 'bash "$gui_installer" --no-host --python "$PY"' in installer
    assert 'PIP install -e "$applications/research_harness[pdf]"' in installer
    assert "prompt_programs_menu" not in installer
    assert "--minimal was removed" in installer
    assert "WITH_STEALTH" in installer
    assert "WITH_AGENT_BROWSER" in installer


def test_native_release_workflow_has_platform_jobs() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    assert "macos-" in workflow
    assert "macos-26-intel" not in workflow
    assert "macos-15-intel" in workflow
    icon_check = (ROOT / "apps" / "desktop" / "scripts" / "check-icon.sh").read_text(
        encoding="utf-8"
    )
    assert "runner: macos-15-intel$" in icon_check
    assert "macos-26-intel" not in icon_check
    assert "ubuntu-" in workflow
    assert "ubuntu-24.04-arm" in workflow
    assert "product-runtime:" in workflow
    assert "cli-installer:" in workflow
    assert "product-runtime-${{ matrix.platform }}-${{ matrix.arch }}" in workflow
    assert "scripts/release/build-product-runtime.sh" in workflow
    assert "scripts/release/archive-product-runtime.sh" in workflow
    assert "scripts/release/prepare-desktop-runtime.sh" in workflow
    assert "scripts/release/verify-release-version.py" in workflow
    assert "scripts/release/create-release-manifest.py" in workflow
    assert "scripts/release/smoke-packaged-runtime.sh" in workflow
    assert "sha256" in workflow.lower()
    assert workflow.count("--publish never") == 1
    assert "AppImage" not in workflow


def test_release_workflow_publishes_structured_release_notes() -> None:
    version = _desktop_package()["version"]
    notes_path = (
        ROOT
        / ".github"
        / "release-notes"
        / f"v{version}.md"
    )
    assert notes_path.is_file()
    notes = notes_path.read_text(encoding="utf-8")
    assert notes.startswith(f"# OpenProgram {version} Release Notes\n")
    assert f"OpenProgram-{version}-mac-arm64-unsigned.dmg" in notes
    assert f"OpenProgram-{version}-mac-x64-unsigned.dmg" in notes
    for section in (
        "## 🐞 Bug fixes",
        "## ✨ New features",
        "## 🚀 Improvements",
        "## 📦 Download and installation",
        "## 🔄 Upgrade guide",
    ):
        assert section in notes
    assert "- **macOS**" in notes
    assert "  - **Package installation**" in notes
    assert "  - **Command-line installation**" in notes
    assert "- **Linux**" in notes
    assert "  - **Command-line / Server installation**" in notes
    assert notes.count("  - **Development installation**") == 2
    assert "share the same complete runtime and browser backend" in notes
    assert "built-in Browser Pane are available only in the macOS Desktop App" in notes
    assert "| User type |" not in notes
    assert "curl -fsSL https://openprogram.io/install | sh" in notes

    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    assert 'notes_file=".github/release-notes/$GITHUB_REF_NAME.md"' in workflow
    assert 'release_version="${GITHUB_REF_NAME#v}"' in workflow
    assert 'test -s "$notes_file"' in workflow
    assert "release notes must be English" in workflow
    assert '--title "OpenProgram $release_version Release"' in workflow
    assert '--notes-file "$notes_file"' in workflow
    assert "--generate-notes" not in workflow


def test_all_versioned_release_notes_use_the_public_english_title() -> None:
    notes_dir = ROOT / ".github" / "release-notes"
    for notes_path in sorted(notes_dir.glob("v*.md")):
        version = notes_path.stem.removeprefix("v")
        notes = notes_path.read_text(encoding="utf-8")
        heading = notes.splitlines()[0]
        assert heading == f"# OpenProgram {version} Release Notes"
        assert not re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", notes), (
            f"release notes must be English: {notes_path.name}"
        )


def test_macos_desktop_matrix_maps_runtime_arch_to_electron_builder_arch() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    assert "arch: x86_64\n            builder_arch: x64" in workflow
    assert "arch: arm64\n            builder_arch: arm64" in workflow
    assert "--${{ matrix.builder_arch }} --publish never" in workflow


def test_linux_complete_runtime_smoke_is_runnable_without_release_credentials() -> None:
    workflow = (ROOT / ".github" / "workflows" / "linux-release-smoke.yml").read_text(
        encoding="utf-8"
    )
    assert "workflow_dispatch:" in workflow
    assert "environment: release" not in workflow
    assert "ubuntu-24.04-arm" in workflow
    assert "scripts/install-release.sh" in workflow
    assert "AppImage" not in workflow
    assert "electron-builder" not in workflow


def test_distribution_workflows_use_node24_action_releases() -> None:
    workflows = [
        (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        for name in ("release.yml", "linux-release-smoke.yml")
    ]
    for workflow in workflows:
        assert "actions/checkout@v7" in workflow
        assert "actions/setup-node@v7" in workflow
        assert "astral-sh/setup-uv@v10.0.1" in workflow
        assert "actions/upload-artifact@v7" in workflow
        assert "actions/download-artifact@v8" in workflow


def test_packaged_smoke_rejects_unreleased_linux_desktop() -> None:
    smoke = (ROOT / "scripts" / "release" / "smoke-packaged-runtime.sh").read_text(encoding="utf-8")
    assert "AppImage" not in smoke
    assert "linux)" not in smoke
    assert "python3 -c" not in smoke


def test_packaged_smoke_reads_formatted_runtime_manifest(tmp_path: Path) -> None:
    runtime = (
        tmp_path
        / "dist"
        / "mac-arm64"
        / "OpenProgram.app"
        / "Contents"
        / "Resources"
        / "runtime"
    )
    runtime.mkdir(parents=True)
    (runtime / "runtime-manifest.json").write_text(
        json.dumps({"python": "python/bin/python3"}, indent=2) + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            str(ROOT / "scripts" / "release" / "smoke-packaged-runtime.sh"),
            "mac",
            str(tmp_path / "dist"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "managed Python is not executable" in result.stderr
    assert "managed Python path missing" not in result.stderr


def test_release_workflow_builds_explicitly_unsigned_macos_artifacts() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    assert "unsigned" in workflow.lower()
    assert 'CSC_IDENTITY_AUTO_DISCOVERY: "false"' in workflow
    for forbidden in (
        "APPLE_API_KEY",
        "APPLE_API_ISSUER",
        "APPLE_TEAM_ID",
        "MAC_CSC_LINK",
        "notarytool",
        "stapler",
        "gh-action-pypi-publish",
    ):
        assert forbidden not in workflow


def test_public_docs_follow_the_release_platform_policy() -> None:
    public_docs = [
        ROOT / "README.md",
        ROOT / "docs" / "README.md",
        ROOT / "docs" / "README.zh.md",
        ROOT / "docs" / "capabilities" / "installing-harnesses.md",
        ROOT / "docs" / "capabilities" / "installing-harnesses.zh.md",
        ROOT / "docs" / "reference" / "cli.md",
        ROOT / "docs" / "reference" / "cli.zh.md",
        ROOT / "docs" / "slides" / "openprogram-intro.html",
        ROOT
        / "docs"
        / "superpowers"
        / "specs"
        / "2026-08-11-framework-adoption-homepage-design.md",
    ]
    forbidden = [
        "Any platform",
        "任意平台",
        "Native macOS / Linux / Windows",
        "Cross-platform (macOS / Linux / Windows)",
        "跨平台（macOS / Linux / Windows）",
        "macOS/Linux/Windows",
    ]
    for path in public_docs:
        contents = path.read_text(encoding="utf-8")
        for phrase in forbidden:
            assert phrase not in contents, f"{path.relative_to(ROOT)}: {phrase}"


def test_linux_install_docs_do_not_claim_a_desktop_artifact() -> None:
    expectations = {
        "docs/install/install.md": "no reduced Linux desktop artifact is published",
        "docs/install/install.zh.md": "不发布精简的 Linux 桌面产物",
    }
    for relative, expected in expectations.items():
        contents = (ROOT / relative).read_text(encoding="utf-8")
        assert "linux-x86_64.AppImage" not in contents
        assert expected in contents


def test_public_docs_describe_one_complete_release_product() -> None:
    public_docs = [
        ROOT / "README.md",
        ROOT / "docs" / "README.md",
        ROOT / "docs" / "README.zh.md",
        ROOT / "docs" / "install" / "install.md",
        ROOT / "docs" / "install" / "install.zh.md",
        ROOT / "docs" / "install" / "upgrade.md",
        ROOT / "docs" / "install" / "upgrade.zh.md",
        ROOT / "docs" / "capabilities" / "installing-harnesses.md",
        ROOT / "docs" / "capabilities" / "installing-harnesses.zh.md",
        ROOT / "docs" / "capabilities" / "workflows" / "README.md",
        ROOT / "docs" / "capabilities" / "workflows" / "README.zh.md",
        ROOT / "docs" / "capabilities" / "workflows" / "gui-agent.md",
        ROOT / "docs" / "capabilities" / "workflows" / "gui-agent.zh.md",
        ROOT / "docs" / "capabilities" / "workflows" / "research-agent.md",
        ROOT / "docs" / "capabilities" / "workflows" / "research-agent.zh.md",
        ROOT / "docs" / "capabilities" / "workflows" / "wiki-agent.md",
        ROOT / "docs" / "capabilities" / "workflows" / "wiki-agent.zh.md",
        ROOT / "docs" / "capabilities" / "tools.md",
        ROOT / "docs" / "capabilities" / "tools.zh.md",
        ROOT / "docs" / "capabilities" / "README.md",
        ROOT
        / "docs"
        / "capabilities"
        / "agentic-programming"
        / "embedding-in-your-own-stack.md",
        ROOT
        / "docs"
        / "capabilities"
        / "agentic-programming"
        / "embedding-in-your-own-stack.zh.md",
        ROOT / "docs" / "integrations" / "channels.md",
        ROOT / "docs" / "integrations" / "channels.zh.md",
        ROOT / "docs" / "start" / "GETTING_STARTED.md",
        ROOT / "docs" / "start" / "GETTING_STARTED.zh.md",
        ROOT / "docs" / "start" / "faq.md",
        ROOT / "docs" / "start" / "faq.zh.md",
        ROOT / "docs" / "slides" / "openprogram-intro.html",
        ROOT / "docs" / "reference" / "design" / "feature-matrix.html",
    ]
    forbidden = (
        "openprogram programs install gui",
        "openprogram programs install research",
        "openprogram programs install wiki",
        "Agent programs are not part",
        "agent Program 不属于",
        "notarized DMG",
        "exact wheel",
        "精确 wheel",
        "pip install 'openprogram[search]'",
        "pip install openprogram[channels]",
    )
    combined = "\n".join(path.read_text(encoding="utf-8") for path in public_docs)
    for phrase in forbidden:
        assert phrase not in combined
    assert "same complete product capabilities" in combined
    assert "相同的完整产品能力" in combined


def test_public_product_surfaces_do_not_offer_python_package_install() -> None:
    public_files = [
        ROOT / ".github" / "CONTRIBUTING.md",
        ROOT / "docs" / "_static_root" / "llms.txt",
        ROOT / "docs" / "server" / "troubleshooting.md",
        ROOT / "docs" / "server" / "troubleshooting.zh.md",
        ROOT / "docs" / "integrations" / "openclaw.md",
        ROOT / "docs" / "integrations" / "openclaw.zh.md",
    ]
    internal_python_installers = {
        ROOT / "openprogram" / "cli" / "commands" / "browser.py",
        ROOT / "openprogram" / "cli" / "commands" / "plugins.py",
        ROOT / "openprogram" / "cli" / "commands" / "programs.py",
        ROOT / "openprogram" / "cli" / "commands" / "upgrade.py",
        ROOT / "openprogram" / "cli" / "setup_sections" / "sections.py",
        ROOT / "openprogram" / "programs" / "_programs.py",
        ROOT / "openprogram" / "programs" / "_registry.py",
        ROOT / "openprogram" / "updater" / "detect.py",
    }
    public_files.extend(
        path
        for path in (ROOT / "openprogram").rglob("*.py")
        if path not in internal_python_installers
        and ROOT / "openprogram" / "programs" / "applications" not in path.parents
    )
    forbidden = (
        "pip install",
        "pip3 install",
        "pipx install",
        "uv tool install openprogram",
        "pypi.org/project/openprogram",
    )
    for path in public_files:
        source = path.read_text(encoding="utf-8").lower()
        for phrase in forbidden:
            assert phrase not in source, f"{path.relative_to(ROOT)}: {phrase}"


def test_openclaw_source_checkout_uses_its_locked_environment() -> None:
    for relative_path in (
        "docs/integrations/openclaw.md",
        "docs/integrations/openclaw.zh.md",
    ):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "uv sync --locked" in source
        assert "uv run --project ~/.openclaw/workspace/OpenProgram python" in source
        assert (
            "python3 ~/.openclaw/workspace/skills/my-agentic-skill/scripts/analyze.py"
            not in source
        )


def test_python_import_troubleshooting_distinguishes_managed_and_source() -> None:
    for relative_path in (
        "docs/server/troubleshooting.md",
        "docs/server/troubleshooting.zh.md",
    ):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "uv sync --locked" in source
        assert "uv run --project /path/to/OpenProgram python" in source
        assert "./scripts/install.sh" not in source


def test_packaged_browser_install_does_not_modify_python_environment(
    monkeypatch, capsys
) -> None:
    from openprogram.cli.commands.browser import _cmd_browser_install

    monkeypatch.setenv("OPENPROGRAM_IMMUTABLE_RUNTIME", "1")
    monkeypatch.setattr(
        "openprogram.cli.commands.browser._pip_install",
        lambda _spec: (_ for _ in ()).throw(AssertionError("pip invoked")),
    )

    assert _cmd_browser_install("playwright") == 1
    output = capsys.readouterr().out.lower()
    assert "complete release" in output
    assert "source checkout" in output

    help_output = subprocess.run(
        [sys.executable, "-m", "openprogram", "browser", "install", "--help"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.lower()
    help_output = " ".join(help_output.split())
    assert "source checkout only" in help_output
    assert "packaged releases reject this command" in help_output


def test_release_manifest_records_hashes(tmp_path: Path) -> None:
    import subprocess

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "OpenProgram-0.6.4-mac-arm64.dmg").write_bytes(b"artifact")
    output = artifacts / "release-manifest.json"
    subprocess.run(
        [
            "python",
            str(ROOT / "scripts" / "release" / "create-release-manifest.py"),
            str(artifacts),
            "--version",
            "v0.6.4",
            "--output",
            str(output),
        ],
        check=True,
    )
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["version"] == "0.6.4"
    assert manifest["files"][0]["sha256"] == (
        "c7c5c1d70c5dec4416ab6158afd0b223ef40c29b1dc1f97ed9428b94d4cadb1c"
    )
