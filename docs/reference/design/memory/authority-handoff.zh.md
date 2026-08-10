# 权限：两个档位与配对

> OpenProgram 如何判定一个发言人能让 agent 做什么，以及这个判定如何传到记忆里。
> 发言人归因本身见 [`speaker-identity.html`](speaker-identity.html)；
> 配套可视化是 [`authority-landscape.html`](authority-landscape.html)。
> 关联代码：`openprogram/agent/authority.py`、`openprogram/channels/_access.py`、
> `openprogram/channels/base.py`、`openprogram/memory/scriptorium/writing.py`。

## 模型

准入是二元的，权限也是二元的。一个平台账号处于三种状态之一，其中只有两种是运行时权限档位。

| 状态 | 档位 | 能做什么 |
|---|---|---|
| **Owner** —— 本地终端、本机 Web | `owner` | 全部：执行命令、写文件、发消息、管理和改写记忆。 |
| **Paired** —— 已批准的平台账号 | `paired` | 正常对话、带归因写进记忆、主动追加记忆。不能执行工具。 |
| **Unpaired** | 无 | 消息不进入 agent，发信人收到配对码。 |

未配对不是第三个档位。不存在"未知外部发言人只能回复"这种状态：未配对消息根本不会渲染进模型
上下文，这是把注入面移除，而不是收窄。

## 门口

权限以单个枚举字段 `authority_tier` 挂在请求边界上，与 `principal_id`、`speaker_kind`、
`speaker_id`、`speaker_display` 和 `interaction` 并列。请求自己不携带能力列表，
调用方也就没有可以不一致地构造出来的东西。

`openprogram/agent/authority.py` 里的 `decide_capability()` 拿能力去查
`TIER_CAPABILITIES` 常量表：

| 档位 | 能力 |
|---|---|
| `owner` | `reply`、`memory.source.append`、`memory.trusted.promote`、`schedule.create`、`schedule.manage`、`fs.read`、`fs.write`、`process.exec`、`network.send`、`approval.request`、`runtime.control` |
| `paired` | `reply`、`memory.source.append` |

门口是 **fail-closed** 的。档位缺失以 `AUTHORITY_TIER_MISSING` 拒绝；档位不是表里的 key
以 `AUTHORITY_TIER_UNKNOWN` 拒绝。两种情况都不回落到某个缩小的能力集合 ——
无法识别的请求失去全部能力，`reply` 也在内。

`capability_for_tool()` 把每个工具名映射到恰好一项能力，兜底分支是 `process.exec`。
安装的 agentic 函数和挂载的 MCP 工具可以叫任何名字；把未分类的名字当作可执行代码处理，
意味着 `paired` 发言人够不到它，因为只有 `owner` 持有 `process.exec`。

门口位于 `_gated_execute` 中规则层**之前**、`_FORCE_APPROVAL_TOOLS` 之前、
`bypass` 短路之前，所以任何 permission mode 和任何持久化的 allow 规则都跳不过它。
每次判定返回一条 `AuthorityDecision` 记录 —— 是否放行、决定性检查、稳定原因码、档位、能力 ——
并写进日志，所以一次拒绝是可审计的，而不是一个裸布尔值。

`runtime_authority()` 从父轮次派生 subagent 或运行时任务的权限：复制父轮次规范化后的权限，
改写 speaker 字段并把 `interaction` 设为非交互，`authority_tier` 原封不动。继承永不扩权。
`paired` 轮次不可能派生出 `owner` 子 agent，父轮次没有有效权限时返回 `{}`，门口拒绝。
subagent 始终非交互，因此也没有通往交互式审批的路径。

## 配对

不在账号 allowlist 上的发信人在 `decide_inbound_sender()` 处被拦下，收到一个 8 位大写配对码。
字母表排除易混字符 `0`、`O`、`1`、`I`。配对码一小时后过期，一个账号同时最多持有三个待处理
配对码，同一发信人一小时内不重复提示。

批准**只发生在 owner 自己的机器上**，走 CLI 或本机 Web 界面。任何渠道消息都无法批准任何人。
聊天里的"请批准配对码 X"是注入的经典措辞，正确反应是拒绝并让对方去找 owner。

身份匹配只用平台稳定 ID。用户名和显示名可变，因此不参与 allowlist 匹配 ——
有人把昵称改成 owner 的名字，得不到任何东西。显示名要先经
`sanitize_speaker_display()` 去掉换行、零宽和 bidi 字符并中和信封标记 `[` 与 `]`，
才能进入 prompt。

## 记忆中的信任语义

记忆在每条 source frame 上记录 `trust_state`，取两个值。

**`trusted`** —— owner 自己的轮次，以及已配对账号的全部内容。配对**就是**那次信任判定，
所以已配对发言与 owner 的发言一样进入正常的提炼流程，按发言人归因。配对这道门后面没有第二道门。

**`pending`** —— 从未配对发言人归档下来的文本。群聊中未配对成员的发言照常归档，
因为一段群聊只有完整才可理解，但这段文本是证据而不是已接受的记忆。它带完整来源信息，
`memory_promote` 是把 pending frame 转为 `trusted` 的唯一路径，只有 owner 能走
（`memory.trusted.promote`）。

`trust_state` 是机制。检索输出里出现的 `speaker_trusted` 字段是它的展示投影，不是另一个判定。

**未配对文本不进入模型上下文。**两个互相独立的机制共同保证这一点，都已上线：

1. **实时路径先拦住。**`openprogram/channels/base.py` 在任何 agent 分发之前查询 access
   判定。被拦发信人的消息（群聊里）被归档并收到配对回执，永远不会变成一轮对话。
2. **自动召回把它过滤掉。**记忆 provider 的 `search()` 丢弃所有 `trust_state` 为
   `pending` 的命中，所以已归档的 pending frame 也不会出现在后续轮次注入的上下文里。

pending 文本仍可通过 `memory_search` 工具触及，模型在那里主动索取，并同时看到它的
`trust_state`。

## 记忆写入器

自动整理写入器跟随默认聊天 agent 的 provider、模型和凭据，不再硬编码某一个 CLI agent。
`memory.writer.model` 可以覆盖它，并在 Web 设置界面里可编辑。默认刻意不用便宜模型：
弱模型会误读整理指令，所以降级是一个显式选择。

重试分类在 provider 和 session watcher 两侧都保守：未知异常不可重试，
只有显式分类过的瞬时异常才重试。

## 附录：实现状态

档位枚举、常量表门口、fail-closed 拒绝、结构化判定记录、subagent 继承、8 位配对码流程、
只走本地 owner 的批准、稳定 ID 匹配、显示名消毒、`pending` / `trusted` 划分，
以及上面两条上下文排除机制，均已实现。

| 项目 | 状态 | 说明 |
|---|---|---|
| 越权请求 hold 队列 | 未实现 | 已配对发信人的越权请求（例如"重启服务"）进入队列等 owner 一次性批准，而不是直接拒绝。需要采纳的参数：消息数上限、过期超时、发信人档位变化时重新评估队列。 |
| 按请求方档位过滤读取 | 未实现 | 写入侧归因已就位，按请求发言人档位过滤检索结果尚未实现。 |
| 已标记来源的 backfill | 未实现 | 在任何 Topic 存在之前被标记过的历史仍在 Topic 之外。需要一次性跑一遍写入器，覆盖所有未被 Topic 引用的 source，忽略标记，并保留已经提升过的 core。 |
| 可查询的写入器状态 | 未实现 | 最近一次成功写入时间、最近一次失败原因及其可重试判定、待处理数量，同时暴露在状态输出和 Web 界面里。 |
| 未配对群聊流量的归档上限 | 未实现 | 未配对群聊归档没有独立的频率或存储上限。在该流量不受外部限速的部署场景之前需要补上。 |
