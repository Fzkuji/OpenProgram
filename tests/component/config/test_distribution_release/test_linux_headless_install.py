"""The terminal install on a Linux server or container without a desktop.

Chromium's system libraries are only needed by the browser tool, so their
absence must not stop the install: the runtime check leaves the Chromium launch
to the browser tool's first use, and the installer says so in one line.
"""
from __future__ import annotations

import os

from ._support import POSIX_SHELL_INTEGRATION, ROOT, Path, _fake_posix_cli_archive, subprocess

# Every library the installer looks for, as `ldconfig -p` lists them.
_FULL_CACHE = [
    "libnss3.so", "libnspr4.so", "libatk-1.0.so.0", "libatk-bridge-2.0.so.0",
    "libcups.so.2", "libglib-2.0.so.0", "libdrm.so.2", "libdbus-1.so.3",
    "libxkbcommon.so.0", "libXcomposite.so.1", "libXdamage.so.1", "libXfixes.so.3",
    "libXrandr.so.2", "libgbm.so.1", "libpango-1.0.so.0", "libcairo.so.2",
    "libX11.so.6", "libxcb.so.1", "libXext.so.6", "libatspi.so.0", "libasound.so.2",
    "libcurl.so.4",
]


def _linux_host(directory: Path, libraries: list[str]) -> Path:
    """Commands that make this machine look like Linux x86_64 with ``libraries``."""
    directory.mkdir()
    uname = directory / "uname"
    uname.write_text(
        "#!/bin/sh\ncase \"$1\" in -s) echo Linux ;; -m) echo x86_64 ;; *) echo Linux ;; esac\n",
        encoding="utf-8",
    )
    cache = "".join(f"\t{name} (libc6,x86-64) => /usr/lib/x86_64-linux-gnu/{name}\n" for name in libraries)
    ldconfig = directory / "ldconfig"
    ldconfig.write_text(f"#!/bin/sh\ncat <<'EOF'\n{cache}EOF\n", encoding="utf-8")
    for command in (uname, ldconfig):
        command.chmod(0o755)
    return directory


def _install(tmp_path: Path, libraries: list[str]) -> tuple[subprocess.CompletedProcess, list[str]]:
    archive, digest = _fake_posix_cli_archive(tmp_path / "fixture", manifest_version="0.6.7")
    # The fixture's Python records how the runtime check is called.
    verify_log = tmp_path / "verify-calls"
    subprocess.run(["tar", "-C", str(tmp_path / "fixture" / "archive"), "-xzf", str(archive)], check=True)
    fake_python = tmp_path / "fixture" / "archive" / "runtime" / "python" / "bin" / "python3"
    body = fake_python.read_text(encoding="utf-8").split("\n", 1)[1]
    fake_python.write_text(
        "#!/bin/sh\n"
        "case \"$*\" in *verify-product-runtime.py*) printf '%s\\n' \"$*\" >> \"$VERIFY_LOG\" ;; esac\n" + body,
        encoding="utf-8",
    )
    subprocess.run(["tar", "-C", str(tmp_path / "fixture" / "archive"), "-czf", str(archive), "runtime"], check=True)
    import hashlib

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    host = _linux_host(tmp_path / "linux-host", libraries)
    result = subprocess.run(
        ["sh", str(ROOT / "scripts" / "release" / "install-release.sh")],
        check=False,
        env=os.environ
        | {
            # No apt-get on this PATH: the notice names the packages instead.
            "PATH": f"{host}:/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(tmp_path / "home"),
            "OPENPROGRAM_VERSION": "0.6.7",
            "OPENPROGRAM_STATE_DIR": str(tmp_path / "state"),
            "OPENPROGRAM_BIN_DIR": str(tmp_path / "bin"),
            "OPENPROGRAM_RUNTIME_ARCHIVE": str(archive),
            "OPENPROGRAM_RUNTIME_SHA256": digest,
            "VERIFY_LOG": str(verify_log),
        },
        capture_output=True,
        text=True,
    )
    calls = verify_log.read_text(encoding="utf-8").splitlines() if verify_log.exists() else []
    return result, calls


@POSIX_SHELL_INTEGRATION
def test_install_without_chromium_libraries_completes_and_says_what_is_missing(tmp_path: Path) -> None:
    result, calls = _install(tmp_path, ["libcurl.so.4"])

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "bin" / "openprogram").is_file()
    assert len(calls) == 1 and calls[0].endswith("--allow-deferred-browser")
    notice = [line for line in result.stderr.splitlines() if "browser tool" in line]
    assert len(notice) == 1
    assert notice[0].startswith("The browser tool will not work until")
    assert "everything else does" in notice[0]
    for package in ("libnss3", "libgbm1", "libx11-6", "libxcb1", "libxext6", "libatspi2.0-0"):
        assert package in notice[0]


@POSIX_SHELL_INTEGRATION
def test_install_with_chromium_libraries_launches_the_browser_in_the_check(tmp_path: Path) -> None:
    result, calls = _install(tmp_path, _FULL_CACHE)

    assert result.returncode == 0, result.stderr
    assert len(calls) == 1 and "--allow-deferred-browser" not in calls[0]
    assert "browser tool" not in result.stderr


def test_preflight_looks_for_every_library_chromium_reports() -> None:
    """The list matches Playwright's for chromium-headless-shell on Debian 12."""
    script = (ROOT / "scripts" / "release" / "install-release.sh").read_text(encoding="utf-8")
    for library in _FULL_CACHE[:-1]:  # libcurl is Git's, checked on its own
        assert f'"{library}:' in script, library

