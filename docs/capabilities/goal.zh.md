# Goal Workflow

Goal 反复执行 Agent，并由独立 completion judge 判断完成条件是否满足。产品里只有一个 Goal Workflow；是否带上当前会话，取决于怎么启动。judge 默认使用会话当前选中的模型；配置 `goal.judge_model` 为 `provider/model` 或裸模型名可覆盖。

## 自己启动

用户手动启动一律带上当前会话作为初始证据（`context_mode=session`）：Programs 表单、`/goal`、welcome 按钮、Retry。打开 **Programs → Workflow → goal → Use**，只填任务（`prompt`）；`condition` 保持隐藏，默认等于这段话。或在输入框键入：

```text
/goal 所有单元测试通过，并且 README 说明了新参数
```

runtime card 仍记录在所属会话中。此后 refinement、judge、轮次上限、用户提问、进度状态和终态对每种入口都相同。

## Agent 或 Python 启动

Agent 自己调用 `goal` 时可传 `context_mode`：`isolated`（不带会话）或 `session`（带上当前会话）。不传则默认 `isolated`。Python 直接调用 `goal(...)` 同样默认 `isolated`。

active Goal 显示在 composer 的 GoalChip 中。judge 提问时使用标准问题面板；问题挂着，工作继续。答到后下一轮工作会注入该回答并重置轮次预算。拒绝回答或答案为空时，Workflow 不会停止，而是自行选择最合理方案继续。

## 状态与控制

```text
/goal
/goal clear
```

`/goal` 查询当前 session 关联的 Goal。`/goal clear` 将 active 或 waiting Goal 标为 cleared；Workflow 在工作轮次之间检查该状态，停止时不得覆盖它。

普通 Stop 控件取消当前 Goal function run。clear 修改 Goal 状态，Stop 取消 execution boundary，两者含义不同。

## 终态

| 状态 | 含义 |
| --- | --- |
| `achieved` | judge 接受条件，并且 checklist 全部完成。 |
| `capped` | 达到轮次上限（`goal.max_turns`，默认 150；config 里配 0 或负数表示无限）。 |
| `error` | 连续 judge 失败、checklist 停滞或连续零工具轮导致终止。 |
| `waiting_user` | judge 提问，正在等待回答。 |
| `cleared` | 当前 session 清除了 Goal。 |
