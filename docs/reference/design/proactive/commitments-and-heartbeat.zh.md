# 承诺与心跳

> agent 记住对话里许下的承诺，在正确的时刻主动开口，而不是等着被问。
> 两个机制一个循环：承诺是状态，心跳是钟。可视化配套页：
> [`commitments-and-heartbeat.html`](commitments-and-heartbeat.html)。
> 关联代码（计划中）：`openprogram/memory/runtime/`、
> `openprogram/functions/tools/cron/`、`openprogram/proactive/`。

## 承诺是什么

承诺是从对话中提取出的带日期义务："周三之前交 rebuttal"、"下周换服务器
密码"、"实验跑完回复审稿人"。它的结构：

| 字段 | 含义 |
|---|---|
| `text` | 义务本身，一句话，祈使式 |
| `due` | 绝对日期或 `null`（无日期的承诺照样浮现，只是不那么紧急） |
| `speaker_id` | 谁许下的 —— owner 或某个已配对发言人，来自 authority |
| `source` | Source v2 引用，与 Topic block 相同的锚点契约 |
| `status` | `open` / `done` / `dismissed` |

## 提取骑在 writer 上

不建新管道。记忆 writer 本来就要读每批会话去提炼 Topic，同一趟顺带产出
承诺记录。提取遵守与提炼相同的信任规则：只有 `trusted` 来源产生承诺 ——
未配对的陌生人没法往 owner 的清单里塞任务。相对日期在提取时就用会话时间
戳换算成绝对日期，因为"下周三"过一个月就没有意义了。

存储是派生视图 `commitments.jsonl`，与 `recent_events.jsonl` 同级、同一套
重建纪律：从有引用的 source 派生、由 writer 重建、永远不是手写权威。标记
`done` 或 `dismissed` 是记录在视图里的状态转移，要么 writer 在后续对话说
了的时候完成（"rebuttal 已经交了"会在下次提炼时闭环），要么显式走
`memory_update`。

## 心跳

一个定时轮次 —— 现有 cron 机制里的一个内置任务 —— prompt 固定且窄：读
open 状态的承诺、对照今天、判断有没有值得 owner 注意的事、有就通过 owner
已经在用的渠道说出来。它在所有约束意义上都是一个正常轮次：携带从 owner
派生的 `runtime_authority`（非交互，因此永远批准不了任何事），出站消息走
现有渠道回复路径，沙盒策略是冻结的 cron 策略。

防止它变成唐僧的四条：

- **没事不说话。**有到期或逾期的承诺才开口，否则静默 —— "今日无事"式的
  汇报是噪音，不发。
- **每条承诺每个升级档只提一次。**到期时提醒一次，逾期超过配置间隔再提
  一次，不是每次心跳都念。提醒状态记录在承诺记录上。
- **安静时段。**`proactive.quiet_hours`（默认 23:00–08:00 本地时间）抑制
  发送；被抑制的条目顺延到下一个允许的心跳。
- **一个节奏旋钮。**`proactive.heartbeat` —— `daily`（默认）、`hourly`、
  `off`。off 时整个循环不动，承诺照常提取、在 `memory status` 里可见，
  只是永不推送。

## 明确不做的

不做任务管理器：没有子任务、优先级、项目 —— 就是一张带引用的扁平日期
清单，因为更复杂的形态作为产品已经失败过（todo 应用的坟场），agent 的
优势恰恰是这张清单自己写自己。不做自主执行者：心跳只提醒不动手。"你说
过要换密码"在范围内；没被要求就自己去换不在。对承诺采取行动，始终是
owner 发起的正常轮次。

## 实现状态

尚未实现。批准后的顺序：writer 里的提取与 `commitments.jsonl` 视图及其
测试 → 状态转移（writer 闭环与 `memory_update`）→ 带提醒状态、安静时段
与渠道送达的心跳 cron 任务 → `memory status` 与 Web 展示。
