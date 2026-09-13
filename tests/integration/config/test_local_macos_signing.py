"""Real local signing across changed builds, without requesting protected data."""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS codesign")
ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/release/local-macos-signing.py"


def command(*args):
    return subprocess.run(list(map(str, args)), capture_output=True, text=True, timeout=60)


def fixture_app(tmp_path):
    app = tmp_path / "OpenProgram.app"
    for bundle, identifier in ((app, "ai.openprogram.desktop"),
            (app / "Contents/Resources/runtime/OpenProgram.app", "ai.openprogram.runtime")):
        contents = bundle / "Contents"
        (contents / "MacOS").mkdir(parents=True)
        (contents / "Resources").mkdir(exist_ok=True)
        (contents / "Info.plist").write_bytes(plistlib.dumps({
            "CFBundleIdentifier": identifier, "CFBundleExecutable": "OpenProgram",
            "CFBundlePackageType": "APPL"}))
        source = tmp_path / "main.c"
        source.write_text("int main(void) { return 0; }\n")
        result = command("/usr/bin/clang", source, "-o", contents / "MacOS/OpenProgram")
        assert result.returncode == 0, result.stderr
        entitlements = tmp_path / "entitlements.plist"
        entitlements.write_bytes(plistlib.dumps({"com.apple.security.cs.allow-jit": True}))
        result = command("/usr/bin/codesign", "--force", "--sign", "-",
                         "--entitlements", entitlements, bundle)
        assert result.returncode == 0, result.stderr
    assert command("/usr/bin/codesign", "--force", "--sign", "-", app).returncode == 0
    return app


def test_local_builds_keep_identity_and_refuse_rotation(tmp_path):
    app = fixture_app(tmp_path)
    state = tmp_path / "signing"
    (tmp_path / "home/Library/Preferences").mkdir(parents=True)
    env = {**os.environ, "HOME": str(tmp_path / "home")}
    def invoke(action, check=True):
        result = subprocess.run([sys.executable, str(SCRIPT), action, "--app", str(app),
                                 "--state-dir", str(state)], env=env,
                                capture_output=True, text=True, timeout=60)
        if check:
            assert result.returncode == 0, result.stderr
        return result
    try:
        invoke("prepare")
        identity = (state / "identity.json").read_bytes()
        invoke("sign")
        requirement = command("/usr/bin/codesign", "-d", "-r-", app).stdout.strip()
        assert requirement.startswith("designated => identifier")
        assert "certificate leaf" in requirement and "cdhash" not in requirement
        for index in range(2):
            invoke("prepare")
            assert (state / "identity.json").read_bytes() == identity
            (app / "Contents/Resources/value").write_text(str(index))
            invoke("sign")
            verification = command("/usr/bin/codesign", "--verify", "--deep", "--strict",
                                   "-R", "=" + requirement.partition("=> ")[2], app)
            assert verification.returncode == 0, verification.stderr
        inner = app / "Contents/Resources/runtime/OpenProgram.app"
        entitlements = command("/usr/bin/codesign", "-d", "--xml", "--entitlements", "-", inner)
        assert plistlib.loads(entitlements.stdout.encode())["com.apple.security.cs.allow-jit"]
        # A second local state cannot take over an already signed application.
        other = tmp_path / "foreign"
        before = (app / "Contents/MacOS/OpenProgram").read_bytes()
        refused = subprocess.run([sys.executable, str(SCRIPT), "prepare", "--app", str(app),
                                  "--state-dir", str(other)], env=env,
                                 capture_output=True, text=True, timeout=60)
        assert refused.returncode != 0 and "foreign" in refused.stderr
        assert (app / "Contents/MacOS/OpenProgram").read_bytes() == before
        assert not (other / "identity.json").exists()
        executable = app / "Contents/MacOS/OpenProgram"
        saved = tmp_path / "external-executable"
        executable.rename(saved)
        executable.symlink_to(saved)
        assert "symlink" in invoke("sign", check=False).stderr
        assert saved.read_bytes() == before
        executable.unlink()
        saved.rename(executable)
        info = app / "Contents/Info.plist"
        original_info = info.read_bytes()
        metadata = plistlib.loads(original_info)
        metadata["CFBundleExecutable"] = "Alternate"
        info.write_bytes(plistlib.dumps(metadata))
        alternate = executable.with_name("Alternate")
        saved.write_bytes(before)
        os.link(saved, alternate)
        assert "executable" in invoke("sign", check=False).stderr
        assert saved.read_bytes() == before
        assert executable.read_bytes() == before
        alternate.unlink()
        info.write_bytes(original_info)
        # Missing key material fails before a new certificate can be generated.
        password = state / "password"
        backup = password.read_bytes()
        password.unlink()
        assert invoke("prepare", check=False).returncode != 0
        assert (state / "identity.json").read_bytes() == identity
        password.write_bytes(backup)
        password.chmod(0o600)
        # Losing the receipt must not create a different identity over a signed App.
        (state / "identity.json").unlink()
        refused = invoke("prepare", check=False)
        assert refused.returncode != 0 and "identity" in refused.stderr.lower()
        assert not (state / "identity.json").exists()
    finally:
        keychain = state / "signing.keychain-db"
        if keychain.exists():
            command("/usr/bin/security", "delete-keychain", keychain)


def test_candidate_sandbox_cannot_read_signing_material(tmp_path, monkeypatch):
    from openprogram.self_update.control.supervisor import _sandbox_profile
    owner = tmp_path / "owner"
    secret = owner / "Library/Application Support/OpenProgram/local-signing/password"
    secret.parent.mkdir(parents=True)
    secret.write_text("fixture-keychain-password")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: owner))
    roots = [tmp_path / name for name in ("candidate", "artifact", "home", "tmp")]
    for root in roots:
        root.mkdir()
    profile = _sandbox_profile(*roots)
    result = command("/usr/bin/sandbox-exec", "-p", profile, "/bin/cat", secret)
    assert result.returncode != 0
    assert "fixture-keychain-password" not in result.stdout
