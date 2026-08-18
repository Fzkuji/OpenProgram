# 嵌入到你自己的技术栈

你已经有了自己的应用、信得过的 LLM 客户端、以及放状态的地方。你想从
OpenProgram 拿走的，是自己重写成本最高的那部分——`@agentic_function` 和把嵌套调用
变成上下文的执行 DAG——除此之外什么都不要。不启动 Web UI，不启动终端 UI，不走 CLI，
也不要背着你在 `~/.openprogram` 里写东西。

这就是嵌入模式：把 OpenProgram 当作一个普通 Python 库，用在别人的运行时里面。
下面五步使用假客户端离线执行，不需要 API key。

## 什么时候该用这个模式

当外层产品是你自己在建——一个服务、你自己的 agent 框架、一套研究 harness——而你只想把
DAG 上下文函数调用当作其中一个组件时，就用嵌入。如果你要的是整套 harness（聊天界面、
会话、provider 管理、工具），那就正常跑平台，见
[快速开始](../../start/GETTING_STARTED.zh.md)。

## 1. 自带 LLM 调用

集成面就是一个函数：

```python
fn(content: list[dict], model: str, response_format: dict | None) -> str
```

`content` 是一串块，每块形如 `{"type": "text", "text": ...}`（也可以是图像块）。
把模型回复作为字符串返回。你原本用的客户端就写在里面：

```python
from openai import OpenAI

client = OpenAI()

def openai_call(content, model="gpt-4o-mini", response_format=None):
    text = "\n".join(b["text"] for b in content if b["type"] == "text")
    reply = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": text}],
        response_format=response_format,
    )
    return reply.choices[0].message.content
```

## 2. 用 Runtime 包起来

```python
from openprogram import Runtime

runtime = Runtime(call=openai_call, model="gpt-4o-mini")
```

`Runtime(call=...)` 直接接收你的函数：不做 provider 探测，不读凭证，构造时不发任何网络
请求。（另一个入口 `create_runtime()` 会按本机配置的 provider 构建 runtime——写独立脚本
时方便，但恰恰是嵌入方不想要的。见 [Providers](../../models/providers.zh.md)。）

四个嵌入入口——`agentic_function`、`Runtime`、`decision`、`Session`——从包根部
re-export，且是懒加载的：光 `import openprogram` 不会拉起 runtime、store 或任何 provider。

## 3. 写你的函数

docstring 就是发给模型的指令。调用会嵌套，DAG 记录谁调用了谁——这正是后续调用拿到的上下文。

```python
from openprogram import agentic_function

@agentic_function
def classify_sentiment(review, runtime=None):
    """Classify the sentiment of a review as positive, negative, or neutral."""
    return runtime.exec(f"Sentiment of this review: {review}")


@agentic_function
def summarize_review(review, runtime=None):
    """Summarize a customer review in one sentence, noting its sentiment."""
    sentiment = classify_sentiment(review, runtime=runtime)
    return runtime.exec(f"Summarize this {sentiment} review: {review}")
```

runtime 只传给**最外层**那次调用。它会沿调用链自动向下传播，嵌套函数转发一下就行。
最外层不传，入口函数就会按本机配置的 provider 自建 runtime，那会去摸凭证和网络。

[编写函数](writing-functions/agentic-function.zh.md)里讲的一切原样适用——装饰器在嵌入
环境里的行为和在平台里完全一致。

## 4. 把会话状态指到你选的目录

`SessionStore(root_path=...)` 是显式目录入口。传了它，就不会去查 `~/.openprogram`。
每个会话是该根目录下的一个 git 仓库。

```python
from openprogram.store import SessionStore, session_scope

store = SessionStore(root_path="/var/lib/myapp/sessions")
store.create_session("review-42", agent_id="main")

with session_scope(store, "review-42"):
    summary = summarize_review(review, runtime=runtime)
```

`session_scope` 在块内把 DAG 写入路由到这个 store，退出时恢复之前的绑定。不进这个
`with` 块同样合法：函数照常执行，只是什么都不持久化——自己维护 trace 的宿主要的就是这个。

## 5. 读回 DAG，或者接自家工具循环

**读回 DAG。** 节点带角色（code / llm / user）、名字、输入、输出，以及一条 `caller` 边。
顺着 `caller` 就能把扁平节点列表还原成调用树：

```python
from openprogram.store import SessionNodeWriter

graph = SessionNodeWriter(store, "review-42").load()
for node in graph:
    kind = "fn" if node.is_code() else "llm" if node.is_llm() else "usr"
    print(kind, node.name, "→", node.output)
```

**把函数交给你自己的循环。** 每个 `@agentic_function` 都提供 `.spec`（由签名和 docstring
生成的 JSON schema）和 `.execute`。`to_openai_tools` 把 spec 重塑成 Chat Completions
`tools=[...]` 数组要的形状：

```python
import json
from openprogram.agentic_programming.tool_format import to_openai_tools

tools = to_openai_tools([classify_sentiment, summarize_review])

reply = client.chat.completions.create(model=..., messages=..., tools=tools)
for call in reply.choices[0].message.tool_calls:
    fn = {"classify_sentiment": classify_sentiment}[call.function.name]
    result = fn.execute(runtime=runtime, **json.loads(call.function.arguments))
```

`runtime` 和模型给的参数并列传进去。模型永远看不到这个参数——它被从 spec 里过滤掉了——
也永远不会去填它。

## 与完整安装的关系

嵌入属于开发者集成模式，不是面向普通用户的精简版本。应使用完整源码开发安装；其
Web、终端、provider、channel、search、browser 和第一方 Program 基线与 release 相同。
嵌入路径只调用 library API，不会启动 Web UI 或 TUI；`tests/embed/` 验收测试约束这一
import 边界，但已安装的产品功能仍保持完整。

## 宿主集成接缝

有前端的宿主可以通过两个 hook 接进来：`set_cancellation_check` 装上 exec 循环在每次
LLM 尝试前调用的检查点（在里面抛异常即可中止本次运行），`set_session_id_provider` 告诉核心
问题该回传给哪个会话。两者都在 `openprogram.agentic_programming.function` 里，默认是
headless 空实现——所以完全不碰它们的嵌入代码行为也是对的：没有任何东西会取消，
`runtime.can_ask()` 报 `False`，因为没人在那边回答。

## 延伸阅读

- [`@agentic_function`](writing-functions/agentic-function.zh.md) —— 装饰器详解
- [Runtime API](../../reference/api/runtime.zh.md) —— `exec()` 的参数与行为
- [嵌入接缝](../../reference/design/runtime/overview.zh.md) —— 这个模式背后的设计契约
