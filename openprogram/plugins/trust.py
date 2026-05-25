"""Trust 等级持久化。

存于 ``~/.openprogram/plugin-trust.json``：``{name: "verified"|"community"|"untrusted"}``。
未列入的插件默认 ``untrusted``，首次启用前必须升级。
"""
from __future__ import annotations

import json
from typing import Literal

from . import paths

TrustLevel = Literal["verified", "community", "untrusted"]
VALID: tuple[str, ...] = ("verified", "community", "untrusted")


def _load() -> dict[str, str]:
    f = paths.trust_file()
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except Exception:
        pass
    return {}


def _save(d: dict[str, str]) -> None:
    paths.trust_file().write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")


def get_trust(name: str) -> str:
    return _load().get(name, "untrusted")


def set_trust(name: str, level: str) -> None:
    if level not in VALID:
        raise ValueError(f"invalid trust level: {level}")
    paths.sanitize_name(name)
    d = _load()
    d[name] = level
    _save(d)


def all_trust() -> dict[str, str]:
    return _load()
