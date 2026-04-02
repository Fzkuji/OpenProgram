# Agentic Context — Design Draft

> 为 LLM 设计的上下文管理系统，借鉴 Python logging 架构。

---

## 1. 设计动机

Python 的 `logging` 解决了一个问题：**程序运行时产生的信息，怎么收集、过滤、格式化、分发？**

我们面对一个平行问题：**Agentic Function 执行时产生的上下文，怎么收集、过滤、格式化、注入给 LLM？**

关键区别：
- Python log → 给**人**看，事后调试
- Agentic Context → 给 **LLM** 看，**实时影响行为**

但架构可以一样。

---

## 2. 核心概念

### 2.1 Python Logging → Agentic Context 映射

| Python Logging | Agentic Context | 说明 |
|---|---|---|
| `Logger` | `Context` | 按函数/模块层级命名 |
| `LogRecord` | `Record` | 单条上下文记录 |
| `Level` | `Level` | 粒度控制 |
| `Handler` | `Handler` | 记录送往哪里 |
| `Formatter` | `Formatter` | 怎么呈现 |
| `Filter` | `Filter` | 过滤规则 |
| `propagate` | `propagate` | 子 → 父传播（自动降级） |

### 2.2 与传统 Scope/Memory/Session 的关系

之前我们分别设计了 Scope（可见性）、Memory（执行日志）、Session（对话历史）。
Agentic Context 把它们统一了：

| 旧概念 | 在 Context 中的体现 |
|--------|-------------------|
| Scope | Context 的 Level + Filter（控制 LLM 能看到什么） |
| Memory | FileHandler（把 Record 存到磁盘） |
| Session History | LLMHandler（把 Record 注入 LLM prompt） |
| Two-Layer Session | propagate + 自动降级（子 TRACE → 父 SUMMARY） |

---

## 3. Level（粒度级别）

```
TRACE    = 10    # 最详细：中间过程、原始数据、LLM reasoning
DETAIL   = 20    # 完整输入输出
SUMMARY  = 30    # 压缩版（一句话描述）
RESULT   = 40    # 只有返回值
SILENT   = 50    # 不产生任何记录
```

**类比 Python：**
```
TRACE   ≈ DEBUG      详细调试信息
DETAIL  ≈ INFO       标准运行信息
SUMMARY ≈ WARNING    重要摘要
RESULT  ≈ ERROR      关键结果
SILENT  ≈ CRITICAL   静默
```

**每个函数设定两个 level：**
- `self_level`：自己产生什么粒度的记录（默认 DETAIL）
- `propagate_level`：向父级传播时降级到什么粒度（默认 SUMMARY）

---

## 4. Record（上下文记录）

```python
@dataclass
class Record:
    """一条上下文记录。"""
    
    timestamp: str              # ISO 时间
    context_name: str           # 产生者（如 "navigate.observe"）
    level: int                  # TRACE / DETAIL / SUMMARY / RESULT
    
    # 内容（至少一个非空）
    text: str = ""              # 文本内容
    data: dict = None           # 结构化数据（JSON）
    media: list[str] = None     # 媒体路径（截图等）
    
    # 元信息
    record_type: str = "info"   # "call", "return", "observation", "decision", "error"
    parent_id: str = None       # 父 Record ID（形成调用树）
    duration_ms: float = None   # 耗时
```

**record_type 类别：**

| 类型 | 说明 | 示例 |
|------|------|------|
| `call` | 函数被调用 | `observe(task="find login")` |
| `return` | 函数返回 | `{target_visible: true}` |
| `observation` | 中间观察 | 截图、OCR 结果、检测结果 |
| `decision` | LLM 推理 | "发现按钮在右上角，准备点击" |
| `error` | 错误 | "template_match 失败" |

---

## 5. Context（上下文管理器）

```python
class Context:
    """
    类比 Python 的 Logger。
    
    每个 Agentic Function 有一个 Context，按层级命名。
    
    层级示例：
        "navigate"
        "navigate.observe"
        "navigate.observe.ocr"
        "navigate.act"
    """
    
    name: str                       # 层级名（如 "navigate.observe"）
    level: int = DETAIL             # 自身记录级别
    propagate_level: int = SUMMARY  # 向父传播的级别
    propagate: bool = True          # 是否向父传播
    handlers: list[Handler]         # 记录处理器
    filters: list[Filter]           # 过滤器
    parent: Context = None          # 父 Context
    children: dict[str, Context]    # 子 Context
```

### 5.1 `getContext()` — 获取/创建 Context

```python
# 类比 logging.getLogger(__name__)
ctx = context.getContext("navigate.observe")

# 自动创建层级：
#   root → navigate → navigate.observe
# 如果 "navigate" 不存在，自动创建
```

### 5.2 记录方法

```python
ctx.trace("OCR found 77 text items", data={"items": ocr_results})
ctx.detail("Screenshot captured", media=["screenshot.png"])
ctx.summary("Found login button at (347, 291)")
ctx.result({"target_visible": True, "location": [347, 291]})

# 带类型的记录
ctx.call(function_name="observe", params={"task": "find login"})
ctx.returns(result={"elements": [...], "target_visible": True})
ctx.error("Template match failed: button not found")
```

### 5.3 传播机制

```
navigate.observe 产生 Record(level=TRACE, text="OCR: 77 items")
│
├── observe 自己的 handlers 收到（level >= TRACE，处理）
│
├── propagate=True → 向 navigate 传播
│   但传播时 level 自动升级为 propagate_level=SUMMARY
│   所以 navigate 收到的是 Record(level=SUMMARY, text="observe: found 156 elements")
│
└── navigate 的 handlers 判断：SUMMARY >= 自己的 level？是 → 处理
```

**降级转换：**
传播时，Record 不是直接传，而是经过一个 **summarizer**：

```python
class Context:
    def _propagate(self, record: Record):
        if self.parent and self.propagate:
            # 降级：生成 SUMMARY 版本
            summary_record = self.summarize(record)
            summary_record.level = self.propagate_level
            self.parent._handle(summary_record)
```

`summarize` 可以是：
- **静态规则**：截断到 N 字符
- **模板**：`"{function}: {result_summary}"`
- **LLM 生成**：调用 LLM 压缩（开销大，按需）

---

## 6. Handler（处理器）

```python
class Handler(ABC):
    """决定 Record 送往哪里。"""
    level: int = TRACE  # 只处理 >= 这个级别的记录
    
    @abstractmethod
    def emit(self, record: Record):
        pass
```

### 6.1 内置 Handler

| Handler | 目标 | 说明 |
|---------|------|------|
| `LLMHandler` | LLM prompt | **核心**。把 Record 格式化后注入 LLM 的对话历史 |
| `FileHandler` | 磁盘文件 | 持久化，等价于原来的 Memory |
| `ConsoleHandler` | 终端 | 给人看的调试输出 |
| `CallbackHandler` | 回调函数 | MCP 通知、WebSocket 推送等 |

**LLMHandler 是最重要的**，它决定了 LLM 在下一次 send() 时看到什么：

```python
class LLMHandler(Handler):
    """
    将 Record 注入 LLM Session 的对话历史。
    
    这是 Agentic Context 跟传统 logging 的最大区别：
    传统 log 是"写下来给人看"，
    LLMHandler 是"写进去影响 LLM 行为"。
    """
    
    def __init__(self, session, level=SUMMARY, formatter=None):
        self.session = session      # 目标 LLM Session
        self.level = level
        self.formatter = formatter or DefaultFormatter()
    
    def emit(self, record: Record):
        # 格式化 Record → 文本/多模态
        message = self.formatter.format(record)
        # 注入到 Session 的历史中
        self.session.inject_context(message)
```

### 6.2 配置示例

```python
# 场景：navigate 函数
#   - 自己的 LLM 只看 SUMMARY 级别
#   - 文件记录所有 TRACE
#   - 控制台打印 DETAIL

navigate_ctx = context.getContext("navigate")
navigate_ctx.addHandler(LLMHandler(session=programmer_session, level=SUMMARY))
navigate_ctx.addHandler(FileHandler("logs/run.jsonl", level=TRACE))
navigate_ctx.addHandler(ConsoleHandler(level=DETAIL))
```

---

## 7. Formatter（格式化器）

```python
class Formatter(ABC):
    """决定 Record 怎么呈现给目标。"""
    
    @abstractmethod
    def format(self, record: Record) -> str | dict:
        pass
```

### 7.1 内置 Formatter

| Formatter | 输出 | 适用 |
|-----------|------|------|
| `TextFormatter` | 纯文本 | Console, CLI LLM |
| `JSONFormatter` | JSON | File, API LLM |
| `CompactFormatter` | 压缩文本 | 节省 token |
| `MarkdownFormatter` | Markdown | 人类可读 |
| `MultimodalFormatter` | 文本+图片 | 支持图片的 LLM |

**给 LLM 的格式跟给人的格式应该不同：**

```python
# 给人看（ConsoleHandler）
"[20:30:15] observe → found 156 elements, target visible ✓"

# 给 LLM 看（LLMHandler + JSONFormatter）
{"function": "observe", "result": {"elements": 156, "target_visible": true}}

# 给 LLM 看（LLMHandler + CompactFormatter）  
"observe: 156 elements, target=visible"
```

---

## 8. Filter（过滤器）

```python
class Filter:
    """决定哪些 Record 要处理。"""
    
    def filter(self, record: Record) -> bool:
        """返回 True 表示处理这条记录。"""
        return True
```

### 8.1 内置 Filter

```python
# 只看特定函数的记录
NameFilter("navigate.observe")

# 只看特定类型
TypeFilter(["call", "return"])  # 只看函数调用和返回

# 只看有媒体的记录
MediaFilter()  # 只要有截图的

# Token 预算限制
TokenBudgetFilter(max_tokens=2000)  # 超过预算就丢弃旧的
```

**TokenBudgetFilter 是 LLM 特有的**：传统 log 不关心大小，但 LLM 有 context window 限制。

---

## 9. 完整示例：GUI Agent

```python
from agentic import context
from agentic.context import TRACE, DETAIL, SUMMARY, RESULT
from agentic.context.handlers import LLMHandler, FileHandler
from agentic.context.formatters import CompactFormatter

# ── 设置 ──

# navigate 的 Context
nav_ctx = context.getContext("navigate")
nav_ctx.level = SUMMARY
nav_ctx.addHandler(LLMHandler(programmer_session, level=SUMMARY))
nav_ctx.addHandler(FileHandler("logs/run.jsonl", level=TRACE))

# observe 的 Context（navigate 的子）
obs_ctx = context.getContext("navigate.observe")
obs_ctx.level = TRACE
obs_ctx.propagate_level = SUMMARY  # 向 navigate 传播时降级

# ── 执行 ──

def observe(task: str):
    """Look at the screen and find all visible UI elements."""
    
    ctx = context.getContext("navigate.observe")
    ctx.call("observe", params={"task": task})
    
    # Python Runtime
    img = take_screenshot()
    ctx.trace("screenshot captured", media=[img])
    
    ocr = run_ocr(img)
    ctx.trace("OCR results", data={"count": len(ocr), "items": ocr})
    
    elements = detect_all(img)
    ctx.trace("detection results", data={"count": len(elements)})
    
    # Agentic Runtime
    prompt = observe.__doc__
    worker = create_session("sonnet")
    reply = worker.send(f"{prompt}\nOCR: {ocr}\nElements: {elements}", images=[img])
    ctx.detail("LLM analysis", data={"reply": reply})
    
    result = parse_result(reply)
    ctx.returns(result)
    
    return result

    # 这时候发生了什么：
    # 1. FileHandler 收到所有 TRACE 记录（完整日志）
    # 2. navigate 的 LLMHandler 收到 SUMMARY：
    #    "observe: found 156 elements, target visible"
    # 3. programmer_session 的 context 里多了一条简短记录
    # 4. 下次 navigate 调 LLM 时，它能看到这个摘要


def navigate(target: str, max_steps: int = 5):
    """Navigate to the target by observing and acting."""
    
    ctx = context.getContext("navigate")
    ctx.call("navigate", params={"target": target})
    
    for step in range(max_steps):
        # observe — 内部产生 TRACE，传播到这里变 SUMMARY
        obs = observe(task=f"find {target}")
        
        if obs.target_visible:
            # act — 同理
            result = act(target=target, observation=obs)
            ctx.returns({"success": True, "steps": step + 1})
            return result
    
    ctx.returns({"success": False, "steps": max_steps})
```

---

## 10. 与 Python Logging 的关键区别

| | Python Logging | Agentic Context |
|---|---|---|
| **目标受众** | 人类（事后调试） | LLM（实时影响行为） |
| **大小限制** | 无（磁盘够就行） | 有（context window） |
| **传播降级** | 不降级（原样传） | **自动降级**（TRACE→SUMMARY） |
| **格式化** | 纯文本为主 | 多模态（文本+图片+JSON） |
| **时效性** | 写了就不管 | 需要主动管理（裁剪、压缩） |
| **TokenBudget** | 不存在 | **必须**（否则 context 爆炸） |
| **Summarizer** | 不需要 | 核心机制（传播时压缩） |

---

## 11. 配置方式

### 11.1 代码配置（类比 `logging.basicConfig()`）

```python
import agentic.context as context

# 快速配置：所有 Context 输出到文件 + LLM
context.basicConfig(
    level=SUMMARY,
    handlers=[
        LLMHandler(session),
        FileHandler("logs/run.jsonl"),
    ]
)
```

### 11.2 声明式配置（类比 `logging.config.dictConfig()`）

```python
context.config({
    "contexts": {
        "navigate": {
            "level": "SUMMARY",
            "propagate_level": "RESULT",
            "handlers": ["llm", "file"],
        },
        "navigate.observe": {
            "level": "TRACE",
            "propagate_level": "SUMMARY",
            "handlers": ["file"],
        },
    },
    "handlers": {
        "llm": {"class": "LLMHandler", "session": "programmer", "level": "SUMMARY"},
        "file": {"class": "FileHandler", "path": "logs/", "level": "TRACE"},
    },
})
```

---

## 12. 开放问题

1. **Summarizer 谁来做？**
   - 静态模板（快但粗糙）
   - LLM 调用（好但费 token）
   - 让函数自己定义（灵活但需要额外工作）

2. **TokenBudget 怎么管？**
   - 滑动窗口（丢弃最旧的）
   - 优先级队列（保留重要的）
   - LLM 压缩（把 10 条 summary 压成 1 条）

3. **图片怎么传播？**
   - 不传播（图片太大，只在本层处理）
   - 缩略图传播
   - 描述文本传播（LLM 把图片变成文字描述）

4. **跟 MCP 怎么集成？**
   - MCP tool 调用自动产生 `call` Record
   - MCP tool 返回自动产生 `return` Record
   - Context 配置可以通过 MCP 暴露

---

*Draft v0.1 — 2026-04-02*
