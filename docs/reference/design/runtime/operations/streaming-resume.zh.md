# 运行中状态的持久化 / 重连恢复

## 问题

UI 上"正在跑"的产物（LLM streaming reply、tool call、agentic function、task spawn、
merge）在跑完之前**只活在内存和 WebSocket stream 里**。刷新页面就丢，因为 SessionDB
里这条 msg 还没写入；要等最后写盘后，新页面才能看到它。

如果 backend 跑到一半挂了或网络断了，那条产物就没了：没有状态可查、无法恢复，也看
不到它的进度。

## 目标

> 任何能在 chat / DAG 上看到的"运行中"产物，**第一时间就持久化一条
> placeholder**，之后每个 incremental update 落盘 + 推 WS。前端任意时
> 刻刷新 / 切回，能看到当前进度 + 继续接收实时流，零状态丢失。

## 五个组件

### 1. 统一 placeholder schema

每条 msg（无论 user / assistant / tool）都新增三个字段：

```
status:          "pending" | "running" | "done" | "error" | "aborted"
started_at:      float (epoch)
last_update_at:  float (epoch)
```

`pending` = 已分配 id 但还没开始；`running` = 正在产出；终态三选一。

**写入时机**：任何会跑一段时间的操作 **第一时间** 就写 placeholder：
- LLM streaming reply: dispatcher 调 LLM 之前
- tool call: tool dispatcher 调函数之前
- agentic function: `_execute/run.py` 跑 function 之前
- task spawn: 已经有 ([`runner.py`](https://github.com/Fzkuji/OpenProgram/blob/main/openprogram/agent/task/runner.py))
- merge: `_execute/_run_merge` 之前

### 2. 增量节流持久化

每条 `status=running` 的 msg 在跑的过程中，**节流 ~250ms** 把当前快照写
回 SessionDB：

- `content`: streaming partial / 当前 tree dump
- `metadata.context_tree`: agentic function 的 DAG snapshot
- `metadata.partial_tokens_used`: 累计 input/output token
- `last_update_at`: now

节流由一个 per-msg `_ThrottledSaver` 实例管理，确保 backend 退出前最
后一次必落盘（atexit / signal hook）。

跑完调 `finalize(status="done", content=...)` 写终态。

### 3. WS 按 msg_id 订阅

新 ws action：

```json
{ "action": "subscribe_msg", "session_id": "...", "msg_id": "..." }
```

backend 维护：

```python
_msg_subscribers: dict[tuple[str, str], set[WebSocket]]
```

每次 placeholder update 时既走持久化又 push 到该 channel：

```json
{
  "type": "msg_update",
  "data": {
    "session_id": "...",
    "msg_id": "...",
    "content_delta": "...",
    "tree": {...},
    "status": "running"
  }
}
```

订阅释放：客户端 `unsubscribe_msg` / 断连 / msg 进入终态后自动清理。

### 4. 前端 load_session 检测 + 自动续连

`session-store` 的 `feedFromConv` 调用之后，扫一遍所有 ChatMsg：

```ts
const running = msgs.filter(m => m.status === "running");
running.forEach(m => wsSend({
  action: "subscribe_msg",
  session_id: sid,
  msg_id: m.id,
}));
```

收到 `msg_update` 事件就 `patch` 对应 ChatMsg 的 content / tree。

UI 层：
- `AssistantBubble` 看到 `status=running` 渲染光标 / streaming 动画
- `RuntimeBlock` 看到 `status=running` 显示运行中 Execution DAG（已经支
  持，因为现在 stream tree 就这么画）
- attach card：跟现有 `status=running` 行为一致（已有）

### 5. 死亡检测 + abort sweep

worker 启动时：
1. 扫 `~/.openprogram/sessions/*/history/*.json`
2. 找 `status=running` 且 `last_update_at` 超过阈值（如 5 分钟）的 msg
3. 改 status=`aborted`，append 一行 `metadata.aborted_reason="worker restart"`

保证 backend 重启后没有"永远在跑"的孤儿 msg。

## Schema 改动

`openprogram/store/session/_msg_adapter.py::_node_to_msg`：

- 写入时把 `node.metadata.status` / `started_at` / `last_update_at`
  反映到 msg dict
- 没有 status 字段的旧节点默认 `status="done"`（向后兼容）

`openprogram/context/nodes.py::Call`：不动 dataclass，新字段全走
`metadata`。

## 不在范围内

- 真正的中断后恢复——backend 跑到一半挂了，重启后继续跑剩下的 sub-call。这需要
  checkpoint LLM context 并重放，工作量数倍于本设计。本设计下中断的 msg 会被标
  aborted，用户用 Retry 按钮重跑。
- 跨 session 全局活跃任务面板（现在有哪些 msg 在跑）。基于 `_msg_subscribers` 的
  keyspace 后续容易扩展。

## 实现状态

设计按可独立使用、独立回滚的几块落地：

| 块 | 范围 |
|---|---|
| 1 | placeholder schema + worker abort sweep |
| 2 | `_execute/run.py` agentic function 写 placeholder + 节流 tree save |
| 3 | dispatcher LLM reply 写 placeholder + streaming content save |
| 4 | inline tool call placeholder（bash 等长跑） |
| 5 | WS `subscribe_msg` channel + per-msg broadcast |
| 6 | 前端 load_session 检测 + auto subscribe + live patch |
| 7 | 测试 + edge cases（重启 / 断连 / msg_id 冲突） |

其中 task spawn 的 placeholder 已经有了
（[`runner.py`](https://github.com/Fzkuji/OpenProgram/blob/main/openprogram/agent/task/runner.py)），
`RuntimeBlock` 和 attach card 的 `status=running` 渲染也已具备。
