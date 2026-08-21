"""Conditional control flow primitive."""

from typing import Callable, Optional
from openprogram.agentic_programming.llm import llm


def conditional(
    condition: str,
    context: str = "",
    if_true: Optional[Callable[[], str]] = None,
    if_false: Optional[Callable[[], str]] = None
) -> str:
    """根据 llm 判定条件执行分支。

    Args:
        condition: 判定条件的自然语言描述
        context: 判定依据的上下文
        if_true: 条件为真时执行
        if_false: 条件为假时执行

    Returns:
        执行分支的返回值
    """
    prompt_parts = [f"判断条件是否成立：{condition}"]
    if context:
        prompt_parts.append(f"\n\n上下文：{context}")
    prompt_parts.append("\n\n回答 YES 或 NO")

    prompt = "".join(prompt_parts)
    judgment = str(llm(prompt) or "")

    if "YES" in judgment.upper():
        return if_true() if if_true else ""
    else:
        return if_false() if if_false else ""
