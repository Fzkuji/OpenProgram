# Session 操作流程

每个操作从触发到磁盘到前端，完整写一遍。

---

## 启动

进程启动时 SessionStore 做一次初始化：

1. 读 `index.json` 加载到内存 `_index` dict
2. 如果文件不存在或 JSON parse 失败 → 扫描所有 session 目录的 meta.json 重建 `_index`，写入 `index.json`
3. 遍历 `_index`，把所有 `status=running` 重置为 `idle`（崩溃恢复）
4. 清理空壳：0 条消息 + 创建超过 1 小时的 session → 删除目录 + 删注册表条目
5. 清理过期归档：`archived=True` 且 `updated_at` 超过 90 天 → 删除
6. 容量检查：注册表超过 1000 条 → 按 `updated_at` 升序删最旧的已归档 session

半残 session 的处理：
- 有 meta.json 没 history/ → 等同空壳，步骤 4 删除
- 有 history/ 没 meta.json → 步骤 2 扫描时读不到 meta.json，不注册，等同不存在

---

## 创建 session

3 个入口可以触发创建：

| 入口 | 场景 |
|------|------|
| `dispatcher.process_user_turn` | 用户发消息时，session 不存在则创建 |
| `channel handler` | 渠道消息到达时创建 |
| `session_context` | CLI / research harness 进入上下文时，session 不存在则创建 |

其他地方不创建 session。

### 完整流程

```
调用方调 create_session(session_id, agent_id, source=..., ...)
  → 创建 <state>/sessions/<session_id>/ 目录
  → 写 meta.json（id, agent_id, title, created_at, updated_at, source, status="idle", ...）
  → 写注册表：_index[session_id] = 摘要条目
  → 注册表原子写磁盘（临时文件 → os.rename）
  → 不广播（前端通过 list_sessions 发现新 session）
```

### 原子性

dispatcher 和 channel handler 的创建与写入第一条消息是原子的——创建后立即 `append_message`，不会产生空壳。

`session_context` 在 `__enter__` 时创建（因为后续 ContextVar 装载需要有效的 session），如果后续没写消息就异常退出，会产生空壳，由启动时清理处理。

---

## 写消息

```
调用方调 append_message(session_id, msg)
  → 写消息到 DAG（Git history/）
  → 如果 msg.role == "user"：
      → preview = 截取 msg.content 前 80 字符
      → _index[session_id]["preview"] = preview
      → _index[session_id]["updated_at"] = time.time()
      → 标记注册表脏（5 秒 debounce 写磁盘）
  → 不广播（消息内容通过独立的 streaming 通道推送）
```

### preview 截取

```python
def _truncate(text: str | None, max_len: int = 80) -> str | None:
    if not text:
        return None
    t = text.strip().replace("\n", " ")
    return t[:77] + "…" if len(t) > max_len else t
```

### 注册表写磁盘节流

`append_message` 更新注册表时，内存立即更新，磁盘写入 debounce（5 秒内最多写一次）。进程退出时 flush。如果进程被 SIGKILL 导致 flush 失败，启动时从 meta.json 重建即可恢复，最多丢失 5 秒的 preview 更新。

其他操作（create、update、delete）立即原子写磁盘。

---

## 更新字段

标题、状态、置顶、归档、未读等字段的更新都走同一条路径：

```
调用方调 update_session(session_id, title="新标题", pinned=True, ...)
  → 写 meta.json（只更新传入的字段）
  → 更新 _index[session_id] 中对应字段 + updated_at
  → 注册表原子写磁盘
```

广播由 WebSocket handler 层在调用 `update_session` 之后通过 `_broadcast` 发起（已实现，rename + flags 均走广播）：

```
→ 广播 session_updated：
  {"type": "session_updated", "data": {"id": "<session_id>", "title": "新标题", "pinned": true}}
→ 前端 handleSessionUpdated 收到后 patch 对应 session 并重渲染
```

`data` 只包含变更的字段，前端做增量 patch。

### status 的写入时机

dispatcher 在 turn 生命周期中写 status：

| 时机 | 写入值 |
|------|--------|
| turn 开始 | `update_session(session_id, status="running")` |
| turn 正常结束（前台） | `update_session(session_id, status="idle")` |
| turn 正常结束（后台） | `update_session(session_id, status="done", unread=True)` |
| turn 失败 | `update_session(session_id, status="failed")` |
| 等待用户输入 | `update_session(session_id, status="needs_input")` |

---

## 命名

标题有三个写入来源，都通过 `update_session(session_id, title=...)` 写入，走上面"更新字段"的完整流程。

### 自动命名（渐进式）

自动命名在对话演进过程中多次触发，随着上下文增多生成更精确的标题。
触发阈值：第 1、6、16、40 轮 assistant 回复时（`_RETITLE_AT_TURNS`）。

```
finalize_turn 末尾 → _maybe_auto_title：
  1. 检查 _user_titled → 用户手动改过名则永不自动重命名
  2. 统计当前 assistant 消息数 → 未命中阈值则跳过
  3. 首次（turn 1）：
     a. 立即截取 title = 用户消息前 50 字符
        → update_session(session_id, title=截取值, _title_gen_count=1)
     b. 启动后台 daemon 线程调 LLM
  4. 后续阈值（turn 6/16/40）：
     a. 直接启动后台 daemon 线程
     b. LLM 输入取最近 20 条消息（而非仅首轮）
  5. 后台线程：
     → 竞态检查：_user_titled 则放弃
     → 首次还检查 title 是否仍为截取值
     → 写入 update_session(session_id, title=LLM结果, _title_gen_count=N+1)
     → 广播 session_updated
```

### 用户主动重命名

- 手动输入新名字 → `update_session(session_id, title=新名字, _user_titled=True)`
  设 `_user_titled` 后自动命名永久停止。
- 让 LLM 重新生成（点按钮，title 为空）→ `_llm_rename()` → `update_session(session_id, title=LLM结果)`
  不设 `_user_titled`，自动命名继续。

LLM 标题生成的细节（prompt、参数、后处理）见 [name.md](name.md)。

---

## 列举

```
前端发送 WebSocket 消息 {"action": "list_sessions"}
  → handle_list_sessions：
      → session_store.list_sessions()：
          → 遍历内存 _index.values()
          → 按 filters 过滤
          → 按 updated_at 降序排序
          → 返回 rows[offset:offset+limit]
      → 补充 project 字段（从项目目录映射）
      → 发送 {"type": "sessions_list", "data": rows}
  → 前端渲染侧边栏和 Chats 页面
```

纯内存操作，不碰磁盘。

### 每条 session 返回的字段

注册表中的 15 个字段 + preview + project（列举时补充），共 17 个。完整列表见 [storage.md](storage.md)。

---

## 删除

```
调用方调 delete_session(session_id)
  → 删除 <state>/sessions/<session_id>/ 整个目录
  → 删除 _index[session_id]
  → 注册表原子写磁盘
  → 广播 session_deleted：
    {"type": "session_deleted", "session_id": "<session_id>"}
  → 前端收到后从列表中移除
```

注册表操作已内化到 `delete_session`。广播由 WebSocket handler 层通过 `_broadcast` 发起。

---

## 归档

```
调用方调 update_session(session_id, archived=True)
  → 走"更新字段"的完整流程
  → 前端收到广播后过滤显示
```

已归档的 session 受启动时数据维护约束：90 天过期 + 1000 容量上限。活跃 session 不受影响。

---

## 注册表写磁盘（通用）

所有注册表写磁盘操作都用原子写：

```
写入临时文件 index.json.tmp
  → os.rename(index.json.tmp, index.json)
```

防止崩溃导致文件损坏。
