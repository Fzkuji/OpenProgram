# Agentic Context

> 用 `@agentic_function` 装饰器自动追踪调用栈。用户写普通 Python，框架在后台全部自动记录。

---

## Context

一个函数的执行记录。**所有字段自动管理，用户不需要手动设置。**

```python
@dataclass
class Context:
    # === 由 @agentic_function 自动记录 ===
    name: str                    # 函数名（从 __name__）
    prompt: str = ""             # docstring（从 __doc__）
    params: dict = None          # 调用参数（从 *args/**kwargs）
    output: Any = None           # 返回值（从 return）
    error: str = ""              # 错误信息（从异常）
    status: str = "running"      # running / success / error
    children: list = None        # 子函数的 Context（自动挂载）
    parent: Context = None       # 父 Context（自动设置）
    start_time: float = 0        # 开始时间
    end_time: float = 0          # 结束时间
    expose: str = "summary"      # 对外暴露粒度
    
    # === 由 runtime.exec 自动记录 ===
    input: dict = None           # 发给 LLM 的文本数据
    media: list[str] = None      # 发给 LLM 的媒体文件路径
    raw_reply: str = ""          # LLM 原始回复
    
    # === 方法 ===
    
    def summarize(
        level: str = None,       # 覆盖粒度
        max_tokens: int = None,  # token 预算限制
        max_siblings: int = None,# 最多包含几个兄弟
        include_parent: bool = True,
    ) -> str:
        """生成到目前为止的上下文摘要。"""
        ...
```

---

## @agentic_function

```python
from agentic import agentic_function, runtime

@agentic_function
def observe(task):
    """Look at the screen and find all visible UI elements."""
    img = take_screenshot()
    ocr = run_ocr(img)
    reply = runtime.exec(
        prompt=observe.__doc__,
        input={"task": task, "ocr": ocr},
        images=[img],
    )
    return parse(reply)
```

装饰器自动做：
1. 记录 `name`（= `observe`）、`prompt`（= docstring）、`params`（= `{"task": task}`）
2. 创建 Context 节点，挂到当前父节点
3. 函数结束后记录 `output` / `error` / `status` / 耗时
4. 恢复父节点为当前层

**用户不需要知道 Context 的存在。写普通 Python 就行。**

---

## runtime.exec

框架提供的 Agentic Runtime 调用接口。自动把调用信息记录到当前 Context。

```python
from agentic import runtime

reply = runtime.exec(
    prompt="Look at the screen...",    # 指令（通常是 docstring）
    input={"task": task},              # 数据
    images=["screenshot.png"],         # 图片路径
    model="sonnet",                    # 模型
    call=my_api_fn,                    # 自定义 LLM 调用函数（可选）
)
```

自动做两件事：
1. 如果没传 `context` 参数，自动调 `ctx.summarize()` 生成上下文摘要
2. 把 `input`、`images`、LLM 原始回复记录到当前 Context

`call` 参数让你用任何 LLM provider：
```python
# 用自己的 session
runtime.exec(prompt=..., call=lambda msgs, model: session.send(msgs))

# 用 OpenAI
runtime.exec(prompt=..., call=lambda msgs, model: openai_call(msgs, model))
```

---

## expose（暴露粒度）

控制 `summarize()` 中这个函数的信息量。通过 decorator 设置：

```python
@agentic_function                      # 默认 expose="summary"
def observe(task): ...

@agentic_function(expose="detail")     # 暴露完整输入输出
def observe(task): ...

@agentic_function(expose="silent")     # 不出现在摘要中
def internal_helper(x): ...
```

| expose | summarize() 中的内容 |
|--------|---------------------|
| `"trace"` | prompt + 完整输入输出 + LLM 原始回复 |
| `"detail"` | 完整输入和输出 |
| `"summary"` | 一句话摘要（默认） |
| `"result"` | 只有返回值 |
| `"silent"` | 不出现 |

---

## 完整示例

```python
from agentic import agentic_function, runtime

@agentic_function
def run_ocr(img):
    """Extract text from screenshot using OCR."""
    texts = ocr_engine.detect(img)
    return {"texts": texts, "count": len(texts)}

@agentic_function
def detect_all(img):
    """Detect all UI elements in screenshot."""
    elements = detector.detect(img)
    return {"elements": elements, "count": len(elements)}

@agentic_function
def observe(task):
    """Look at the screen and find all visible UI elements.
    Check if the target described in task is visible."""
    img = take_screenshot()
    ocr = run_ocr(img)                 # 自动成为 observe 的 child
    elements = detect_all(img)          # 自动成为 observe 的 child
    reply = runtime.exec(               # 自动记录 input/media/reply
        prompt=observe.__doc__,
        input={"task": task, "ocr": ocr, "elements": elements},
        images=[img],
    )
    return parse(reply)

@agentic_function
def act(target, location):
    """Click the specified target at the given location."""
    click(location)
    return {"clicked": True, "target": target}

@agentic_function
def navigate(target):
    """Navigate to the target by observing and acting."""
    obs = observe(task=f"find {target}")        # 自动成为 navigate 的 child
    if obs["target_visible"]:
        result = act(target=target, location=obs["location"])  # 自动成为 child
        return {"success": True}
    return {"success": False}

# 运行 — 就是普通的函数调用
navigate("login")
```

执行后 Context 树：

```
navigate ✓ 3200ms → {success: True}
├── observe ✓ 1200ms → {target_visible: True, location: [347, 291]}
│   ├── run_ocr ✓ 50ms → {texts: [...], count: 3}
│   └── detect_all ✓ 80ms → {elements: [...], count: 3}
└── act ✓ 820ms → {clicked: True}
```

---

## Traceback

报错时自动生成：

```
Agentic Traceback:
  navigate(target="login") → error, 4523ms
    observe(task="find login") → success, 1200ms
    act(target="login") → error, 820ms: "element not interactable"
```

---

## 持久化

```python
from agentic import get_root_context

root = get_root_context()
root.save("logs/run.jsonl")    # 机器可读
root.save("logs/run.md")       # 人类可读
```

---

## 总结

1. **`@agentic_function`** — 装饰器，自动追踪调用栈（name, params, output, error, timing, children）
2. **`runtime.exec`** — Agentic Runtime 调用，自动记录 input/media/reply，自动注入上下文摘要
3. **`expose`** — 控制对外暴露粒度（trace/detail/summary/result/silent）
4. **用户写普通 Python** — 不需要知道 Context 的存在
