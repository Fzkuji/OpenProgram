# `openprogram/plugins/`

> OpenProgram plugins subsystem.

## Overview

四来源插件 (pip / npm / local / project-pinned)，三种 manifest
(plugin.json / pyproject.toml / package.json) 统一解析。设计稿见
``docs/design/skills-and-plugins.md``。

宿主由 ``openprogram.webui.routes.plugins`` 暴露 HTTP API；本包只
负责解析、加载、注册表与持久化，不假设宿主结构。

## Files in this directory

- **`autoupdate.py`** — Plugin auto-update
- **`hooks.py`** — Plugin lifecycle hooks
- **`installer.py`** — 四来源安装器。
- **`loader.py`** — 四来源 plugin 扫描与加载。
- **`manifest.py`** — 统一 manifest 解析。
- **`marketplace.py`** — Marketplace 管理。
- **`paths.py`** — 统一路径：~/.openprogram/ 下的插件相关目录。
- **`registry.py`** — 贡献注册表。
- **`sandbox.py`** — 沙箱策略 (stub)。
- **`trust.py`** — Trust 等级持久化。

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
