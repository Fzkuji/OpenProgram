"""Auto 权限档的安全分类器（对齐 Claude Code 网页端 "Auto mode"）。

Auto 档：每个工具调用执行前，先用硬规则挡掉明显安全/危险的，只对"拿不准"的
调一次轻量 LLM 判定安全还是危险——安全放行、危险拒。省掉逐次弹审批，又不像
bypass 那样盲目放行。

三级过滤（省 LLM 调用）：
  1. 明显安全（只读工具）→ 直接放行，不调 LLM
  2. 明显危险（bash/exec/shell 等代码执行）→ 直接拒，不调 LLM
  3. 拿不准（write/edit 等）→ 调一次 haiku 判定
LLM 不可用/出错 → fail-safe 拒（auto 档下宁可拦错，不放过危险）。
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# 明显安全的只读/无副作用工具——auto 档直接放行，永不调 LLM。
SAFE_AUTO_ALLOWLIST = frozenset({
    "read", "read_file", "grep", "glob", "list", "list_files",
    "web_search", "web_fetch", "tool_search", "todo",
    "ask_user_question", "send_message", "sleep",
    "enter_plan_mode", "exit_plan_mode",
})

# 明显危险的代码执行工具——auto 档直接拒，永不调 LLM。
RISKY_AUTO_DENYLIST = frozenset({
    "bash", "exec", "shell", "execute_code", "process",
})


async def auto_classify_tool(tool_name: str, args: dict) -> tuple[bool, str]:
    """判定工具+参数是否该拒。返回 (should_block, reason)。
    只对"拿不准"的工具调用（前两级由调用方用 allowlist/denylist 挡掉）。
    出错 fail-safe → block。"""
    try:
        import time
        from openprogram.providers.models import get_model
        from openprogram.providers.stream import complete_simple
        from openprogram.providers.types import (
            Context, UserMessage, SimpleStreamOptions,
        )

        model = (get_model("anthropic", "claude-haiku-4-5-20251001")
                 or get_model("anthropic", "claude-sonnet-4-6"))
        if model is None:
            return True, "分类器模型不可用"

        system_prompt = (
            "你是工具执行安全分类器。根据工具名和参数，判定这次执行安全还是危险。\n"
            "只回一行 JSON：{\"safe\": true/false, \"reason\": \"简短理由\"}。\n"
            "危险：删除/覆盖重要文件、改系统或凭据配置、联网执行外部代码、不可逆操作。\n"
            "安全：改工作目录内的普通项目文件、常规增删改。"
        )
        user_text = (
            f"工具：{tool_name}\n"
            f"参数：{json.dumps(args, ensure_ascii=False)[:800]}\n"
            "这次执行安全吗？"
        )
        ctx = Context(
            system_prompt=system_prompt,
            messages=[UserMessage(content=user_text, timestamp=int(time.time() * 1000))],
            tools=[],
        )
        result = await complete_simple(
            model, ctx, SimpleStreamOptions(temperature=0.0, max_tokens=120),
        )
        text = ""
        for block in (result.content or []):
            if getattr(block, "text", None):
                text += block.text
        text = text.strip()
        # 容错：从回复里抠出 JSON。
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            obj = json.loads(text[start:end + 1])
            safe = bool(obj.get("safe", False))
            reason = str(obj.get("reason", "")) or "分类器判定"
            return (not safe), reason
        return True, f"分类器回复无法解析：{text[:60]}"
    except Exception as e:  # noqa: BLE001
        logger.warning("auto classifier error: %s", e)
        return True, f"分类器不可用：{type(e).__name__}"
