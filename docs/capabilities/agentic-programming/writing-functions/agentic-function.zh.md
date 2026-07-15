# @agentic_function

`@agentic_function` 包装一个 Python 函数，其函数体可以通过 `runtime.exec()`
发起 LLM 调用。除非使用 `expose="hidden"`，否则该包装器会把这次函数调用记录到
会话 DAG 中。

本页讲解使用模式。元数据规则见
[`function-metadata.md`](function-metadata.md)。

## 基本模式

```python
from openprogram import agentic_function

@agentic_function(input={
    "text": {"description": "Text to translate."},
})
def translate_to_chinese(text: str, runtime=None) -> str:
    """Translate text to Chinese."""
    return runtime.exec(content=[{"type": "text", "text": (
        "Translate the following text to Chinese. Return only the translation.\n\n"
        f"Text:\n{text}"
    )}])
```

给 `runtime` 参数一个 `None` 默认值。直接调用时 runtime 会被自动注入，但当模型
通过 `tools=[...]` 选中该函数时，工具分发只会绑定模型提供的参数——没有默认值的
`runtime` 参数会在注入发生之前就抛出 `TypeError`。

docstring 是函数级别的描述。`content` 块才是这次 LLM 调用真正的指令和数据。

## 直接组合

当顺序固定时，使用直接的 Python 调用。

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

## LLM 选择的工具

当应当由模型来决定调用哪个函数时，使用 `runtime.exec(tools=[...])`。工具是可选启用的：
裸的 `runtime.exec(content=...)` 不带任何工具（不过工具函数体内部嵌套的 `exec`
会继承外层的工具列表）——
见 [`tool-calling.md`](../choosing-the-next-step/tool-calling.md)。

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
    )
```

`@agentic_function` 提供了 `.spec` 和 `.execute`，因此被装饰的函数可以直接传入
`tools=[...]`。

除了直接组合和工具之外，挑选下一步的第三种方式是通过 `exec(choices=...)` 或
`decision.make` 给出一个决策菜单——见
[`next-step-decision.md`](../choosing-the-next-step/next-step-decision.md)。

## 装饰器字段

装饰器字段（`expose`、`render_range`、`input`、
`system`、`workdir_mode`……）的文档**只在一处**：
[`function-metadata.md`](function-metadata.md) §3。位于
[`../../../reference/api/agentic-function.md`](../../../reference/api/agentic-function.md) 的 API 参考
附带一份精简的速查表。

本页讲解使用*模式*；它有意不重复逐字段的参考说明。如果你想知道某个字段的作用，
请前往 `function-metadata.md`。
