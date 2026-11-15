#!/usr/bin/env python3
"""Which wheels a release runtime installs, and whether the lock has them.

The runtime ships to every machine its documentation names, not only to the
build runner. So packages are installed for a fixed target, never for the
runner: binary wheels only, tagged for the oldest system the release supports
(``macosMinimum`` and ``glibcMinimum`` in product-runtime.json). The few
packages a target builds from source against a static library its CI leg
prepares are listed in ``sourceBuilds``.

    runtime-wheels.py target
        The target of the Python running this, e.g. windows-x86_64. The build
        asks the Python it packages, so everything it bundles matches it.
    runtime-wheels.py uv-args [--target macos-x86_64]
        The `uv pip install` arguments for this machine's (or that) target.
        Standard library only, so the bare build Python can run it.
    runtime-wheels.py check [--target ...] [--lock uv.lock]
        Every package the runtime installs on each target has a wheel for it
        (or is listed as a source build). Needs `packaging`.
"""
from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# The base build runs this with -I and a bare Python, so the checkout is put
# on the path for the one standard-library-only helper it shares.
sys.path.insert(0, str(ROOT))
from openprogram.host._platform.architecture import interpreter_architecture  # noqa: E402
PRODUCT = ROOT / "scripts" / "release" / "product-runtime.json"
EXTRAS = ("all", "search")  # what build-base.sh exports

# target: (uv --python-platform prefix, marker environment)
TARGETS = {
    "macos-arm64": ("aarch64-apple-darwin", {"sys_platform": "darwin", "platform_machine": "arm64",
                                             "platform_system": "Darwin", "os_name": "posix"}),
    "macos-x86_64": ("x86_64-apple-darwin", {"sys_platform": "darwin", "platform_machine": "x86_64",
                                             "platform_system": "Darwin", "os_name": "posix"}),
    "linux-x86_64": ("x86_64-manylinux", {"sys_platform": "linux", "platform_machine": "x86_64",
                                          "platform_system": "Linux", "os_name": "posix"}),
    "linux-arm64": ("aarch64-manylinux", {"sys_platform": "linux", "platform_machine": "aarch64",
                                          "platform_system": "Linux", "os_name": "posix"}),
    "windows-x86_64": ("x86_64-pc-windows-msvc", {"sys_platform": "win32", "platform_machine": "AMD64",
                                                  "platform_system": "Windows", "os_name": "nt"}),
    "windows-arm64": ("aarch64-pc-windows-msvc", {"sys_platform": "win32", "platform_machine": "ARM64",
                                                  "platform_system": "Windows", "os_name": "nt"}),
}


def product(path: Path = PRODUCT) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def host_target() -> str:
    """The target of the Python running this, which is the Python being packaged.

    Its own architecture, not the machine's: x64 Python on Windows on Arm
    installs x64 wheels.
    """
    system = {"darwin": "macos", "linux": "linux", "win32": "windows"}[sys.platform]
    return f"{system}-{interpreter_architecture()}"


def uv_args(target: str, config: dict) -> list[str]:
    """`uv pip install` arguments that pick wheels for ``target``'s oldest system.

    On macOS uv reads the version from MACOSX_DEPLOYMENT_TARGET, which the
    build sets to ``macosMinimum``; that also targets any source build.
    """
    prefix, _environment = TARGETS[target]
    if target.startswith("linux-"):
        prefix = f"{prefix}_{config['glibcMinimum'].replace('.', '_')}"
    arguments = ["--python-platform", prefix, "--only-binary", ":all:"]
    for name in config.get("sourceBuilds", {}).get(target, []):
        arguments += ["--no-binary", name]
    return arguments


def _supported_tags(target: str, config: dict, python: tuple[int, int]) -> set:
    from packaging import tags

    system, arch = target.split("-")
    if system == "macos":
        floor = tuple(int(part) for part in config["macosMinimum"].split("."))[:2]
        platforms = list(tags.mac_platforms(floor, arch))
    elif system == "linux":
        machine = "aarch64" if arch == "arm64" else "x86_64"
        glibc_minor = int(config["glibcMinimum"].split(".")[1])
        platforms = [f"manylinux_2_{minor}_{machine}" for minor in range(glibc_minor, 4, -1)]
        platforms += [f"manylinux2014_{machine}"]
        if machine == "x86_64":
            platforms += ["manylinux2010_x86_64", "manylinux1_x86_64"]
    else:
        platforms = ["win_arm64" if arch == "arm64" else "win_amd64"]
    interpreter = f"cp{python[0]}{python[1]}"
    return (set(tags.cpython_tags(python, abis=[interpreter], platforms=platforms))
            | set(tags.compatible_tags(python, interpreter, platforms)))


def _environment(target: str, python_version: str) -> dict[str, str]:
    major_minor = ".".join(python_version.split(".")[:2])
    return {
        **TARGETS[target][1],
        "python_version": major_minor,
        "python_full_version": python_version,
        "implementation_name": "cpython",
        "implementation_version": python_version,
        "platform_python_implementation": "CPython",
        "platform_release": "",
        "platform_version": "",
        "extra": "",
    }


def installed_packages(lock: dict, target: str, python_version: str) -> list[dict]:
    """The lock's packages the runtime installs on ``target``."""
    from packaging.markers import Marker

    environment = _environment(target, python_version)

    def applies(marker: str | None) -> bool:
        return marker is None or Marker(marker).evaluate(environment)

    by_name: dict[str, list[dict]] = {}
    for package in lock["package"]:
        by_name.setdefault(package["name"], []).append(package)

    def resolve(dependency: dict) -> dict:
        candidates = by_name[dependency["name"]]
        if "version" in dependency:
            candidates = [package for package in candidates if package["version"] == dependency["version"]]
        if len(candidates) > 1:
            candidates = [package for package in candidates
                          if any(applies(marker) for marker in package.get("resolution-markers", []))]
        if len(candidates) != 1:
            raise RuntimeError(f"cannot tell which {dependency['name']} the lock installs on {target}")
        return candidates[0]

    root = next(package for package in lock["package"] if package.get("source", {}).get("editable") == ".")
    pending = [(root, set(EXTRAS))]
    seen: dict[tuple[str, str], dict] = {}
    expanded: set[tuple[str, str, str]] = set()
    while pending:
        package, extras = pending.pop()
        key = (package["name"], package["version"])
        for group in [None, *sorted(extras)]:
            if (*key, group or "") in expanded:
                continue
            expanded.add((*key, group or ""))
            dependencies = (package.get("dependencies", []) if group is None
                            else package.get("optional-dependencies", {}).get(group, []))
            for dependency in dependencies:
                if not applies(dependency.get("marker")):
                    continue
                child = resolve(dependency)
                if child is not root:
                    seen[(child["name"], child["version"])] = child
                pending.append((child, set(dependency.get("extra", []))))
    return sorted(seen.values(), key=lambda package: package["name"])


def missing_wheels(lock: dict, target: str, config: dict) -> list[str]:
    """Problems: a package with no wheel for ``target``, or a needless source build."""
    from packaging.utils import parse_wheel_filename

    python_version = config["python"]
    python = tuple(int(part) for part in python_version.split(".")[:2])
    supported = _supported_tags(target, config, python)
    source_builds = set(config.get("sourceBuilds", {}).get(target, []))
    problems = []
    installed = installed_packages(lock, target, python_version)
    for package in installed:
        wheels = [parse_wheel_filename(wheel["url"].rsplit("/", 1)[1])[3] for wheel in package.get("wheels", [])]
        has_wheel = any(tag in supported for wheel_tags in wheels for tag in wheel_tags)
        name = package["name"]
        if has_wheel and name in source_builds:
            problems.append(f"{target}: {name} {package['version']} has a wheel now; take it out of sourceBuilds")
        elif not has_wheel and name not in source_builds:
            floor = (f"macOS {config['macosMinimum']}" if target.startswith("macos")
                     else f"glibc {config['glibcMinimum']}" if target.startswith("linux") else "Windows")
            problems.append(f"{target}: {name} {package['version']} has no wheel for Python "
                            f"{python[0]}.{python[1]} on {floor}")
    names = {package["name"] for package in installed}
    problems += [f"{target}: sourceBuilds lists {name}, which this target does not install"
                 for name in sorted(source_builds - names)]
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("target", "uv-args", "check"))
    parser.add_argument("--target", choices=sorted(TARGETS))
    parser.add_argument("--lock", type=Path, default=ROOT / "uv.lock")
    args = parser.parse_args()
    if args.command == "target":
        print(host_target())
        return 0
    config = product()
    if args.command == "uv-args":
        print("\n".join(uv_args(args.target or host_target(), config)))
        return 0
    lock = tomllib.loads(args.lock.read_text(encoding="utf-8"))
    problems = [problem for target in ([args.target] if args.target else sorted(TARGETS))
                for problem in missing_wheels(lock, target, config)]
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
