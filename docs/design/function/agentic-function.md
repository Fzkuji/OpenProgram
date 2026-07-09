# @agentic_function

`@agentic_function` 包装一个 Python 函数，其函数体可以通过
`runtime.exec()` 发起 LLM 调用。除非使用 `expose="hidden"`，否则该包装器会
将函数调用记录到会话 DAG 中。

本页讲解使用模式。元数据规则见
[`function-metadata.md`](function-metadata.md)。

## 基本模式

```python
from openprogram import agentic_function

@agentic_function(input={
    "text": {"description": "Text to translate."},
})
def translate_to_chinese(text: str, runtime) -> str:
    """Translate text to Chinese."""
    return runtime.exec(content=[{"type": "text", "text": (
        "Translate the following text to Chinese. Return only the translation.\n\n"
        f"Text:\n{text}"
    )}])
```

docstring 是函数级别的描述。`content` 块是本次 LLM 调用的实际指令和数据。

## 直接组合

当执行顺序固定时，使用直接的 Python 调用。

```python
@agentic_function(input={
    "task": {"description": "Research task."},
})
def research_pipeline(task: str, runtime) -> dict:
    """Run a fixed research pipeline."""
    survey = survey_topic(topic=task, runtime=runtime)
    gaps = identify_gaps(survey=survey, runtime=runtime)
    ideas = generate_ideas(gaps=gaps, runtime=runtime)
    return {"survey": survey, "gaps": gaps, "ideas": ideas}
```

## 由 LLM 选择工具

当应由模型决定调用哪个函数时，使用 `runtime.exec(tools=[...])`。

```python
@agentic_function(input={
    "task": {"description": "User task."},
})
def research_assistant(task: str, runtime) -> str:
    """Choose and run the appropriate research helper."""
    return runtime.exec(
        content=[{"type": "text", "text": (
            "Choose the appropriate helper for this task and complete the work.\n\n"
            f"Task:\n{task}"
        )}],
        tools=[survey_topic, identify_gaps, generate_ideas],
        tool_choice="auto",
    )
```

`@agentic_function` 提供了 `.spec` 和 `.execute`，因此被装饰的函数可以
直接传入 `tools=[...]`。

## 装饰器字段

装饰器字段（`expose`、`render_range`、`input`、`no_tools`、
`system`、`workdir_mode` 等）的文档**集中在一处**：
[`function-metadata.md`](function-metadata.md) §3。位于
[`../../api/agentic-function.md`](../../api/agentic-function.md) 的 API 参考
也提供了相同的表格，便于快速查阅。

本页讲解使用*模式*；它有意不重复
逐字段的参考说明。如果你想了解某个字段的作用，
请前往 `function-metadata.md`。
