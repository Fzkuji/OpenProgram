"""Guard: every local module the Electron main/preload entrypoints require must
be packed by apps/desktop/package.json build.files, or app.asar ships incomplete and
the app dies at launch with "Cannot find module".

An entry in build.files is one file ("main.js") or a whole folder ("main/**",
dotfiles included), the same expansion apps/desktop/tests/packaged-files.js and
package-incremental-app.sh make.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[3] / "apps" / "desktop"
ENTRYPOINTS = ("main.js", "main/preload.js")

# require("./x") / require('./x') / from "./x"
_LOCAL_REF = re.compile(r"""(?:require\(|from\s+)["'](\.[^"']+)["']""")


def _resolve(base: Path, spec: str) -> Path | None:
    target = (base.parent / spec).resolve()
    for candidate in (target, target.with_suffix(".js"), target / "index.js"):
        if candidate.is_file():
            return candidate
    return None


def _required_files() -> set[str]:
    """Transitively expand local requires from the entrypoints."""
    seen: set[Path] = set()
    queue = [DESKTOP / name for name in ENTRYPOINTS]
    while queue:
        current = queue.pop()
        if current in seen or not current.is_file():
            continue
        seen.add(current)
        for spec in _LOCAL_REF.findall(current.read_text(encoding="utf-8")):
            resolved = _resolve(current, spec)
            if resolved is not None:
                queue.append(resolved)
    return {path.relative_to(DESKTOP).as_posix() for path in seen}


def _whitelist() -> list[str]:
    package = json.loads((DESKTOP / "package.json").read_text(encoding="utf-8"))
    return package["build"]["files"]


def _packaged_files() -> set[str]:
    files: set[str] = set()
    for entry in _whitelist():
        if entry.endswith("/**"):
            folder = DESKTOP / entry[:-3]
            files.update(
                path.relative_to(DESKTOP).as_posix()
                for path in folder.rglob("*")
                if path.is_file()
            )
        else:
            files.add(entry)
    return files


def test_every_required_module_is_packaged():
    missing = sorted(_required_files() - _packaged_files())
    assert not missing, (
        "apps/desktop/package.json build.files is missing modules that main.js/preload.js "
        f"require (app.asar would ship broken): {missing}"
    )


def test_whitelist_has_no_dead_entries():
    dead = sorted(
        name
        for name in _whitelist()
        if not (
            (DESKTOP / name[:-3]).is_dir() and any((DESKTOP / name[:-3]).iterdir())
            if name.endswith("/**")
            else (DESKTOP / name).is_file()
        )
    )
    assert not dead, f"apps/desktop/package.json build.files lists missing files: {dead}"


def test_packaged_files_include_preload_and_shell_integration():
    """Files loaded by path, not require(), ship through the main/** folder."""
    packaged = _packaged_files()
    assert "main/preload.js" in packaged
    assert "main/web-views/webtab-preload.js" in packaged
    for name in (".zshenv", ".zprofile", ".zshrc", ".zlogin", "hooks.zsh", "hooks.bash"):
        assert f"main/terminal/shell-integration/{name}" in packaged
    assert not any(name.startswith(("tests/", "scripts/")) for name in packaged)


def test_packaging_scripts_expand_folder_entries():
    """The asar repacker expands "folder/**", replaces main/ whole and drops
    top-level modules from before the move into main/."""
    text = (DESKTOP / "scripts" / "package-incremental-app.sh").read_text(encoding="utf-8")
    assert '"/**"' in text
    assert 'rm -rf "$desktop_stage/main"' in text
    assert 'for staged_module in "$desktop_stage"/*.js; do' in text
    assert 'rm -rf "$desktop_stage/tab-transfer" "$desktop_stage/shell-integration"' in text
