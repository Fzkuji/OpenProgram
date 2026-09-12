#!/usr/bin/env python3
"""Build and publish the manifest-bound local OnlyOffice asset pack.

The command is intentionally preparation-time only.  The server consumes the
published directory and never invokes this module while opening a document.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Source-tree build tools use the same stdlib-only validator as installed workers.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

SOURCE = "d15d12b6945be4d8b0f3aa1806120e740d2950ee"
PACKAGE_VERSION = "0.3.34"
HOST_BUILD_ID = "office-host-0.3.34-r1"
PATCH_SHA256 = "dc31dd9d3ee4cf777d3e2d19c5a00d76eec2115e239720cd09298be919741b9a"
BOOTSTRAP = {
    "office-host.html", "reset.html", "document_editor_service_worker.js", "sw.js",
    "plugins.json", "themes.json", "onlyoffice-runtime-assets.json",
}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def files(root: Path) -> list[str]:
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and not p.is_symlink()
    )


def run(command: list[str], cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def _copy_source(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True)
    archive = destination / "source.tar"
    with archive.open("wb") as stream:
        subprocess.run(["git", "-C", str(source), "archive", "HEAD"], stdout=stream, check=True)
    run(["tar", "-xf", str(archive)], destination)
    archive.unlink()


def _overlay_fonts(stage: Path, font_pack: Path | None, font_input: Path | None) -> None:
    if font_pack is None:
        return
    required = {
        "onlyoffice-browser-font-assets.json",
        "onlyoffice-browser-font-source-map.json",
    }
    available = set(files(font_pack))
    if not required <= available:
        raise RuntimeError("font pack is missing generated manifests")
    for relative in sorted(available):
        if relative in required or relative.startswith(("fonts/", "sdkjs/common/", "server/FileConverter/")):
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(font_pack / relative, target)
    if font_input is not None:
        license_files = [
            p for p in files(font_input)
            if Path(p).name.lower().startswith(("license", "ofl")) or p.lower().endswith("license_ofl.txt")
        ]
        for relative in license_files:
            target = stage / "licenses" / "font-input" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(font_input / relative, target)


def _prune(stage: Path) -> None:
    for relative in ("index.html", "save-e2e.html", "burst-e2e.html", "fixtures"):
        target = stage / relative
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    for relative in files(stage / "assets") if (stage / "assets").is_dir() else []:
        if Path(relative).name.startswith(("main-", "saveE2E-", "burstE2E-")):
            (stage / "assets" / relative).unlink()


def _manifest(stage: Path, source: Path, patch: Path, lock: Path, runtime: dict) -> dict:
    source_head = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    (stage / "licenses" / "onlyoffice-browser").mkdir(parents=True, exist_ok=True)
    license_source = source / "LICENSE"
    if license_source.is_file():
        shutil.copy2(license_source, stage / "licenses/onlyoffice-browser/LICENSE")
    all_files = files(stage)
    licenses = [
        p for p in all_files
        if p.startswith("licenses/") and (
            Path(p).name.lower().startswith(("license", "ofl")) or p.lower().endswith(".license")
        )
    ]
    # License files are served and are included in the same byte/digest
    # inventory; `licenses` is the explicit legal-document index.
    assets = [p for p in all_files if p != "openprogram-office-assets.json"]
    if not BOOTSTRAP <= set(assets):
        raise RuntimeError("prepared Office pack is missing bootstrap resources")
    runtime_bytes = (stage / "onlyoffice-runtime-assets.json").read_bytes()
    native_identity = hashlib.sha256(runtime_bytes).hexdigest()
    manifest = {
        "version": 1,
        "packageVersion": PACKAGE_VERSION,
        "source": SOURCE,
        "hostBuildId": HOST_BUILD_ID,
        "expectedHostIdentity": native_identity,
        "assembly": {
            "sourceHead": source_head,
            "sourcePackageVersion": PACKAGE_VERSION,
            "adoptionPatchSha256": digest(patch),
            "reviewedNpmLockSha256": digest(lock),
            "runtimePrune": runtime,
            "exclusions": ["index.html", "save-e2e.html", "burst-e2e.html", "fixtures/**", "assets/main-*", "assets/saveE2E-*", "assets/burstE2E-*"],
        },
        "assets": [
            {"path": p, "bytes": (stage / p).stat().st_size, "sha256": digest(stage / p)}
            for p in assets
        ],
        "licenses": licenses,
    }
    return manifest


def prepare(args: argparse.Namespace) -> Path:
    source = args.source.resolve()
    output = args.output.resolve()
    patch = args.patch.resolve()
    lock = args.lock.resolve()
    if not (source / ".git").exists():
        raise RuntimeError(f"source checkout is not a git repository: {source}")
    head = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if head != SOURCE:
        raise RuntimeError(f"source revision mismatch: expected {SOURCE}, got {head}")
    package = json.loads((source / "package.json").read_text(encoding="utf-8"))
    if package.get("version") != PACKAGE_VERSION:
        raise RuntimeError("OnlyOffice package version mismatch")
    if digest(patch) != PATCH_SHA256:
        raise RuntimeError("adoption patch digest mismatch")
    if digest(lock) != args.lock_sha256:
        raise RuntimeError("reviewed npm lock digest mismatch")
    if args.font_pack is None or args.font_input is None:
        raise RuntimeError("--font-pack and --font-input are required for the reproducible Office pack")
    from font_assets import validate_font_assets
    font_provenance = validate_font_assets(args.font_pack, args.font_input)
    output.parent.mkdir(parents=True, exist_ok=True)
    stage_parent = Path(tempfile.mkdtemp(prefix=f".{output.name}.stage-", dir=output.parent))
    build_source = stage_parent / "source"
    pack_stage = stage_parent / "pack"
    try:
        _copy_source(source, build_source)
        shutil.copy2(lock, build_source / "package-lock.json")
        run(["git", "apply", "--check", str(patch)], build_source)
        run(["git", "apply", str(patch)], build_source)
        run([args.npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"], build_source)
        run([args.npm, "run", "build"], build_source)
        run([args.npm, "run", "build:lib"], build_source)
        dist = build_source / "dist"
        if not dist.is_dir():
            raise RuntimeError("OnlyOffice build did not produce dist")
        shutil.copytree(dist, pack_stage)
        runtime_script = build_source / "scripts/build-onlyoffice-runtime-assets.mjs"
        run([args.node, str(runtime_script), "--input", str(pack_stage), "--prune-root"], build_source)
        _prune(pack_stage)
        _overlay_fonts(pack_stage, args.font_pack.resolve(), args.font_input.resolve())
        runtime_manifest = json.loads((pack_stage / "onlyoffice-runtime-assets.json").read_text(encoding="utf-8"))
        materials = pack_stage / "source"
        materials.mkdir()
        run(["git", "-C", str(source), "archive", "--format=tar.gz",
             f"--output={materials / 'onlyoffice-browser.tar.gz'}", SOURCE], source)
        shutil.copy2(patch, materials / "adoption.patch")
        shutil.copy2(lock, materials / "package-lock.json")
        for name in ("README.md", "prepare.py", "font_assets.py", "font-generation.mjs", "font-verification.mjs"):
            shutil.copy2(Path(__file__).with_name(name), materials / name)
        # Keep portable source names and digests, not the build machine's paths.
        (materials / "font-provenance.json").write_text(json.dumps(font_provenance, indent=2) + "\n")
        manifest = _manifest(pack_stage, source, patch, lock, runtime_manifest)
        manifest["assembly"]["fonts"] = font_provenance

        (pack_stage / "openprogram-office-assets.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        from openprogram.office_assets import install_office_pack
        install_office_pack(pack_stage, output)
        return output
    except Exception:
        raise
    finally:
        shutil.rmtree(stage_parent, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--font-pack", type=Path)
    parser.add_argument("--font-input", type=Path)
    parser.add_argument("--patch", type=Path, default=Path(__file__).with_name("adoption.patch"))
    parser.add_argument("--lock", type=Path, default=Path(__file__).with_name("package-lock.json"))
    parser.add_argument("--lock-sha256", default="7b71a099e703545a80d06454ae2af6f52e52f0c3f1fbe5ead31dfdf11faf2590")
    parser.add_argument("--npm", default="npm")
    parser.add_argument("--node", default="node")
    args = parser.parse_args(argv)
    try:
        print(f"prepared Office pack at {prepare(args)}")
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"office preparation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
