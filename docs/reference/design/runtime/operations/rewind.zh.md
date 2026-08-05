# Rewind — 回退到任意历史消息

> 状态: **已实现** (2026-06)
> 参考: Claude Code `/rewind`
> 代码: `agent/_rewind.py`、`webui/ws_actions/chat.py`、`web/components/chat/messages/message-actions.tsx`

---

## 1. 行为定义

用户点击某条 **user 消息**上的 ↩ 按钮（或输入 `/rewind N`）后：

1. **文件恢复**：该消息对应的 turn 及之后所有 turn 的文件修改被 checkpoint 恢复
2. **消息文本回填**：该 user 消息的文本内容放回聊天输入框
3. **界面更新**：该消息及之后的所有对话从界面上移除
4. **DAG 分支**：旧的对话保留在 DAG 中（不删除），当前分支头移到该消息之前
5. 用户可以编辑输入框中的文本，重新发送 → 从那个点开始一条新分支

## 2. 和 Claude Code 的对比

| | Claude Code | OpenProgram |
|---|---|---|
| 触发 | `/rewind` 列出检查点，选一个 | ↩ 按钮 + `/rewind N` |
| 回退粒度 | per-prompt（每条用户消息） | 同 |
| 文件恢复 | checkpoint 快照恢复 | checkpoint 快照恢复 |
| 消息回填 | 用户消息文本放回输入框 | 同 |
| 对话处理 | fork conversation（新分支） | DAG 分支（旧对话保留不删） |
| bash 盲点 | 警告"不影响手动/bash 编辑" | 我们有统一入口触发，bash 也覆盖 |

## 3. 实现方案

### 3.1 后端 `_rewind.py`

`rewind_to(session_id, user_msg_id)`:

1. 在 DAG 中找到 `user_msg_id` 对应的 user 节点
2. 提取该节点的 `output`（即用户消息文本）
3. 找到该节点及之后的所有 assistant/llm 节点（按 seq 排序）
4. 对每个 assistant 节点调用 `revert_turn` 恢复文件
5. 对所有被 rewind 的节点标记 `metadata.rewound = True`
6. 把 store 的 head 移到目标之前的最后一个节点（rewind 到最开头时为
   `None`——head 绝不能停在已被 rewind 的节点上）
7. 返回 `{ user_text, turns_reverted, restored_paths, new_head_id, errors }`

关键：**直接接受 user 节点 ID**，不需要转换成 assistant ID。

`new_head_id` 是本次 rewind 落到的 head。自己持有 head 镜像的调用方必须把它写
回去——见 3.2。

### 3.2 后端 WS handler

`handle_rewind(ws, cmd)`:
- 接收 `{ session_id, target_msg_id }`
- 有运行中的 turn 时返回 `{ code: "run_active" }` 拒绝，避免 rewind 把 HEAD
  从正在流式输出的回复底下挪走
- 调用 `rewind_to`
- 只要 `new_head_id` 非 None 就交给 `server._set_active_head`，并重估
  context stats。判据是 head 是否移动，不是 `errors` 是否为空：文件恢复
  失败只是 rewind 的部分失败，store head 已经移动，镜像必须跟上；
  `errors` 继续随结果帧返回，作为部分失败的警告
- 返回 `{ type: "rewind_result", data: { session_id, user_text, ... } }`

**这一步 `_set_active_head` 不是可有可无的。** `rewind_to` 只写 store；webui
另有一份按会话的内存镜像 `_sessions[sid]`（`head_id` + `messages`），
`_save_session` 会把它原样写回 store。rewind 若跳过镜像，镜像仍停在 rewind 前
的 head，下一次保存就把 rewind 悄悄撤销了。所有移动 HEAD 的路径都走
`_set_active_head` 正是为此——它一次完成写 store、把新分支读回镜像、清消息缓存。

`handle_rewind_list(ws, cmd)`:
- 接收 `{ session_id }` —— 不带参数的 `/rewind` 发的就是它
- 返回 `{ type: "rewind_points", data: { session_id, points } }`，最新的在前

**这两类帧每一个都带 `session_id`，错误帧也带。** 监听方按它匹配：两个会话
同时 rewind 会发出同类型的帧，只按 type 匹配的监听方会认领先到的那个。

### 3.3 前端

`rewindToHere()`:
1. 发 WS action `{ action: "rewind", session_id, target_msg_id: msg.id }`
2. 收到 `session_id` 对得上的 `rewind_result` 后：
   - 调用 `useSessionStore.getState().setComposerInput(data.user_text)` 回填输入框
   - 调用 `wsSend({ action: "load_session", session_id })` 刷新消息列表（rewound 的消息不再显示在当前分支）
   - 显示 toast
3. 监听器同时挂 30 秒超时兜底。否则一次没发出帧就失败的 rewind 会让这行的
   操作按钮一直禁用到刷新页面。

不带参数的 `/rewind` 发 `rewind_list`；`use-ws.ts` 把返回的回退点渲染成
transcript 里一条带编号的 system 行，用户打 `/rewind N` 时编号还在屏幕上。
