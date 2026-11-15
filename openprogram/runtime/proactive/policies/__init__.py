"""MVP 三条 policy（policies-mvp.md）。

install_mvp_policies() 注册三条；worker 启动时 install_proactive 之后调一次。
"""
from __future__ import annotations

from ..policy import register_policy
from .dangerous_command import DangerousCommandGuard
from .unvalidated_completion import UnvalidatedCompletionNudge
from .test_gap import TestGapWatcher

__all__ = [
    "DangerousCommandGuard",
    "UnvalidatedCompletionNudge",
    "TestGapWatcher",
    "install_mvp_policies",
]


def install_mvp_policies() -> None:
    register_policy(DangerousCommandGuard())
    register_policy(UnvalidatedCompletionNudge())
    register_policy(TestGapWatcher())
