#!/usr/bin/env python3
"""Decide what a release builds, then what it can honestly publish.

    release-matrix.py select
        Read the switches and which credentials exist from the environment and
        print the GitHub Actions outputs: the runtime, seed and desktop
        matrices, and what this release leaves out and why.

    release-matrix.py assemble ARTIFACT_ROOT --version vX.Y.Z --plan JSON \\
        --notes FILE --notes-output FILE
        Turn the downloaded artifacts into what the release carries: drop the
        internal ones and anything that did not pass, refuse when the wheel,
        the Office archive or every macOS and Linux command-line runtime is
        missing, and write the release notes for what exists. Each file is
        uploaded once, under its
        versioned name; the docs site's latest.html finds the newest one.
        --incomplete-output FILE lists what the plan built for and the release
        does not carry, one per line; the workflow fails on it after publishing.

No certificates never means no release: each desktop leg signs when its
credentials exist and otherwise builds the unsigned form. One platform failing
never blocks the others.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, fields
import json
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Mapping

# A repository variable set to "false" switches these off.
ON_BY_DEFAULT = {
    "windows_runtime": "OPENPROGRAM_RELEASE_WINDOWS_RUNTIME",
    "desktop_mac": "OPENPROGRAM_RELEASE_DESKTOP_MAC",
    "desktop_windows": "OPENPROGRAM_RELEASE_DESKTOP_WINDOWS",
    "desktop_linux": "OPENPROGRAM_RELEASE_DESKTOP_LINUX",
}
# A repository variable set to "true" switches these on. Windows on Arm waits
# for Windows x64 to run. The small macOS App waits for the owner's check that
# privacy grants survive a switch, and command-line seeds wait for a released
# fetcher, because an install on the version store cannot update without one.
OFF_BY_DEFAULT = {
    "windows_arm64": "OPENPROGRAM_RELEASE_WINDOWS_ARM64",
    "mac_thin": "OPENPROGRAM_RELEASE_MAC_THIN",
    "cli_seeds": "OPENPROGRAM_RELEASE_CLI_SEEDS",
}
# Computed by the workflow from secrets; only "true" or "false" reaches here.
AVAILABLE = {
    "mac_signing": "OPENPROGRAM_MAC_SIGNING_AVAILABLE",
    "windows_signing": "OPENPROGRAM_WINDOWS_SIGNING_AVAILABLE",
    "update_key": "OPENPROGRAM_UPDATE_KEY_TRUSTED",
}

_NO_UPDATE_KEY = (
    "this release has no trusted update signing key, and an App installed "
    "without one could never update"
)
_WINDOWS_RUNTIME = ("windows-2025", "windows", "x86_64")
_WINDOWS_ARM_RUNTIME = ("windows-11-vs2026-arm", "windows", "arm64")
_LINUX_BASELINE = {"x86_64": "ubuntu-22.04", "arm64": "ubuntu-22.04-arm"}
_PLATFORM_KEY = {"mac": "macos", "win": "windows", "linux": "linux"}
# The file a user installs with, per desktop leg. A leg whose artifact has
# none of these did not produce an App, whatever else it uploaded.
_INSTALLER = {"mac": "*.dmg", "win": "*.exe", "linux": "*.deb"}
_RUNTIME_ARCHIVE = {"macos": "*.tar.gz", "linux": "*.tar.gz", "windows": "*.zip"}
_LABELS = {
    ("macos", "arm64"): "macOS Apple Silicon",
    ("macos", "x86_64"): "macOS Intel",
    ("linux", "x86_64"): "Linux x86_64",
    ("linux", "arm64"): "Linux arm64",
    ("windows", "x86_64"): "Windows x64",
    ("windows", "arm64"): "Windows on Arm",
}


@dataclass(frozen=True)
class Switches:
    windows_runtime: bool = True
    windows_arm64: bool = False
    desktop_mac: bool = True
    desktop_windows: bool = True
    desktop_linux: bool = True
    mac_thin: bool = False
    cli_seeds: bool = False
    mac_signing: bool = False
    windows_signing: bool = False
    update_key: bool = False


def switches_from_environment(environment: Mapping[str, str]) -> Switches:
    values: dict[str, bool] = {}
    for name in (field.name for field in fields(Switches)):
        if name in ON_BY_DEFAULT:
            values[name] = environment.get(ON_BY_DEFAULT[name], "").strip().lower() != "false"
        elif name in OFF_BY_DEFAULT:
            values[name] = environment.get(OFF_BY_DEFAULT[name], "").strip().lower() == "true"
        else:
            values[name] = environment.get(AVAILABLE[name], "").strip().lower() == "true"
    return Switches(**values)


def _builder_arch(arch: str) -> str:
    return "x64" if arch == "x86_64" else arch


def _desktop_row(runner: str, platform: str, arch: str, signing: str, layout: str) -> dict[str, str]:
    return {
        "runner": runner, "platform": platform, "runtime_platform": _PLATFORM_KEY[platform],
        "arch": arch, "builder_arch": _builder_arch(arch), "signing": signing, "layout": layout,
    }


def release_matrices(switches: Switches = Switches()) -> dict[str, list]:
    """What one release builds. ``omitted`` is written for the release notes."""
    runtime_targets = [
        ("macos-26", "macos", "arm64"),
        ("macos-15-intel", "macos", "x86_64"),
        ("ubuntu-24.04", "linux", "x86_64"),
        ("ubuntu-24.04-arm", "linux", "arm64"),
    ]
    omitted: list[str] = []
    warnings: list[str] = []

    if switches.windows_runtime:
        runtime_targets.append(_WINDOWS_RUNTIME)
        if switches.windows_arm64:
            runtime_targets.append(_WINDOWS_ARM_RUNTIME)
    else:
        omitted.append("Windows command-line runtime: switched off for this release.")
    if not switches.windows_arm64:
        omitted.append("Windows on Arm: not built yet; Windows x64 comes first.")
    runtime = [{"runner": runner, "platform": platform, "arch": arch}
               for runner, platform, arch in runtime_targets]

    desktop: list[dict[str, str]] = []
    if switches.desktop_mac:
        signing = "signed" if switches.mac_signing else "unsigned"
        layout = "full"
        if switches.mac_thin:
            if switches.mac_signing and switches.update_key:
                layout = "thin"
            else:
                warnings.append(
                    "OPENPROGRAM_RELEASE_MAC_THIN is on, but the small macOS App needs the Apple "
                    "signing secrets and a trusted update key; building the full App instead."
                )
        desktop += [
            _desktop_row("macos-15", "mac", "arm64", signing, layout),
            _desktop_row("macos-15-intel", "mac", "x86_64", signing, layout),
        ]
    else:
        omitted.append("macOS App: switched off for this release.")

    if not switches.desktop_windows:
        omitted.append("Windows App: switched off for this release.")
    elif not switches.update_key:
        omitted.append(f"Windows App: {_NO_UPDATE_KEY}.")
    else:
        signing = "signed" if switches.windows_signing else "unsigned"
        desktop.append(_desktop_row(_WINDOWS_RUNTIME[0], "win", "x86_64", signing, "thin"))
        if switches.windows_arm64:
            desktop.append(_desktop_row(_WINDOWS_ARM_RUNTIME[0], "win", "arm64", signing, "thin"))

    if not switches.desktop_linux:
        omitted.append("Linux App: switched off for this release.")
    elif not switches.update_key:
        omitted.append(f"Linux App: {_NO_UPDATE_KEY}.")
    else:
        desktop += [_desktop_row(runner, "linux", arch, "none", "thin")
                    for arch, runner in _LINUX_BASELINE.items()]

    seeds: dict[tuple[str, str], dict[str, str]] = {}
    for row in desktop:
        if row["layout"] == "thin":
            seeds[(row["runtime_platform"], row["arch"])] = {
                "runner": row["runner"], "platform": row["runtime_platform"], "arch": row["arch"],
                "signing": "signed" if row["platform"] == "mac" else "none", "cli_asset": "false",
            }
    if switches.cli_seeds and not switches.update_key:
        warnings.append(
            "OPENPROGRAM_RELEASE_CLI_SEEDS is on, but there is no trusted update key; "
            "command-line installs stay on the complete runtime."
        )
    elif switches.cli_seeds:
        for row in runtime:
            key = (row["platform"], row["arch"])
            runner = _LINUX_BASELINE[row["arch"]] if row["platform"] == "linux" else row["runner"]
            seeds.setdefault(key, {
                "runner": runner, "platform": row["platform"], "arch": row["arch"],
                "signing": "none", "cli_asset": "false",
            })["cli_asset"] = "true"
    return {
        "runtime": runtime, "seed": list(seeds.values()), "desktop": desktop,
        "omitted": omitted, "warnings": warnings,
        # With a trusted key every runtime leg also builds its update files.
        "update_files": switches.update_key,
    }


def _select() -> int:
    plan = release_matrices(switches_from_environment(os.environ))
    for name in ("runtime", "seed", "desktop"):
        print(f"{name}={json.dumps({'include': plan[name]}, separators=(',', ':'))}")
        print(f"{name}_count={len(plan[name])}")
    print(f"plan={json.dumps(plan, separators=(',', ':'))}")
    for warning in plan["warnings"]:
        print(f"::warning::{warning}", file=sys.stderr)
    return 0


# --- what the release carries -------------------------------------------------


def _manifest_platforms(root: Path) -> set[str]:
    manifest = root / "official-update" / "update-manifest.json"
    if not manifest.is_file():
        return set()
    return set(json.loads(manifest.read_text(encoding="utf-8")).get("platforms", {}))


def _label(platform: str, arch: str) -> str:
    return _LABELS.get((platform, arch), f"{platform} {arch}")


def _remove(path: Path) -> None:
    shutil.rmtree(path) if path.is_dir() else path.unlink()


def keep_publishable(root: Path, plan: Mapping[str, list]) -> list[str]:
    """Reduce ROOT to what may ship and return what is missing, in plain words."""
    missing: list[str] = []
    verified = {path.name.removeprefix("cli-verified-")
                for path in root.glob("cli-verified-*") if path.is_dir()}
    update_platforms = _manifest_platforms(root)
    # Platforms whose missing update files are already said by another line.
    told: set[str] = set()
    # Internal artifacts: the App's seed, the install checks' receipts and the
    # logs a failed leg keeps for diagnosis.
    for path in sorted(root.iterdir()):
        if path.name.startswith(("seed-", "cli-verified-", "diagnostics-")):
            _remove(path)
    for row in plan.get("runtime", []):
        key = f"{row['platform']}-{row['arch']}"
        label = _label(row["platform"], row["arch"])
        directory = root / f"product-runtime-{key}"
        if not directory.is_dir():
            told.add(key)
            missing.append(f"{label} command-line runtime: its build failed.")
        elif not any(directory.glob(_RUNTIME_ARCHIVE[row["platform"]])):
            _remove(directory)
            missing.append(f"{label} command-line runtime: its build left no archive.")
        elif key not in verified:
            _remove(directory)
            missing.append(f"{label} command-line runtime: it did not pass the install check.")
    for row in plan.get("desktop", []):
        key = f"{row['runtime_platform']}-{row['arch']}"
        label = _label(row["runtime_platform"], row["arch"])
        directory = root / f"desktop-{row['layout']}-{row['platform']}-{row['arch']}"
        if not directory.is_dir():
            missing.append(f"{label} App: its build failed.")
        elif not any(directory.glob(_INSTALLER[row["platform"]])):
            _remove(directory)
            missing.append(f"{label} App: its build left no installer.")
        elif row["layout"] == "thin" and key not in update_platforms:
            _remove(directory)
            told.add(key)
            missing.append(f"{label} App: its update files could not be built, so it could never update.")
        else:
            if row["platform"] == "linux" and not any(directory.glob("*.AppImage")):
                missing.append(f"{label} AppImage: its build failed; the deb package is available.")
    if plan.get("update_files"):
        # A full-layout App and the command-line install do not read these yet,
        # so nothing is removed; the release still says they are not there.
        for row in plan.get("runtime", []):
            key = f"{row['platform']}-{row['arch']}"
            if key not in update_platforms and key not in told:
                missing.append(f"{_label(row['platform'], row['arch'])} update files: could not be built.")
    for directory in sorted(root.glob("cli-seed-*")):
        if directory.name.removeprefix("cli-seed-") not in update_platforms:
            _remove(directory)
    missing.extend(plan.get("omitted", []))
    return missing


def refusal(root: Path) -> str | None:
    """Why this release must not publish at all, or None."""
    if not any((root / "developer-python-distributions").glob("*.whl")):
        return "the Python wheel is missing"
    # The App installs Office from its own version's release, so every release
    # carries the pinned archive exactly once (the office job checks its bytes).
    if len(list((root / "office-assets").glob("OfficeAssets-*.zip"))) != 1:
        return "the Office archive is missing"
    runtimes = [*root.glob("product-runtime-macos-*"), *root.glob("product-runtime-linux-*")]
    if not any(path.is_dir() for path in runtimes):
        return "no macOS or Linux command-line runtime was built"
    return None


def asset_names(root: Path) -> set[str]:
    return {path.name for path in root.rglob("*") if path.is_file()}


def _published_name(name: str, names: set[str]) -> str | None:
    if name in names:
        return name
    stem, dot, extension = name.rpartition(".")
    unsigned = f"{stem}-unsigned{dot}{extension}"
    return unsigned if dot and unsigned in names else None


def write_notes(text: str, version: str, names: set[str], missing: list[str]) -> str:
    """The release notes, listing only what this release really carries."""
    link = re.compile(
        r"\[[^\]]*\]\(https://github\.com/[^/]+/[^/]+/releases/download/"
        rf"v{re.escape(version)}/([^)\s]+)\)"
    )
    lines: list[str] = []
    for line in text.splitlines():
        found = link.findall(line)
        if not found:
            lines.append(line)
            continue
        if not any(_published_name(name, names) for name in found):
            continue
        for name in found:
            published = _published_name(name, names)
            if published is None:
                gone = re.compile(
                    r"(?: · )?\[[^\]]*\]\(https://[^)\s]*/" + re.escape(name) + r"\)(?= · )?"
                )
                line = gone.sub("", line, count=1)
            elif published != name:
                line = line.replace(f"/{name})", f"/{published})")
        lines.append(re.sub(r"(\|\s*) · ", r"\1", line))
    result = "\n".join(lines).rstrip() + "\n"
    if missing:
        result += "\n## Not in this release\n\n" + "".join(f"- {item}\n" for item in missing)
    unsigned = sorted(name for name in names if "-unsigned." in name)
    if unsigned:
        result += "\n## Unsigned packages\n\n"
        if any(name.endswith((".dmg", ".zip")) for name in unsigned):
            result += (
                "- **macOS:** this App has no Apple signature. Open it once, then choose "
                "**System Settings > Privacy & Security > Open Anyway**.\n"
            )
        if any(name.endswith(".exe") for name in unsigned):
            result += (
                "- **Windows:** this installer has no publisher signature. In the warning choose "
                "**More info > Run anyway**. A PC with Smart App Control on cannot run it; use "
                "the command-line install there.\n"
            )
    return result


def _assemble(options: argparse.Namespace) -> int:
    version = options.version.removeprefix("v")
    plan = json.loads(options.plan)
    missing = keep_publishable(options.artifact_root, plan)
    reason = refusal(options.artifact_root)
    if reason is not None:
        raise SystemExit(f"refusing to publish {options.version}: {reason}")
    # The manifest is written after this step, from what is left here.
    names = asset_names(options.artifact_root) | {"release-manifest.json"}
    notes = write_notes(options.notes.read_text(encoding="utf-8"), version, names, missing)
    options.notes_output.write_text(notes, encoding="utf-8")
    for item in missing:
        print(f"::warning::{item}", file=sys.stderr)
    if options.incomplete_output is not None:
        # What a switch or a missing credential left out was decided before the
        # build; everything else in the list was planned and is not here.
        decided = set(plan.get("omitted", []))
        incomplete = [item for item in missing if item not in decided]
        options.incomplete_output.write_text("".join(f"{item}\n" for item in incomplete), encoding="utf-8")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("select").set_defaults(run=lambda _options: _select())
    assemble = commands.add_parser("assemble")
    assemble.add_argument("artifact_root", type=Path)
    assemble.add_argument("--version", required=True)
    assemble.add_argument("--plan", required=True, help="the plan output of `select`")
    assemble.add_argument("--notes", type=Path, required=True)
    assemble.add_argument("--notes-output", type=Path, required=True)
    assemble.add_argument("--incomplete-output", type=Path)
    assemble.set_defaults(run=_assemble)
    options = parser.parse_args()
    return options.run(options)


if __name__ == "__main__":
    raise SystemExit(main())
