"""
ask_user —— 在 @agentic_function 执行途中向用户提问。

这不是范式原语，是一个内置工具函数。它需要"暂停执行→等回答→恢复"的能力，
所以一起捆绑了：
  - ask_user(question)            用户向的接口
  - set_ask_user(handler)         外部（WebUI / CLI）注册应答回调
  - FollowUp                      "函数暂停"的载体对象
  - run_with_follow_up(func, ...) 把 ask_user 转成非阻塞返回值

唯一依赖引擎的一行：`_current_ctx.get()`，用来定位当前 Context 节点。
"""

from __future__ import annotations

import queue as _queue
import sys
import threading
from typing import Callable, Optional

from openprogram.agentic_programming.context import Context, _current_ctx


# ---------------------------------------------------------------------------
# Handler registry (模块级单例)
# ---------------------------------------------------------------------------

_ask_user_handler_global: Optional[Callable] = None
_ask_user_lock = threading.Lock()


def set_ask_user(handler: Optional[Callable[[str], str]]) -> None:
    """
    注册一个全局 ask_user 回调。

    handler 接收问题字符串，返回用户的答案（可阻塞，例如等 WebSocket）。
    线程安全 —— 跨线程工作（ContextVar 做不到）。
    """
    global _ask_user_handler_global
    with _ask_user_lock:
        _ask_user_handler_global = handler


def has_ask_user_handler() -> bool:
    """True if a subsequent ``ask_user()`` call would have somewhere to send the question.

    Checks the same chain as ``ask_user`` itself:
      1. Per-node handler on the current Context (or any ancestor)
      2. Global handler set via ``set_ask_user``
      3. TTY-backed stdin (terminal interactive mode)

    Returns False only when none of the above is available — i.e. the
    caller is running headless / in a subprocess with no registered
    handler. Use this to decide whether to skip interactive steps like
    ``clarify`` that would just bounce off a None answer.
    """
    ctx = _current_ctx.get(None)
    while ctx is not None:
        if ctx.ask_user_handler is not None:
            return True
        ctx = ctx.parent
    with _ask_user_lock:
        if _ask_user_handler_global is not None:
            return True
    if sys.stdin is not None and sys.stdin.isatty():
        return True
    return False


def ask_user(question: str) -> Optional[str]:
    """
    在 @agentic_function 执行中向用户提问。

    回调查找顺序：
      1. 当前 Context 节点上的 `ask_user_handler` 字段（若设置）
      2. 逐级向上走 parent 链，第一个带 handler 的祖先胜出
      3. 全局 handler（由 set_ask_user 注册，WebUI 用这个）
      4. 默认终端 `input()`（仅当 stdin 是 TTY 时）

    返回用户答案；如果没有任何 handler 可用，返回 None。
    """
    # 1-2. 爬 Context 链找 per-node handler
    ctx = _current_ctx.get(None)
    while ctx is not None:
        if ctx.ask_user_handler is not None:
            return ctx.ask_user_handler(question)
        ctx = ctx.parent

    # 3. 全局 handler（给 WebUI / 后台服务用）
    with _ask_user_lock:
        handler = _ask_user_handler_global
    if handler is not None:
        return handler(question)

    # 4. 终端输入（交互模式最后兜底）
    if sys.stdin is not None and sys.stdin.isatty():
        try:
            return input(f"[follow-up] {question}\n> ")
        except EOFError:
            return None

    return None


# ---------------------------------------------------------------------------
# FollowUp —— 非阻塞的暂停/恢复载体
# ---------------------------------------------------------------------------

class FollowUp:
    """
    一个来自运行中函数的待答问题。

    当一个函数在 `run_with_follow_up(...)` 下执行并调用 `ask_user(...)` 时，
    `run_with_follow_up` 返回 FollowUp 而不是阻塞。函数所在的后台线程还活着，
    卡在 `ask_user` 里等答案。

    调用 `.answer(text)` 提供答案，函数从暂停处恢复（所有局部变量、调用栈、
    Context 树完全保留）。下一个返回值可能是：
      - 函数的最终结果（执行完成）
      - 另一个 FollowUp（函数又问了下一个问题）
      - 抛出异常（函数出错）

    用例：
        result = run_with_follow_up(my_func, arg1, arg2)
        while isinstance(result, FollowUp):
            print(f"Q: {result.question}")
            answer = get_answer_somehow(result.question)
            result = result.answer(answer)
    """

    def __init__(self, question: str, _answer_q: _queue.Queue, _result_q: _queue.Queue):
        self.question = question
        self._answer_q = _answer_q
        self._result_q = _result_q

    def answer(self, text: str):
        """给出答案并等下一个结果。"""
        self._answer_q.put(text)
        result = self._result_q.get()
        if isinstance(result, _WrappedException):
            raise result.exception
        return result

    def __repr__(self):
        return f"FollowUp(question={self.question!r})"


class _WrappedException:
    """内部包装器，让我们在 queue 里区分异常和正常结果。"""
    __slots__ = ("exception",)

    def __init__(self, exc: BaseException):
        self.exception = exc


def run_with_follow_up(func, *args, **kwargs):
    """
    跑一个可能调 ask_user 的函数，支持非阻塞 follow-up。

    不同于直接调用（ask_user 会阻塞当前线程），这个把函数放到后台线程里跑。
    函数调 ask_user 的那一刻，本方法返回 FollowUp 给调用方；函数的完整状态
    （局部变量、调用栈、Context 树）保留在后台等答案。

    用例：
        # agent 自动应答：
        result = run_with_follow_up(edit, fn=broken_func, runtime=rt)
        while isinstance(result, FollowUp):
            answer = runtime.exec(f"Answer: {result.question}")
            result = result.answer(answer)

        # 简单的阻塞式（等价于直接调 + 终端 handler）：
        result = run_with_follow_up(my_func, x=1)
        if isinstance(result, FollowUp):
            result = result.answer(input(f"{result.question}\\n> "))
    """
    answer_q: _queue.Queue = _queue.Queue()
    result_q: _queue.Queue = _queue.Queue()

    def _handler(question: str) -> str:
        # 把 FollowUp 送给调用方
        result_q.put(FollowUp(question, answer_q, result_q))
        # 阻塞等调用方回答
        return answer_q.get()

    def _run():
        with _ask_user_lock:
            prev_handler = _ask_user_handler_global
        set_ask_user(_handler)
        try:
            val = func(*args, **kwargs)
            result_q.put(val)
        except BaseException as e:
            result_q.put(_WrappedException(e))
        finally:
            set_ask_user(prev_handler)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    result = result_q.get()
    if isinstance(result, _WrappedException):
        raise result.exception
    return result


__all__ = [
    "ask_user",
    "set_ask_user",
    "has_ask_user_handler",
    "FollowUp",
    "run_with_follow_up",
]
