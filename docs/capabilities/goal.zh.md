# Goal Workflow

Goal 会反复运行 Working Agent，并由独立 completion judge 根据现有证据判定。目标、进度、资源用量和最近 checkpoint 保存在所属 session 中，因此重新加载页面不会删除 Goal。

工作 Agent 在已有权限内修改产物、执行验证命令。判定和目标整理阶段只获得文件读取与检索工具，不获得 shell 或产物编辑工具。缺少验证命令结果时，判定要求工作 Agent 补充执行，不自行修改产物使检查通过。这是工具能力限制，不表示检索没有缓存写入或网络请求。

## 启动 Goal

打开 **Programs → Workflow → goal → Use**，或输入：

```text
/goal 所有单元测试通过，并且 README 说明了新参数
```

Programs 和 Python 调用默认使用隔离上下文。`/goal` 会把当前会话快照作为初始证据。除此以外，两种入口使用相同的 Working Agent 与 judge 实现。

可选限制包括 `max_rounds`、`max_tokens`、`max_elapsed_s` 和 `max_cost_usd`。Goal 恢复后继续累计限制与用量。
创建表单默认只显示目标正文；展开“高级参数”可设置预算、角色与上下文模式。直接输入 `goal(...)` 时，显式参数会原样传入执行；内部恢复字段不在此处开放编辑。

单个工作轮次默认限时 300 秒，Python 和 CLI 调用可以通过 `timeout_s` 覆盖。累计预算在 controller 边界检查，因此当前阶段可能先超出累计上限，再由该上限阻止后续阶段。

Python 调用可用 `model`、`effort` 选择工作角色，用 `judge_model`、`judge_effort`、`judge_timeout_s` 选择判定角色。未指定判定模型时使用已配置的 `goal.judge_model`，否则使用已解析的工作模型。带 provider 的名称接受 `provider:model` 或 `provider/model`。Goal 保存两个角色的实际模型身份、认证路由、推理设置和超时，不保存凭证。恢复时沿用保存值，不随当前会话默认设置改变。角色不可用时，目标进入可恢复暂停且不开始工作；重试保留原选择，不自动换 provider。旧 Goal 没有角色配置时，在首次恢复时解析并标注迁移。自定义 callable Runtime 必须由 Python 调用者再次提供，不会被还原为托管 provider。

详情弹窗和 `/goal` 状态显示保存的工作、判定角色设置。

目标暂停或等待时，可展开“配置 Agent”，修改工作和判定角色的 provider、模型、推理强度及超时。Provider 是已配置的认证路由，不是凭证。保存不改变目标、证据、问题与累计用量；恢复时验证模型。模型不可用时，可修正选择后再次恢复。执行中的目标需要先暂停才能修改角色。

TUI 使用相同设置，不需要打开浏览器：

```text
/goal help
/goal role work openai_codex gpt-5.4 effort=high timeout_s=300
/goal role judge openai_codex gpt-5.4 effort=medium timeout_s=180
/goal budget max_turns=10 max_tokens=10000 max_elapsed_s=3600 max_cost_usd=5
```

请使用已配置的 provider 和模型名称；示例不会自动选择模型。预算设为零表示移除该上限。`/goal` 显示已解析或等待验证的角色配置，并明确标注未知成本。

## 查看与控制

composer 中的 Goal 状态条会打开详情弹窗。弹窗显示目标、状态、checklist、资源用量、最近判定原因和全部待答问题，并支持编辑、暂停、继续、逐项回答、调整执行限制和终止。

已达成、已取消和不可完成的 Goal 在其停止请求已确认后不再占用输入区，原始目标与输出仍可在对应执行记录中查看。暂停、等待和可恢复失败的目标保留详情入口，页面重载后仍可查看和继续。

暂停、编辑和终止先保存目标变更，再请求取消对应执行。取消投递失败不会撤销已保存的变更，界面会报告停止尚未确认。“重试停止”或 `/goal stop` 只重试原执行的取消命令，不创建新 Goal，也不重置用量。Web 弹窗和 TUI 分别显示执行状态未知、正在停止或执行已停止，并在相关执行事件、重新连接时刷新；弹窗还支持“刷新状态”。已取消但停止尚未确认的目标继续保留操作入口。旧执行及其全部后代结束前不能恢复；新建替代 Goal 也会在初始化工作和用量前检查已保存的停止请求。没有执行记录的纯脚本 Goal 在后续 Goal 边界协作停止，界面不能确认其物理执行状态。

进度更新不会覆盖未保存的正文和限制。其他客户端修改相同内容时，弹窗保留本地草稿并提供明确的“采用最新”操作；请求携带所显示目标的身份与版本，保存失败也保留草稿。继续前需要保存或放弃修改，包括回答问题后恢复工作。关闭再打开同一目标会保留草稿；切换会话时，编辑器重置为该会话已保存的目标。成本未知时显示“未知”，不显示零美元。

目标完成时，已打开的编辑器保持打开，避免丢弃未保存的正文；输入区的目标按钮仍会消失。
终止操作在同一弹窗内要求确认，保留已保存的工作与历史记录。Escape 会先退出终止确认，再关闭弹窗。

展开执行记录可以检查 LLM 回复。执行树显示已保存的提示词预览和实际输出；空回复显示“无文本输出”。复制结果包含回复，也兼容使用旧回复字段的记录。

TUI 在输入框上方显示当前 Goal 的状态、阶段、目标摘要、检查项进度和待答数量。
该区域只读，不替换输入框，也不要求立即回答。使用 `/goal` 查看完整详情和操作。
目标结束后该区域隐藏，执行结果仍保留在会话历史中。

进入会话或重新连接时，TUI 读取已保存的 Goal，并按版本顺序应用实时更新。
离线时保留最后确认的快照并标注离线；刷新失败会明确显示，不会清除目标或自动继续执行。

TUI 提供相同操作：

```text
/goal
/goal pause
/goal stop
/goal resume
/goal edit 将综述范围收窄到知识编辑
/goal answer 使用 2023 年之后发表的论文
/goal answer <question-id> 使用 2023 年之后发表的论文
/goal clear
```

编辑会创建新的目标 revision，并暂停 Goal，等待显式继续。旧 revision 的未答问题保留为 `superseded` 审计记录，不再阻塞新 revision。答案会在被消费前持久化，因此 worker 重启不会丢失答案。不指定问题 ID 时回答最早的待答问题；指定 ID 可以回答队列中的其他问题。

## 等待与重启恢复

Goal 暂停请求已保存，不代表当前执行已经停止。生成恢复调用和实际进入 Goal 时
都会检查关联执行：记录不可用、执行尚未结束或仍有活动后代时，不启动新运行。
`/goal` 将执行观察与 Goal 状态分别显示。
旧执行仍在停止时提交的回答会保留，界面会说明未自动恢复的原因。

没有执行记录的脚本调用仍使用跨进程 Goal 控制器锁。同一运行中的父执行可以在
前一次 Goal 调用返回后顺序恢复，但不能用这一例外绕过用户暂停或编辑。

存储错误不被当作目标不存在。已保存状态无法读取时，控制操作报告状态暂不可用，
不启动或恢复工作；Goal API 返回 HTTP 503，而不是 404。终态保存失败时不发布
完成快照或完成通知，最后一次成功保存的状态仍作为恢复依据。

Goal HTTP 操作接口接受可选的 `expected` 对象，包含所读快照的 `goal_id`、`revision`、`run_id` 或 `version`。不匹配时返回冲突，不执行操作。状态提交先于取消送达，保存冲突不会取消运行。HTTP 和 `/goal` 生成的恢复描述符绑定所读快照，Workflow 在开始工作前再次校验；期间目标改变时，需要依据当前状态重新请求恢复。

启动恢复在判定执行失去 owner 前，检查保存的主机、进程 ID、进程启动身份及 attempt 租约。第二个本地 controller 不修改仍存活的 owner；确认进程退出后可恢复，不必等待租约到期。尚未创建 attempt 的 admission 有 30 秒保护期限。进程身份无法确认时，在有效租约内保守保留；这不提供跨主机 owner 发现。没有进程证据的旧记录沿用既有恢复规则。

问题采用异步处理。某部分必需工作依赖无法安全确认的信息时，judge 会记录问题。如果仍有独立工作，Goal 会继续执行这些工作，问题保留在 Goal 弹窗中。只有所有剩余工作都依赖答案时，Goal 才进入 `waiting_user`。Goal 不会猜测或执行依赖未答问题的事项。

多个问题可以累积。可以在 Goal 弹窗或通过 `/goal answer` 逐项回答。向正在执行的 Goal 提交答案时，controller 会在下一个边界消费答案，不会启动第二个 execution。等待中的 Goal 收到任意新答案后恢复，完成因此能够执行的工作；其他问题继续保持待答。用户主动暂停的 Goal 不会因收到答案自动恢复。Goal 问题不会替换或禁用普通聊天输入框。

有人值守时，judge 可以针对影响目标方向的歧义、缺失访问权限或明确审批记录问题，但普通实现选择仍由 agent 自主完成。无人值守时，问题不会打断执行；Goal 会先完成全部安全且不依赖答案的工作，然后在必要时等待。composer 的“无人值守”控制项为当前 session 选择模式；页面重新连接后会把该选择重新同步给 worker。

如果 worker 在 Goal 完善、工作或判定期间重启，持久化状态会变为 `paused_recoverable`。Goal 弹窗仍显示目标、checklist、用量、checkpoint 和原因。使用“继续”或 `/goal resume` 从该状态启动新的 execution。

恢复时会向工作 agent 和 judge 重新提供当前 revision 已确认的答案，以及有长度上限的历史工作证据。编辑目标后创建新 revision；旧决定保留在历史记录中，但不会自动用于新目标。

同一主机、共享同一 session store 的 worker 使用独占 Goal controller 锁。第二个 controller 在调用模型前被拒绝，启动中的 worker 不会修改其他 worker 正在执行的 Goal。controller 退出或进程崩溃后，由操作系统释放锁。这不提供跨主机或网络文件系统的分布式 ownership。

`waiting_external` 同样停止工作轮次，但目前没有实现外部事件自动唤醒。外部依赖发生变化后需要显式继续。

## 状态

| 状态 | 含义 |
| --- | --- |
| `refining`、`active`、`running`、`evaluating` | Goal 正在执行。 |
| `waiting_user` | Goal 需要用户回答。 |
| `waiting_external` | 外部依赖变化后才能继续。 |
| `paused`、`paused_recoverable` | 用户暂停，或 worker 重启后可恢复。 |
| `blocked` | 依赖或权限阻止进展，发生变化后可以恢复。 |
| `impossible` | 当前目标与约束无法同时满足。 |
| `stalled` | 连续多轮没有被接受的进展。 |
| `budget_exhausted` | 达到轮次、token、时间或成本限制。 |
| `failed` | execution 或连续 judge 判定失败。 |
| `achieved` | judge 接受结果与 checklist。 |
| `cancelled` | 用户终止 Goal。 |

执行时间限制只统计 controller 的 active time；处于 `waiting_user`、`waiting_external`、`paused` 或 `paused_recoverable` 的时间不计入执行预算。token 与成本使用 active-run cursor 增量核算，因此 Goal 等待期间同一 session 的其他操作不会计入 Goal。
