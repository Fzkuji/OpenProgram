"""Build sealed versions in a temporary root, without Python or a real build."""
from __future__ import annotations

from pathlib import Path
import shutil
import sys

from openprogram.operations.self_update.versions import layout as paths
from openprogram.operations.self_update.versions import pointer, store
from openprogram.operations.self_update.versions.layout import Layout

REPOSITORY = Path(__file__).resolve().parents[2]
BASE = "py3.12-0123456789abcdef"


def make_layout(root: Path, *, tools: bool = False) -> Layout:
    layout = Layout(root)
    root.mkdir(parents=True, exist_ok=True)
    python = paths.base_python(layout.base(BASE))
    python.parent.mkdir(parents=True, exist_ok=True)
    if not python.exists():
        python.symlink_to(sys.executable)
    if tools:
        (root / "bootstrap").mkdir(exist_ok=True)
        for name in ("versions.py", "restore_version.py"):
            shutil.copy2(REPOSITORY / "scripts/release/bootstrap" / name, root / "bootstrap" / name)
    return layout


def make_version(
    layout: Layout, version_id: str, *, base: str = BASE, commit: str | None = None,
    extra: dict | None = None, frontend: str = "<html>frontend</html>\n",
) -> Path:
    """Seal a small version whose ``python`` prints what it was started with."""
    staging = layout.versions / f".staging-{version_id}"
    site = paths.site_packages(staging, base)
    package = site / "openprogram"
    (package / "__pycache__").mkdir(parents=True)
    (package / "__init__.py").write_text(f'VERSION = "{version_id}"\n')
    (package / "module.py").write_text("value = 1\n")
    (package / "__pycache__" / "module.cpython-312.pyc").write_bytes(b"bytecode")
    bundle = paths.frontend_bundle(site)
    bundle.mkdir(parents=True)
    (bundle / "index.html").write_text(frontend)
    (staging / "desktop").mkdir()
    (staging / "desktop" / "main.js").write_text("exports.start = () => {};\n")
    binary = staging / "venv" / "bin"
    binary.mkdir(parents=True)
    python = binary / "python"
    python.write_text(f'#!/bin/sh\necho "ran {version_id}"\nprintf "%s\\n" "$@"\n')
    python.chmod(0o755)
    (binary / "python3").symlink_to("python")
    manifest = {
        "id": version_id, "commit": commit or ("a" * 40), "base": base, "shell_compat": 1,
        "built_by": "test", "built_at": "2026-11-08T00:00:00Z", "components": ["python"],
        "parent": None, "summary": f"summary of {version_id}", **(extra or {}),
    }
    return store.seal(layout, staging, manifest)


def point_at(layout: Layout, version_id: str, **fields) -> pointer.Pointer:
    layout.pointer.unlink(missing_ok=True)
    return pointer.create(layout, pointer.Pointer(generation=5, version=version_id, base=BASE, writer="test", **fields))
