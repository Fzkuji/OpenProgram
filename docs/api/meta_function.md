# create, fix & improve

> Source: [`agentic/meta_functions/`](../../agentic/meta_functions/)

Meta function。用自然语言描述生成新的 `@agentic_function`，以及修复和改进现有函数。

所有代码生成 meta function（create / fix / improve）都调用 `generate_code()` 作为底层。
`generate_code()` 的 docstring 包含完整的 Agentic Programming 设计规范（单一来源）。

当 LLM 信息不足时，可通过 `follow_up()` 函数向调用方提问，而非直接生成代码。

---

## Function: `create()`

```python
@agentic_function
def create(description: str, runtime: Runtime, name: str = None, as_skill: bool = False)
```

### 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `description` | `str` | *(必填)* | 函数应该做什么。尽量具体，说明参数和期望输出 |
| `runtime` | `Runtime` | *(必填)* | Runtime 实例。用于生成代码，也注入到生成的函数中 |
| `name` | `str \| None` | `None` | 覆盖生成函数的名称。`None` = 使用 LLM 选择的名称 |
| `as_skill` | `bool` | `False` | 是否同时创建 SKILL.md |

### 返回值

- `callable` — 一个标准的 `@agentic_function`，具备完整的 Context 追踪。
- `dict` — `{"type": "follow_up", "question": "..."}` 当 LLM 信息不足需要提问时。

### 异常

| 异常 | 原因 |
|------|------|
| `SyntaxError` | 生成的代码有语法错误 |
| `ValueError` | 代码包含不允许的 import、使用 async、执行失败、或没有定义 `@agentic_function` |

---

## Function: `fix()`

```python
@agentic_function
def fix(
    fn,
    runtime: Runtime,
    instruction: str = None,
    name: str = None,
    max_rounds: int = 5,
)
```

当已有函数运行失败、输出格式不稳定、或你想做定向改写时，用 `fix()`。它会自动从 `fn` 中提取源码、函数名，以及最近 Context 树里的错误 / retry 历史，再交给 LLM 重写。

### 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `fn` | `callable` | *(必填)* | 要修复的函数对象。通常是 `create()` 生成的函数，也可以是手写的 `@agentic_function` |
| `runtime` | `Runtime` | *(必填)* | 用来分析与重写函数的 Runtime |
| `instruction` | `str \| None` | `None` | 额外修复要求，例如"改成返回 JSON" |
| `name` | `str \| None` | `None` | 覆盖修复后函数的名称 |
| `max_rounds` | `int` | `5` | 最多允许多少轮重写 |

### 返回值

- `callable` — 修复后的函数。
- `dict` — `{"type": "follow_up", "question": "..."}` 当 LLM 信息不足需要提问时。

### 异常

| 异常 | 原因 |
|------|------|
| `SyntaxError` | 修复后的代码仍有语法错误 |
| `ValueError` | 修复后的代码包含不允许的 import、async、或无法执行 |
| `RuntimeError` | 超过 `max_rounds` 仍未得到可编译代码 |

---

## Function: `improve()`

```python
@agentic_function
def improve(
    fn,
    runtime: Runtime,
    goal: str = "general improvement",
    name: str = None,
)
```

优化现有函数：改进 prompt、增强健壮性、清理代码等。

### 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `fn` | `callable` | *(必填)* | 要改进的函数对象 |
| `runtime` | `Runtime` | *(必填)* | 用来分析与改写函数的 Runtime |
| `goal` | `str` | `"general improvement"` | 优化目标，如"更好的 prompt"、"更健壮" |
| `name` | `str \| None` | `None` | 覆盖改进后函数的名称 |

### 返回值

- `callable` — 改进后的函数。
- `dict` — `{"type": "follow_up", "question": "..."}` 当 LLM 信息不足需要提问时。

---

## 安全机制

生成的代码在受限环境中执行：

| 限制 | 说明 |
|------|------|
| 仅允许白名单 import | 只能导入框架预先允许的一小部分标准库模块；像 `subprocess`、第三方包这类导入会被拦截 |
| 禁止 async | 只允许同步函数 |
| 受限 builtins | 没有 `exec`、`eval`、`open` 等危险能力；`__import__` 也会经过白名单校验 |
| 语法校验 | 执行前先编译检查 |

---

## 使用方式

### 基本用法

```python
from agentic import Runtime
from agentic.meta_functions import create

runtime = Runtime(call=my_llm, model="sonnet")

# 用描述创建函数
summarize = create(
    "Summarize text into 3 bullet points. Take a 'text' parameter.",
    runtime=runtime,
)

# 像普通函数一样调用
result = summarize(text="Long article about AI...")
print(result)
```

### fix 修复

```python
from agentic.meta_functions import create, fix

analyze = create("Analyze sentiment of text", runtime=runtime)

try:
    result = analyze(text="This is great!")
except Exception:
    analyze = fix(
        fn=analyze,
        runtime=runtime,
        instruction="Return exactly one word: positive, negative, or neutral.",
    )
    result = analyze(text="This is great!")
```

### improve 优化

```python
from agentic.meta_functions import improve

better_analyze = improve(
    fn=analyze,
    runtime=runtime,
    goal="让 prompt 更精确，输出格式更稳定",
)
```

### 处理 follow_up

当 LLM 信息不足时，create / fix / improve 会返回 follow_up 而非函数：

```python
result = fix(fn=broken_func, runtime=runtime)

if isinstance(result, dict) and result.get("type") == "follow_up":
    # LLM 需要更多信息
    print(f"LLM asks: {result['question']}")
    # 将答案通过 instruction 传回
    result = fix(fn=broken_func, runtime=runtime, instruction=result["question"] + ": ...")
else:
    # 正常返回函数
    fixed_func = result
```

### `fix()` 会自动拿到什么

`fix(fn=..., runtime=...)` 会自动收集：

- `fn` 的源码（若可读）
- `fn.__doc__` / 名称，用来恢复原始意图
- `fn.context` 里的失败记录，包括 retry attempts 和异常信息
- 你额外传入的 `instruction`

### create + fix + retry 模式

```python
def create_with_retry(description, runtime, sample_kwargs, attempts=3):
    fn = create(description, runtime=runtime)

    for _ in range(attempts):
        if isinstance(fn, dict):  # follow_up
            break
        try:
            fn(**sample_kwargs)
            return fn
        except Exception:
            fn = fix(
                fn=fn,
                runtime=runtime,
                instruction="Make the output schema explicit and validate edge cases.",
            )

    return fn
```

这个模式适合"先生成，再用真实样例验证，不对就继续修"的工作流。

---

## 架构

```
create(description) ──→ generate_code(task) ──→ extract/validate/compile/save
fix(fn, instruction) ──→ generate_code(task) ──→ extract/validate/compile/save
improve(fn, goal)    ──→ generate_code(task) ──→ extract/validate/compile/save
```

`generate_code()` 是底层 meta function：
- docstring 包含完整的 Agentic Programming 设计规范
- 通过 function dispatch 支持 `follow_up()` 提问
- 返回 `{"type": "code", "content": "..."}` 或 `{"type": "follow_up", "question": "..."}`

Docstring 去重：基于函数名，同一个函数的 docstring 只发送给 LLM 一次。
