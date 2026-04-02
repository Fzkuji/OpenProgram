# Agentic Context — v2 Design (Call Stack Model)

> 像 Python 有 Traceback，Agentic Programming 有 Agentic Context。

---

## 1. 核心类比

Python 运行时维护一个 **调用栈（Call Stack）**：

```
main()
  → navigate("login")
    → observe(task="find login")
      → detect_all(screenshot)    ← 当前执行位置
```

每一帧（Frame）包含：
- 函数名、参数
- 局部变量
- 返回地址

**Agentic Programming 也需要一个调用栈**，但每一帧额外包含 LLM 相关信息：

```
main()
  → navigate("login")
    prompt: "Navigate to target..."
    model: sonnet
    → observe(task="find login")
      prompt: "Look at the screen..."
      model: sonnet
      input: [screenshot.png, OCR: 77 items]
      output: {target_visible: true}
      → detect_all(screenshot)
        (pure Python, no LLM)
```

---

## 2. Frame（栈帧）

Python 的栈帧有局部变量。我们的栈帧有 LLM 的输入输出。

```python
@dataclass
class Frame:
    """一个 Agentic Function 的执行帧。"""
    
    # === 基本信息（等价于 Python Frame）===
    function_name: str          # 函数名
    params: dict                # 调用参数
    parent: Frame | None        # 调用者
    children: list[Frame]       # 被调用的子函数
    
    # === Agentic 扩展（LLM 相关）===
    prompt: str = ""            # docstring（指令）
    model: str = ""             # 使用的模型
    input_data: dict = None     # 发给 LLM 的数据（OCR、检测结果等）
    input_media: list[str] = None  # 发给 LLM 的图片
    output: Any = None          # LLM 的返回（解析后）
    raw_reply: str = ""         # LLM 的原始回复
    
    # === 执行状态 ===
    status: str = "running"     # running / success / error
    error: str = ""             # 错误信息
    start_time: float = 0
    end_time: float = 0
    
    @property
    def duration_ms(self) -> float:
        return (self.end_time - self.start_time) * 1000
    
    @property
    def is_agentic(self) -> bool:
        """是否调用了 LLM（有些函数是纯 Python）。"""
        return bool(self.prompt)
```

---

## 3. Stack（调用栈）

```python
class Stack:
    """
    Agentic 调用栈。
    
    每个任务（run）有一个 Stack。
    函数开始时 push，结束时 pop。
    跟 Python 的调用栈行为一致。
    """
    
    root: Frame               # 根帧（整个任务）
    current: Frame            # 当前执行的帧
    
    def push(self, function_name: str, params: dict) -> Frame:
        """函数开始执行，压栈。"""
        frame = Frame(
            function_name=function_name,
            params=params,
            parent=self.current,
        )
        self.current.children.append(frame)
        self.current = frame
        return frame
    
    def pop(self, output=None, error=None) -> Frame:
        """函数执行完毕，弹栈。"""
        self.current.output = output
        self.current.error = error
        self.current.status = "error" if error else "success"
        frame = self.current
        self.current = self.current.parent
        return frame
```

---

## 4. Traceback（回溯）

Python 报错时打印 Traceback。我们也一样：

### 4.1 错误时的完整 Traceback

```
Agentic Traceback (most recent call last):

  Frame: navigate(target="login button")
    prompt: "Navigate to the target by observing and acting..."
    model: sonnet | status: error | 4523ms
    
    Frame: observe(task="find login button")
      prompt: "Look at the screen and find all visible UI elements..."
      model: sonnet | status: success | 1200ms
      input: screenshot.png + OCR(77 items) + detect(106 elements)
      output: {target_visible: true, location: [347, 291]}
    
    Frame: act(target="login button", location=[347, 291])
      prompt: "Click the specified target..."
      model: sonnet | status: error | 820ms
      input: template_match → (347, 291)
      error: "Element not interactable — covered by overlay"

AgenticError: act() failed — Element not interactable — covered by overlay
```

### 4.2 正常执行时的摘要视图

```
navigate(target="login button") ✓ 4523ms
├── observe(task="find login") ✓ 1200ms → {target_visible: true}
├── act(target="login", loc=[347,291]) ✓ 820ms → {clicked: true}
└── verify(expected="logged in") ✓ 650ms → {verified: true}
```

### 4.3 用代码生成

```python
class Stack:
    def traceback(self, level="summary") -> str:
        """
        生成可读的回溯信息。
        
        level:
          "summary" — 一行一个函数（正常运行时）
          "detail"  — 显示 prompt、input、output
          "trace"   — 显示所有中间数据 + raw reply
        """
        ...
    
    def traceback_for_llm(self, level="summary") -> str:
        """
        生成给 LLM 看的回溯信息。
        
        格式化为 LLM 容易理解的结构。
        可以注入到下一次 LLM 调用中。
        """
        ...
```

---

## 5. 给 LLM 看的调用栈（核心创新）

Python 的 traceback 只在报错时给人看。

**我们的调用栈在正常运行时也要给 LLM 看** — 因为 LLM 需要知道之前发生了什么才能做出正确决策。

### 5.1 场景：navigate 调用 act

navigate 要调用 act，它需要告诉 act 的 LLM：
- observe 已经找到目标了（SUMMARY）
- 目标在 (347, 291)（RESULT）

**不需要**告诉 act 的 LLM：
- observe 的 OCR 有 77 个 text item（TRACE — 太细了）
- observe 的 prompt 是什么（不相关）

### 5.2 Level 控制粒度

```python
class Level:
    TRACE   = 10   # 所有中间数据、raw reply、prompt
    DETAIL  = 20   # 完整输入输出
    SUMMARY = 30   # 一句话摘要（"observe found 156 elements"）
    RESULT  = 40   # 只有返回值
    SILENT  = 50   # 不暴露任何信息
```

**每个函数声明：**
- `self_level`：自己内部记录的粒度（默认 DETAIL）
- `expose_level`：向调用者（或兄弟函数）暴露的粒度（默认 SUMMARY）

### 5.3 构建 LLM 输入

当一个 Agentic Function 要调用 LLM 时，它的输入由以下部分组装：

```python
def build_llm_input(frame: Frame, stack: Stack) -> list[dict]:
    """
    为当前 Frame 的 LLM 调用组装 messages。
    """
    messages = []
    
    # 1. System prompt（固定）
    messages.append({
        "role": "system",
        "content": "You are a helpful assistant..."
    })
    
    # 2. 调用栈上下文（已完成的兄弟函数的摘要）
    parent = frame.parent
    if parent:
        for sibling in parent.children:
            if sibling is frame:
                break  # 只看之前的兄弟
            if sibling.status != "running":
                summary = sibling.summarize(level=frame.expose_level)
                messages.append({
                    "role": "user",
                    "content": f"[Prior step] {summary}"
                })
                messages.append({
                    "role": "assistant",
                    "content": "Understood."
                })
    
    # 3. 当前 prompt + 输入数据
    content = []
    content.append({"type": "text", "text": frame.prompt})
    if frame.input_data:
        content.append({"type": "text", "text": json.dumps(frame.input_data)})
    if frame.input_media:
        for img in frame.input_media:
            content.append({"type": "image", "path": img})
    
    messages.append({"role": "user", "content": content})
    
    # 4. 输出格式要求
    if frame.return_schema:
        messages.append({
            "role": "user", 
            "content": f"Return JSON matching: {frame.return_schema}"
        })
    
    return messages
```

---

## 6. Frame.summarize() — 降级机制

一个 Frame 在不同 level 下的表现：

```python
# observe 函数执行完毕后：

frame.summarize(Level.TRACE)
# → "observe(task='find login')
#    prompt: 'Look at the screen...'
#    input: screenshot.png, OCR: ['Login', 'Password', ...共77项]
#    raw_reply: '{"elements": [...], "target_visible": true}'
#    output: ObserveResult(elements=[...156项], target_visible=True)
#    duration: 1200ms"

frame.summarize(Level.DETAIL)
# → "observe(task='find login')
#    input: screenshot + 77 OCR items + 106 detected elements
#    output: {target_visible: true, location: [347, 291], element_count: 156}
#    duration: 1200ms"

frame.summarize(Level.SUMMARY)
# → "observe: found 156 elements, target 'login' visible at (347, 291)"

frame.summarize(Level.RESULT)
# → {target_visible: true, location: [347, 291]}
```

**SUMMARY 怎么生成？**

三种策略（由简到复杂）：

1. **模板**（默认，零成本）
   ```python
   f"{function_name}: {one_line_description_of_output}"
   ```

2. **用户自定义**（函数作者写）
   ```python
   def observe(...):
       """..."""
       ...
       return result, summary="found {n} elements, target {'visible' if found else 'not found'}"
   ```

3. **LLM 生成**（昂贵，按需）
   ```python
   summary = llm.send(f"Summarize this execution in one sentence: {frame.detail()}")
   ```

---

## 7. 持久化（存盘 = Memory）

整个 Stack 可以序列化存到文件，这就是之前的 Memory：

```python
class Stack:
    def save(self, path: str, format="jsonl"):
        """把完整调用栈存到文件。"""
        for frame in self.walk():  # 深度优先遍历
            record = {
                "function": frame.function_name,
                "params": frame.params,
                "prompt": frame.prompt,
                "model": frame.model,
                "input": frame.input_data,
                "output": frame.output,
                "status": frame.status,
                "error": frame.error,
                "duration_ms": frame.duration_ms,
                "depth": frame.depth,  # 调用深度
                "parent": frame.parent.function_name if frame.parent else None,
            }
            write_jsonl(path, record)
    
    def save_readable(self, path: str):
        """生成人类可读的 Markdown 版本。"""
        # 就是 traceback(level="detail") 写到 .md 文件
        ...
```

**输出：**
```
logs/run_20260403_003000/
├── stack.jsonl      ← 机器可读（完整调用栈）
├── stack.md         ← 人类可读（Markdown traceback）
└── media/
    └── screenshot_001.png
```

---

## 8. 与传统概念的统一

| 之前的概念 | 现在是什么 |
|-----------|----------|
| Scope | Frame 的 `expose_level`（向外暴露的粒度） |
| Memory | Stack 的 `save()`（持久化到文件） |
| Session History | Stack 的 `build_llm_input()`（组装 LLM messages） |
| Two-Layer Session | 父 Frame 只看子 Frame 的 SUMMARY |

**全部统一到一个调用栈模型里。**

---

## 9. 用法

### 9.1 最简用法（不需要框架）

```python
# 你可以完全不用我们的框架，手动管理
def observe(task: str):
    """Look at the screen..."""
    img = take_screenshot()
    ocr = run_ocr(img)
    prompt = observe.__doc__
    reply = llm_call(prompt, input={"task": task, "ocr": ocr}, images=[img])
    return parse(reply)
```

### 9.2 用 Stack 追踪

```python
from agentic import Stack

stack = Stack()

def observe(task: str):
    """Look at the screen..."""
    frame = stack.push("observe", {"task": task})
    frame.prompt = observe.__doc__
    
    img = take_screenshot()
    ocr = run_ocr(img)
    frame.input_data = {"ocr_count": len(ocr), "task": task}
    frame.input_media = [img]
    
    reply = llm_call(frame.prompt, input=frame.input_data, images=[img])
    result = parse(reply)
    
    stack.pop(output=result)
    return result

def navigate(target: str):
    """Navigate to the target..."""
    frame = stack.push("navigate", {"target": target})
    frame.prompt = navigate.__doc__
    
    obs = observe(task=f"find {target}")   # 自动成为子 Frame
    
    if obs.target_visible:
        result = act(target=target, location=obs.location)
        stack.pop(output=result)
        return result
    
    stack.pop(error="target not found")

# 执行完后：
print(stack.traceback())       # 给人看
stack.save("logs/run.jsonl")   # 存盘
```

### 9.3 用 Context Manager（更 Pythonic）

```python
from agentic import Stack

stack = Stack()

def observe(task: str):
    """Look at the screen..."""
    with stack.frame("observe", task=task) as f:
        f.prompt = observe.__doc__
        
        img = take_screenshot()
        ocr = run_ocr(img)
        f.input_data = {"ocr": ocr}
        f.input_media = [img]
        
        reply = llm_call(f.prompt, input=f.input_data, images=[img])
        result = parse(reply)
        f.output = result
        return result
    # 自动 pop，异常时自动记录 error
```

---

## 10. API 调用层

Stack 管调用链，但实际发 API 请求是另一个层：

```python
def llm_call(
    prompt: str,
    input: dict = None,
    images: list = None,
    context: str = None,    # 从 stack.traceback_for_llm() 来
    schema: dict = None,
    model: str = "sonnet",
) -> str:
    """
    最底层的 LLM API 调用。
    
    组装 messages → 调用 API → 返回文本。
    无状态，每次调用独立。
    """
    messages = []
    
    # Context（之前的调用摘要）
    if context:
        messages.append({"role": "user", "content": context})
        messages.append({"role": "assistant", "content": "Understood."})
    
    # Prompt + Input + Images
    content = [{"type": "text", "text": prompt}]
    if input:
        content.append({"type": "text", "text": json.dumps(input)})
    if images:
        for img in images:
            content.append({"type": "image", ...})
    messages.append({"role": "user", "content": content})
    
    # Schema
    if schema:
        messages.append({"role": "user", "content": f"Return JSON: {schema}"})
    
    # 调用 API
    return api.chat(model=model, messages=messages)
```

**注意**：`llm_call` 是无状态的。如果需要多轮对话，由调用者（或 Stack）管理历史并通过 `context` 参数传入。

---

## 11. 总结

```
Python 有什么          →  我们对应什么
─────────────────────────────────────
Call Stack             →  Stack
Stack Frame            →  Frame
Traceback              →  stack.traceback()
局部变量               →  frame.input_data / output
函数返回值             →  frame.output（只有这个对外可见）
sys.settrace()         →  stack.push() / stack.pop()
logging                →  stack.save()（持久化）
try/except             →  frame.error（错误捕获）
```

**核心原则：**
1. **调用栈是核心数据结构**（不是 log，不是 session）
2. **Frame 知道一切**（prompt、input、output、error）
3. **向外暴露只看 level**（TRACE/DETAIL/SUMMARY/RESULT）
4. **API 调用是无状态的**（Stack 管状态）
5. **可以不用框架**（手动管理完全可行，框架只是方便）

---

*Draft v2 — 2026-04-03*
