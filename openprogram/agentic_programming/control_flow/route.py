"""Route control flow primitive."""

from typing import List
from openprogram.agentic_programming.llm import llm


def route(
    question: str,
    options: List[str],
    context: str = ""
) -> str:
    """让 llm 从 options 中选择一个。

    Args:
        question: 选择的问题描述
        options: 可选项列表
        context: 可选的上下文信息

    Returns:
        选中的选项（options 中的一个）
    """
    if not options:
        raise ValueError("route() options must not be empty")
    prompt_parts = [question, f"\n\n选项：{', '.join(options)}"]
    if context:
        prompt_parts.append(f"\n\n上下文：{context}")
    prompt_parts.append("\n\n只回答选项内容")

    prompt = "".join(prompt_parts)
    result = str(llm(prompt) or "")

    for opt in options:
        if opt in result:
            return opt

    return options[0]
