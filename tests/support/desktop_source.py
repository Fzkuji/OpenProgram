"""Read every composed desktop source for source-contract checks."""
from pathlib import Path


def read_desktop_source(root: Path) -> str:
    desktop = root / "apps" / "desktop"
    paths = [desktop / "main.js", *sorted((desktop / "main").glob("*.js")), *sorted((desktop / "main").glob("*/*.js"))]
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def read_desktop_bridge_source(root: Path) -> str:
    folder = root / "apps/web/lib/desktop"
    paths = [folder / "desktop-bridge.ts", *sorted((folder / "bridge").glob("bridge-*.ts"))]
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def read_server_source(root: Path) -> str:
    folder = root / "openprogram/interfaces/server"
    paths = [folder / "server.py", *sorted((folder / "server_runtime").glob("*.py"))]
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)
