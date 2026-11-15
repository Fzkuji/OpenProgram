"""Real sealed versions of this checkout, in a temporary root, for trial tests."""
from __future__ import annotations

from pathlib import Path
import shutil
import site as site_module
import subprocess
import sys

from openprogram.operations.self_update.versions import layout as paths
from openprogram.operations.self_update.versions import store as version_store
from openprogram.operations.self_update.versions.layout import Layout

from .sealed_versions import BASE as _SEALED_BASE, REPOSITORY

# A trial really runs these versions, so the base names the interpreter running the tests.
BASE = f"py{sys.version_info.major}.{sys.version_info.minor}-{_SEALED_BASE.rsplit('-', 1)[1]}"

_IGNORED = shutil.ignore_patterns("__pycache__", "_frontend", "*.pyc", "node_modules", ".pytest_cache")
_FRONTEND_PAGE = (
    '<!doctype html><html><head><script src="/_next/static/app.js"></script></head>'
    "<body><textarea></textarea></body></html>\n"
)


def compile_launcher(output: Path) -> Path:
    subprocess.run(
        ["cc", "-O1", "-DOPENPROGRAM_LAUNCHER_EXEC", str(REPOSITORY / "scripts/release/runtime-launcher.c"),
         "-o", str(output)],
        check=True, capture_output=True, text=True,
    )
    return output


def make_base(layout: Layout) -> None:
    python = paths.base_python(layout.base(BASE))
    python.parent.mkdir(parents=True, exist_ok=True)
    if not python.exists():
        python.symlink_to(sys.executable)


def _add_running_dependencies(site: Path) -> None:
    """A venv's --system-site-packages reaches the base Python; dependencies may live in the running venv."""
    if sys.prefix == sys.base_prefix:
        return
    site.mkdir(parents=True, exist_ok=True)
    (site / "running-dependencies.pth").write_text(
        "".join(f"{path}\n" for path in site_module.getsitepackages()), encoding="utf-8")


def build_version(layout: Layout, version_id: str, commit: str, *, patch=None, extra: dict | None = None) -> Path:
    """This checkout's ``openprogram`` as a sealed version; ``patch(site)`` changes it."""
    staging = layout.versions / f".staging-{version_id}"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", "--system-site-packages", str(staging / "venv")],
        check=True, capture_output=True, text=True,
    )
    site = paths.site_packages(staging, BASE)
    _add_running_dependencies(site)
    shutil.copytree(REPOSITORY / "openprogram", site / "openprogram", ignore=_IGNORED)
    (site / "openprogram" / "_build_revision.txt").write_text(commit + "\n", encoding="utf-8")
    bundle = paths.frontend_bundle(site)
    bundle.mkdir(parents=True, exist_ok=True)
    for name in ("index.html", "chat.html"):
        (bundle / name).write_text(_FRONTEND_PAGE, encoding="utf-8")
    (bundle / "docs").mkdir()
    (bundle / "docs" / "index.html").write_text("<html>docs</html>\n", encoding="utf-8")
    (staging / "desktop").mkdir()
    (staging / "desktop" / "main.js").write_text("exports.start = () => {};\n", encoding="utf-8")
    if patch is not None:
        patch(site)
    return version_store.seal(layout, staging, {
        "id": version_id, "commit": commit, "base": BASE, "shell_compat": 1, "built_by": "test",
        "built_at": "2026-11-09T00:00:00Z", "components": ["python"], "parent": None, **(extra or {}),
    })


__all__ = ["BASE", "build_version", "compile_launcher", "make_base"]
