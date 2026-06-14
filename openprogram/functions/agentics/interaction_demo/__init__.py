"""interaction_demo — 把所有"问用户、等用户决定"的交互形态串行走一遍。

你点一次 Run，它就依次：单选 → 多选 → 自由文本 → 确认(是/否) →
工具批准 → 多字段表单，每一步停下来等你在输入框（web）/ 输入槽（TUI）/
聊天里（channel）作答，答完自动进入下一步，最后把你每一步的回答汇总返回。

用途：人工自测 runtime.ask / confirm / form 的端到端体验（输入框就地变形、
选项/多选/自由文本/确认/批准摘要/多字段表单的渲染与作答、答完收回）。
每一步都容错：你拒绝（UserDeclined）或超时（AskTimeout）都不会让流程崩，
会记成 "(declined)" / "(timeout)" 并继续下一步。

注册：AGENTIC_MODULES + TOOLSETS["full"]["tools"]（LLM 可见、fn-form 可启动）。
"""
from __future__ import annotations

from openprogram.agentic_programming.function import agentic_function
from openprogram.agentic_programming.runtime import Runtime


def _step(label, fn):
    """跑一步交互，把三态（答了 / 拒绝 / 超时）收敛成一行结果字符串。"""
    from openprogram.agent.questions import UserDeclined, AskTimeout
    try:
        return f"{label}: {fn()!r}"
    except UserDeclined:
        return f"{label}: (declined)"
    except AskTimeout:
        return f"{label}: (timeout)"


@agentic_function(input={})
def interaction_demo(runtime: Runtime) -> dict:
    """串行演示所有用户交互形态，返回每一步的回答汇总。"""
    if not runtime.can_ask():
        return {"error": "当前没有可应答的前端（headless）——请在 web / TUI / "
                          "已绑定的 channel 会话里运行本函数。"}

    results: list[str] = []

    # 1) 单选（带自由输入兜底）
    results.append(_step(
        "单选",
        lambda: runtime.ask(
            "1/6 单选：你最喜欢哪个日期库？",
            options=["dayjs", "date-fns", "luxon"],
            allow_custom=True, timeout=300,
        ),
    ))

    # 2) 多选
    results.append(_step(
        "多选",
        lambda: runtime.ask(
            "2/6 多选：勾选你用过的语言（可多选）。",
            options=["Python", "TypeScript", "Rust", "Go"],
            multi=True, allow_custom=True, timeout=300,
        ),
    ))

    # 3) 纯自由文本（无选项）
    results.append(_step(
        "自由文本",
        lambda: runtime.ask(
            "3/6 自由文本：用一句话描述你现在在做的事。",
            timeout=300,
        ),
    ))

    # 4) 确认（是 / 否）
    results.append(_step(
        "确认",
        lambda: runtime.confirm(
            "4/6 确认：要继续演示剩下的交互吗？",
            detail="选「取消」会把这步记成 False，但流程仍会继续。",
            timeout=300,
        ),
    ))

    # 5) 工具批准（approval —— 危险动作摘要 + 允许/拒绝）
    #    用 ask 的 approval 形态触发同一套 UI（kind 由前端按 detail/options 呈现）。
    results.append(_step(
        "批准",
        lambda: runtime.confirm(
            "5/6 批准：允许执行一个示例命令吗？",
            detail="tool: bash\nargs: {\"cmd\": \"echo hello\"}",
            timeout=300,
        ),
    ))

    # 6) 多字段表单（runtime.form）
    results.append(_step(
        "表单",
        lambda: runtime.form(
            "6/6 表单：填一下这个示例配置。",
            {
                "name": {"type": "string", "title": "名字", "default": "demo"},
                "count": {"type": "integer", "title": "次数", "default": 3},
                "mode": {"type": "string", "title": "模式", "enum": ["fast", "slow"]},
                "verbose": {"type": "boolean", "title": "详细日志", "default": False},
            },
            timeout=300,
        ),
    ))

    return {"演示完成": True, "你的回答": results}
