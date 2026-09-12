#!/usr/bin/env python3
"""Select explicit release platforms and emit GitHub Actions matrix outputs."""
from __future__ import annotations

import argparse
import json


def release_matrices(windows: bool = False) -> dict[str, list[dict[str, str]]]:
    targets = [
        ("macos-26", "macos", "arm64"),
        ("macos-15-intel", "macos", "x86_64"),
        ("ubuntu-24.04", "linux", "x86_64"),
        ("ubuntu-24.04-arm", "linux", "arm64"),
    ]
    if windows:
        targets += [
            ("windows-2025", "windows", "x86_64"),
            ("windows-11-vs2026-arm", "windows", "arm64"),
        ]
    runtime = [dict(runner=runner, platform=platform, arch=arch)
               for runner, platform, arch in targets]
    desktop = [dict(
        runner="macos-15" if platform == "macos" and arch == "arm64" else runner,
        platform="mac" if platform == "macos" else "win",
        runtime_platform=platform, arch=arch,
        builder_arch="x64" if arch == "x86_64" else arch,
    ) for runner, platform, arch in targets if platform != "linux"]
    return {"runtime": runtime, "desktop": desktop}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows", action="store_true")
    options = parser.parse_args()
    for name, matrix in release_matrices(options.windows).items():
        print(f"{name}={json.dumps({'include': matrix}, separators=(',', ':'))}")
