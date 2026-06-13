"""DangerousCommandGuard — 挡路规则（policies-mvp.md §1）。

工具即将执行时，如果是危险的 shell 命令，拦下来。判断**到参数级**，不是
关键词匹配——否则误报会多到用户习惯性秒批，护栏就废了。

诚实边界（threat-model）：这是**防手滑的护栏，不是安全边界**。base64/
变量拼接/写脚本再执行/绕过 bash 用别的工具——它都拦不住。真要防对手靠沙箱。
"""
from __future__ import annotations

import re

from openprogram.agent.event_bus import Event

from ..actions import Action, Gate
from ..policy import Policy

# 受保护分支（推 --force 到这些才拦；自己的 feature 分支常见，不拦）
_PROTECTED_BRANCHES = ("main", "master", "release", "prod", "production")


def _is_dangerous(cmd: str) -> str | None:
    """返回拦截理由，或 None（安全/不确定都放行——宁可漏报不可误报刷屏）。"""
    c = cmd.strip()
    low = c.lower()

    # rm -rf：只拦"扫到根/家/系统目录"的，放行 /tmp、node_modules 等日常目标
    has_rf = (re.search(r"\brm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r|-r\s+-f|-f\s+-r)\b", low)
              or re.search(r"\brm\s+--recursive\s+--force\b", low)
              or re.search(r"\brm\s+--force\s+--recursive\b", low))
    if has_rf:
        # 危险目标：整盘 /、家目录、$HOME、通配 /*、向上 ..、明确系统目录。
        # 注意 /tmp、/var/folders 等是日常安全目标，不在此列。
        if re.search(r"\brm\b[^|;&]*\s(/|~|\$home|/\*|\*)\s*$", low) \
           or re.search(r"\brm\b[^|;&]*\s(/etc|/usr|/bin|/lib|/sys|/boot|/var/(?!folders)\S*|/system|/library)\b", low) \
           or re.search(r"\brm\b[^|;&]*\s~/?\s*$", low) \
           or re.search(r"\brm\b[^|;&]*\s\.\.(/|\s|$)", low):
            return f"rm -rf 指向系统/家/根目录，破坏性极高：{c}"
        return None  # 日常 rm -rf <子目录>（/tmp/x、node_modules…）不拦，避免刷屏

    # git push --force 到受保护分支
    if re.search(r"git\s+push\b.*(--force\b|-f\b)", low):
        if any(re.search(rf"\b{b}\b", low) for b in _PROTECTED_BRANCHES):
            return f"强推到受保护分支，会覆盖他人提交：{c}"
        return None  # 推自己 feature 分支的 --force 常见，不拦

    # kubectl delete namespace / terraform destroy — 整片删除
    if re.search(r"kubectl\s+delete\s+(namespace|ns)\b", low):
        return f"删除整个 k8s namespace：{c}"
    if re.search(r"terraform\s+destroy\b", low):
        return f"terraform destroy 会拆掉基础设施：{c}"
    if re.search(r"\bdrop\s+database\b", low):
        return f"DROP DATABASE：{c}"

    return None


class DangerousCommandGuard(Policy):
    on = {"tool.before"}
    lane = "gate"
    cooldown_s = 0.0   # 护栏不冷却——每次危险命令都要拦
    name = "DangerousCommandGuard"

    def evaluate(self, event: Event, state) -> Action | None:
        if event.payload.get("tool") != "bash":
            return None
        args = event.payload.get("args") or {}
        cmd = args.get("command") if isinstance(args, dict) else None
        if not isinstance(cmd, str) or not cmd:
            return None
        reason = _is_dangerous(cmd)
        if reason:
            return Gate.ask(reason)   # ask：拦下并请用户确认（这版按拦截处理）
        return None
