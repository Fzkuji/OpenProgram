# Agentic Function 之间的调用

一个 `@agentic_function` 可以用两种方式调另一个 `@agentic_function`：

## 1. Python 直接调（代码驱动）

```python
@agentic_function
def pipeline(task: str, runtime: Runtime) -> str:
    a = step_a(task=task, runtime=runtime)
    b = step_b(input=a, runtime=runtime)
    return b
```

适合固定流程、你自己清楚调用顺序的场景。

## 2. 塞进 tools=[...]（LLM 驱动）

```python
@agentic_function
def assistant(task: str, runtime: Runtime) -> str:
    return runtime.exec(
        content=[{"type": "text", "text": task}],
        tools=[step_a, step_b, step_c],
        tool_choice="auto",
    )
```

适合"LLM 分析任务，自行决定调谁、调几次、按什么顺序"的场景。provider 原生
`tool_use` 协议保证：函数名不会越界、参数符合 JSON Schema、可要求"必调一个"
或"必调指定那个"。

`@agentic_function` 自带 `.spec` 和 `.execute`，直接塞进 `tools=[...]` 就能用；
不需要另写 render_options + parse_args 那套中间层（旧版方案
已经在 2026-04 移除）。

## 历史方案

> 之前这里描述的是基于 `render_options` + `parse_args` 的土
> 工具调用方案。它在 provider 没有 native tool_use 时是合理的兜底，但现在主流
> provider（OpenAI / Anthropic / Gemini）都原生支持 tool_use，没必要再维护。

完整样例参考：
- `openprogram/programs/functions/third_party/llm_call_example.py`
- 工具实现：`openprogram/tools/<name>/`
