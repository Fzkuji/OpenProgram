"""Compatibility entry for the Python CLI application in ``apps/cli``."""

from pathlib import Path
import sys


def _validate_loaded_canonical(package_root: Path) -> None:
    existing = sys.modules.get("openprogram_cli")
    if existing is None:
        return
    loaded_file = getattr(existing, "__file__", None)
    if not loaded_file:
        raise ImportError("refusing an already-loaded foreign openprogram_cli package")
    loaded = Path(loaded_file).resolve()
    allowed = {
        (package_root / "apps/cli/python/openprogram_cli/__init__.py").resolve(),
        (package_root / "openprogram_cli/__init__.py").resolve(),
    }
    if loaded not in allowed:
        raise ImportError(
            f"refusing an already-loaded foreign openprogram_cli package: {loaded}"
        )


def _application_dir() -> Path:
    from openprogram.updater.detect import repo_root

    checkout = repo_root()
    package_root = checkout or Path(__file__).resolve().parents[2]
    _validate_loaded_canonical(package_root)
    source = package_root / "apps/cli/python/openprogram_cli/_impl"
    installed = package_root / "openprogram_cli/_impl"
    for candidate in (source, installed):
        if (candidate / "application.py").is_file():
            return candidate
    raise ImportError("OpenProgram CLI application package is missing")


_APP_DIR = _application_dir()
__path__.insert(0, str(_APP_DIR))
_APPLICATION_FILE = _APP_DIR / "application.py"
# Execute the application in this stable module namespace so established
# ``openprogram.cli`` imports and monkeypatch targets share one state with the
# canonical ``openprogram_cli`` entry instead of loading a second CLI instance.
exec(
    compile(_APPLICATION_FILE.read_bytes(), str(_APPLICATION_FILE), "exec"),
    globals(),
    globals(),
)
