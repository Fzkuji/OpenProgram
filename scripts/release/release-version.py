#!/usr/bin/env python3
"""Every place a release's version is written, changed or checked together.

    release-version.py set X.Y.Z
        Write X.Y.Z into every place below, or change nothing when any of
        them cannot be found.
    release-version.py check vX.Y.Z [--commit REV]
        Exit 0 when every place says X.Y.Z, in the working tree or in the
        commit REV; otherwise name each place that differs.

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
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


@dataclass(frozen=True)
class Place:
    """One file and the single line in it that holds the version."""

    path: str
    pattern: re.Pattern[str]  # group 1 is the version
    parsed: Callable[[str], str | None] | None = None  # a real parser, to confirm the edit

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


V = r"([0-9]+\.[0-9]+\.[0-9]+)"
PLACES = (
    Place("pyproject.toml", re.compile(rf'(?m)^\[project\]\n(?:[^\[\n].*\n|\n)*?version = "{V}"$'), _toml_project),
    Place("apps/desktop/package.json", re.compile(rf'(?m)\A\{{\n  "name": "openprogram-desktop",\n  "version": "{V}",$'), _json_version),
    Place("package-lock.json", re.compile(rf'(?m)^    "apps/desktop": \{{\n      "name": "openprogram-desktop",\n      "version": "{V}",$'), _npm_workspace),
    Place("uv.lock", re.compile(rf'(?m)^\[\[package\]\]\nname = "openprogram"\nversion = "{V}"\nsource = \{{ editable = "\." \}}$'), _uv_project),
    Place("scripts/release/install-release.sh", re.compile(rf'(?m)^OPENPROGRAM_VERSION="\$\{{OPENPROGRAM_VERSION:-{V}\}}"$')),
    Place("scripts/release/install-release.ps1", re.compile(
        rf'(?m)^\$Version = if \(\$env:OPENPROGRAM_VERSION\) \{{ \$env:OPENPROGRAM_VERSION \}} else \{{ "{V}" \}}$')),
)


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
    return [f"{path} says {said or 'nothing'}" for path, said in versions(read).items() if said != version]


def set_version(version: str, root: Path = ROOT) -> list[str]:
    """Write ``version`` everywhere, or raise and write nothing."""
    if not VERSION.fullmatch(version):
        raise SystemExit(f"a release version looks like 1.2.3, not {version}")
    updated: dict[Path, str] = {}
    for place in PLACES:
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
    return [place.path for place in PLACES]


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
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", options.tag):
        print(f"a release tag looks like v1.2.3, not {options.tag}", file=sys.stderr)
        return 2
    version = options.tag[1:]
    read = _from_commit(ROOT, options.commit) if options.commit else _from_tree(ROOT)
    wrong = mismatches(version, read)
    if wrong:
        print(
            f"tag {options.tag} does not match this source: {'; '.join(wrong)}. "
            f"Run `python scripts/release/release-version.py set {version}`, commit and promote, or change the tag.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
