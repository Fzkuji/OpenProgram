#!/usr/bin/env python3
"""Keep macOS local builds under one certificate-bound application identity."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile


def run(*args: str) -> str:
    label = f"local signing failed: {Path(args[0]).name} {args[1]}"
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        # TimeoutExpired includes argv. Both CLI and imported controller calls
        # must redact it before an error reaches durable update state or logs.
        raise RuntimeError(label) from None
    if result.returncode:
        # security arguments can contain the private keychain password.
        detail = "" if Path(args[0]).name == "security" else ": " + result.stderr.strip()
        raise RuntimeError(label + detail)
    return result.stdout


def private(path: Path, *, directory=False) -> None:
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError("local signing identity must be private and owned by this user")


@contextmanager
def locked(state: Path):
    import fcntl
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    private(state, directory=True)
    fd = os.open(state / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        private(state / "lock")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def fingerprint(certificate: bytes) -> str:
    # codesign identifies certificates by SHA-1; signatures still use SHA-256.
    return hashlib.sha1(certificate).hexdigest()


def check_app(app: Path, certificate: str | None, identifier="ai.openprogram.desktop") -> None:
    if not app.exists():
        if app.is_symlink():
            raise RuntimeError("invalid application path")
        return
    if app.is_symlink():
        raise RuntimeError("invalid application path")
    # The trusted updater signs an untrusted build artifact. Do not let its
    # signature targets redirect writes to another bundle or hard-linked file.
    for relative in ("Contents", "Contents/Info.plist", "Contents/MacOS",
                     "Contents/MacOS/OpenProgram", "Contents/_CodeSignature",
                     "Contents/_CodeSignature/CodeResources"):
        target = app / relative
        if target.is_symlink():
            raise RuntimeError("symlink in application signing target")
        if target.is_file() and target.stat().st_nlink != 1:
            raise RuntimeError("hard link in application signing target")
    with (app / "Contents/Info.plist").open("rb") as stream:
        metadata = plistlib.load(stream)
        if metadata.get("CFBundleIdentifier") != identifier:
            raise RuntimeError("local signing requires an OpenProgram application")
        if metadata.get("CFBundleExecutable") != "OpenProgram":
            raise RuntimeError("invalid managed application executable")
    result = subprocess.run(["/usr/bin/codesign", "--display", "--verbose=4", str(app)],
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError("application signing identity is unavailable")
    if "Signature=adhoc" in result.stderr.splitlines():
        return
    with tempfile.TemporaryDirectory(prefix="openprogram-cert-") as directory:
        prefix = str(Path(directory) / "cert")
        run("/usr/bin/codesign", "--display", "--extract-certificates=" + prefix, str(app))
        leaf = Path(prefix + "0")
        if not certificate or not leaf.is_file() or fingerprint(leaf.read_bytes()) != certificate:
            raise RuntimeError("refusing foreign application signing identity; local identity must match")


def load(state: Path) -> dict | None:
    receipt = state / "identity.json"
    if not receipt.exists():
        if any(p.name != "lock" for p in state.iterdir()):
            raise RuntimeError("local signing identity is incomplete; refusing automatic replacement")
        return None
    for name in ("identity.json", "certificate.der", "password", "signing.keychain-db"):
        private(state / name)
    value = json.loads(receipt.read_text())
    if (value.get("schema") != 1 or not re.fullmatch(r"[0-9a-f]{40}", value.get("certificate", ""))
            or fingerprint((state / "certificate.der").read_bytes()) != value["certificate"]):
        raise RuntimeError("invalid local signing identity")
    run("/usr/bin/openssl", "x509", "-inform", "DER", "-in", str(state / "certificate.der"),
        "-checkend", "86400", "-noout")
    return value


def create(state: Path) -> dict:
    password = secrets.token_hex(32)
    (state / "password").write_text(password)
    (state / "password").chmod(0o600)
    keychain = str(state / "signing.keychain-db")
    with tempfile.TemporaryDirectory(prefix="generate-", dir=state) as directory:
        stage = Path(directory)
        config = stage / "openssl.cnf"
        config.write_text("[req]\ndistinguished_name=dn\nx509_extensions=extensions\nprompt=no\n"
                          "[dn]\nCN=OpenProgram Local Development\n[extensions]\n"
                          "basicConstraints=critical,CA:TRUE\n"
                          "keyUsage=critical,digitalSignature,keyCertSign\n"
                          "extendedKeyUsage=critical,codeSigning\n")
        run("/usr/bin/openssl", "req", "-new", "-newkey", "rsa:3072", "-nodes", "-x509",
            "-days", "3650", "-config", str(config), "-keyout", str(stage / "key.pem"),
            "-out", str(stage / "certificate.pem"))
        run("/usr/bin/openssl", "x509", "-in", str(stage / "certificate.pem"), "-outform", "DER",
            "-out", str(state / "certificate.der"))
        (state / "certificate.der").chmod(0o600)
        run("/usr/bin/openssl", "pkcs12", "-export", "-inkey", str(stage / "key.pem"),
            "-in", str(stage / "certificate.pem"), "-out", str(stage / "identity.p12"),
            "-passout", "file:" + str(state / "password"))
        run("/usr/bin/security", "create-keychain", "-p", password, keychain)
        run("/usr/bin/security", "unlock-keychain", "-p", password, keychain)
        run("/usr/bin/security", "import", str(stage / "identity.p12"), "-k", keychain,
            "-P", password, "-T", "/usr/bin/codesign")
        run("/usr/bin/security", "set-key-partition-list", "-S", "apple-tool:", "-s",
            "-k", password, keychain)
    (state / "signing.keychain-db").chmod(0o600)
    value = {"schema": 1, "certificate": fingerprint((state / "certificate.der").read_bytes())}
    (state / "identity.json").write_text(json.dumps(value) + "\n")
    (state / "identity.json").chmod(0o600)
    return value


def ready(state: Path) -> None:
    keychain = str(state / "signing.keychain-db")
    run("/usr/bin/security", "unlock-keychain", "-p", (state / "password").read_text(), keychain)
    # codesign needs the certificate's keychain on its search list, even with
    # --keychain. Preserve all existing entries and never change the default
    # keychain or certificate trust settings.
    entries = shlex.split(run("/usr/bin/security", "list-keychains", "-d", "user"))
    if keychain not in entries:
        run("/usr/bin/security", "list-keychains", "-d", "user", "-s", *entries, keychain)


def verify_signer(state: Path, identity: dict) -> None:
    # Prove the key is usable before a build stops the worker or replaces files.
    # The copied system executable is only signed, never executed.
    with tempfile.TemporaryDirectory(prefix="probe-", dir=state) as directory:
        probe = Path(directory) / "probe"
        shutil.copyfile("/usr/bin/true", probe)
        run("/usr/bin/codesign", "--force", "--sign", identity["certificate"],
            "--keychain", str(state / "signing.keychain-db"), "--timestamp=none", str(probe))
        run("/usr/bin/codesign", "--verify", "--strict", str(probe))


def sign(app: Path, state: Path, identity: dict) -> None:
    for relative in ("Contents", "Contents/Resources", "Contents/Resources/runtime"):
        if (app / relative).is_symlink():
            raise RuntimeError("symlink in runtime signing target")
    helper = app / "Contents/Resources/runtime/OpenProgram.app"
    bundles = (helper, app)
    for bundle in bundles:
        if not bundle.is_dir():
            raise RuntimeError("managed application or runtime is missing")
        check_app(bundle, identity["certificate"],
                  "ai.openprogram.runtime" if bundle == helper else "ai.openprogram.desktop")
    ready(state)
    for bundle in bundles:
        with (bundle / "Contents/Info.plist").open("rb") as stream:
            identifier = plistlib.load(stream)["CFBundleIdentifier"]
        requirement = f'identifier "{identifier}" and certificate leaf = H"{identity["certificate"]}"'
        run("/usr/bin/codesign", "--force", "--sign", identity["certificate"],
            "--keychain", str(state / "signing.keychain-db"), "--timestamp=none",
            "--preserve-metadata=entitlements,flags", "--requirements", "=designated => " + requirement,
            str(bundle))
        run("/usr/bin/codesign", "--verify", "--strict", "-R", "=" + requirement, str(bundle))
    run("/usr/bin/codesign", "--verify", "--deep", "--strict", str(app))


def state_directory() -> Path:
    return Path.home() / "Library/Application Support/OpenProgram/local-signing"


def operate(action: str, app: Path, state: Path | None = None) -> None:
    if sys.platform != "darwin":
        raise RuntimeError("local macOS signing requires macOS")
    state = state if state is not None else state_directory()
    # Inspect the final component before resolving aliases (notably /var).
    if state.is_symlink() or app.is_symlink():
        raise RuntimeError("signing paths must not be symlinks")
    state = state.resolve()
    with locked(state):
        identity = load(state)
        check_app(app, identity["certificate"] if identity else None)
        if action == "prepare":
            identity = identity or create(state)
            ready(state)
            verify_signer(state, identity)
        elif identity:
            sign(app, state, identity)
        else:
            raise RuntimeError("local signing identity is missing; run prepare first")


def prepare_app(app: Path) -> None:
    operate("prepare", app)


def sign_app(app: Path) -> None:
    operate("sign", app)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "sign"))
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, default=state_directory())
    args = parser.parse_args()
    operate(args.action, args.app, args.state_dir)
    print("Local signing identity ready" if args.action == "prepare" else "Local application signature verified")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc) if isinstance(exc, RuntimeError) else "local signing identity unavailable", file=sys.stderr)
        raise SystemExit(1) from None
