#!/usr/bin/env python3
"""Build ``openprogram-tailnet`` (apps/tailnet), the embedded Tailscale sidecar.

    build-tailnet.py                     this machine's target
    build-tailnet.py --all               every target a release ships
    build-tailnet.py --target linux-arm64 [--target ...]
    build-tailnet.py --install RUNTIME   this machine's target into RUNTIME/bin

Binaries land in ``apps/tailnet/dist/<goos>-<goarch>/``, where a Worker
running from the checkout finds one (``host/security/remote_access.py``).
``--install`` copies this machine's into a runtime being assembled and signs
it ad hoc on macOS, like the rest of the runtime before the App is signed.
``--optional`` turns a missing Go into a warning, for local refreshes on a
machine that has none; a release always has Go (``actions/setup-go``).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "apps" / "tailnet"
DIST = SOURCE / "dist"
NAME = "openprogram-tailnet"
TARGETS = ("darwin-arm64", "darwin-amd64", "linux-amd64", "linux-arm64", "windows-amd64", "windows-arm64")


def _go() -> str:
    found = shutil.which("go")
    if not found:
        raise FileNotFoundError("Go is not installed (macOS: brew install go)")
    return found


def _host(go: str) -> str:
    out = subprocess.run([go, "env", "GOOS", "GOARCH"], check=True, capture_output=True, text=True).stdout
    goos, goarch = out.split()
    return f"{goos}-{goarch}"


def output(target: str) -> Path:
    goos = target.split("-", 1)[0]
    return DIST / target / (f"{NAME}.exe" if goos == "windows" else NAME)


def build(go: str, target: str) -> Path:
    if target not in TARGETS:
        raise ValueError(f"unknown target {target}; one of {', '.join(TARGETS)}")
    goos, goarch = target.split("-", 1)
    path = output(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GOOS": goos, "GOARCH": goarch, "CGO_ENABLED": "0", "GOFLAGS": "-mod=readonly"}
    subprocess.run([go, "build", "-trimpath", "-ldflags=-s -w -buildid=", "-o", str(path), "."],
                   cwd=SOURCE, env=env, check=True)
    print(f"built {path.relative_to(REPO)}", flush=True)
    return path


def install(binary: Path, runtime: Path) -> Path:
    destination = runtime / "bin" / binary.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(binary, destination)
    destination.chmod(0o755)
    if sys.platform == "darwin":
        subprocess.run(["codesign", "--force", "--sign", "-", "--timestamp=none", str(destination)], check=True)
    print(f"installed {destination}", flush=True)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true", help="build every release target")
    parser.add_argument("--target", action="append", default=[], help="goos-goarch, repeatable")
    parser.add_argument("--install", type=Path, metavar="RUNTIME", help="copy this machine's build into RUNTIME/bin")
    parser.add_argument("--optional", action="store_true", help="warn instead of failing when Go is missing")
    options = parser.parse_args()
    try:
        go = _go()
    except FileNotFoundError as exc:
        if options.optional:
            print(f"warning: {exc}; {NAME} not built, so Open from anywhere is unavailable", file=sys.stderr)
            return 0
        raise
    host = _host(go)
    targets = list(TARGETS) if options.all else (options.target or [host])
    built = {target: build(go, target) for target in dict.fromkeys(targets)}
    if options.install is not None:
        install(built.get(host) or build(go, host), options.install)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
