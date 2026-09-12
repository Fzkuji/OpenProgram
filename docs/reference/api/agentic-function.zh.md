# agentic_function

> Source: [`openprogram/agentic_programming/function.py`](https://github.com/Fzkuji/OpenProgram/blob/main/openprogram/agentic_programming/function.py)

`@agentic_function` 把普通 Python 函数变成 Agentic Function:每次调用记录为 session DAG 的一个 `code` 节点,函数体内的 `llm()` 调用记录为 `llm` 节点。

本文定义 agentic function 的装饰器和编写规范。

## 用法

```python
from openprogram import agentic_function
from openprogram.agentic_programming import llm

@agentic_function
def f(x: str, runtime) -> str:
    """One-line summary of what f does."""
    return llm([{"type": "text", "text": f"...{x}..."}])
```

裸用 `@agentic_function` 或带参数 `@agentic_function(...)` 都可以。

## 装饰器参数

### Agentic 专属参数

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `expose` | `str` | `"io"` | **朝外**:别人渲染 DAG 时能看到我的什么。`"io"` = 对外只露函数名和返回值,内部(LLM 交换、子调用)隐藏;`"llm"` = 反过来,只露内部 LLM 交换,藏函数自己的名字/返回值和嵌套的 code 子调用;`"full"` = 全可见(docstring + 参数 + 输出 + LLM 回复 + 内部);`"hidden"` = 根本不写 DAG 节点。其余值在装饰时抛 `ValueError` |
| `render_range` | `dict` | `None` | **朝内**:本函数内部 `llm()` 拼 prompt 时,从 DAG 读多少历史节点。形状 `{"callers": N, "subcalls": M}`,两个数字都是 **节点计数(按 `seq` 切片)**:<br>• `callers` — 本函数 frame **启动前**的节点,取最近 N 个(`None` 默认 = 不限,`0` = 全墙)<br>• `subcalls` — 本函数 frame **启动后**已写入的节点,取最近 N 个(`-1` 默认 = 不限,frame 自然看见自己的进度;`N>=0` = 只想截 prompt 时显式设;`0` = 完全墙掉 in-frame)<br>`{"callers":0,"subcalls":0}` = 跟外界和自己 frame 全断绝 |
| `input` | `dict` | `None` | 每个参数的 UI 元数据,WebUI 据此渲染输入表单。每个参数支持的字段:`description`(参数名旁的标签)、`placeholder`(示例文字)、`multiline`(`True` = textarea)、`options`(允许值列表,渲染为下拉框并写进 JSON-schema `enum`)、`hidden`(`True` = 从表单和 LLM 工具 schema 里排除) |
| `system` | `str` | `None` | 本函数 LLM 调用的 system prompt(调用期间盖到注入的 runtime 上,调用后恢复) |

### 工具注册参数

每个 `@agentic_function` 还会注册进共享注册表(`openprogram.programs`),成为 LLM 可调用的工具,与 `@function` 装饰的工具并列。以下参数控制这次注册,名字和语义与 `@function` 一致:

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
- **函数体内 `llm()`**:每次调用写一个 `llm` 节点。
- **退出函数**:回填同一个 `code` 节点的 `output` / `status`。

`expose="hidden"` 时不写任何节点。standalone 运行(没安装 DAG store)时记录全部 no-op,函数照常执行。

## 可恢复步骤与代码版本

为同步编排函数声明 `@agentic_function(resumable=True)`，将外部操作放入 `step("稳定名称", 操作函数, *args, **kwargs)`。步骤输入和结果必须可保存为 JSON；继续执行时，已完成步骤直接返回保存的结果，不再调用操作函数。重复名称按出现次数区分，已执行步骤的顺序和输入必须保持兼容。

嵌套编排使用 `workflow("名称", 函数, *args, **kwargs)`。并行编排使用 `parallel({"分支名": (函数, args, kwargs)})`；各分支独立保存进度，所有分支停止后才释放任务所有权。

设置项 `execution.code_change_policy` 默认是 `keep_original`，继续使用保存的原函数和 Python 辅助函数源码；`use_latest` 保留已完成步骤的结果，并用当前代码执行后续兼容步骤。任务的“继续”操作可以覆盖此设置。A 已采用 B 后，再更新到 C 时选择保留原代码，会继续 B；尚未通过已有进度兼容性检查的候选版本不会取代已采用版本。新代码需要转换局部状态时，应在剩余步骤之前通过纯编排代码转换已保存的 JSON。已记录步骤被删除、重排、输入不兼容或固定依赖不可用时，任务保持暂停。权限和工具参数约定仍然需要通过兼容性检查。

手动调用恢复原执行记录；聊天调用将最终结果交给原来的待完成工具调用。重启导致的中断沿用两小时自动继续期限。主动暂停、取消、未回答的授权请求以及结果不确定的外部操作不会自动重试。

此功能在显式步骤处恢复，不保存任意 Python 调用栈。步骤外的外部调用和状态修改、异步编排、生成器、活动句柄及非 JSON 结果不支持此恢复约定。外部操作已经开始、但结果尚未保存时进程退出，必须先核对操作结果。函数运行界面显示是否声明可恢复步骤，普通函数中断后需要重新运行。

步骤之间不得依赖可变的 Python 全局变量、闭包或默认参数。需要持续保存的状态应通过步骤的 JSON 输入和结果传递，进程内变量修改不能作为恢复依据。
