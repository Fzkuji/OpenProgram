"""Validation and retry control flow primitive."""

from typing import Callable
from openprogram.agentic_programming.llm import llm


def validate_and_retry(
    action: Callable[[], str],
    check: str,
    retry: Callable[[], str],
    max_retries: int = 2
) -> str:
    """执行 action，用 llm 判定结果是否满足 check，不满足则执行 retry。

    Args:
        action: 返回字符串结果的函数
        check: 自然语言判定条件，如 "文件数量 >= 3"
        retry: 不满足时执行的补救函数
        max_retries: 最多重试次数

    Returns:
        满足条件的结果，或最后一次重试的结果
    """
    result = action()

    for attempt in range(max_retries + 1):
        judgment = str(llm(
            f"判断结果是否满足：{check}\n\n结果：{result}\n\n回答 YES 或 NO"
        ) or "")

        if "YES" in judgment.upper():
            return result

        if attempt < max_retries:
            result = retry()

    return result
