"""OpenProgram plugins subsystem.

四来源插件 (pip / npm / local / project-pinned)，三种 manifest
(plugin.json / pyproject.toml / package.json) 统一解析。设计稿见
``docs/design/integrations/skills-and-plugins.md``。

宿主由 ``openprogram.webui.routes.plugins`` 暴露 HTTP API；本包只
负责解析、加载、注册表与持久化，不假设宿主结构。
"""
from __future__ import annotations

from .manifest import PluginManifest, parse_manifest_dir
from .loader import (
    Plugin,
    list_plugins,
    get_plugin,
    load_plugin,
    unload_plugin,
    reload_plugin,
    rescan,
)

__all__ = [
    "PluginManifest",
    "parse_manifest_dir",
    "Plugin",
    "list_plugins",
    "get_plugin",
    "load_plugin",
    "unload_plugin",
    "reload_plugin",
    "rescan",
]
