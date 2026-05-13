# create() 设计规范

`create()` 是一个 meta function，用于根据自然语言描述自动生成 `@agentic_function`。
生成的函数保存到 `openprogram/programs/functions/third_party/`，可在网页端和 CLI 中直接使用。

## Docstring 规范

Docstring 就是 LLM 的 prompt。框架自动将 docstring 作为上下文发送给 LLM。

### 必须包含
- 一行摘要：函数做什么
- 具体指令：输出格式、约束条件、特殊要求
- Args：每个参数的含义和类型
- Returns：返回值的结构和含义

### 禁止包含
- 角色扮演（"You are a helpful assistant"）
- 空洞指令（"Complete the task"、"Do your best"）
- 重复 content 中已有的数据

### 示例

```python
@agentic_function
def sentiment(text: str) -> str:
    """分析文本情感倾向，返回 positive、negative 或 neutral。

    Args:
        text: 待分析的文本。

    Returns:
        情感标签，仅限 positive/negative/neutral 三选一。
    """
    return runtime.exec(content=[
        {"type": "text", "text": text},
    ])
```

## Content 规范

`runtime.exec(content=[...])` 中只放数据，不放指令。

```python
# 正确：只传数据
runtime.exec(content=[{"type": "text", "text": text}])

# 错误：在 content 里重复指令
runtime.exec(content=[{"type": "text", "text": f"Please analyze the sentiment of: {text}. Return one word."}])
```

## 函数类型判断

| 条件 | 类型 | 用 @agentic_function? | 用 runtime.exec()? |
|------|------|----------------------|-------------------|
| 需要 LLM 推理 | agentic function | 是 | 是 |
| 纯确定性逻辑 | 普通 Python 函数 | 否 | 否 |

## exec() 调用规则

- 一个 `@agentic_function` 可以调用多次 `runtime.exec()`（每次创建一个 exec 子节点）
- 一个函数可以调用多个其他 `@agentic_function`
- exec 节点是函数节点的子节点，通过 `summarize()` 自动获取上下文

## LLM 动态选择函数（dispatch 模式）

当函数需要让 LLM 决定调用哪个子函数时：

### 函数注册表

```python
available = {
    "polish_text": {
        "function": polish_text,
        "description": "按指定风格润色文本",
        "input": {
            "text": {"source": "context"},       # 代码自动填充
            "style": {                            # LLM 决定
                "source": "llm",
                "type": str,
                "options": ["academic", "casual", "concise"],
                "description": "润色风格",
            },
        },
        "output": {"polished_text": str},
    },
}
```

### 参数来源

| source | 含义 | 谁提供 | LLM 是否可见 |
|--------|------|-------|-------------|
| `"context"` | 从上下文自动填充（如 task → text） | Python 代码 | 否 |
| `"llm"` | LLM 在回复中指定 | LLM | 是 |
| runtime | 框架自动注入 | 框架 | 否 |

### 调用流程

直接用原生 tool_use，没有 render_options + parse_args 这些中间层：

```python
# 把子函数放进 tools=[...]，LLM 发 function_call 事件，runtime 本地分发
reply = runtime.exec(
    content=[{"type": "text", "text": task}],
    tools=[summarize_text, polish_text, translate_to_chinese],
    tool_choice="auto",       # "required" 强制调一个；指定 name 强制选某个
)
# reply = LLM 在调完所有工具后给出的最终文字
```

`@agentic_function` 本身带 `.spec`（OpenAI JSON Schema）和 `.execute`，
runtime 自动把二者组合成工具。Python 信号里的 `runtime: Runtime` 这类注入参数
对 LLM 不可见；`input={"x": {"hidden": True}}` 也能主动藏。

### 容错机制

tool_use 的原生协议已经消化了大部分 render_options 时代需要手动处理的 case：

| 情况 | 处理 |
|------|------|
| 函数名写错 | 协议层限制，只能从 tools 列表里选 |
| 多余 / 缺失参数 | JSON Schema 校验失败，模型按描述重填 |
| JSON 解析失败 | 没有文本解析环节 |
| 工具执行异常 | runtime 把异常作为 function_call_output 喂回，模型可修正 |
| 循环不收敛 | runtime 到 `max_iterations` 抛 RuntimeError |

### ask_user 机制

仍然有效，但建议写成一个 `ask_user` 工具，和其他工具一起塞进 `tools=[...]`：

```python
@agentic_function(input={"question": {"description": "问题"}, "runtime": {"hidden": True}})
def ask_user(question: str, runtime: Runtime) -> str:
    \"\"\"当任务信息不足时向用户提问，返回用户的回答。\"\"\"
    return input_from_user(question)   # 具体实现由调用方注入

# LLM 发现信息不足时，直接 function_call("ask_user", {"question": "..."})
# 不需要 check_task 特殊返回结构
```

## 代码风格

### 变量命名
- 加载的函数：`loaded_func`
- 解包装饰器后的函数：`unwrapped_func`
- 编译生成的函数：`compiled_func`
- 注册表 key：`"function"` 不是 `"fn"`

### 文件组织
- 原子工具（shell / file / search 等）放在 `openprogram/tools/<name>/`，
  每个目录一个 `<name>.py` + `__init__.py`，参考 `bash/` 的结构。
- `@agentic_function` 天然就是工具，不需要额外包装就能塞进 `tools=[...]`。

## 健壮性规则

- 有明确输出格式时，在 docstring 中精确定义，不让 LLM 猜
- 涉及文本输入时，处理特殊字符和边界情况
- 依赖外部状态时，校验输入并给出清晰的错误信息
- 结果会被其他函数使用时，优先返回结构化数据（dict/JSON）
- 格式重要时，在 docstring 中给出示例
