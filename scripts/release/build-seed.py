#!/usr/bin/env python3
"""Pack a built base and sealed version into the seed a release carries.

    build-seed.py --root R --version V --repository REPO \\
        --launcher BIN [--helper APP] --compat apps/desktop/shell/compat.json \\
        --output DIR [--compression zst|gz]

The App's shell reads ``.tar.zst``; the command-line installers read ``.tar.gz``
because a stock ``tar`` everywhere can.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

BOOTSTRAP = ("install_seed.py", "versions.py", "restore_version.py", "bin-openprogram.sh", "bin-openprogram.cmd")
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MACH_O = (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca")


# macOS tar otherwise adds a ``._name`` entry beside every file with extended
# attributes, and those would count as edits to a sealed version.
TAR_ENVIRONMENT = {**os.environ, "COPYFILE_DISABLE": "1"}


def _tar_program() -> str:
    # Git Bash puts GNU tar first on PATH, and GNU tar reads C:\... as a remote
    # host. Windows' own tar understands the paths and matches what installs it.
    if os.name == "nt":
        return str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe")
    return "tar"


def _tar(arguments: list[str], root: Path) -> None:
    subprocess.run([_tar_program(), "-C", str(root), *arguments], check=True, env=TAR_ENVIRONMENT)


def _pack(archive: Path, root: Path, members: list[str], compression: str) -> None:
    if compression == "gz":
        _tar(["-czf", str(archive), *members], root)
        return
    environment = {**os.environ, "ZSTD_CLEVEL": "19"}
    tar = subprocess.Popen([_tar_program(), "-C", str(root), "-cf", "-", *members], stdout=subprocess.PIPE, env=TAR_ENVIRONMENT)
    with open(archive, "wb") as output:
        compressor = subprocess.run(["zstd", "-q", "-T0", "-"], stdin=tar.stdout, stdout=output, env=environment)
    tar.stdout.close()
    if tar.wait() != 0 or compressor.returncode != 0:
        raise SystemExit(f"could not pack {archive.name}")


def _macho(path: Path) -> bool:
    with path.open("rb") as stream:
        return stream.read(4) in MACH_O


def _sign_base(base: Path, identity: str) -> None:
    """Binaries in a base are only ever signed here, before it is packed."""
    entitlements = REPOSITORY_ROOT / "apps/desktop/build/entitlements.mac.inherit.plist"
    binaries = [item for item in base.rglob("*") if item.is_file() and not item.is_symlink() and _macho(item)]
    for binary in sorted(binaries, key=lambda item: len(item.parts), reverse=True):
        subprocess.run(
            ["codesign", "--force", "--sign", identity, "--timestamp", "--options", "runtime",
             "--entitlements", str(entitlements), str(binary)],
            check=True, capture_output=True,
        )


def build(arguments: argparse.Namespace) -> Path:
    root = arguments.root.resolve()
    version_dir = root / "versions" / arguments.version
    manifest = json.loads((version_dir / "version.json").read_text(encoding="utf-8"))
    base = manifest["base"]
    if arguments.sign_identity:
        _sign_base(root / "bases" / base, arguments.sign_identity)
    output = arguments.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    extension = "tar.zst" if arguments.compression == "zst" else "tar.gz"
    _pack(output / f"base.{extension}", root, [f"bases/{base}"], arguments.compression)
    members = [f"versions/{arguments.version}"]
    frontend = root / "pristine-frontend" / f"{manifest['frontend_sha']}.zip"
    if frontend.is_file():
        members.append(f"pristine-frontend/{frontend.name}")
    _pack(output / f"version.{extension}", root, members, arguments.compression)

    subprocess.run(
        ["git", "-C", str(arguments.repository), "bundle", "create", str(output / "repo.bundle"), arguments.ref],
        check=True, capture_output=True,
    )
    if arguments.launcher.suffix == ".exe":
        shutil.copy2(arguments.launcher, output / "openprogram-launcher.exe")
    elif arguments.helper is None:
        shutil.copy2(arguments.launcher, output / "openprogram-launcher")
    if arguments.helper is not None:
        helper = output / "helper" / "OpenProgram.app"
        shutil.rmtree(helper, ignore_errors=True)
        shutil.copytree(arguments.helper, helper, symlinks=True)

    compat = json.loads(arguments.compat.read_text(encoding="utf-8"))
    (output / "shell-compat.json").write_text(
        json.dumps({"min": compat["min"], "max": compat["max"]}) + "\n", encoding="utf-8",
    )
    bootstrap = output / "bootstrap"
    bootstrap.mkdir(exist_ok=True)
    source = REPOSITORY_ROOT / "scripts/release/bootstrap"
    for name in BOOTSTRAP:
        shutil.copy2(source / name, bootstrap / name)
    (output / "seed.json").write_text(json.dumps({
        "schema": 1,
        "version": arguments.version,
        "base": base,
        "commit": manifest["commit"],
        "built_root": str(root),
        "shell_compat": manifest.get("shell_compat"),
    }, indent=2) + "\n", encoding="utf-8")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--ref", default="HEAD", help="What the bundle carries.")
    parser.add_argument("--launcher", type=Path, required=True)
    parser.add_argument("--helper", type=Path, help="The signed macOS helper bundle.")
    parser.add_argument("--compat", type=Path, default=REPOSITORY_ROOT / "apps/desktop/shell/compat.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compression", choices=("zst", "gz"), default="zst")
    parser.add_argument("--sign-identity", help="Developer ID that signs the base's macOS binaries.")
    print(build(parser.parse_args(argv)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
