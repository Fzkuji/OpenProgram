"""release desktop install tests."""
from __future__ import annotations
from ._support import (
    MACOS_DESKTOP_INSTALL,
    Path,
    ROOT,
    _desktop_package,
    _fake_desktop_app,
    json,
    os,
    plistlib,
    pytest,
    subprocess,
    sys,
    time,
)


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
@pytest.mark.parametrize("build_only", [True, False])
def test_packager_build_only_writes_artifact_without_installing(
    tmp_path: Path, build_only: bool,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    source = _fake_desktop_app(tmp_path / "built", "0.6.4")
    installed_marker = tmp_path / "installer-called"
    signing_trace = tmp_path / "signing-trace"
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        '  */local-macos-signing.py) printf "%s\\n" "$2" >> "$SIGNING_TRACE"; exit 0 ;;\n'
        "esac\n"
        f'exec "{sys.executable}" "$@"\n', encoding="utf-8",
    )
    fake_python.chmod(0o755)

    fake_npm = fake_bin / "npm"
    fake_npm.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'case " $* " in *" electron-builder "*) case " $* " in *" --config.mac.identity=- "*) ;; *) exit 49 ;; esac ;; esac\n'
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
        '  */smoke-packaged-runtime.sh) printf "smoke\\n" >> "$SIGNING_TRACE"; exit 0 ;;\n'
        '  */install-app.sh) touch "$INSTALL_CALLED"; printf "install\\n" >> "$SIGNING_TRACE"; exit 0 ;;\n'
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
            *(["--output", str(output)] if build_only else []),
        ],
        check=False,
        env={
            "FAKE_BUILT_APP": str(source),
            "HOME": str(tmp_path / "home"),
            "INSTALL_CALLED": str(installed_marker),
            "SIGNING_TRACE": str(signing_trace),
            "PATH": f"{fake_bin}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            "TMPDIR": str(tmp_path),
        },
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    # --output is an isolated self-update artifact; only its trusted controller
    # may access the owner's persistent identity after deferred resource writes.
    assert signing_trace.read_text().splitlines() == (
        ["smoke"] if build_only else ["prepare", "sign", "smoke", "install"])
    assert output.is_dir() is build_only
    assert installed_marker.exists() is not build_only
    if build_only:
        assert f"OpenProgram App artifact written to {output}" in completed.stdout



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

