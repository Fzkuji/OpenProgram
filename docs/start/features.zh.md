# 功能详解

README 中的 [核心特性](../README.md) 表格
指向这里，呈现每项特性背后更完整的来龙去脉。
[Agentic Programming 设计理念](../capabilities/agentic-programming/philosophy.md)
一文讲的是*为什么*；本页讲的是*它在日常使用中如何体现*。

## 自动上下文

每一次 `@agentic_function` 调用都会作为一个节点记录进会话的
扁平对话 DAG —— 与用户消息、LLM 调用共存于同一个 DAG。
嵌套调用会自动串接：

```
login_flow ✓ 8.8s
├── observe ✓ 3.1s → "found login form at (200, 300)"
├── click ✓ 2.5s → "clicked login button"
└── verify ✓ 3.2s → "dashboard confirmed"
```

当 `verify` 调用 LLM 时，它会自动看到
`observe` 和 `click` 返回的内容。无需手动管理上下文：
你只管编写函数，运行时会把 DAG 串接起来。

装饰器上有两个开关，控制一次调用向后续 LLM 调用贡献什么：

```python
@agentic_function(expose="full", render_range={"callers": 1})
def navigate(target): ...
```

`expose` 决定后续调用能看到这次调用内部的多少 ——
`io`（默认：只有输入 / 输出）、`llm`（只有它的 LLM 往来）、
`full`（全部）、`hidden`（完全不写节点）。
`render_range={"callers": N}` 限制函数自身能看到多少此前的历史
（`0` 表示完全隔断）；`{"subcalls": N}` 在长循环里约束它自己
帧内历史的规模。

## 编写函数的函数

编写、修复和搭建 `@agentic_function` 本身就是
agent 的工作，用普通的文件编辑工具并按照
[Agentic function API](../reference/api/agentic-function.zh.md) 完成。
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
- **Merge**（把两条或多条分支聚合成一条汇总回复）

涉及文件的分支在底层运行于**独立的 git worktree** 中，
因此在不同分支上并发的两个 agent 不会争抢同一份源码树。
其他框架通过复制消息来分叉对话；我们分叉的是底层的仓库。

## 自己写自己的记忆

记忆只在一个地方，`~/.openprogram/memory/`，而且全部是可以用任何
编辑器打开的Markdown。

| 路径 | 放什么 |
|---|---|
| `core.md` | 一小段常驻内容，注入每个会话的system prompt |
| `topics/` | 一个主题一个文件，比如`topics/people/dave.md`。每个段落带一个ID，并标注这条事实的出处 |
| `sources/` | 那些出处指向的对话原文。只追加，不改写 |
| `timeline/` | 同一批事实按日期排列，从`topics/`重建 |
| `.scriptorium/` | 内部状态：每个对话读到哪了，以及避免两个写入者相撞的锁。它不是记忆 |

不会每轮都写。说完的对话先攒着，攒到够写一次了才跑一趟，由这一趟
判断每条事实属于哪个主题、写进那个文件。一条事实在哪儿说的，不决定
它存在哪儿，所以关于Dave的细节最终都落在`topics/people/dave.md`，
无论它是分几次对话学到的。安静超过半小时的对话不管攒了多少都会被
写掉，短对话不会一直等一个永远凑不齐的批次。

每次写入要么整体落地，要么完全不落地。引用了没提供的出处、指向了
不存在的段落、或者破坏了主题格式的改动会被整体拒绝，工作区一个字节
都不变。两个写入者不会交错：后台写入发现工作区被占用时，等一秒就
放弃，不会让你等，下一轮再来。

写入只会让文件越来越长，所以每天凌晨3点还有第二趟：把已经涵盖多个
主题的文件拆开，合并说同一件事的段落，修复链接。
`openprogram memory sleep`可以现在就跑这一趟，不等到今晚。

用CLI查看和手动修：

```bash
openprogram memory status                        # 在哪、有什么、当前版本号
openprogram memory recall xelatex thesis         # 搜索并打印匹配的段落
openprogram memory show topics/people/dave.md
openprogram memory edit topics/people/dave.md    # 用$EDITOR打开，校验通过才落地
openprogram memory export                        # 把整个工作区打成tar.gz
```

Web UI的Memory页面读的是同一个工作区。agent通过`memory_search`、
`memory_grep`、`memory_get`、`memory_browse`、`memory_update`、
`memory_status`访问它。没有「保存这条」这样的工具：记录对话本来就在
后台发生，`memory_update`是用来订正已有内容、或者写下你当场要求
记住的东西的。

## Mini-DAG — 右栏中的执行视图

每个对话都有一个右栏 mini-DAG，它画出每个节点
（用户消息、LLM 调用、代码 Call、attach）以及它们之间的边。
该视图随聊天一起滚动：点击某个节点会把对话滚动到对应的消息，
面板会保持当前查看的范围处于高亮。渲染规则的规范在
[`design/runtime/dag/rendering.md`](../reference/design/runtime/dag/rendering.md)，新增节点类型时请参阅它。

## 多账户 + 密钥轮换

同一个 provider、多个账户 —— 每个账户还可有多个密钥 —— 在每个界面上
以相同的方式管理。一个**账户就是一份 profile**：某个 provider 的
一套独立凭据。

```bash
openprogram providers login openai --account work      # 添加第二个账户
openprogram providers login openai --account personal
openprogram providers use openai work                  # 用 "work" 账户运行 openai
openprogram providers use openai                        # 切回默认账户
openprogram providers list                              # 当前激活的会被标记
```

同一个面板存在于 **web**（Settings → Providers）和 **TUI**
（`/login <provider>`）中：列出 / 添加 / 激活 / 重命名 / 删除。终端里的 `/login`
会在那里就地完成整个登录 —— OAuth、设备码、从 CLI 导入，
或粘贴一个 API key —— 而不会把你弹去浏览器。Claude 订阅账户
（`claude-code`）也在完全相同的面板背后 —— 只是这套通用界面的
一个实例。

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
[`design/providers/auth/unified-account-management.md`](../reference/design/providers/auth/unified-account-management.md)。

## 多 agent + 多 channel（未来走向）

dispatcher 已经支持每个会话有多个 `agent_id` —— 每一行都标记了
产生它的 agent，侧边栏可以按作者用颜色区分，channel 层把
外部传输（Telegram / Discord / Slack / WeChat）映射到按账户区分的身份。
跨 channel 的消息路由 + 一套声明式的工具可用性系统，
作为下一批特性在跟踪中。
