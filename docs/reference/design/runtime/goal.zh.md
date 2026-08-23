# Goal Workflow

Goal 能力只有一个可执行 Workflow：

```python
goal(
    prompt,
    condition,
    *,
    model="",
    effort="",
    max_rounds=None,
    timeout_s=None,
    context_mode="isolated",
)
```

`goal()` 独占规格完善、工作 Agent 轮次、完成判定、用户提问、进度状态、停止规则和终态。dispatcher 与 slash command 层不再维护第二个 Goal 循环。

## 两种调用界面

Programs 表单用 `context_mode="isolated"` 调用 `goal()`。DAG 和 runtime card 可以归属当前 session，但 Workflow 内的模型调用不能读取 Goal 调用之前的聊天历史。

`/goal <condition>` 用 `context_mode="session"` 调用同一个函数。函数开始时读取当前会话的压缩视图，并把它作为初始证据。表单与 slash command 唯一允许的行为差异是上下文来源。

不带参数的 `/goal` 查询当前 Goal 状态。`/goal clear` 把 active 或 waiting Goal 标为 cleared；正在运行的 Workflow 在工作轮次之间重新读取状态，停止时不得覆盖 clear。

## 一个状态机

两种界面使用同一个 Goal 状态与终态：

- `active`：正在完善、工作或判定；
- `waiting_user`：judge 请求决定，Runtime 问题正在等待回答；
- `achieved`：judge 接受条件，并且所有 checklist 项完成；
- `capped`：达到轮次上限；
- `error`：连续 judge 失败或 checklist 停滞导致终止；
- `cleared`：用户清除 Goal。

Workflow 将状态写入所属 session，因此 GoalChip、status/clear、页面恢复和所有调用界面观察同一个对象。状态只保存控制数据，不保存复制的 session context 快照。

## 上下文与信任边界

`render_range={"callers": 0}` 让每个 Goal 调用与调用前 DAG 历史隔离。session 模式只恢复函数入口显式生成的 session 快照。工作模型收到明确说明：该快照是非可信会话数据。完成 judge 接收同一份初始快照，加上当前 GoalRun 已产生的结果。

refinement 与 judgment 使用只读 spawn 调用。它们沿用现有受限工具集，可以检查工作目录，不移动 session head，也不创建另一个 Goal。

## 用户提问与取消

`need_user` 在同一 Workflow execution 中使用 Runtime question channel。Goal 状态先变为 `waiting_user`，问题经过现有子进程 question bridge，回答返回后恢复同一循环。不再把下一条普通聊天消息解释成另一套 session loop 的回答。

Stop 使用普通 function run 的 cancellation boundary。`/goal clear` 是每轮之间检查的协作式状态变化，不新增执行控制器。

## 实现状态

满足下列条件后，单一 Workflow 合同才算实现：

- Programs 与 `/goal` 都进入已注册的 `goal` 函数；
- dispatcher 不再存在 `continue_goal_turns` 循环；
- isolated 模式不读取或采用 session Goal 上下文；
- session 模式显式传入 session 快照；
- Goal、command、dispatcher、Web、question、cancellation 和 reload 的聚焦检查通过；
- 默认 App 验证两种界面显示同一 Goal 状态和 runtime execution。
