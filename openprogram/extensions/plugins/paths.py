"""统一路径：~/.openprogram/ 下的插件相关目录。

插件状态跟随当前 profile 的状态目录（默认 ``~/.openprogram/``）。这里集中管理，避免散落。
"""
from __future__ import annotations

from pathlib import Path


def root() -> Path:
    """The active profile's state directory (``~/.openprogram`` by default)."""
    from openprogram.state.paths import get_state_dir
    p = Path(get_state_dir())
    p.mkdir(parents=True, exist_ok=True)
    return p


def plugins_dir() -> Path:
    p = root() / "plugins"
    p.mkdir(parents=True, exist_ok=True)
    return p


def npm_root() -> Path:
    """npm install --prefix 的根；node_modules/<name> 是实际包。"""
    p = plugins_dir()
    p.mkdir(parents=True, exist_ok=True)
    return p


def npm_modules_dir() -> Path:
    return npm_root() / "node_modules"


def trust_file() -> Path:
    return root() / "plugin-trust.json"


def options_dir() -> Path:
    p = root() / "plugin-options"
    p.mkdir(parents=True, exist_ok=True)
    return p


def marketplaces_file() -> Path:
    return root() / "marketplaces.json"


def project_pins(cwd: Path | None = None) -> Path:
    base = Path(cwd) if cwd else Path.cwd()
    return base / ".openprogram" / "plugins.json"


def sanitize_name(name: str) -> str:
    """允许 [a-zA-Z0-9_-]，其余拒绝。防 path traversal。"""
    import re
    if not name or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError(f"invalid plugin name: {name!r}")
    return name
