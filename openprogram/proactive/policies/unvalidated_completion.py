"""UnvalidatedCompletionNudge — 旁观规则（policies-mvp.md §3）。

模型说"完成了"，但这一轮改了文件却没跑过任何验证（测试 / 浏览器验证），
多半是没真验。默认动作是 **Inject 悄悄推模型自己去验**（不打扰用户）——
与 repo 现有"改完自验"规范对齐，比把球踢给用户点确认更省事。模型被推了
还是不验，才升级成用户可见 Notify（升级逻辑留待后续）。
"""
from __future__ import annotations

from openprogram.agent.event_bus import Event

from ..actions import Action, Inject
from ..policy import Policy

# 模型"声称完成"的弱信号（payload 暂无 claimed_completion 字段，先按 origin
# 兜底：response_completed 且本轮有改动无验证即触发；后续 L2 推断更准）
_DONE_HINTS = ("完成", "done", "完毕", "搞定", "finished", "已实现", "已修复")


class UnvalidatedCompletionNudge(Policy):
    on = {"model.response_completed"}
    lane = "observer"
    cooldown_s = 900.0   # 同一 session 15 分钟内不重复推
    name = "UnvalidatedCompletionNudge"

    def evaluate(self, event: Event, state) -> Action | None:
        # 本轮有文件改动、但没有任何验证动作 → 推一把
        if state.turn_has_file_change and not state.turn_has_verification:
            return Inject(
                "你这一轮改了文件但没有运行任何验证（测试 / 跑一下 / 浏览器看效果）。"
                "下结论前请先验证。"
            )
        return None
