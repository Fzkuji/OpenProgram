# Context

> Source: [`agentic/context.py`](../../agentic/context.py)

执行记录。每次 `@agentic_function` 调用自动创建一个节点，节点通过 `parent/children` 形成树。

用户不需要手动创建或修改 Context 对象。

---

## Class: `Context`

```python
@dataclass
class Context
```

### 字段

**由 `@agentic_function` 设置（进入时）：**

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `name` | `str` | `""` | 函数名 |
| `prompt` | `str` | `""` | docstring（也是 LLM prompt） |
| `params` | `dict` | `{}` | 调用参数 |
| `parent` | `Context \| None` | `None` | 父节点 |
| `children` | `list[Context]` | `[]` | 子节点列表 |
| `render` | `str` | `"summary"` | 默认渲染级别 |
| `compress` | `bool` | `False` | 是否隐藏子节点 |
| `source_file` | `str` | `""` | 定义该函数的源码文件绝对路径，供可视化器重启后继续定位源码 |
| `start_time` | `float` | `0.0` | 开始时间戳 |

**由 `@agentic_function` 设置（退出时）：**

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `output` | `Any` | `None` | 返回值 |
| `error` | `str` | `""` | 错误信息 |
| `status` | `str` | `"running"` | `"running"` → `"success"` 或 `"error"` |
| `end_time` | `float` | `0.0` | 结束时间戳 |

**由 `Runtime.exec()` 设置：**

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `raw_reply` | `str \| None` | `None` | LLM 原始回复（`None` = 没调用过 LLM） |

---

## 属性

### `path`

```python
Context.path -> str
```

自动计算的树路径。格式：`{parent_path}/{name}_{index}`。

同名兄弟按顺序编号：`observe_0` 是第一个，`observe_1` 是第二个。

```python
"login_flow/observe_0"
"login_flow/navigate_0/click_0"
```

### `duration_ms`

```python
Context.duration_ms -> float
```

执行耗时（毫秒）。还在运行时返回 `0.0`。

---

## 方法

### `summarize()`

```python
Context.summarize(
    depth=-1,
    siblings=-1,
    level=None,
    include=None,
    exclude=None,
    branch=None,
    max_tokens=None,
) -> str
```

从 Context 树中提取文本，用于 LLM prompt 注入。`Runtime.exec()` 自动调用此方法。

#### 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `depth` | `int` | `-1` | 显示多少层祖先。`-1`=全部，`0`=不显示，`1`=只看父节点 |
| `siblings` | `int` | `-1` | 显示多少个之前的兄弟。`-1`=全部，`0`=不显示，`N`=最近 N 个 |
| `level` | `str \| None` | `None` | 覆盖所有节点的渲染级别 |
| `include` | `list[str] \| None` | `None` | 路径白名单，支持 `*` 通配符 |
| `exclude` | `list[str] \| None` | `None` | 路径黑名单，支持 `*` 通配符 |
| `branch` | `list[str] \| None` | `None` | 展开指定节点的子节点 |
| `max_tokens` | `int \| None` | `None` | token 预算，超出时丢弃最早的兄弟 |

#### 返回值

`str` — 可直接注入 LLM prompt 的文本。始终包含 execution context header 和当前调用信息。

#### 示例

```python
ctx.summarize()                              # 全部祖先 + 全部兄弟
ctx.summarize(depth=1, siblings=3)           # 父节点 + 最近 3 个兄弟
ctx.summarize(depth=0, siblings=0)           # 最小上下文（只有当前调用信息）
ctx.summarize(level="detail")               # 所有节点强制 detail 级别
ctx.summarize(include=["login/observe_0/*"]) # 只看 observe 的子节点
ctx.summarize(exclude=["login/click_0"])     # 排除 click
ctx.summarize(max_tokens=1000)              # 限制 token 数
```

---

### `tree()`

```python
Context.tree(indent=0) -> str
```

完整的树视图，用于调试。显示所有节点，不受 `render` 或 `compress` 影响。

#### 返回值

`str` — 人类可读的树结构。

#### 示例

```python
print(login_flow.context.tree())
```

输出：
```
login_flow ✓ 8800ms → dashboard verified
  observe ✓ 3100ms → found login form
  click ✓ 2500ms → clicked login button
  verify ✓ 3200ms → dashboard verified
```

---

### `traceback()`

```python
Context.traceback() -> str
```

错误追踪，格式类似 Python traceback。

#### 返回值

`str` — 错误链。

#### 示例

```python
# 当某个子函数出错时
print(login_flow.context.traceback())
```

输出：
```
Agentic Traceback:
  login_flow(username="admin") → error, 4523ms
    observe(task="find login") → success, 1200ms
    click(element="login") → error, 820ms
      error: element not interactable
```

---

### `save()`

```python
Context.save(path: str)
```

保存完整的 Context 树到文件。

| 扩展名 | 格式 |
|--------|------|
| `.md` | 人类可读（同 `tree()` 输出） |
| `.json` | 单个嵌套 JSON 对象，适合完整 roundtrip / 可视化恢复 |
| `.jsonl` | 每行一个 JSON 对象，包含所有字段 |

#### 示例

```python
login_flow.context.save("logs/run.jsonl")  # 机器可读
login_flow.context.save("logs/run.md")     # 人类可读
```

> **注意：** 顶层函数执行完后会自动保存到 `agentic/logs/`，通常不需要手动调用。
