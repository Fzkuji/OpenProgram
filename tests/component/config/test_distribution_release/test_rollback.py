"""release rollback tests."""
from __future__ import annotations
from ._support import (
    MACOS_DESKTOP_INSTALL,
    Path,
    ROOT,
    _fake_desktop_app,
    os,
    plistlib,
    pytest,
    subprocess,
)


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

