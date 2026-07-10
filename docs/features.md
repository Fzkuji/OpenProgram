# 核心特性 — 详细导览

README 中的 [核心特性](../README.md#key-features) 表格
指向这里，呈现每项特性背后更完整的来龙去脉。
[Agentic Programming 设计理念](philosophy/agentic-programming.md)
一文讲的是*为什么*；本页讲的是*它在日常使用中如何体现*。

## 自动上下文

每一次 `@agentic_function` 调用都会创建一个 **Context** 节点。
节点构成一棵树，并自动注入到 LLM 调用中：

```
login_flow ✓ 8.8s
├── observe ✓ 3.1s → "found login form at (200, 300)"
├── click ✓ 2.5s → "clicked login button"
└── verify ✓ 3.2s → "dashboard confirmed"
```

当 `verify` 调用 LLM 时，它会自动看到
`observe` 和 `click` 返回的内容。无需手动管理上下文：
你只管编写函数，运行时会把这棵树串接起来。

## Deep Work — 自主质量循环

对于需要持续投入和高标准的复杂任务，`deep_work` 会运行一个
自主的「规划—执行—评估」循环，直到结果达到指定的质量等级：

```python
from openprogram.functions.agentics.deep_work import deep_work

result = deep_work(
    task="Write a survey on context management in LLM agents.",
    level="phd",        # high_school → bachelor → master → phd → professor
    runtime=runtime,
)
```

agent 会先在前期厘清需求，然后完全自主地工作 —— 执行、自我评估、
反复修订，直到产出通过质量审查。状态会持久化到磁盘，
因此被中断的工作可以从断点处继续。

## 编写函数的函数

编写、修复和搭建 `@agentic_function` 本身就是
agent 的工作 —— 用普通的文件编辑工具完成，由
**`agentic-programming` skill**
（[`skills/agentic-programming/SKILL.md`](../skills/agentic-programming/SKILL.md)）指导。
没有专门的 `create()` / `fix()` 框架调用：
它们无非是包了一次 LLM 调用加一次文件写入，而 agent
可以直接做这些事。

这个 skill 就是完整的规范 —— 文件放在哪里、装饰器的元数据、
docstring 与 `content` 的拆分、一份基于规则的校验清单，
以及一个冒烟测试。agent 读它、写出函数、校验、运行它；
`write → run → fail → fix` 这个循环依然意味着程序在使用中不断改进。

## 对话即 git DAG

会话历史像 git 仓库那样存储，而不是一个扁平列表。
每一次交流都是一次 commit，分支是一等公民，
右侧栏暴露了常见的 git 操作：

- **Branch off**（从任意过去的交流分叉）以探索另一种走法，
  同时不丢失原有的线索
- **Attach**（附加来自另一个会话的上下文，跨会话复用）
  作为一条带标签的用户消息
- **Merge**（当两条线索的分支汇合时合并它们）
- **Cherry-pick**（跨分支挑选特定的 commit）

涉及文件的分支在底层运行于**独立的 git worktree** 中，
因此在不同分支上并发的两个 agent 不会争抢同一份源码树。
其他框架通过复制消息来分叉对话；我们分叉的是底层的仓库。

## 分层记忆

记忆不是一个大杂烩。`~/.openprogram/memory/` 下有六个独立的
存储，覆盖不同的时间尺度和用途：

| 层 | 存放什么 |
|---|---|
| `journal` | 短期 —— 近期观察、原始笔记 |
| `wiki` | 持久 —— agent 决定保留下来的事实 |
| `sleep` | 周期性整合（离线守护进程把 journal 合并进 wiki） |
| `scheduler` | 由 cron 驱动的回忆，在特定时刻浮现某条记忆 |
| `recall_counts` | 命中计数，用于提升高频使用记忆的权重 |
| `store` | 项目范围的键值对 |

打开 `/memory` 即可查看或手动编辑任意一层；agent
会根据它学到的内容决定写入哪一层。之所以拆分，是因为
「记住这个直到我让你忘记」和「记住这个再撑 10 轮」
需要不同的存储策略。

## Mini-DAG — 右栏中的执行视图

每个对话都有一个右栏 mini-DAG，它画出每个节点
（用户消息、LLM 调用、代码 Call、attach）以及它们之间的边。
该视图随聊天一起滚动：点击某个节点会把对话滚动到对应的消息，
面板会保持当前查看的范围处于高亮。对于扇出密集的追踪记录，
可通过一个开关切换到 d3-hierarchy 布局；
新增节点类型时请参阅 [`design/runtime/dag/dag-rendering.md`](design/runtime/dag/dag-rendering.md)。

## 多账户 + 密钥轮换

同一个 provider、多个账户 —— 每个账户还可有多个密钥 —— 在每个界面上
以相同的方式管理。一个**账户就是一份 profile**：某个 provider 的
一套独立凭据。

```bash
openprogram providers login openai --profile work      # 添加第二个账户
openprogram providers login openai --profile personal
openprogram providers use openai work                  # 用 "work" 账户运行 openai
openprogram providers use openai                        # 切回默认账户
openprogram providers list                              # 当前激活的会被标记
```

同一个面板存在于 **web**（Settings → Providers）和 **TUI**
（`/login <provider>`）中：列出 / 添加 / 激活 / 重命名 / 删除。终端里的 `/login`
会在那里就地完成整个登录 —— OAuth、设备码、从 CLI 导入，
或粘贴一个 API key —— 而不会把你弹去浏览器。claude-code 把
它的 Claude 订阅（Meridian）后端也放在完全相同的面板背后，所以它
只是这套通用界面的一个实例。

**api-key 类型的 provider** 也获得同样的多凭据模型，以一份密钥列表呈现：
粘贴一个密钥（会先校验）它就加入列表，给每个密钥**命名**，并
用 *Use* 选出哪个是**激活的**（即被使用的那个）。这与 OAuth provider 为
账户提供的「多份凭据、在它们之间切换」是同一个思路 —— 只是把登录换成了密钥。
**轮换是一个可选开关**，默认关闭：关着时只调用激活的密钥；打开后，
被限流的密钥会冷却，由下一个接手（`429` → 冷却 + 轮换，
`402` 因计费而冷却更久，`5xx` 短暂冷却），并配有策略选择器（`in order` /
`spread evenly` / `random` / `least used`）以及 ↑ / ↓ 优先级。你以旧方式
（环境变量 / 配置）已经设置好的密钥会被迁移进列表，因此不会丢失任何东西。
设计 + 状态：
[`design/providers/auth/unified-account-management.md`](design/providers/auth/unified-account-management.md)。

## 多 agent + 多 channel（未来走向）

dispatcher 已经支持每个会话有多个 `agent_id` —— 每一行都标记了
产生它的 agent，侧边栏可以按作者用颜色区分，channel 层把
外部传输（目前是 Discord）映射到按账户区分的身份。
跨 channel 的消息路由 + 一套声明式的工具可用性系统，
作为下一批特性在跟踪中（状态见项目的待办任务列表）。
