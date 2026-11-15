"""The Windows seed install hands tar.exe only paths it can read.

Windows' tar.exe (bsdtar) reads its arguments in the ANSI code page, so a
profile folder with a character outside it (a Chinese user name on English
Windows) reached it as '?' and every command-line seed install there failed.
The installer now runs tar inside the destination with a relative, ASCII path
to the archive. The behaviour test needs Windows PowerShell; elsewhere the
rule is checked in the source.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

REPOSITORY = Path(__file__).resolve().parents[3]
INSTALLER = REPOSITORY / "scripts/release/install-release.ps1"


def _function(name: str) -> str:
    text = INSTALLER.read_text(encoding="utf-8")
    found = re.search(rf"^function {name} \{{\n(.*?)^\}}\n", text, re.S | re.M)
    assert found, f"{name} is not in install-release.ps1"
    return found.group(0)


def test_every_seed_unpack_goes_through_the_relative_path_helper():
    text = INSTALLER.read_text(encoding="utf-8")
    assert not re.search(r'"tar\.exe"', text), "tar.exe is called with paths it may not read"
    assert "Expand-TarArchive $Archive $BaseWork" in _function("Install-SeedBase")
    assert "Expand-TarArchive $Archive $Work" in _function("Install-FromSeed")
    helper = _function("Expand-TarArchive")
    assert "Push-Location -LiteralPath $Destination" in helper
    assert '"System32\\tar.exe"' in helper, "a tar earlier on PATH (Git's GNU tar) must not be picked up"
    assert "-xzf $Relative" in helper and "-C " not in helper


@pytest.mark.skipif(os.name != "nt" or shutil.which("powershell") is None, reason="needs Windows PowerShell")
def test_tar_is_given_an_ascii_relative_path_under_a_chinese_profile(tmp_path):
    profile = tmp_path / "李华 Wang" / ".openprogram"
    archive = profile / ".install-downloads" / "seed.tar.gz"
    work = profile / ".seed-install-1"
    archive.parent.mkdir(parents=True)
    work.mkdir(parents=True)
    archive.write_bytes(b"")
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
        "function Invoke-Native { param([string]$FilePath) "
        "[Console]::Out.WriteLine((Get-Location).Path); [Console]::Out.WriteLine(($args -join '|')) }\n"
        + _function("Expand-TarArchive")
        + f"Expand-TarArchive '{archive}' '{work}'\n"
    )
    done = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, encoding="utf-8", timeout=120,
    )
    assert done.returncode == 0, done.stderr
    location, arguments = done.stdout.splitlines()[-2:]
    assert Path(location) == work
    assert arguments == "-xzf|..\\.install-downloads\\seed.tar.gz"
