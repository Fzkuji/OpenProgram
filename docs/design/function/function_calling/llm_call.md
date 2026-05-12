# LLM 决定调用哪个函数（tool_use）

**历史状态：** 2026-04 之前，这里描述的是靠 prompt 让 LLM 吐 `{"call":..., "args":...}`
文本、再用 `render_options` / `build_options` / `parse_args` 拼起来的"土工具调用"方案。
这套已经被 provider 原生 `tool_use` 协议替代，旧模块已删除。保留本文档只为方便
回溯为什么要换。

## 现在怎么做

直接把子函数塞进 `runtime.exec(tools=[...])`：

```python
@agentic_function
def research_assistant(task: str, runtime: Runtime) -> str:
    """LLM 决定调哪个子函数。"""
    return runtime.exec(
        content=[{"type": "text", "text": task}],
        tools=[summarize_text, polish_text, translate_to_chinese],
        tool_choice="auto",   # "required" 强制调一个；指定 name 强制选某个
    )
```

`@agentic_function` 自带 `.spec`（JSON Schema，自动从函数签名 + docstring
生成）和 `.execute`（调用包装后的函数）。Runtime 的 tool-use 循环：

1. 把 tools 声明发给 LLM；
2. LLM 吐 `function_call` 事件；
3. Runtime 本地调对应函数；
4. 结果以 `function_call_output` 回填；
5. 直到 LLM 吐纯文本 → 作为最终回复返回。

完整实现见：
- `openprogram/agentic_programming/runtime.py` 的 `Runtime.exec(tools=...)`
- `openprogram/providers/openai_codex.py` 的 `exec_with_tools` 和 SSE 解析
- 运行样例 `openprogram/programs/functions/third_party/llm_call_example.py`

## 为什么换

| 维度 | 旧方案（render_options） | 新方案（tool_use） |
|---|---|---|
| 输出格式 | 文本嵌 JSON，靠 prompt 劝说 | 独立事件类型，协议保证 |
| 参数校验 | 手动 parse_args + fix_call_params 重试 | JSON Schema 校验，模型原生回退 |
| 函数名越界 | 自己查注册表并 fallback | tools 声明是白名单，协议级拒绝 |
| 多工具并发 | 不支持 | `parallel_tool_calls=True` 原生支持 |
| 必选某个 | prompt 里求（不可靠） | `tool_choice={"type":"function","name":"X"}` |
| 代码行数 | 需要 build_options + render_options + parse_args 三件套 | `tools=[fn]` 一行 |

旧方案是在没有 native tool_use 时手搓的弱版本；现在主流 provider（OpenAI /
Anthropic / Gemini）都支持 tool_use，没必要再保留。
