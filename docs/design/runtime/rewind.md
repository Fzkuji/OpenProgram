# Rewind — 回退到任意历史消息

> 状态: **实现中** (2026-06)
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
6. 返回 `{ user_text, turns_reverted, restored_paths, errors }`

关键：**直接接受 user 节点 ID**，不需要转换成 assistant ID。

### 3.2 后端 WS handler

`handle_rewind(ws, cmd)`:
- 接收 `{ session_id, target_msg_id }`
- 调用 `rewind_to`
- 返回 `{ type: "rewind_result", data: { user_text, ... } }`

### 3.3 前端

`rewindToHere()`:
1. 发 WS action `{ action: "rewind", session_id, target_msg_id: msg.id }`
2. 收到 `rewind_result` 后：
   - 调用 `useSessionStore.getState().setComposerInput(data.user_text)` 回填输入框
   - 调用 `wsSend({ action: "load_session", session_id })` 刷新消息列表（rewound 的消息不再显示在当前分支）
   - 显示 toast
