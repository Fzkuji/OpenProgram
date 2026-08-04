# Distill——把会话变成可复用流程

一个会话记录了一次做成了的工作。蒸馏把这份记录转成下次能直接起步的产物：一份 `SKILL.md` 或一个 `@agentic_function`。

设计刻意做薄。新增的只有一个把会话渲染成文本的模块；其余全是既有基础设施，而"什么算好流程"的判断放在 skill 正文里，不写进代码。

## 组成

| 部件 | 位置 | 做什么 |
|---|---|---|
| 转写器 | `openprogram/store/session/transcript.py` | `render_session_transcript()`——把会话的一条分支渲染成 LLM 可读的纯文本 |
| 模型可调工具 | `openprogram/functions/tools/session_transcript/` | `session_transcript`——转写器的 `@function` 封装，缺省读当前会话 |
| distill skill | `openprogram/skills_bundled/distill/` | 指导 agent 怎么提取流程、怎么写出来 |
| 产品文档 | `docs/capabilities/distill.md` | 面向用户的说明 |

## 转写器

`get_branch` 返回的是对话链——由 `predecessor` 串起的 user/assistant 节点。工具调用和函数调用节点不在这条链上，它们通过 `caller` 挂在发起它们的那个 assistant 轮次上（[DAG overview](../dag/overview.md)）。转写器把两者合起来：走一遍分支，在每一轮下面打印 `caller` 指向它的那些调用。这和 `graph_builder` 给网页 DAG 用的是同一条边，只是读成散文而不是坐标。

签名：

```python
render_session_transcript(
    session_id,
    head_id=None,               # 缺省：会话的活跃 head
    include_function_calls=True,
    max_chars=60_000,
    store=None,                 # 缺省：agent.session_db.default_db()
) -> str
```

store 走 `default_db()` 而不是写死 `~/.openprogram` 路径，这样绑定到项目的会话能通过同一套定位逻辑解析。`store` 参数是给测试用的。

每条调用打印名字、成功或 `FAILED` 标记、截断后的参数、截断后的结果。失败的调用保留而不过滤：一次失败加上它的修正，正是"坑"在记录里的样子，也是蒸馏中最有价值的材料。

两类节点会被显式标注而不是当普通轮次处理——因为这两种情况下，读者会从表面内容得出错误结论：

- **压缩摘要**（`context/summary`）代表一段被折叠的范围。标注出来，读者才知道那里是细节被丢了，而不是会话本来就单薄。
- **spawn 分支根**开启一条子分支。标注出来，嵌套 agent 的工作才不会被当成主线读。

截断分两层。单字段上限防止一次失控的工具结果（读了个大文件）把周围的推理挤掉；总预算切在最后一个放得下的完整轮次上，并写明丢了几轮——被截断的转写不会看起来像完整的。

`tools/dag_dump.py` 仍是调试视图，看节点 id、lane、tier。两者不重叠。

## 会话发现复用已有工具

`list_sessions` 和 `list_branches`（`functions/tools/agent_collab/`）已经能列出会话 id 和 `SID:HEAD` 分支端点。这正是 `session_transcript` 要的参数，所以不新增列表工具。工具接受整串 `SID:HEAD` 传进 `session_id` 并自行拆分，因为那就是 `list_branches` 打印的形式。

## 判断放在 skill 里

蒸馏本身是判断任务：决定转写里哪些能泛化、哪些只属于那一天。这里没有任何确定性可言，所以不存在 `distill()` 函数——skill 正文告诉 agent 要提取什么（目标、前置条件、步骤、决策点、坑），它用自己的 `Write` 工具写出文件。

这沿用 agentic programming 里删掉 `create()` / `edit()` / `improve()` 封装时定下的先例：函数体全部内容就是一次 LLM 调用加一次文件写入的，这层 agent 不需要。

skill 也负责选产出形态。运行时需要判断的流程写成 `SKILL.md`；机械的流程按 [agentic-programming](../../function/calling-unification.zh.md) 的约定写成 `@agentic_function`。

## 产物落进既有 skill 管道

蒸馏出的 skill 写到 `~/.openprogram/skills/<name>/` 或 `<cwd>/skills/<name>/`——这是加载器已经在合并的五个来源中的两个。因此它不用重启就生效（watcher 热重载），并自动可以用 `/<name>` 调用（`commands/_skill_adapter.py` 把每个被发现的 skill 投射进 slash command 注册表）。

这就是这个功能不需要自己的子系统的原因：存储、发现、重载、调用路径全都已经在了。蒸馏要做的只是把文件写到对的位置。

## 命名

产品词是 **distill**，不是 "workflow"。在本仓库里 "workflow" 已经指 agentic program——一个预置的完整 agent harness，文档在 `docs/capabilities/workflows/`。蒸馏出的流程是更小的东西：从一次会话里提取的知识，不是一个分发的程序。复用这个词会把同一个文档 Tab 里并排出现的两个概念混成一个。

## 实现状态

已按本文实现。
