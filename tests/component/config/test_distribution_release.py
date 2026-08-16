from __future__ import annotations

import json
import os
import plistlib
import re
import runpy
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[3]


def _desktop_package() -> dict:
    return json.loads((ROOT / "desktop" / "package.json").read_text(encoding="utf-8"))


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


def test_local_desktop_build_installs_one_canonical_app(tmp_path: Path) -> None:
    package = _desktop_package()
    assert package["scripts"]["dist"] == "npm run app:install"
    assert package["scripts"]["app:install"] == "bash scripts/package-and-install-app.sh"
    assert "dist:dir" not in package["scripts"]

    installer = ROOT / "desktop" / "scripts" / "install-app.sh"
    packager = (ROOT / "desktop" / "scripts" / "package-and-install-app.sh").read_text(
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
    assert '"$builder" --dir --mac --publish never' in packager
    smoke = 'bash "$repo_root/scripts/smoke-packaged-runtime.sh" mac "$package_dir"'
    assert smoke in packager
    assert 'env -u DESTDIR bash "$script_dir/install-app.sh" "$built_app"' in packager
    assert packager.index(smoke) < packager.index(
        'env -u DESTDIR bash "$script_dir/install-app.sh" "$built_app"'
    )
    assert 'lock_root="$HOME/Library/Caches/OpenProgram"' in packager
    assert '/usr/bin/shlock -p "$$" -f "$lock_file"' in packager
    assert '"$web_build_dir" "$web_output_dir" "$frontend_stage_dir"' in packager
    assert 'rm -rf "$repo_root/build"' in (
        ROOT / "scripts" / "build-product-runtime.sh"
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


def test_local_desktop_install_preserves_recovery_copy_when_restore_fails(
    tmp_path: Path,
) -> None:
    installer = ROOT / "desktop" / "scripts" / "install-app.sh"
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


def test_concurrent_local_desktop_install_cannot_nest_the_canonical_app(
    tmp_path: Path,
) -> None:
    installer = ROOT / "desktop" / "scripts" / "install-app.sh"
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


def test_packager_honors_one_stable_user_lock_across_worktrees(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    lock_file = home / "Library" / "Caches" / "OpenProgram" / "app-package.lock"
    lock_file.parent.mkdir(parents=True)
    subprocess.run(
        ["/usr/bin/shlock", "-p", str(os.getpid()), "-f", str(lock_file)],
        check=True,
    )
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": str(tmp_path),
    }

    competing = subprocess.run(
        ["bash", str(ROOT / "desktop" / "scripts" / "package-and-install-app.sh")],
        check=False,
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert competing.returncode != 0
    assert "another OpenProgram App package is running" in competing.stderr
    assert lock_file.read_text(encoding="utf-8").strip() == str(os.getpid())


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
            str(ROOT / "scripts" / "smoke-packaged-runtime.sh"),
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


def test_macos_legacy_icon_source_uses_the_standard_visible_bounds() -> None:
    source = (ROOT / "desktop" / "build" / "icon.svg").read_text(encoding="utf-8")
    assert 'id="op-macos-icon-mask"' in source
    assert 'id="op-icon-background"' in source
    assert 'clip-path="url(#op-macos-icon-clip)"' in source
    assert 'x="100" y="100" width="824" height="824" rx="185"' in source


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
    assert 'exclude = ["openprogram.programs.agentic_functions.*"]' not in pyproject


def test_packaged_worker_uses_isolated_embedded_python() -> None:
    helper = (ROOT / "desktop" / "packaged-runtime.js").read_text(encoding="utf-8")
    main = (ROOT / "desktop" / "main.js").read_text(encoding="utf-8")
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
    from openprogram._cli_cmds.programs import _cmd_install, _cmd_uninstall

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
    installer = (ROOT / "scripts" / "install-release.sh").read_text(encoding="utf-8")
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
    installer = (ROOT / "scripts" / "install-release.sh").read_text(encoding="utf-8")
    assert 'probe_home="$release_dir/.probe-home-$$"' in installer
    assert 'HOME="$probe_home" OPENPROGRAM_WEB_PORT="$probe_port"' in installer
    start = installer.index('"$python_bin" -I -B -m openprogram worker start')
    health = installer.index("/healthz", start)
    stop = installer.index('"$python_bin" -I -B -m openprogram worker stop', health)
    switch = installer.index("os.replace(sys.argv[1], sys.argv[2])", stop)
    assert start < health < stop < switch


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
        "site/index.html",
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
    staging = (ROOT / "scripts" / "build-product-runtime.sh").read_text(
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
    assert (
        '"$local_python" -m openprogram worker stop >/dev/null 2>&1\n'
        in final_window
    )
    assert "worker stop >/dev/null 2>&1 || true" not in final_window


def test_local_app_refresh_rejects_a_different_product_version_before_build(
    tmp_path: Path,
) -> None:
    installed = _fake_desktop_app(tmp_path, "0.6.7")
    verifier = ROOT / "scripts" / "verify-release-version.py"
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
    assert "source version 0.6.6 != installed App version 0.6.7" in result.stderr

    refresh = (ROOT / "scripts" / "refresh-local-app.sh").read_text(
        encoding="utf-8"
    )
    gate = refresh.index("--require-source-match")
    assert gate < refresh.index('wheel_dir="$(mktemp')
    assert gate < refresh.index('"$repo_root/scripts/stage-release-assets.sh"')
    assert gate < refresh.index("openprogram worker stop")


def test_release_frontend_staging_removes_stale_export_before_build() -> None:
    staging = (ROOT / "scripts" / "stage-release-assets.sh").read_text(
        encoding="utf-8"
    )
    cleanup = staging.index('rm -rf "$source_dir"')
    build = staging.index('npm run build --prefix "$web_dir"')
    assert cleanup < build


def test_release_frontend_staging_includes_prebuilt_docs() -> None:
    staging = (ROOT / "scripts" / "stage-release-assets.sh").read_text(
        encoding="utf-8"
    )
    assert "tools.docs_site.build" in staging
    assert 'docs_target_dir="$target_dir/docs"' in staging
    assert 'cp -R "$docs_source_dir/." "$docs_target_dir/"' in staging


def test_release_asset_staging_invokes_locked_docs_builder(tmp_path) -> None:
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    fake_bin = tmp_path / "bin"
    scripts.mkdir(parents=True)
    fake_bin.mkdir()
    (repo / "web").mkdir()
    script = scripts / "stage-release-assets.sh"
    script.write_text(
        (ROOT / "scripts" / "stage-release-assets.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    script.chmod(0o755)

    fake_npm = fake_bin / "npm"
    fake_npm.write_text(
        """#!/bin/sh
prefix=""
previous=""
for argument in "$@"; do
  if [ "$previous" = "--prefix" ]; then prefix="$argument"; fi
  previous="$argument"
done
if [ "$1" = "run" ] && [ "$2" = "build" ]; then
  mkdir -p "$prefix/out"
  printf '<html>web</html>\\n' > "$prefix/out/index.html"
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
        "run --isolated --locked --with markdown-it-py --with mdit-py-plugins "
        "--with pygments python -m tools.docs_site.build"
    )


def test_product_runtime_installs_complete_default_capabilities() -> None:
    staging = (ROOT / "scripts" / "build-product-runtime.sh").read_text(
        encoding="utf-8"
    )
    verifier = (ROOT / "scripts" / "verify-product-runtime.py").read_text(
        encoding="utf-8"
    )
    product_config = (ROOT / "config" / "product-runtime.json").read_text(
        encoding="utf-8"
    )
    assert "--frozen --no-dev" in staging
    assert "--extra all --extra search" in staging
    assert "--require-hashes" in staging
    assert '--no-deps "$wheel"' in staging
    assert "playwright.sync_api" in verifier
    assert "playwright install chromium" in staging
    assert "easyocr" in staging
    assert '"${program_dir}[pdf]"' in staging
    assert "https://download.pytorch.org/whl/cpu" in staging
    assert '"opencv-python==$opencv_version"' in staging
    assert staging.count('--constraint "$program_constraints"') == 3
    assert 'importlib.metadata.version(distribution).split("+", 1)[0]' in verifier
    assert "Salesforce/GPA-GUI-Detector" in product_config
    assert "GUI-Agent-Harness" in product_config
    assert "Research-Agent-Harness" in product_config
    assert "Wiki-Agent-Harness" in product_config


def test_product_runtime_rejects_installed_openprogram_version_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = runpy.run_path(str(ROOT / "scripts" / "verify-product-runtime.py"))
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


def test_product_manifest_requires_one_complete_capability_set() -> None:
    manifest = json.loads(
        (ROOT / "config" / "product-runtime.json").read_text(encoding="utf-8")
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
        "ocr.default",
        "model.gpa_detector",
        "program.gui",
        "program.research",
        "program.wiki",
    }
    assert set(manifest["programs"]) == {"gui", "research", "wiki"}
    assert manifest["programs"]["gui"]["numpy"] == "1.26.4"
    assert manifest["programs"]["gui"]["opencv"] == "4.11.0.86"
    assert manifest["programs"]["gui"]["torch"] == "2.2.2"
    assert manifest["programs"]["gui"]["torchvision"] == "0.17.2"
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
    assert "ubuntu-" in workflow
    assert "ubuntu-24.04-arm" in workflow
    assert "product-runtime:" in workflow
    assert "cli-installer:" in workflow
    assert "product-runtime-${{ matrix.platform }}-${{ matrix.arch }}" in workflow
    assert "scripts/build-product-runtime.sh" in workflow
    assert "scripts/archive-product-runtime.sh" in workflow
    assert "scripts/prepare-desktop-runtime.sh" in workflow
    assert "scripts/verify-release-version.py" in workflow
    assert "scripts/create-release-manifest.py" in workflow
    assert "scripts/smoke-packaged-runtime.sh" in workflow
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
        "## 🐞 修复问题",
        "## ✨ 新增功能",
        "## 🚀 优化改进",
        "## 📦 下载与安装",
        "## 🔄 版本指南",
    ):
        assert section in notes
    assert "- **macOS**" in notes
    assert "  - **安装包安装**" in notes
    assert "  - **命令行安装**" in notes
    assert "- **Linux**" in notes
    assert "  - **命令行 / Server 安装**" in notes
    assert notes.count("  - **开发安装**") == 2
    assert "安装包和命令行安装包含相同的完整产品功能" in notes
    assert "| 用户类型 |" not in notes
    assert "curl -fsSL https://openprogram.io/install | sh" in notes

    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    assert 'notes_file=".github/release-notes/$GITHUB_REF_NAME.md"' in workflow
    assert 'release_version="${GITHUB_REF_NAME#v}"' in workflow
    assert 'test -s "$notes_file"' in workflow
    assert '--title "OpenProgram $release_version Release"' in workflow
    assert '--notes-file "$notes_file"' in workflow
    assert "--generate-notes" not in workflow


def test_all_versioned_release_notes_use_the_public_english_title() -> None:
    notes_dir = ROOT / ".github" / "release-notes"
    for notes_path in sorted(notes_dir.glob("v*.md")):
        version = notes_path.stem.removeprefix("v")
        heading = notes_path.read_text(encoding="utf-8").splitlines()[0]
        assert heading == f"# OpenProgram {version} Release Notes"


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
    smoke = (ROOT / "scripts" / "smoke-packaged-runtime.sh").read_text(encoding="utf-8")
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
            str(ROOT / "scripts" / "smoke-packaged-runtime.sh"),
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


def test_release_manifest_records_hashes(tmp_path: Path) -> None:
    import subprocess

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "OpenProgram-0.6.4-mac-arm64.dmg").write_bytes(b"artifact")
    output = artifacts / "release-manifest.json"
    subprocess.run(
        [
            "python",
            str(ROOT / "scripts" / "create-release-manifest.py"),
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
