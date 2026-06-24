# Runtime — 设计理由

API 用法见 [`../api/runtime.md`](../../api/runtime.md)。本文档只讲**为什么**
Runtime 长成这样、哪些备选方案被否决、哪些权衡是有意识做的。

## 1 runtime = 1 session

每个 `Runtime` 实例绑定一个 provider session,生命周期 1:1:

```
create_runtime()      = 开 session
runtime.exec()        = session 里发一次请求
runtime.close()       = 关 session
```

不提供 `reset()` / `new_session()`,要新 session 就再 `create_runtime()`。

**为什么不让一个 Runtime 复用多 session?**

- CLI provider(Claude Code / Codex / Gemini CLI)的 session 状态在子进程里。
  复用就要在 Runtime 上挂"当前哪个 session id"这种可变状态,引入并发竞态。
- API provider 本身无状态,做"多 session"也只是上层 dict,跟"多个 Runtime"
  等价,没新功能。
- ContextVar 自动注入(下条)依赖"runtime 跟当前函数树绑定"这个简单模型;
  多 session 会让注入语义复杂化。

代价:用户想跑两套独立对话要管两个 runtime 对象。可以接受,因为这种场景少。

## ContextVar 自动注入 runtime

`@agentic_function` 装饰器读 `_current_runtime` ContextVar,如果当前函数
没传 `runtime=` 参数就用它;入口函数也没有,就自动 `create_runtime()`。

**为什么不让函数显式声明 runtime?**

显式声明的话每个 agentic function 都得在签名里加 `runtime: Runtime`,
而且每次嵌套调用都得显式传 `runtime=runtime` 透传——纯粹是 plumbing
样板代码,跟函数逻辑无关。ContextVar 把它隐去:子函数自然继承父函数的
runtime,入口处自动起一个,出口处自动关。

**为什么不用 module-level singleton?**

singleton 跨线程 / 跨协程共享,两个并发 agent 会互相踩 session 状态。
ContextVar 按线程 + 协程隔离,天然并发安全。

## Session-provider vs API-provider 共用一套抽象

无论底层是 Claude Code CLI(有 session)还是 Anthropic API(无 session),
对 `@agentic_function` 作者都是一样的接口 `runtime.exec(content=[...])`。
框架靠 `has_session` 属性区分两类 provider 在内部走不同路径:

| | session provider (CLI) | API provider |
|---|---|---|
| 对话记忆 | 子进程自己管 | 无,每次 exec 独立 |
| 上下文注入 | 跳过 DAG render,只发 docstring + 当次 content | 通过 `render_context` + `render_dag_messages` 从 DAG 拼历史 |
| `render_range.subcalls` | 不生效(session 自己记得对话) | 生效(用来限制注入历史的窗口) |

**为什么不分两套独立的 Runtime 类?**

作者写 `gui_agent` 时不应该关心后端是哪种 provider。强行分开就要每个函数
两份实现,违背"函数描述任务、provider 描述执行通道"的分层。共用抽象的
代价是 `has_session` 这点条件分支,值得。

## Retry 在 runtime 层,不在 provider 层

`exec()` / `async_exec()` 内置 `max_retries` 默认 2,任何 provider 都
享受重试。

**为什么不在每个 provider 类里各自加 retry?**

- 重试策略对所有 provider 一致(网络超时 / 速率限制 / 5xx),没必要重复
- 失败报告统一格式(`Attempt N: ErrorType: msg`),便于排查
- `TypeError` / `NotImplementedError` 这类编程错误统一不重试(只有
  runtime 层知道"这是 provider 实现 bug,重试也没用")

provider 层只关心"把请求发出去、把回复拿回来"。重试 / 节流 / 缓存这种
横切关注点全在 runtime。

## DAG 写入:进出函数都写 code 节点,exec 写 llm 节点

```
进入 @agentic_function       → 写一个 code 节点 (status=running)
                              → 设 _call_id ContextVar 指向此节点
函数体里 runtime.exec()      → 在当前 _call_id 下写一个 llm 节点
                              → 节点的 called_by = _call_id
退出函数(return / except)     → 回填同一 code 节点的 output / status
```

`expose="hidden"` 时跳过 code 节点写入(但 `_call_id` 仍设了一个 phantom
id,以便函数体内 LLM 调用有 frame 可参照)。

**为什么不只在退出时写一个完成态节点?**

- 函数还在跑时 webui visualizer 需要立刻能看到"它在跑"(显示 spinner)
- 异常退出时也要有节点存在(才能记错误信息)

写两次(entry + exit 回填)比写一次(完成时)更适合实时可观察。

## 相关实现文件

- `openprogram/agentic_programming/runtime.py` — Runtime 基类、`exec` / `_call` 协议、retry 循环
- `openprogram/agentic_programming/function.py` — 装饰器 / `_inject_runtime` / `_call_id` / `_current_runtime` ContextVar
- `openprogram/providers/__init__.py` — `detect_provider` / `create_runtime` 自动检测
- `openprogram/providers/<vendor>/runtime.py` — 各 provider 的 `_call` 实现
- `openprogram/context/nodes.py` `render_context` — DAG → reads 计算(`render_range` 的实际语义)
- `openprogram/context/render.py` `render_dag_messages` — reads → provider messages 转换
