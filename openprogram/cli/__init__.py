"""Compatibility entry for the Python CLI application in ``apps/cli``."""

from pathlib import Path


def _application_dir() -> Path:
    package_root = Path(__file__).resolve().parents[2]
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
