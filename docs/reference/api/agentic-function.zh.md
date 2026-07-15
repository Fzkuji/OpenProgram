# agentic_function

> Source: [`openprogram/agentic_programming/function.py`](https://github.com/Fzkuji/OpenProgram/blob/main/openprogram/agentic_programming/function.py)

`@agentic_function` 把普通 Python 函数变成 Agentic Function:每次调用记录为 session DAG 的一个 `code` 节点,函数体内的 `runtime.exec` 调用记录为 `llm` 节点。

完整的编写规范——文件布局、docstring 与 `content` 的分工、参数元数据、校验清单、冒烟测试——见 [`skills/agentic-programming/SKILL.md`](https://github.com/Fzkuji/OpenProgram/blob/main/skills/agentic-programming/SKILL.md)。本文只列装饰器本身。

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

### Agentic 专属参数

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `expose` | `str` | `"io"` | **朝外**:别人渲染 DAG 时能看到我的什么。`"io"` = 对外只露函数名和返回值,内部(LLM 交换、子调用)隐藏;`"llm"` = 反过来,只露内部 LLM 交换,藏函数自己的名字/返回值和嵌套的 code 子调用;`"full"` = 全可见(docstring + 参数 + 输出 + LLM 回复 + 内部);`"hidden"` = 根本不写 DAG 节点。其余值在装饰时抛 `ValueError` |
| `render_range` | `dict` | `None` | **朝内**:本函数内部 `runtime.exec` 拼 prompt 时,从 DAG 读多少历史节点。形状 `{"callers": N, "subcalls": M}`,两个数字都是 **节点计数(按 `seq` 切片)**:<br>• `callers` — 本函数 frame **启动前**的节点,取最近 N 个(`None` 默认 = 不限,`0` = 全墙)<br>• `subcalls` — 本函数 frame **启动后**已写入的节点,取最近 N 个(`-1` 默认 = 不限,frame 自然看见自己的进度;`N>=0` = 只想截 prompt 时显式设;`0` = 完全墙掉 in-frame)<br>`{"callers":0,"subcalls":0}` = 跟外界和自己 frame 全断绝 |
| `input` | `dict` | `None` | 每个参数的 UI 元数据,WebUI 据此渲染输入表单。每个参数支持的字段:`description`(参数名旁的标签)、`placeholder`(示例文字)、`multiline`(`True` = textarea)、`options`(允许值列表,渲染为下拉框并写进 JSON-schema `enum`)、`hidden`(`True` = 从表单和 LLM 工具 schema 里排除) |
| `workdir_mode` | `str` | `None` | 工作目录选择器模式:`"optional"` / `"hidden"` / `"required"`,其余值抛 `ValueError`。消费方是 WebUI——它通过 AST 解析源码文本读取,所以必须以字面量写在装饰器调用里才生效 |
| `system` | `str` | `None` | 本函数 LLM 调用的 system prompt(调用期间盖到注入的 runtime 上,调用后恢复) |

### 工具注册参数

每个 `@agentic_function` 还会注册进共享注册表(`openprogram.functions`),成为 LLM 可调用的工具,与 `@function` 装饰的工具并列。以下参数控制这次注册,名字和语义与 `@function` 一致:

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `as_tool` | `bool` | `True` | 注册为 LLM 可调用工具。`False` = 只能 Python 直接调用 |
| `name` | `str` | `None` | 工具名覆盖。默认取函数的 `__name__` |
| `description` | `str` | `None` | 工具描述覆盖。默认取函数 docstring |
| `parameters` | `dict` | `None` | JSON-schema 参数覆盖。默认由签名类型注解加 `input` 元数据自动生成(runtime 注入参数和 `hidden` 参数被排除) |
| `label` | `str` | `None` | 工具 UI 里显示的可读标签 |
| `toolset` | `tuple` | `()` | 本工具所属的工具集名(供 `exec(toolset=...)` 预设使用) |
| `unsafe_in` | `tuple` | `()` | 在哪些渠道来源下视为不安全并被过滤 |
| `check_fn` | `Callable` | `None` | 逐次调用的门禁:分发前调用,返回假值则拦下 |
| `requires_env` | `tuple` | `()` | 必须设置的环境变量名,缺了就不提供该工具 |
| `can_use` | `Callable` | `None` | 工具解析时求值的动态可用性谓词 |
| `max_result_chars` | `int` | `None` | 回喂给模型的工具结果截断上限。`None` = 注册表默认 `DEFAULT_MAX_RESULT_CHARS`(30,000 字符) |
| `persist_full` | `bool` | `False` | 把未截断的完整结果落盘,供 agent 回读 |
| `head_ratio` | `float` | `None` | 截断时头部保留的比例,其余留尾部。`None` = 注册表默认 `DEFAULT_HEAD_RATIO`(0.7) |
| `requires_approval` | — | `None` | 转发给工具注册表的审批要求(与 `@function` 同形) |
| `cache` | `bool` | `False` | 工具分发调用按 `(name, args)` 记忆化结果 |
| `cache_ttl` | `float` | `300.0` | `cache=True` 时的缓存寿命(秒) |
| `timeout` | `float` | `None` | 工具分发调用的硬性墙钟超时(秒),到点模型收到错误结果 |
| `available_if` | `Callable` | `None` | 导入时门禁:返回假值(或抛异常)则整个装饰器跳过,模块级名字保持普通函数——不包 wrapper、不注册 |
| `defer` | `bool` | `False` | 注册为延迟工具(schema 按需加载,不随每次调用发送) |
| `register_globally` | `bool` | `True` | `False` = 构建工具但不进全局注册表 |

函数名、参数名 / 类型 / 默认值、一句话摘要都从函数签名和 docstring 自动读取,不在装饰器里重复(见 SKILL.md §3)。

## Runtime 注入

名为 `runtime`、`exec_runtime` 或 `review_runtime` 的参数会自动注入:调用方没传(或传 `None`)时,先从当前调用链取 runtime;作为入口调用时经 `create_runtime()`(自动检测)新建,函数返回时再关闭。一个函数可以声明多个 runtime 参数,全部填同一个 runtime。这些参数不会出现在 LLM 工具 schema 和 WebUI 表单里。

## 自省与安全

- `fn.spec` — 自动生成的 JSON-schema 工具 spec(`{"name", "description", "parameters"}`);`fn.execute(**kwargs)` 用 LLM 提供的 kwargs 调用 wrapper。
- 自递归兜底:函数自我重入超过 5 层抛 `RecursionError`(注入的情境 prompt 也会引导模型不要自调用)。
- 预调用钩子(`add_pre_invocation_hook` / `remove_pre_invocation_hook`)在每次调用开头运行,可以抛 `CancelledError` 中止本次调用(WebUI 的停止按钮就是这么实现的)。

## 记录到 DAG

- **进入函数**:写一个 `code` 节点(`output=None`, `status="running"`),函数 docstring 一并存进该节点的 `metadata.doc`,渲染上下文时拼在 `函数名(参数)` 前面。
- **函数体内 `runtime.exec`**:每次调用写一个 `llm` 节点。
- **退出函数**:回填同一个 `code` 节点的 `output` / `status`。

`expose="hidden"` 时不写任何节点。standalone 运行(没安装 DAG store)时记录全部 no-op,函数照常执行。
