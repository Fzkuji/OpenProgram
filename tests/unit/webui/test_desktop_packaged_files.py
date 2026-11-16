"""Guard: every local module the Electron main/preload entrypoints require must
be packed by apps/desktop/package.json build.files, or app.asar ships incomplete and
the app dies at launch with "Cannot find module".

An entry in build.files is one file ("main.js") or a whole folder ("main/**",
dotfiles included), the same expansion apps/desktop/tests/packaged-files.js,
refresh-local-app.sh and package-incremental-app.sh make.
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


def test_refresh_script_reads_build_files_instead_of_a_hand_list():
    """The stable refresh packs build.files, not a duplicated name list."""
    root = DESKTOP.parents[1]
    packer = (root / "scripts" / "release" / "pack-desktop-archive.sh").read_text(
        encoding="utf-8"
    )
    assert "apps/desktop/package.json" in packer
    assert "build.files" in packer
    assert "main.js menu-geometry.js" not in packer
    refresh = (root / "scripts/local-instances/refresh-local-app.sh").read_text(encoding="utf-8")
    assert "scripts/release/pack-desktop-archive.sh" in refresh
    assert "main/windows/window-lifecycle.js" in _packaged_files()


def test_packaging_scripts_expand_folder_entries():
    """Both asar repackers expand "folder/**", replace main/ whole and drop
    top-level modules from before the move into main/."""
    root = DESKTOP.parents[1]
    for script in ("scripts/release/pack-desktop-archive.sh", "apps/desktop/scripts/package-incremental-app.sh"):
        text = (root / script).read_text(encoding="utf-8")
        assert '"/**"' in text, script
        assert 'rm -rf "$desktop_stage/main"' in text, script
        assert 'for staged_module in "$desktop_stage"/*.js; do' in text, script
        assert 'rm -rf "$desktop_stage/tab-transfer" "$desktop_stage/shell-integration"' in text, script


def test_refresh_script_rejects_stale_checkout_for_default_app():
    refresh = (DESKTOP.parents[1] / "scripts" / "local-instances" / "refresh-local-app.sh").read_text(
        encoding="utf-8"
    )

    assert 'default_app_path="$(canonicalize_app_path "/Applications/OpenProgram.app")"' in refresh
    assert 'app_path="$(canonicalize_app_path "${OPENPROGRAM_APP_PATH:-$default_app_path}" "$default_app_path")"' in refresh
    assert 'test "$app_path" = "$default_app_path"' in refresh
    assert "merge-base --is-ancestor" in refresh
    assert "for protected_ref in refs/heads/main refs/remotes/origin/main; do" in refresh
    assert 'merge-base --is-ancestor "$protected_ref" HEAD' in refresh
    assert "refusing to refresh the default App" in refresh
    assert "behind $protected_ref" in refresh


def test_refresh_script_replaces_the_archive_by_rename():
    """An App launched mid-refresh must never read a half-copied app.asar:
    the archive goes into the staged App, which replaces the installed one by rename."""
    refresh = (DESKTOP.parents[1] / "scripts" / "local-instances" / "refresh-local-app.sh").read_text(
        encoding="utf-8"
    )
    assert 'cp "$desktop_asar" "$installed_asar' not in refresh
    copy = refresh.index('cp "$desktop_asar" "$staged_app/Contents/Resources/app.asar"\n')
    assert copy < refresh.index('mv "$app_path" "$previous_app"\n') < refresh.index('mv "$staged_app" "$app_path"\n')

