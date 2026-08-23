# Goal Workflow

Goal 反复执行 Agent，并由独立 completion judge 判断完成条件是否满足。产品里只有一个 Goal Workflow，两种调用方式只负责提供不同初始上下文。

## 使用 Programs 表单

打开 **Programs → Workflow → goal → Use**，填写 `prompt` 和 `condition`。

这是直接调用：GoalRun 不读取此前聊天历史，但 runtime card 仍记录在所属会话中。

## 使用当前会话

在输入框键入：

```text
/goal 所有单元测试通过，并且 README 说明了新参数
```

这会调用同一个 Goal Workflow，同时把当前会话的压缩上下文作为初始证据。此后 refinement、judge、轮次上限、用户提问、进度状态和终态都与 Programs 表单相同。

active Goal 显示在 composer 的 GoalChip 中。judge 提问时使用标准问题面板，用户回答后恢复同一个 Workflow execution。

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
| `capped` | 达到配置的轮次上限。 |
| `error` | 连续 judge 失败或 checklist 停滞导致终止。 |
| `waiting_user` | judge 提问，正在等待回答。 |
| `cleared` | 当前 session 清除了 Goal。 |
