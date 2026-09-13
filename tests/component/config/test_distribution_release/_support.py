from __future__ import annotations


import importlib


import json


import os


import platform


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


ROOT = Path(__file__).resolve().parents[4]


MACOS_DESKTOP_INSTALL = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="requires macOS app bundle and LaunchServices tools",
)


POSIX_SHELL_INTEGRATION = pytest.mark.skipif(
    sys.platform == "win32",
    reason="exercises the macOS/Linux shell installer; Windows uses PowerShell",
)


def _desktop_package() -> dict:
    return json.loads((ROOT / "apps" / "desktop" / "package.json").read_text(encoding="utf-8"))


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


def _copied_public_installer(tmp_path: Path) -> Path:
    wrapper = tmp_path / "install-release.sh"
    wrapper.write_text(
        (ROOT / "scripts" / "install-release.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return wrapper


def _fake_posix_cli_archive(
    root: Path,
    *,
    manifest_version: str,
    worker_start_exit: int = 0,
) -> tuple[Path, str]:
    import hashlib

    runtime = root / "archive" / "runtime"
    (runtime / "python" / "bin").mkdir(parents=True)
    (runtime / "bin").mkdir()
    fake_python = runtime / "python" / "bin" / "python3"
    fake_python.write_text(
        "#!/bin/sh\n"
        "if [ \"$#\" -eq 3 ] && [ \"$3\" = - ]; then\n"
        "  printf '23457\\n'\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$#\" -eq 4 ] && [ \"$3\" = - ]; then\n"
        "  case \"$4\" in\n"
        f"    */runtime-manifest.json) exec {sys.executable!r} \"$@\" ;;\n"
        "    start)\n"
        "      if [ -n \"${PROBE_ENV_LOG:-}\" ]; then\n"
        "        {\n"
        "          printf '%s\\n' \"$HOME\" \"$XDG_CONFIG_HOME\"\n"
        "          printf '%s\\n' \"$OPENPROGRAM_STATE_DIR\" \"$OPENPROGRAM_PROFILE\"\n"
        "          printf '%s\\n' \"$OPENPROGRAM_NO_WEB\" \"$OPENPROGRAM_WORKDIR\" \"$PWD\"\n"
        "          if [ -e \"$XDG_CONFIG_HOME/systemd/user/openprogram-worker.service\" ]; then\n"
        "            printf 'unit-visible\\n'\n"
        "          else\n"
        "            printf 'unit-hidden\\n'\n"
        "          fi\n"
        "        } > \"$PROBE_ENV_LOG\"\n"
        "      fi\n"
        f"      exit {worker_start_exit} ;;\n"
        "  esac\n"
        "fi\n"
        "if [ \"$#\" -ge 5 ] && [ \"$3\" = - ]; then\n"
        f"  exec {sys.executable!r} \"$@\"\n"
        "fi\n"
        "case \"$*\" in\n"
        f"  *'openprogram --version'*) printf 'openprogram {manifest_version}\\n' ;;\n"
        "  *) : ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    (runtime / "bin" / "python").symlink_to("../python/bin/python3")
    (runtime / "bin" / "verify-product-runtime.py").write_text(
        "# acceptance fixture\n", encoding="utf-8"
    )
    (runtime / "runtime-manifest.json").write_text(
        json.dumps(
            {
                "openprogram": manifest_version,
                "python": "python/bin/python3",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    archive = root / "OpenProgram-runtime.tar.gz"
    subprocess.run(
        ["tar", "-C", str(root / "archive"), "-czf", str(archive), "runtime"],
        check=True,
    )
    return archive, hashlib.sha256(archive.read_bytes()).hexdigest()

