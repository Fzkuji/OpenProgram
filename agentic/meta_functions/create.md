# create() 设计规范

`create()` 是一个 meta function，用于根据自然语言描述自动生成 `@agentic_function`。
生成的函数保存到 `agentic/functions/`，可在网页端和 CLI 中直接使用。

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

- 一个 `@agentic_function` 最多调一次 `runtime.exec()`
- 需要多次 LLM 调用时，拆成多个 `@agentic_function`
- 一个函数可以调用多个其他 `@agentic_function`

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

```python
# 1. 构建函数目录（只展示 source="llm" 的参数）
catalog = build_catalog(available)

# 2. 调用 LLM
reply = runtime.exec(content=[
    {"type": "text", "text": f"{task}\n\n== Functions ==\n{catalog}"},
])

# 3. 解析 LLM 的选择
action = parse_action(reply)
# action = {"call": "polish_text", "args": {"style": "academic"}}

# 4. 准备参数（合并 LLM 参数 + context 填充 + runtime 注入）
args = prepare_args(action, available, runtime, context={"text": task})
# args = {"text": task, "style": "academic", "runtime": runtime}

# 5. 调用函数
result = available[action["call"]]["function"](**args)

# 6. 后续处理（result 可继续使用）
```

### 容错机制

| 情况 | 处理 |
|------|------|
| 函数名不存在 | 返回 LLM 原始回复 |
| 多余参数 | 过滤掉函数签名里没有的 |
| 缺少必要参数 | 调 `fix_call_params` 让 LLM 补全 |
| JSON 解析失败 | 返回 LLM 原始回复 |
| LLM 信息不足 | 通过 `follow_up()` 向调用方提问 |

### follow_up 机制

当 LLM 判断信息不足以完成任务时，可以通过 function dispatch 调用 `follow_up()`，
而不是硬猜或输出不完整的代码。

`generate_code()` 的 available 函数中包含 `follow_up`，LLM 可以选择调用它：

```python
# LLM 输出：
{"call": "follow_up", "args": {"question": "输入格式是 JSON 还是纯文本？"}}
```

`generate_code()` 返回 `{"type": "follow_up", "question": "..."}`，
由调用方（create / fix / improve）向上传递，最终由上层 agent 或用户处理。

## 代码风格

### 变量命名
- 加载的函数：`loaded_func`
- 解包装饰器后的函数：`unwrapped_func`
- 编译生成的函数：`compiled_func`
- 注册表 key：`"function"` 不是 `"fn"`

### 文件组织
- 工具函数各自独立文件：`build_catalog.py`、`parse_action.py`、`prepare_args.py`
- `available` 注册表写在使用它的函数内部，不定义为模块级常量

## 健壮性规则

- 有明确输出格式时，在 docstring 中精确定义，不让 LLM 猜
- 涉及文本输入时，处理特殊字符和边界情况
- 依赖外部状态时，校验输入并给出清晰的错误信息
- 结果会被其他函数使用时，优先返回结构化数据（dict/JSON）
- 格式重要时，在 docstring 中给出示例
