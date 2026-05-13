"""clarify — pre-check before code generation.

Three primitives, closely related:

  - ``follow_up(question, runtime)`` — @agentic_function that surfaces
    a clarifying question through the call chain so an upper-level
    agent (or human) can answer.
  - ``clarify(task, runtime)`` — @agentic_function that asks the LLM
    "is the task spec ready, do you need a follow-up, or should we
    abort?" and returns one of three dicts.
  - ``check_task`` — backward-compatible alias for ``clarify``.

NOTE: Do NOT add shortcut functions here (e.g. ``_has_prior_context``,
``_looks_obviously_vague``). The LLM must always decide via
``runtime.exec()``. See commit 5094410 for rationale.
"""

from __future__ import annotations

from openprogram.agentic_programming.function import agentic_function
from openprogram.agentic_programming.runtime import Runtime
from openprogram.programs.functions.buildin._utils import parse_json


@agentic_function
def follow_up(question: str, runtime: Runtime) -> str:
    """向调用方提出问题以获取补充信息。

    当 LLM 判断信息不足以完成任务时，通过此函数向调用方提问。
    问题会沿调用链返回，由上层的 agent 或用户处理。

    Args:
        question: 需要回答的具体问题。
        runtime: LLM 运行时实例。

    Returns:
        问题本身（由调用方处理并在后续调用中提供答案）。
    """
    return question


def _reply_looks_like_follow_up(reply: str) -> bool:
    """Heuristically detect a non-JSON clarification request.

    Fallback for when clarify's LLM call doesn't return parseable JSON.
    Kept conservative: only treat as not-ready when the reply clearly
    asks for more information.
    """
    if not reply:
        return False

    lower = reply.lower()
    english_markers = (
        "question:",
        "unclear",
        "need more",
        "ambiguous",
        "please provide",
        "need clarification",
        "missing information",
    )
    chinese_markers = (
        "需要更多信息",
        "需要补充",
        "请提供",
        "不清楚",
        "有歧义",
        "缺少",
        "无法判断",
        "请说明",
    )
    return any(marker in lower for marker in english_markers) or any(
        marker in reply for marker in chinese_markers
    )


@agentic_function(render_range={"depth": 0, "siblings": 0})
def clarify(task: str, runtime: Runtime) -> dict:
    """Review a task before code generation and decide whether to ask the user first.

    Ask a clarifying question if:
    - The instruction is vague, investigative, or open-ended
    - Critical details are missing
    - The intent is ambiguous

    Exit (stop the task entirely) if:
    - The task is fundamentally impossible or nonsensical
    - The user's request doesn't match the current operation (e.g. asking to explain code in a fix flow)
    - After multiple failed attempts, the approach is clearly not working

    Proceed without asking if:
    - The instruction is specific and actionable
    - A prior Q/A pair already clarified the ambiguity

    Return JSON:
    - {"ready": false, "question": "your specific question"}
    - {"ready": true}
    - {"exit": true, "reason": "why this task should stop"}

    Args:
        task: The full task description (code, errors, instructions, etc.).
        runtime: LLM runtime instance.

    Returns:
        dict with "ready" (bool) and optionally "question" (str).
    """
    reply = runtime.exec(content=[
        {"type": "text", "text": (
            "You are reviewing a task before code generation begins.\n\n"
            "Ask a clarifying question if:\n"
            "- The instruction is vague, investigative, or open-ended "
            "(e.g. 'look into this', 'why is this happening', 'improve it')\n"
            "- Critical details are missing (what to change, expected behavior, constraints)\n"
            "- The intent is ambiguous (multiple valid interpretations)\n\n"
            "Exit (stop the task) if:\n"
            "- The task is fundamentally impossible or nonsensical\n"
            "- The request doesn't match the operation (e.g. asking to explain code in a fix flow)\n"
            "- After repeated failures, the approach is clearly not working\n\n"
            "Proceed without asking if:\n"
            "- The instruction is specific and actionable\n"
            "- A prior Q/A pair already clarified the ambiguity\n\n"
            "Return ONLY JSON:\n"
            '{"ready": false, "question": "your specific question"}\n'
            '{"ready": true}\n'
            '{"exit": true, "reason": "why this task should stop"}\n\n'
            f"Task:\n{task}"
        )},
    ])

    # Try to parse JSON from reply
    try:
        result = parse_json(reply)
        if "exit" in result or "ready" in result:
            return result
    except ValueError:
        pass

    # Fallback: if the reply looks like code, treat as ready (LLM skipped the JSON step)
    if reply.strip().startswith(("```", "def ", "import ", "@", "from ")):
        return {"ready": True}

    # If the reply clearly asks for more information, treat as not ready.
    if _reply_looks_like_follow_up(reply):
        lines = [l.strip() for l in reply.strip().splitlines() if l.strip()]
        question_lines = [
            l for l in lines if not l.startswith(("def ", "import ", "@", "```", "#"))
        ]
        question = "\n".join(question_lines[:3]) if question_lines else reply[:200]
        return {"ready": False, "question": question}

    # Default: ready to proceed
    return {"ready": True}


def check_task(task: str, runtime: Runtime) -> dict:
    """Backward-compatible alias for clarify().

    Older tests and external callers still import check_task from this module.
    Keep the compatibility shim while the clearer name, clarify(), becomes the
    primary public entry point.
    """
    return clarify(task=task, runtime=runtime)
