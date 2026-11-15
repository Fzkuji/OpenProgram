#!/usr/bin/env python3
"""Make the assets an official release publishes for version stores.

    platform   build one platform's sealed version and pack it
    finalize   add the Git bundle, SHA256SUMS and the signed manifest
    public-key print the value to put in RELEASE_PUBLIC_KEYS for a signing key
    generate-key write a new signing key to a path and print only its public value
    sign-file  write <file>.sig, for example for update-source.json

A signing key is an Ed25519 PEM with no passphrase. ``generate-key`` makes one
that only its owner can read, and never replaces an existing file.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from openprogram.operations.self_update.official import assets  # noqa: E402
from openprogram.operations.self_update.versions import build as builder  # noqa: E402
from openprogram.operations.self_update.versions.layout import Layout  # noqa: E402


def _platform(options: argparse.Namespace) -> int:
    name = options.platform or assets.current_platform()
    options.root.mkdir(parents=True, exist_ok=True)
    layout = Layout(options.root.resolve())
    built = builder.build_version(layout, builder.BuildRequest(
        "release", options.commit, repository=options.repository.resolve(), built_by="official release",
    ))
    entry = assets.package_platform(
        layout, built.version, options.output.resolve(), repository=options.repository.resolve(),
        platform=name,
    )
    print(json.dumps(entry, sort_keys=True))
    return 0


def _finalize(options: argparse.Namespace) -> int:
    assets.finalize_release(
        options.directory.resolve(), release=options.release.removeprefix("v"), commit=options.commit,
        repository=options.repository.resolve(), private_key_pem=options.signing_key.read_bytes(),
    )
    return 0


def _public_key(options: argparse.Namespace) -> int:
    print(assets.public_key_hex(options.signing_key.read_bytes()))
    return 0


def _generate_key(options: argparse.Namespace) -> int:
    print(assets.generate_signing_key(options.path))
    return 0


def _sign_file(options: argparse.Namespace) -> int:
    assets.sign_file(options.file, options.signing_key.read_bytes())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    platform = commands.add_parser("platform")
    platform.add_argument("--repository", type=Path, required=True)
    platform.add_argument("--commit", required=True)
    platform.add_argument("--root", type=Path, required=True, help="an empty directory to build in")
    platform.add_argument("--output", type=Path, required=True)
    platform.add_argument("--platform")
    platform.set_defaults(run=_platform)

    finalize = commands.add_parser("finalize")
    finalize.add_argument("--directory", type=Path, required=True)
    finalize.add_argument("--release", required=True)
    finalize.add_argument("--commit", required=True)
    finalize.add_argument("--repository", type=Path, required=True)
    finalize.add_argument("--signing-key", type=Path, required=True)
    finalize.set_defaults(run=_finalize)

    public_key = commands.add_parser("public-key")
    public_key.add_argument("--signing-key", type=Path, required=True)
    public_key.set_defaults(run=_public_key)

    generate_key = commands.add_parser("generate-key")
    generate_key.add_argument("path", type=Path, help="where to write the private key; it must not exist")
    generate_key.set_defaults(run=_generate_key)

    sign_file = commands.add_parser("sign-file")
    sign_file.add_argument("file", type=Path)
    sign_file.add_argument("--signing-key", type=Path, required=True)
    sign_file.set_defaults(run=_sign_file)

    options = parser.parse_args()
    try:
        return options.run(options)
    except (assets.ReleaseError, builder.BuildError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
