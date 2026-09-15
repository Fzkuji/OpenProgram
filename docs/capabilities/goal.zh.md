# 聊天 Goal

Goal 让目标跨普通聊天轮次保持有效。当前 agent 使用会话的工具、模型、权限和历史执行任务。一次聊天成功结束后，runtime 为仍 active 的 Goal 发起下一轮普通聊天。聊天模式不嵌套工作 agent，也不自动另起独立 judge。

## 开始与规划

输入 `/goal <目标>`，或使用 **Programs → Workflow → goal → Use**。表单启动聊天 Goal，不执行 Python Workflow。也可以明确要求 agent 创建 Goal。

agent 使用 `todo_create`、`todo_update` 和 `todo_list` 维护多步骤工作计划。Goal 工作期间创建的 todo 关联目标及其 revision。每轮结束后，Goal 详情显示计划进度。目标独立于计划保存：全部勾选不能单独证明目标已完成。

agent 调用 `update_goal(status="complete")` 前，必须用当前实际证据核实每项要求。关联 todo 尚未完成时，runtime 拒绝标记完成。这是结构检查，不是独立语义验证。简单目标不必为了形式创建 todo。

## 控制执行

页面重载后，Goal 详情保留目标、状态、用量和进度。通过普通聊天输入补充要求，遵循已有排队或插入当前执行的选择。

- `/goal`：读取当前状态。
- `/goal pause`：保存暂停状态，并请求取消当前执行。
- `/goal resume`：恢复暂停目标，继续累计用量。
- `/goal edit <目标>`：保存新 revision 并暂停，准备好后显式恢复。
- `/goal clear`：取消目标。
- `/goal budget max_turns=10 max_tokens=10000`：修改上限，零表示移除对应上限。

Goal 默认不限制执行轮数。只有显式设置正数 `goal.max_turns` 或启动时轮数预算才有上限；达到上限不表示完成。进度标记显示 todo 完成情况，不显示执行轮数。已有 Goal 保留保存的预算，可用 `/goal budget max_turns=0` 移除旧轮数上限，不重置工作或用量。显式 token、active-time 和成本限制按累计用量计算。预算耗尽后必须先增加或移除上限才能恢复。等待时间不计入执行时间。自动续轮不会扩大工具权限。

执行失败或取消后停止自动续轮。worker 重启后，中断的 Goal 保持可恢复暂停，需要显式恢复；尚未实现外部事件自动唤醒。等待或尚未结束的执行不代表目标完成。

`create_goal`、`get_goal` 和 `update_goal` 是短时间的状态操作，不负责执行任务。只有同一个已核实阻塞连续出现至少三轮，且没有独立工作可做时，agent 才能标记 `blocked`。runtime 校验最低轮数；是否为同一个阻塞由 agent 根据证据判断。

## Python Workflow 兼容

聊天表单保留显式的轮数、token、活跃时间和费用限额；独立 Workflow 角色参数和隔离上下文会明确报错，聊天工作应通过会话设置配置。Rich REPL 的 `/goal` 启动普通 canonical chat，并显示 execution ID。

直接 Python 调用和组合 Workflow 中的 `goal()` 保留已有 work/refinement/judge 合同。这个兼容路径与聊天 Goal 分开，仍接受 `context_mode`、work/judge 模型设置和执行上限。聊天模式使用会话工作模型，不使用单独角色设置。

迁移及实现证据见[工程设计](../reference/design/runtime/goal.zh.md)。
