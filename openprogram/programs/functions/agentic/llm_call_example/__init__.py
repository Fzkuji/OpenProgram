"""
llm_call_example — demonstrates LLM-driven function dispatch via tool_use.

Shows how an @agentic_function exposes sub-functions as tools and lets the
LLM pick one. Parameters come directly from the model as structured JSON —
no catalog/prompt-engineering, no regex parse, no missing-arg retry.

Context tree:
    research_assistant
    └ polish_text            (example — tool picked by the model)

Usage:
    from openprogram.providers.registry import create_runtime
    from openprogram.programs.functions.agentic.llm_call_example import research_assistant

    rt = create_runtime()
    result = research_assistant(task="帮我用学术风格润色这段话: ...", runtime=rt)
"""

from __future__ import annotations

from openprogram.agentic_programming import llm
from openprogram.agentic_programming.function import agentic_function
from openprogram.agentic_programming.runtime import Runtime


# ---------------------------------------------------------------------------
# Sub-functions (each @agentic_function auto-exposes as a tool via .spec)
# ---------------------------------------------------------------------------

@agentic_function(input={
    "text": {"description": "Text to summarize", "placeholder": "Paste your text here..."},
})
def summarize_text(text: str) -> str:
    """将文本压缩为简洁的摘要。

    Args:
        text: 需要总结的原始文本。

    Returns:
        简洁的摘要文本。
    """
    return llm([
        {"type": "text", "text": f"Please summarize:\n\n{text}"},
    ])


@agentic_function(input={
    "text": {"description": "English text to translate", "placeholder": "e.g. Hello, how are you?"},
})
def translate_to_chinese(text: str) -> str:
    """将英文文本翻译为中文。

    Args:
        text: 需要翻译的英文文本。

    Returns:
        翻译后的中文文本。
    """
    return llm([
        {"type": "text", "text": f"Translate to Chinese:\n\n{text}"},
    ])


@agentic_function(input={
    "text": {"description": "Text to polish", "placeholder": "Paste your text here..."},
    "style": {"description": "Style", "placeholder": "academic", "options": ["academic", "casual", "concise"]},
})
def polish_text(text: str, style: str) -> str:
    """按指定风格润色文本。

    Args:
        text: 需要润色的文本。
        style: 润色风格，可选 "academic"（学术）、"casual"（口语）、"concise"（精简）。

    Returns:
        润色后的文本。
    """
    return llm([
        {"type": "text", "text": f"Polish this text in {style} style:\n\n{text}"},
    ])


# ---------------------------------------------------------------------------
# Entry point — LLM decides which function to call via tool_use
# ---------------------------------------------------------------------------

@agentic_function(input={
    "task": {"description": "Research task", "placeholder": "e.g. 帮我用学术风格润色这段话: ..."},
    "runtime": {"hidden": True},
})
def research_assistant(task: str, runtime: Runtime) -> str:
    """分析研究任务，通过 tool_use 让 LLM 选择合适的子函数完成工作。

    LLM 在一次对话里看到三个工具（summarize_text / translate_to_chinese /
    polish_text），直接发 function_call 事件。Runtime 负责本地分发并把结果
    塞回去，最终返回文字回复。

    Args:
        task: 用户的研究任务描述。
        runtime: LLM 运行时实例。

    Returns:
        LLM 在调完子函数后给出的最终回复（字符串）。
    """
    # tool loop, migrates with agent() in a later batch
    return runtime.exec(
        content=[
            {"type": "text", "text": (
                f"{task}\n\n"
                "Pick the most suitable tool to accomplish the task. "
                "You may call one or more tools. Return a short final reply summarizing what you did."
            )},
        ],
        tools=[summarize_text, translate_to_chinese, polish_text],
        tool_choice="auto",
    )
