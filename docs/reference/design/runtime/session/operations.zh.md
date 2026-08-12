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

## 主项目绑定

会话的**主工作目录**就是它所绑定项目的路径。草稿阶段可以自由选择，**第一条真实消息提交时定格**——从那一轮起，会话的 cwd、权限围栏基线、会话仓库位置（`<project>/.openprogram/sessions/<id>/`）都不再变动。定格的理由正在于此：这三样都跟着主目录走，而只有 `create_session` 能放置仓库，事后换项目会让仓库滞留在旧项目里，模型却在新目录里干活。

追加工作目录是相反的设计，也应该如此：会话生命周期内随时增删。见 [additional-working-directories.zh.md](../additional-working-directories.zh.md)。

### 选择发生在哪里

```
草稿会话，在 composer chip 里选了项目
  → pendingProjectsByChat[chatKey]（前端 store，尚未发送）
  → 第一条 chat 帧带上 project_id
  → handle_chat 用该项目建会话 → 仓库落在项目内部
  → chat_ack 再补一次幂等的 set_session_project（标签 + 反向索引）
```

`set_session_project` 在任何时候都接受"绑到会话已有的那个项目"——上面那条 ack 后的幂等 bind 走的就是这条路，它到达时第一轮已经提交。而在已有轮次的会话上绑**另一个**项目会被拒，理由为 `project is frozen after the first turn`（`ws_actions/project.py:FROZEN_ERROR`）。composer chip 与之对齐：已有 session id 的会话只读展示已绑项目，不再给选择器。

### 修复缺失的目录

定格后的主目录仍可**重定位**，且只为修复：目录在磁盘上被移动或改名，项目指向了不存在的位置。这种状态下 `project_workdir_for` 返回 `None`——绝不悄悄换成默认项目的家目录——于是该轮工具 cwd 回落到会话自己的 `workdir/`，而 `project_path_missing` 报出消失的那个路径。`list_projects` 把它作为每个项目的 `path_missing` 下发，chip 因此转为警示态，菜单里出现"定位文件夹…"。

修复动作是 `relocate_project` ws action：

```
relocate_project {session_id, project_id, path}
  → project_store.relocate_project：校验新目录、改写项目 path、**保留 id**
    （会话反向索引、项目设置、定格的绑定全挂在 id 上）
  → record_relocate：追加下面的记录节点
  → project_relocated {ok, old_path, path, node_id} + 一份新的 projects_list
```

它改的是**项目的路径**，而非会话→项目的绑定，所以定格之后依然合法。绑定到该项目的每个会话都跟着移动。默认项目拒绝重定位：它的路径是家目录，每次 `get_default_project` 读取都会被恢复。

### relocate 记录节点

这次移动改变了此后每一轮的运行位置，所以记进会话图，而不是无声改注册表（`openprogram/store/project/relocate_node.py`）：

| 字段 | 值 |
|---|---|
| `role` | `code` |
| `name` | `project/relocate` |
| `caller` | `ROOT` |
| `predecessor` | `None` |
| `output` | `<旧路径> → <新路径>` |
| `metadata` | `{display: "runtime", project_id, old_path, new_path}` |

形状照搬 `context/system_prompt`（dag/overview.md §3、§7）：写入不变式只约束对话节点；`caller` 已设，store 便不推进 head——记录重定位不动分支尖端。名字刻意**不放在** `context/` 下：那是隐藏于对话流的管线机器，而重定位是值得看见的用户动作。

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

广播由 WebSocket handler 层在调用 `update_session` 之后通过 `_broadcast` 发起，rename 与 flags 均走广播：

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

命名只有**一套权威实现**：`openprogram/agent/dispatcher/titles.py`。所有入口（WS / fn-form / channel / CLI / spawn）的命名都汇到 `finalize_turn 末尾 → _maybe_auto_title`，没有第二套截断/锁死逻辑。标题写入都通过 `update_session(session_id, title=...)`，走上面"更新字段"的完整流程。

### 锁标记（权威，仅两把 + 一个内部计数）

| 标记 | 类型 | 含义 | 谁来设 |
|------|------|------|--------|
| `_user_titled` | bool | 用户手动改名 → 永久锁，自动命名永不再跑 | **只有** rename 操作在用户输入了名字时设 |
| `_auto_titled` | bool | 自动命名已产出过至少一个标题（首轮截断或任意 LLM 写入）→ "别重复截断"去重位 | **只有** `_maybe_auto_title` 设 |
| `_title_gen_count` | int | 渐进式重命名内部计数（命中到 `_RETITLE_AT_TURNS` 第几个）| `_maybe_auto_title` 内部，非入口锁 |

不存在既表示"已截断"又表示"永久锁"的单一标记：一个标记兼做两件事会与两阶段流程冲突——阶段 1 的截断本就应当被 LLM 标题取代。因此各入口不自己做截断、也不设自己的锁，统一由 `_maybe_auto_title` 完成阶段 1（截断）和阶段 2（LLM）。入口唯一可设的锁是 `_user_titled`，由 rename 操作设置。

### 自动命名（渐进式，两阶段）

自动命名在对话演进过程中多次触发，随着上下文增多生成更精确的标题。
触发阈值：第 1、6、16、40 轮 assistant 回复时（`_RETITLE_AT_TURNS`）。

```
finalize_turn 末尾 → _maybe_auto_title：
  1. 检查 _user_titled → 用户手动改过名则永不自动重命名
  2. 统计当前 assistant 消息数 → 未命中阈值则跳过
  3. 首次（turn 1，阶段 1 立即截断）：
     a. title = _title_from_text(用户首条消息)
        （剥 [attachment:]/<attachment-preview>/<file> 标记 → 取首行 → 截 50 字，超出加 …）
        → update_session(session_id, title=截取值, _auto_titled=True, _title_gen_count=1)
     b. 启动后台 daemon 线程调 LLM（阶段 2）
  4. 后续阈值（turn 6/16/40）：
     a. 直接启动后台 daemon 线程
     b. LLM 输入取最近 20 条消息（而非仅首轮）
  5. 后台线程（阶段 2）：
     → 竞态检查：_user_titled 则放弃
     → 首次还检查 title 是否仍为阶段 1 截取值（被改过就放弃写入）
     → 写入 update_session(session_id, title=LLM结果, _auto_titled=True, _title_gen_count=N+1)
     → 广播 session_updated
```

### channel（微信 / Discord 等）

channel 会话命名与普通会话**完全一样**，走同一套两阶段 LLM 命名，channel 端**不**对标题内容做任何额外操作 / 锁定 / 干预（不设 `_user_titled`、不设 `_auto_titled`、不预截断）。来源标识只在前端显示层加方括号品牌前缀（如 `[WeChat] 周末计划讨论`），不进 title 本体。

### 空会话

创建即写第一条消息是原子的，正常不产生空会话；万一产生由启动清理或手动删除处理。命名层不对空会话做特殊过滤。

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
          → 除非 include_archived=True，否则丢掉已归档的行
          → 按 filters 过滤
          → 按 updated_at 降序排序
          → 返回 rows[offset:offset+limit]
      → 补充 project 字段（从项目目录映射）
      → 发送 {"type": "sessions_list", "data": rows}
  → 前端渲染侧边栏和 Chats 页面
```

纯内存操作，不碰磁盘。

### 已归档的行

`list_sessions` 默认隐藏已归档会话：只有默认列举遵守归档，归档才真的能约束列表
长度。两种方式看到它们：

- `include_archived=True` 同时返回已归档和活跃的行
- `archived=True` 只返回已归档的

那些必须遍历每个会话、不管标志的维护流程（记忆扫描、运行态修复、"清空全部"、
渠道绑定查找、agent 寻址）显式传 `include_archived=True`。侧边栏 payload 也发送
全部行，前端在活跃/已归档/全部三个视图之间切换无需再请求一次。

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

注册表操作在 `delete_session` 内部完成。广播由 WebSocket handler 层通过 `_broadcast` 发起。

---

## 归档

归档让会话列表不再无限增长。它只是 session meta 上的一个布尔标志：不删数据、
不搬目录，随时可逆。

```
调用方调 set_archived(session_id, True)
  → update_session(session_id, archived=True)
  → 走"更新字段"的完整流程
  → 前端收到广播后过滤显示
```

`set_archived` 对不存在的 session id 返回 `False`，CLI 和 REST 端点据此报告
会话不存在，而不是静默成功。

### 归档不动的东西

`updated_at` 记录的是最后一次追加消息的时间，归档不追加任何消息，因此不碰这个
时间戳。这是[索引一致性契约](index-consistency.html)：取消归档后会话回到列表
里原来的位置，不会被顶到最上面。消息、分支、history 文件都不动，归档期间
`get_messages` 照常可读。

### 入口

| 界面 | 操作 |
|------|------|
| WebSocket | `{"action": "update_session_flags", "session_id": ..., "archived": true}` |
| REST | `POST /api/sessions/archive` / `POST /api/sessions/unarchive`，body `{"session_id": ...}` |
| CLI | `openprogram sessions archive <id>` / `openprogram sessions unarchive <id>` |

三个入口都经 `set_archived` 写同一个标志并广播 `session_updated`，所以每个
打开的标签页看到的状态一致。

已归档的 session 受启动时数据维护约束：90 天过期 + 1000 容量上限。活跃 session 不受影响。

---

## 注册表写磁盘（通用）

所有注册表写磁盘操作都用原子写：

```
写入临时文件 index.json.tmp
  → os.rename(index.json.tmp, index.json)
```

防止崩溃导致文件损坏。
