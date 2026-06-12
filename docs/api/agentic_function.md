# agentic_function

> Source: [`openprogram/agentic_programming/function.py`](../../openprogram/agentic_programming/function.py)

`@agentic_function` 把普通 Python 函数变成 Agentic Function:每次调用记录为 session DAG 的一个 `code` 节点,函数体内的 `runtime.exec` 调用记录为 `llm` 节点。

完整的编写规范——文件布局、docstring 与 `content` 的分工、参数元数据、校验清单、冒烟测试——见 [`skills/agentic-programming/SKILL.md`](../../skills/agentic-programming/SKILL.md)。本文只列装饰器本身。

## 用法

```python
from openprogram import agentic_function

@agentic_function
def f(x: str, runtime) -> str:
    """One-line summary of what f does."""
    return runtime.exec(content=[{"type": "text", "text": f"...{x}..."}])
```

裸用 `@agentic_function` 或带参数 `@agentic_function(...)` 都可以。

## 装饰器参数

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `expose` | `str` | `"io"` | **朝外**:别人渲染 DAG 时能看到我的什么。`"io"` = 本函数的 input/output 节点对外可见,内部直接的 LLM 调用对外隐藏;`"llm"` = 反过来,只露内部 LLM 交换,藏 input/output;`"full"` = 全可见;`"hidden"` = 根本不写 DAG 节点 |
| `render_range` | `dict` | `None` | **朝内**:本函数内部 `runtime.exec` 拼 prompt 时,从 DAG 读多少历史节点。形状 `{"callers": N, "subcalls": M}`,两个数字都是 **节点计数(按 `seq` 切片)**:<br>• `callers` — 本函数 frame **启动前**的节点,取最近 N 个(`None` 默认 = 不限,`0` = 全墙)<br>• `subcalls` — 本函数 frame **启动后**已写入的节点,取最近 N 个(`-1` 默认 = 不限,frame 自然看见自己的进度;`N>=0` = 只想截 prompt 时显式设;`0` = 完全墙掉 in-frame)<br>`{"callers":0,"subcalls":0}` = 跟外界和自己 frame 全断绝 |
| `input` | `dict` | `None` | 每个参数的 UI 元数据(`description` / `placeholder` / `multiline` / `options` / `hidden` 等),WebUI 据此渲染输入表单 |
| `workdir_mode` | `str` | `None` | 工作目录选择器模式:`"optional"` / `"hidden"` / `"required"`,其余值报错。消费方是 WebUI——它通过 AST 解析源码文本读取,所以必须以字面量写在装饰器调用里才生效 |
| `system` | `str` | `None` | 本函数 LLM 调用的 system prompt(调用期间盖到注入的 runtime 上,调用后恢复) |

函数名、参数名 / 类型 / 默认值、一句话摘要都从函数签名和 docstring 自动读取,不在装饰器里重复(见 SKILL.md §3)。

## 记录到 DAG

- **进入函数**:写一个 `code` 节点(`output=None`, `status="running"`),函数 docstring 一并存进该节点的 `metadata.doc`,渲染上下文时拼在 `函数名(参数)` 前面。
- **函数体内 `runtime.exec`**:每次调用写一个 `llm` 节点。
- **退出函数**:回填同一个 `code` 节点的 `output` / `status`。

`expose="hidden"` 时不写任何节点。standalone 运行(没安装 DAG store)时记录全部 no-op,函数照常执行。
