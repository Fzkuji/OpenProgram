"""贡献注册表。

已加载 plugin 在此暴露 commands / skills / mcpServers / providers / hooks / agents
/ sidebar / web 入口。宿主侧通过 getter 读取并注入到自己的子系统。

设计原则：本模块不主动操作 host (chat / mcp / providers)，只持有数据。
真正接合由调用方完成 (例如 routes/plugins.py 在 toggle 时调用相应注入 api)。
"""
from __future__ import annotations

from threading import RLock
from typing import Any


_lock = RLock()
# {name: {"sidebar": [...], "web": "/abs/path", "commands": {...}, ...}}
_contrib: dict[str, dict[str, Any]] = {}


def set_plugin_contrib(name: str, contrib: dict[str, Any]) -> None:
    with _lock:
        _contrib[name] = dict(contrib)


def clear_plugin_contrib(name: str) -> None:
    with _lock:
        _contrib.pop(name, None)


def get_plugin_contrib(name: str) -> dict[str, Any]:
    with _lock:
        return dict(_contrib.get(name, {}))


def all_contrib() -> dict[str, dict[str, Any]]:
    with _lock:
        return {k: dict(v) for k, v in _contrib.items()}


def get_sidebar_items() -> list[dict[str, Any]]:
    """所有已启用 plugin 注册的侧栏项。前端从 /api/plugins/sidebar 拉。"""
    out: list[dict[str, Any]] = []
    for name, c in all_contrib().items():
        for item in c.get("sidebar", []) or []:
            if isinstance(item, dict):
                row = dict(item)
                row["plugin"] = name
                out.append(row)
    return out


def get_web_routes() -> dict[str, str]:
    """{plugin_name: 静态目录绝对路径}，用于挂载 /api/plugins/<name>/web/*。"""
    out: dict[str, str] = {}
    for name, c in all_contrib().items():
        web = c.get("web")
        if isinstance(web, str) and web:
            out[name] = web
    return out


def try_register_skills(plugin_name: str, skills_dir: str) -> None:
    """对接 skills 子系统 (另一个 agent 实现)。

    skills loader 会暴露 ``register_plugin_skills(plugin_name, dir)``；
    此处仅在它存在时调用，缺席不抛。
    """
    try:
        from openprogram.skills.loader import register_plugin_skills  # type: ignore
    except Exception:
        return
    try:
        register_plugin_skills(plugin_name, skills_dir)  # type: ignore[misc]
    except Exception:
        pass


def try_unregister_skills(plugin_name: str) -> None:
    try:
        from openprogram.skills.loader import unregister_plugin_skills  # type: ignore
    except Exception:
        return
    try:
        unregister_plugin_skills(plugin_name)  # type: ignore[misc]
    except Exception:
        pass
