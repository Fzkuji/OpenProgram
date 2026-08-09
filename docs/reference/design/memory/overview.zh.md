# 记忆子系统

OpenProgram 怎么让 agent 跨会话"记住"事情。

> 本文覆盖记忆子系统的全貌。实体层的 git 底座见
> [`git-as-entity-memory.zh.md`](git-as-entity-memory.zh.md) 与
> [`entity-memory.zh.md`](entity-memory.zh.md)。
>
> 路径约定：所有状态在 `~/.openprogram/`（即 `get_state_dir()`）；
> 具名 profile 用 `~/.openprogram-<profile>/`。

## 为什么需要它

原始 LLM 在会话结束时忘掉一切。每开一个新对话都要用户重讲同样的事
（"我是产品经理，别用黑话"、"项目在 `~/Projects/foo`"）。记忆子系统把
聊完的对话写进持久文件，再把相关部分喂回去。

两个我们在意的性质：

1. **模型不用问就拿到该知道的事。**用户还没重复，稳定的偏好和项目事实
   已经在提示词里了。
2. **存储可读可审。**记忆就是纯 Markdown，编辑器能开、Git 能 diff。每条
   陈述都带脚注指向它出自哪条消息，所以任何可疑的内容都能追回原话。

## 磁盘上的三层

```
<state>/memory/
    core.md                  常驻块，每个会话都注入
    topics/                  可编辑的语义记忆
        people/dave.md
        projects/budget-tracker.md
    sources/                 只追加的证据，由运行时写入
        openprogram/<session-id>.md
    timeline/                派生的时间轴，每次写入后重建
        2026/08/09.md
    recent_events.jsonl      派生
    relations.json           派生
    .scriptorium/            运行时状态：游标、写锁、历史
```

**sources** 是说过的话，原样归档、从不编辑。**topics** 是这些话的含义
——一个人、一个项目、一个反复出现的主题各一个文件。每个 topic 段落以
稳定的 `^block-id` 结尾，并引用一条脚注：

```markdown
Craig is building a budget tracker in Flask, due 2024-04-15.[^e-1175dea39c] ^f888f60e

[^e-1175dea39c]: Time: `2024-03-15`; Sources: [openprogram/sess-7f2a/msg_2f9b](../sources/openprogram/sess-7f2a.md#source-8339b8d3)
```

块 ID 是其他视图和链接找到这个段落的凭据，编辑和移动都不改变它。脚注是
把一条陈述追回原话的路径。

`timeline/`、`recent_events.jsonl`、`relations.json` 是派生的，每次成功
写入后从 topics 重建。手改它们没有意义。

## 什么时候写

不在对话过程中写。轮次先攒着，攒到值得一次模型调用（约 16000 token）
才让模型写。每轮都写意味着每轮一次模型调用，而且写出来的记忆会长得像
逐字稿，不像知识。

三个触发点：

| 触发 | 位置 | 做什么 |
|---|---|---|
| 一轮结束 | `provider.sync_turn` | 会话过线才写 |
| 会话空闲 | `session_watcher` | 把剩下的写掉，不论多少 |
| 每天 03:00 | `scheduler` | 重新整理 topic 文件 |

对话内容是从会话存储里读回来的，不在进程里缓存。那个存储持久且有序，
所以一轮的身份能挺过 worker 重启，`runtime/online.py` 里的游标才能判断
哪些已经写过。用模块级缓冲的话，重启即丢，而且给出的位置在两次运行之间
会变——游标恰恰无法容忍这一点。

每次只取达到阈值的前若干轮，不是整个积压：跑了一整天的会话，积压量远超
一次模型调用装得下的规模。

## 夜间整理为什么必要

写入只会让文件变长，没有任何环节让它变短。放着不管，工作区会变成一个
主题一个巨型文件、时间线被主题切碎——正是这个形态让排序类和计数类问题
答不出来。03:00 的整理会拆开已经涵盖多个主题的文件、合并重复的段落、
修复链接。

也可以随时手动跑：`openprogram memory sleep`。

## 模型看到什么

- **每个会话**：`core.md`，包在 `<memory-context>` 围栏里注入，这样回忆
  出来的事实不会被误当成用户此刻在说的话。
- **每一轮**：`prefetch` 针对这条消息找到的内容——对块和来源做 BM25
  检索，取前五条，同样加围栏。
- **按需**：`memory_*` 工具。

## 工具

| 工具 | 用途 |
|---|---|
| `memory_search` | 按语义找段落 |
| `memory_grep` | 找确切的名字、ID 或短语 |
| `memory_get` | 读一个文件、一节，或带脚注的单个块 |
| `memory_browse` | 看有什么 |
| `memory_update` | 以 unified diff 更正或新增某一处 |
| `memory_status` | 规模与当前版本号 |

没有"保存这个"的工具。记录对话是后台写入器的职责。`memory_update` 是给
两种情况用的：用户明确要求现在记住的事，以及模型看得出记错了的地方。

## 写入是事务性的

一次 `memory_update` 同时带上证据和引用它的编辑，并对照调用方读到的版本
号校验。引用了未提供的来源、链接到不存在的块、或破坏 topic 格式的补丁会
被整体拒绝，工作区一个字节都不变。派生视图只在成功安装之后才重建。

一把跨进程锁（`.scriptorium/write.lock`）把写入者串行化，所以后台写入和
聊天中的写入不会交错。后台写入拿锁只等一秒，拿不到就放弃而不是让用户
等着；下一轮会再来。

## 代码地图

包分成契约和它的一个实现。

```
openprogram/memory/           框架侧
    provider.py               MemoryProvider —— 契约
    __init__.py               get_provider() / set_provider()
    store.py                  记忆位置；从旧布局的迁移
    scheduler.py              守护线程，03:00 维护
    session_watcher.py        空闲会话收尾
    scriptorium/              随包提供的实现
        provider.py           满足契约
        writing.py            累积、写入、整理
        management/           写入事务、暂存、校验
        retrieval/            BM25 与向量检索
        markdown/             topic 格式
        prompts/              对写入模型说的话
        runtime/              游标、阈值、派生视图
        agent_runtime/        实际执行写入的进程
```

agent 循环、工具、网页端、CLI 都不指名任何实现，一律调 `get_provider()`。
换记忆系统就是写一个满足 `MemoryProvider` 的类，让 `get_provider()` 返回它。
`set_provider()` 是受支持的入口，测试也用它。

写入跑在用户自己的登录和默认模型上，所以后台记忆不需要另配凭证。
`openprogram memory sleep --model` 和 `scheduler.start_in_worker(model=...)`
可以覆盖。

## 从旧记忆层迁移

工作区位置没变，已有安装还在原地找到记忆。变的是里面的东西：`journal/`
和 `wiki/` 没有了，换成 `sources/` 和 `topics/`；`core.md` 不变。

首次使用时，`store.ensure()` 把 `journal/`、`wiki/`、`.state/`、
`index.sqlite` 移到 `<state>/memory-superseded/`。是移动不是删除，而且移到
同级目录而非子目录：留在工作区里仍然会被列出来，而为了腾地方给新格式就
删掉别人的笔记，那不叫迁移。

## 失效模式

| 失效 | 后果 |
|---|---|
| 没有可用的写入进程 | 推迟并重试；对话安全地留在会话存储里 |
| 写到一半模型不可达 | 该轮整体回滚；游标不前进，同样的内容会重试 |
| 锁被别的写入者占着 | 跳过这次；下一轮再试 |
| 手改破坏了格式 | `openprogram memory edit` 会先校验并报告，再重建视图 |

记忆绝不会把对话一起拖垮：每个 provider 钩子都自己吞掉异常并记日志。

## 插件点

`MemoryProvider`（`provider.py`）是记忆与 agent 运行时之间的接口：

| 钩子 | 何时调用 |
|---|---|
| `system_prompt_block()` | 会话开始 |
| `prefetch(query)` | 每轮之前 |
| `sync_turn(user, assistant, session_id=)` | 每轮之后 |
| `on_session_end(messages, session_id=)` | 会话边界 |
| `on_pre_compress(messages)` | 上下文压缩前 |
| `maintain(**kwargs)` | 每晚 |
| `get_tool_schemas()` / `handle_tool_call()` | 可选的额外工具 |

每个都有默认实现，所以一个实现只需要写它真正有事可做的那几个。
