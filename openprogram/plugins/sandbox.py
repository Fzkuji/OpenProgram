"""沙箱策略 (stub)。

设计稿映射：

| trust       | 加载方式            | 当前实现 |
|-------------|---------------------|----------|
| verified    | in-process import   | 已实现 (loader.load_in_process) |
| community   | subprocess + RPC    | TODO     |
| untrusted   | 拒绝加载，UI 升级   | loader 拒绝 |

Hook 执行应始终走 subprocess (与 claude-code 一致)，本期同样 TODO。
"""
from __future__ import annotations

from typing import Any


def load_in_process(plugin: Any) -> Any:
    """In-process 加载。真正逻辑在 ``loader._import_entrypoints``。"""
    from .loader import _import_entrypoints  # local import 避免循环
    return _import_entrypoints(plugin)


def load_subprocess(plugin: Any) -> Any:
    """Subprocess + JSON-RPC 沙箱。

    TODO: 字段设计 ::
        - stdin/stdout 管道帧格式 (LSP 风格 Content-Length)
        - 资源限额 (resource.setrlimit: RLIMIT_AS / RLIMIT_CPU / RLIMIT_NOFILE)
        - FS 白名单 (chdir 到 plugin.root + 只读)
        - hook 调用 round-trip 时间预算
        - 进程崩溃 → 标 plugin 失败，不杀主进程
    """
    raise NotImplementedError("subprocess sandbox not implemented yet")
