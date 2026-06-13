"""TestGapWatcher — 旁观规则（policies-mvp.md §2）。

改了核心代码却没动测试，提醒一下。触发**收窄到收尾时机**（模型说完一轮、
本轮有改动），不是每次文件变更都查——否则改注释/重命名/前端都误报。

设计的完整形态是"先 Prepare 只读 reviewer 做功课、置信度过阈值才 Notify"
（避免误报、成本可见）。这版先做直接 Notify 的最小骨架，Prepare 接入留待
后续单元（需要受限工具集 spawn + 开销 ledger）。先把链路跑通、可用。
"""
from __future__ import annotations

from openprogram.agent.event_bus import Event

from ..actions import Action, Notify
from ..policy import Policy

# 核心模块路径片段——改这些目录才算"核心代码"
_CORE_HINTS = ("/auth/", "/billing", "/payment", "/security",
               "openprogram/agent/", "openprogram/providers/", "openprogram/proactive/")
_TEST_HINTS = ("test_", "_test", "/tests/", "spec.")


def _is_core(path: str) -> bool:
    p = path.lower()
    return any(h in p for h in _CORE_HINTS) and not _is_test(path)


def _is_test(path: str) -> bool:
    p = path.lower()
    return any(h in p for h in _TEST_HINTS)


class TestGapWatcher(Policy):
    on = {"model.response_completed"}   # 收尾时机查，不是每次 file.changed
    lane = "observer"
    cooldown_s = 1800.0   # 同一情况 30 分钟内不重复提
    name = "TestGapWatcher"

    def evaluate(self, event: Event, state) -> Action | None:
        if not state.turn_has_file_change:
            return None
        changed = state.changed_files
        core_changed = [p for p in changed if _is_core(p)]
        any_test_changed = any(_is_test(p) for p in changed)
        if core_changed and not any_test_changed:
            sample = ", ".join(sorted(core_changed)[:3])
            return Notify(
                f"改了核心代码（{sample}）但没动测试，考虑补一下测试覆盖。",
                severity="info",
                ref=sample,
            )
        return None
