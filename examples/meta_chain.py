"""
Meta Chain — Dynamic function generation with create().

Uses create() to dynamically build a processing pipeline at runtime.
The pipeline steps are generated from natural language descriptions,
then chained together.

This example demonstrates:
    - Using create() to generate functions from descriptions
    - Dynamic pipeline construction
    - Meta-programming with agentic functions

Usage:
    # The mock runtime makes this runnable without an API key.
    python examples/meta_chain.py
"""

from agentic import agentic_function, Runtime
from agentic.meta_function import create


# ── Mock LLM that generates code ──────────────────────────────

_STEP_COUNTER = 0


def mock_llm(content, model="default", response_format=None):
    """Mock LLM that generates agentic function code or executes reasoning."""
    global _STEP_COUNTER
    text = " ".join(b.get("text", "") for b in content if b.get("type") == "text")

    # If this is a create() call — generate function code
    if "Write a Python function" in text:
        _STEP_COUNTER += 1
        n = _STEP_COUNTER

        if "extract" in text.lower() or "key point" in text.lower():
            return '''
@agentic_function
def extract_key_points(text):
    """Extract the main points from the given text."""
    return runtime.exec(content=[
        {"type": "text", "text": f"Extract 3 key points from: {text}"},
    ])
'''
        elif "translate" in text.lower():
            return '''
@agentic_function
def translate_to_chinese(text):
    """Translate the given text to Chinese."""
    return runtime.exec(content=[
        {"type": "text", "text": f"Translate to Chinese: {text}"},
    ])
'''
        elif "format" in text.lower() or "markdown" in text.lower():
            return '''
@agentic_function
def format_as_markdown(text):
    """Format the text as a clean markdown document."""
    return runtime.exec(content=[
        {"type": "text", "text": f"Format as markdown with headers and bullets: {text}"},
    ])
'''
        else:
            return f'''
@agentic_function
def process_step_{n}(text):
    """Process step {n}."""
    return runtime.exec(content=[
        {{"type": "text", "text": f"Process: {{text}}"}},
    ])
'''

    # If this is a runtime.exec() call inside a generated function
    if "extract" in text.lower() and "key point" in text.lower():
        return (
            "Key Points:\n"
            "1. Agentic Programming lets Python and LLMs cooperate inside functions\n"
            "2. The decorator @agentic_function records all execution into a Context tree\n"
            "3. Runtime.exec() handles context injection and LLM calls automatically"
        )
    elif "translate" in text.lower() or "chinese" in text.lower():
        return (
            "要点：\n"
            "1. Agentic Programming 让 Python 和 LLM 在函数内部协作\n"
            "2. @agentic_function 装饰器将所有执行记录到 Context 树中\n"
            "3. Runtime.exec() 自动处理上下文注入和 LLM 调用"
        )
    elif "format" in text.lower() or "markdown" in text.lower():
        return (
            "# 要点总结\n\n"
            "## 1. 协作执行\n"
            "Agentic Programming 让 Python 和 LLM 在函数内部协作\n\n"
            "## 2. 自动记录\n"
            "@agentic_function 装饰器将所有执行记录到 Context 树中\n\n"
            "## 3. 上下文管理\n"
            "Runtime.exec() 自动处理上下文注入和 LLM 调用"
        )
    else:
        return f"Processed: {text[:100]}"


# ── Runtime ─────────────────────────────────────────────────────

runtime = Runtime(call=mock_llm, model="mock")


# ── Dynamic Pipeline ───────────────────────────────────────────

@agentic_function
def run_pipeline(text: str, steps: list):
    """
    Build and run a dynamic processing pipeline.

    Each step is a natural language description. create() generates
    a function for each step, then they're chained together.
    """
    result = text
    generated_fns = []

    for desc in steps:
        fn = create(desc, runtime=runtime)
        generated_fns.append(fn)
        result = fn(result)

    return result


# ── Entry Point ────────────────────────────────────────────────

if __name__ == "__main__":
    input_text = (
        "Agentic Programming is a paradigm where Python functions and LLMs "
        "cooperate. Functions are decorated with @agentic_function, which "
        "records execution into a Context tree. Runtime.exec() handles "
        "context injection and LLM calls automatically."
    )

    pipeline_steps = [
        "Extract key points from text. Take a 'text' parameter and return bullet points.",
        "Translate text to Chinese. Take a 'text' parameter.",
        "Format text as clean markdown. Take a 'text' parameter.",
    ]

    print("── Input ──")
    print(input_text)
    print(f"\n── Pipeline ({len(pipeline_steps)} steps) ──")
    for i, step in enumerate(pipeline_steps, 1):
        print(f"  {i}. {step}")

    result = run_pipeline(input_text, pipeline_steps)

    print("\n── Output ──")
    print(result)

    print("\n── Context Tree ──")
    print(run_pipeline.context.tree())
