#!/usr/bin/env python3
"""Every place a release's version is written, changed or checked together.

    release-version.py set VERSION
        Write VERSION into the places below, or change nothing when any of
        them cannot be found.
    release-version.py check vVERSION [--commit REV]
        Exit 0 when every place agrees with VERSION, in the working tree or in
        the commit REV; otherwise name each place that differs.

A version is X.Y.Z or X.Y.Z.W. X.Y.0 is a feature release: Actions builds the
installers, so every place says it. Any other version is a code-only release,
published as an in-app update with no build: only the code's own places
(pyproject.toml, uv.lock) move, and the installer places (the Electron
package, its lock entry, the install scripts' default) keep the 3-part
version of the last installer build in the same X.Y series.

The Release workflow builds the tagged tree and stops at the first place that
disagrees: ``verify-release-version.py`` reads the project files and the
installers' default version, and ``uv run --locked`` / ``uv sync --locked``
refuse a ``uv.lock`` whose own project entry is stale. ``publish-github.sh
--tag`` runs ``check`` on the commit it is about to tag, so a half-bumped
version is refused before the tag leaves the machine.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:\.[0-9]+)?")
TAG = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+(?:\.[0-9]+)?")


@dataclass(frozen=True)
class Place:
    """One file and the single line in it that holds the version."""

    path: str
    pattern: re.Pattern[str]  # group 1 is the version
    parsed: Callable[[str], str | None] | None = None  # a real parser, to confirm the edit
    installer: bool = False  # moves only with a feature release X.Y.0

    def read(self, text: str) -> str | None:
        found = self.pattern.findall(text)
        return found[0] if len(found) == 1 else None

    def write(self, text: str, version: str) -> str:
        match = self.pattern.search(text)
        assert match is not None
        return text[:match.start(1)] + version + text[match.end(1):]


def _toml_project(text: str) -> str | None:
    return tomllib.loads(text).get("project", {}).get("version")


def _json_version(text: str) -> str | None:
    return json.loads(text).get("version")


def _npm_workspace(text: str) -> str | None:
    return json.loads(text).get("packages", {}).get("apps/desktop", {}).get("version")


def _uv_project(text: str) -> str | None:
    for package in tomllib.loads(text).get("package", []):
        if package.get("name") == "openprogram" and package.get("source") == {"editable": "."}:
            return package.get("version")
    return None


V = r"([0-9]+\.[0-9]+\.[0-9]+(?:\.[0-9]+)?)"
PLACES = (
    Place("pyproject.toml", re.compile(rf'(?m)^\[project\]\n(?:[^\[\n].*\n|\n)*?version = "{V}"$'), _toml_project),
    Place("apps/desktop/package.json", re.compile(rf'(?m)\A\{{\n  "name": "openprogram-desktop",\n  "version": "{V}",$'),
          _json_version, installer=True),
    Place("package-lock.json", re.compile(rf'(?m)^    "apps/desktop": \{{\n      "name": "openprogram-desktop",\n      "version": "{V}",$'),
          _npm_workspace, installer=True),
    Place("uv.lock", re.compile(rf'(?m)^\[\[package\]\]\nname = "openprogram"\nversion = "{V}"\nsource = \{{ editable = "\." \}}$'), _uv_project),
    Place("scripts/release/install-release.sh", re.compile(rf'(?m)^OPENPROGRAM_VERSION="\$\{{OPENPROGRAM_VERSION:-{V}\}}"$'),
          installer=True),
    Place("scripts/release/install-release.ps1", re.compile(
        rf'(?m)^\$Version = if \(\$env:OPENPROGRAM_VERSION\) \{{ \$env:OPENPROGRAM_VERSION \}} else \{{ "{V}" \}}$'),
        installer=True),
)


def _parts(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def is_feature_release(version: str) -> bool:
    """X.Y.0: the release Actions builds installers for."""
    parts = _parts(version)
    return len(parts) == 3 and parts[2] == 0


def installer_version_fits(installer: str | None, version: str) -> bool:
    """An installer place under a code-only ``version``: a 3-part X.Y.Z of the same X.Y, not newer."""
    if installer is None or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", installer):
        return False
    return _parts(installer)[:2] == _parts(version)[:2] and _parts(installer) <= _parts(version)


def _from_tree(root: Path) -> Callable[[str], str | None]:
    def read(path: str) -> str | None:
        file = root / path
        return file.read_text(encoding="utf-8") if file.is_file() else None
    return read


def _from_commit(root: Path, commit: str) -> Callable[[str], str | None]:
    def read(path: str) -> str | None:
        shown = subprocess.run(["git", "-C", str(root), "show", f"{commit}:{path}"], capture_output=True, text=True)
        return shown.stdout if shown.returncode == 0 else None
    return read


def versions(read: Callable[[str], str | None]) -> dict[str, str | None]:
    """What each place says; ``None`` when the file or its version line is missing."""
    found = {}
    for place in PLACES:
        text = read(place.path)
        found[place.path] = None if text is None else place.read(text)
    return found


def mismatches(version: str, read: Callable[[str], str | None]) -> list[str]:
    installer = {place.path for place in PLACES if place.installer}
    wrong = []
    for path, said in versions(read).items():
        if path in installer and not is_feature_release(version):
            fits = installer_version_fits(said, version)
        else:
            fits = said == version
        if not fits:
            wrong.append(f"{path} says {said or 'nothing'}")
    return wrong


def set_version(version: str, root: Path = ROOT) -> list[str]:
    """Write ``version`` into its places, or raise and write nothing.

    A code-only version leaves the installer places as they are.
    """
    if not VERSION.fullmatch(version):
        raise SystemExit(f"a release version looks like 1.2.3 or 1.2.3.4, not {version}")
    feature = is_feature_release(version)
    updated: dict[Path, str] = {}
    for place in PLACES:
        if place.installer and not feature:
            continue
        file = root / place.path
        text = file.read_text(encoding="utf-8") if file.is_file() else None
        if text is None or place.read(text) is None:
            raise SystemExit(f"cannot find the version line in {place.path}; nothing was changed")
        new = place.write(text, version)
        if place.parsed is not None and place.parsed(new) != version:
            raise SystemExit(f"{place.path} does not read {version} after the edit; nothing was changed")
        updated[file] = new
    for file, text in updated.items():
        with file.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)
    return [place.path for place in PLACES if feature or not place.installer]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    setter = commands.add_parser("set")
    setter.add_argument("version")
    checker = commands.add_parser("check")
    checker.add_argument("tag")
    checker.add_argument("--commit")
    options = parser.parse_args(argv)
    if options.command == "set":
        for path in set_version(options.version):
            print(f"{path}: {options.version}")
        return 0
    if not TAG.fullmatch(options.tag):
        print(f"a release tag looks like v1.2.3 or v1.2.3.4, not {options.tag}", file=sys.stderr)
        return 2
    version = options.tag[1:]
    read = _from_commit(ROOT, options.commit) if options.commit else _from_tree(ROOT)
    wrong = mismatches(version, read)
    if wrong:
        print(
            f"tag {options.tag} does not match this source: {'; '.join(wrong)}. "
            f"Run `python scripts/release/release-version.py set {version}`, commit it, or change the tag.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
