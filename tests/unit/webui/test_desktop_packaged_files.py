"""Guard: every local module the Electron main/preload entrypoints require must
be listed in apps/desktop/package.json build.files, or app.asar ships incomplete and
the app dies at launch with "Cannot find module".
"""

from __future__ import annotations

import json
import re
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[3] / "apps" / "desktop"
ENTRYPOINTS = ("main.js", "preload.js")

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
    return {str(path.relative_to(DESKTOP)) for path in seen}


def _whitelist() -> list[str]:
    package = json.loads((DESKTOP / "package.json").read_text(encoding="utf-8"))
    return package["build"]["files"]


def test_every_required_module_is_packaged():
    missing = sorted(_required_files() - set(_whitelist()))
    assert not missing, (
        "apps/desktop/package.json build.files is missing modules that main.js/preload.js "
        f"require (app.asar would ship broken): {missing}"
    )


def test_whitelist_has_no_dead_entries():
    dead = sorted(name for name in _whitelist() if not (DESKTOP / name).exists())
    assert not dead, f"apps/desktop/package.json build.files lists missing files: {dead}"


def test_refresh_script_reads_build_files_instead_of_a_hand_list():
    """refresh-local-app.sh must copy build.files, not a duplicated name list."""
    refresh = (DESKTOP.parents[1] / "scripts" / "refresh-local-app.sh").read_text(
        encoding="utf-8"
    )
    assert "apps/desktop/package.json" in refresh
    assert "build.files" in refresh
    assert "main.js menu-geometry.js" not in refresh
    assert "window-lifecycle.js" in _whitelist()
