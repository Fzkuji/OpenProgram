#!/usr/bin/env python3
"""The Office archive every release uploads, under the name the App pins.

The archive cannot be rebuilt in CI: its fonts and their licences, and the
pinned upstream editor build, are inputs only the owner's machine holds
(scripts/release/office/README.md). So each release carries forward the copy
the newest published release has, and the first one comes from the owner,
once, through a draft release of its tag.

    office-archive.py name
        Print the pinned file name.
    office-archive.py fetch FILE
        Download the newest published release's copy to FILE and verify it.
        Exit 3 when that release has none.
    office-archive.py verify FILE --tag vX.Y.Z
        Exit 0 when FILE is the pinned archive; otherwise say what must be
        provided and how, as a GitHub Actions error.
    office-archive.py check FILE
        Say whether FILE is the pinned archive; exit 1 when it is not.
    office-archive.py released vX.Y.Z
        Ask GitHub (gh) whether the newest published release or the draft of
        the tag has the pinned archive; exit 3 when neither has it.
    office-archive.py seed vX.Y.Z FILE
        On the owner's machine, before tagging a release that is the first to
        carry the archive: verify FILE, then put it on the draft release of
        the tag with gh, where the Release workflow finds it.
        `publish-github.sh --tag vX.Y.Z --office-archive FILE` does this when
        GitHub has no copy.
    office-archive.py pack PREPARED DIRECTORY
        Zip the pack scripts/release/office/prepare.py published at PREPARED
        into DIRECTORY, named after the editor, the patch and the pack's own
        manifest, and write the new name, size and SHA-256 into install.py
        and the download limit in safe_http.py.

Size and SHA-256 come from openprogram/programs/applications/office_editor/
install.py, read without importing it, so no dependency is needed here
(``pack`` alone loads the pack validator next to it).
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
INSTALL = ROOT / "openprogram/programs/applications/office_editor/install.py"
SAFE_HTTP = ROOT / "openprogram/host/security/safe_http.py"
NOT_PUBLISHED = 3


def pins(path: Path = INSTALL) -> dict[str, object]:
    """``OFFICE_RELEASES``, ``OFFICE_ARCHIVE_NAME``, ``OFFICE_ARCHIVE_SHA256`` and ``OFFICE_DOWNLOAD_BYTES``."""
    wanted = {"OFFICE_RELEASES", "OFFICE_ARCHIVE_NAME", "OFFICE_ARCHIVE_SHA256", "OFFICE_DOWNLOAD_BYTES"}
    found: dict[str, object] = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in wanted:
                found[node.targets[0].id] = ast.literal_eval(node.value)
    missing = wanted - set(found)
    if missing:
        raise SystemExit(f"{path} no longer declares {', '.join(sorted(missing))}")
    return found


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def problem(path: Path, pinned: dict[str, object]) -> str | None:
    """Why ``path`` is not the pinned archive, or ``None``."""
    if not path.is_file():
        return "it is missing"
    size = path.stat().st_size
    if size != pinned["OFFICE_DOWNLOAD_BYTES"]:
        return f"it has {size} bytes, not {pinned['OFFICE_DOWNLOAD_BYTES']}"
    digest = sha256(path)
    if digest != pinned["OFFICE_ARCHIVE_SHA256"]:
        return f"its SHA-256 is {digest}, not {pinned['OFFICE_ARCHIVE_SHA256']}"
    return None


def fetch(output: Path, pinned: dict[str, object], opener=urllib.request.urlopen) -> int:
    url = f"{pinned['OFFICE_RELEASES']}/latest/download/{pinned['OFFICE_ARCHIVE_NAME']}"
    try:
        response = opener(urllib.request.Request(url, headers={"User-Agent": "OpenProgram-release"}), timeout=60)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            print(f"the newest published release has no {pinned['OFFICE_ARCHIVE_NAME']}", file=sys.stderr)
            return NOT_PUBLISHED
        raise
    output.parent.mkdir(parents=True, exist_ok=True)
    with response, output.open("wb") as handle:
        shutil.copyfileobj(response, handle, 1024 * 1024)
    reason = problem(output, pinned)
    if reason is not None:
        output.unlink()
        # A published copy that differs from the pin is never carried forward.
        raise SystemExit(f"the newest release's {pinned['OFFICE_ARCHIVE_NAME']} is not the pinned archive: {reason}")
    return 0


def missing_message(tag: str, pinned: dict[str, object], reason: str) -> str:
    name = pinned["OFFICE_ARCHIVE_NAME"]
    return (
        f"{tag} cannot be released without the Office archive {name} "
        f"({pinned['OFFICE_DOWNLOAD_BYTES']} bytes, SHA-256 {pinned['OFFICE_ARCHIVE_SHA256']}): "
        f"neither the newest published release nor a draft release of {tag} has it ({reason}). "
        f"On the machine that holds the file run `python scripts/release/office-archive.py seed {tag} "
        f"/path/to/{name}`, then run this workflow again."
    )


def seed(tag: str, path: Path, pinned: dict[str, object], run=subprocess.run) -> int:
    if path.name != pinned["OFFICE_ARCHIVE_NAME"]:
        raise SystemExit(f"the release file must be named {pinned['OFFICE_ARCHIVE_NAME']}, not {path.name}")
    reason = problem(path, pinned)
    if reason is not None:
        raise SystemExit(f"{path} is not the pinned Office archive: {reason}")
    view = run(["gh", "release", "view", tag, "--json", "isDraft", "--jq", ".isDraft"],
               capture_output=True, text=True)
    if view.returncode == 0 and view.stdout.strip() != "true":
        raise SystemExit(f"{tag} is already public; a release is never changed after publishing")
    if view.returncode == 0:
        command = ["gh", "release", "upload", tag, str(path), "--clobber"]
    else:
        command = ["gh", "release", "create", tag, str(path), "--draft",
                   "--title", f"OpenProgram {tag.removeprefix('v')} Release",
                   "--notes", "The Release workflow writes these notes when it publishes."]
    run(command, check=True)
    print(f"{path.name} is on the draft release {tag}; the Release workflow carries it from there")
    return 0


def released(tag: str, pinned: dict[str, object], run=subprocess.run) -> bool:
    """Whether the newest published release or the draft of ``tag`` has the archive."""
    name = pinned["OFFICE_ARCHIVE_NAME"]
    # Without a tag gh views the newest published release, never a draft.
    for target in ([], [tag]):
        listed = run(["gh", "release", "view", *target, "--json", "assets", "--jq", ".assets[].name"],
                     capture_output=True, text=True)
        if listed.returncode == 0 and name in listed.stdout.split():
            return True
    return False


def _pack_module():
    """``office_editor/assets.py``, loaded by path so the Programs are not imported."""
    name = "openprogram_office_assets"
    if name not in sys.modules:
        sys.path.insert(0, str(ROOT))
        spec = importlib.util.spec_from_file_location(name, INSTALL.with_name("assets.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module  # dataclasses look their module up while it loads
        spec.loader.exec_module(module)
    return sys.modules[name]


# Fixed metadata, so the same pack always gives the same bytes.
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_ZIP_MODE = (stat.S_IFREG | 0o644) << 16


def pack(prepared: Path, directory: Path, assets=None) -> Path:
    """Zip the validated pack at ``prepared`` the way the App unpacks it."""
    assets = assets or _pack_module()
    selected = assets.validate_prepared_office_pack(prepared)
    names = sorted([*selected.assets, "openprogram-office-assets.json"])
    if len(names) > assets.MAX_ASSETS:
        raise SystemExit(f"the pack has {len(names)} files; the App unpacks at most {assets.MAX_ASSETS}")
    manifest = hashlib.sha256(selected.manifest_bytes).hexdigest()
    name = f"OfficeAssets-{assets.OFFICE_SOURCE[:7]}-{assets.OFFICE_PATCH_SHA256[:8]}-{manifest[:8]}.zip"
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / name
    partial = directory / f".{name}.partial"
    with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for relative in names:
            entry = zipfile.ZipInfo(relative, _ZIP_TIME)
            entry.external_attr = _ZIP_MODE
            entry.compress_type = zipfile.ZIP_DEFLATED
            with (selected.root / relative).open("rb") as source, bundle.open(entry, "w") as target:
                shutil.copyfileobj(source, target, 1024 * 1024)
    partial.replace(output)
    return output


def write_pins(archive: Path, install: Path = INSTALL, safe_http: Path = SAFE_HTTP) -> dict[str, object]:
    """Pin ``archive`` in the App and in its download limit; change nothing on a miss."""
    data = archive.stat().st_size
    digest = sha256(archive)
    edits = {
        install: [
            (r'^OFFICE_ARCHIVE_NAME = "[^"]*"$', f'OFFICE_ARCHIVE_NAME = "{archive.name}"'),
            (r'^OFFICE_ARCHIVE_SHA256 = "[0-9a-f]{64}"$', f'OFFICE_ARCHIVE_SHA256 = "{digest}"'),
            (r'^OFFICE_DOWNLOAD_BYTES = [0-9_]+$', f"OFFICE_DOWNLOAD_BYTES = {data}"),
        ],
        safe_http: [
            (r'(replace\(_download\("office\.assets", URLTrustClass\.FIXED_PUBLIC_SERVICE\),\s+'
             r'max_decoded_body_bytes=)[0-9_]+', rf"\g<1>{data}"),
        ],
    }
    updated = {}
    for path, replacements in edits.items():
        text = path.read_text(encoding="utf-8")
        for pattern, replacement in replacements:
            text, count = re.subn(pattern, replacement, text, flags=re.MULTILINE)
            if count != 1:
                raise SystemExit(f"cannot find the line {pattern!r} in {path}; nothing was changed")
        updated[path] = text
    for path, text in updated.items():
        path.write_text(text, encoding="utf-8")
    return {"OFFICE_ARCHIVE_NAME": archive.name, "OFFICE_ARCHIVE_SHA256": digest, "OFFICE_DOWNLOAD_BYTES": data}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("name")
    fetch_parser = commands.add_parser("fetch")
    fetch_parser.add_argument("output", type=Path)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("file", type=Path)
    verify_parser.add_argument("--tag", required=True)
    commands.add_parser("check").add_argument("file", type=Path)
    commands.add_parser("released").add_argument("tag")
    pack_parser = commands.add_parser("pack")
    pack_parser.add_argument("prepared", type=Path)
    pack_parser.add_argument("directory", type=Path)
    seed_parser = commands.add_parser("seed")
    seed_parser.add_argument("tag")
    seed_parser.add_argument("file", type=Path)
    options = parser.parse_args(argv)
    if options.command == "pack":
        archive = pack(options.prepared, options.directory)
        pinned = write_pins(archive)
        print(f"{archive}: {pinned['OFFICE_DOWNLOAD_BYTES']} bytes, SHA-256 {pinned['OFFICE_ARCHIVE_SHA256']}; "
              "pinned in install.py and safe_http.py")
        return 0
    pinned = pins()
    if options.command == "name":
        print(pinned["OFFICE_ARCHIVE_NAME"])
        return 0
    if options.command == "fetch":
        return fetch(options.output, pinned)
    if options.command == "check":
        reason = problem(options.file, pinned)
        facts = (f"{pinned['OFFICE_ARCHIVE_NAME']} ({pinned['OFFICE_DOWNLOAD_BYTES']} bytes, "
                 f"SHA-256 {pinned['OFFICE_ARCHIVE_SHA256']})")
        if options.file.name != pinned["OFFICE_ARCHIVE_NAME"]:
            reason = reason or f"it is named {options.file.name}"
        if reason is not None:
            print(f"Office archive: {options.file} is not the pinned {facts}: {reason}", file=sys.stderr)
            return 1
        print(f"Office archive: {options.file} is the pinned {facts}")
        return 0
    if options.command == "released":
        if released(options.tag, pinned):
            print(f"Office archive: GitHub has {pinned['OFFICE_ARCHIVE_NAME']} for {options.tag}")
            return 0
        print(f"Office archive: neither the newest release nor the {options.tag} draft has "
              f"{pinned['OFFICE_ARCHIVE_NAME']}", file=sys.stderr)
        return NOT_PUBLISHED
    if options.command == "verify":
        reason = problem(options.file, pinned)
        if reason is not None:
            print(f"::error::{missing_message(options.tag, pinned, reason)}")
            return 1
        return 0
    return seed(options.tag, options.file, pinned)


if __name__ == "__main__":
    raise SystemExit(main())
