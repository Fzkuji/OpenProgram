# Async Task 生命周期

> 把 sub-agent 调用从同步阻塞改成"显式 task 实体 + 后台 worker + 可查可取消"。对齐 Claude Code 的 TaskCreate / TaskList / TaskGet / TaskUpdate / TaskStop。
>
> 当前现状基线：`run_agent_turn` (`openprogram/agent/sub_agent_run.py`) 同步阻塞 → 调用 `process_user_turn` → 返回 `AgentTurnResult`。`/task` tool、`/spawn` WS action、`_merge.process_merge_turn` 都直接复用这条同步路径。
> WebUI 已经有的协作机制：每个 session 一个 worker thread (`_execute_in_context`)，一个 `_cancel_events` dict (`_pause_stop.py`)，一个 `_running_tasks` dict 用于 UI spinner。这些是底盘，新的 Task 抽象在其之上叠加。

---

## Part 1. Async Task 生命周期需要考虑的维度

凡是要做 spawn / list / get / cancel 一套生命周期，下面 15 个点都得明确。后面每个场景都按这张 checklist 逐项填一遍。

### D1. Task 实体存什么

`Task` 至少要带：

- `task_id`：spawn 立刻返回的稳定 id（独立于 user_msg_id / assistant_msg_id）
- `subject`：单行简介（用于 list / panel 显示）
- `description`：完整 prompt（agent 调用时复用为 `prompt`）
- `agent_id`：跑哪个 profile
- `status`：D2 的状态枚举
- `created_at / queued_at / started_at / completed_at`：时间戳
- `parent_session_id`：task 跑在哪个 session 上（task 永远绑定一个 session）
- `parent_task_id`：spawn 这个 task 的 task（顶层 user-spawn 时为 None）
- `parent_msg_id`：触发 spawn 的 user / assistant msg id（用于把 attach 卡片挂回去）
- `context_mode`：`inherit` / `clean`
- `head_id`：task 完成后 sub-agent 落地的 assistant msg id（运行中为 None）
- `result_text`：sub-agent 最终回复（运行中为 None）
- `error`：失败时的错误字符串
- `cancel_requested_at`：cancel 信号写入时间
- `attempt`：重试时的计数（初版固定 0）

不要塞 `cancel_event` / `future` 这种 runtime 对象到 entity；entity 只描述 "what"，"how to cancel / wait" 由 runner 内部 map 解决（见 D5）。

### D2. 状态机

```
pending → queued → running → completed
                          ↘ cancelled
                          ↘ errored
```

- `pending`：spawn API 收到、entity 写入持久层、还没排上 worker pool。
- `queued`：交给 ThreadPoolExecutor 但还没 pick up（worker 全忙）。
- `running`：worker pick up 并开始 `process_user_turn`。
- `completed`：sub-agent 正常返回，`head_id` 和 `result_text` 写入。
- `cancelled`：cancel 事件被消费且 worker 退出（部分输出可能落地）。
- `errored`：worker 抛异常 / 进程崩溃恢复时未完成的 task。

每次状态转移都更新对应时间戳，不可逆。`pending → cancelled` 直接跳过 queued / running 也允许（用户在 worker pick up 前就 stop）。

### D3. Worker pool 模型

用 `concurrent.futures.ThreadPoolExecutor`，与现有 `_execute_in_context` 的 daemon thread 模型同构，且不需要把整个 codebase async-color。理由：

- `process_user_turn` 内部已经自己开 `asyncio.new_event_loop()` 跑 agent loop，外部包 thread 不会双重事件循环冲突。
- BashTool / 文件 IO 都是阻塞调用，线程模型对它们零成本。
- 并发上限按 `OPENPROGRAM_TASK_WORKERS` 环境变量配置，默认 4。超过上限 task 停在 `queued`，FIFO 公平。
- Backpressure：单 session 不限制（task 之间是 sibling，UI 显示串行的 spinner 不影响），全局 pool 上限就够了。后续要 priority 再加。

### D4. 持久化

复用现有 session-git meta：在 session repo 的 `meta.json` 旁边加一个 `tasks.json`，结构 `{task_id: TaskRow}`。每个 task 状态转移调一次 `commit_turn("task: ...")`，落 git 历史。

理由：

- task 永远绑定单一 session（D6），存在 session 目录下天然分区。
- session 已经走 git-as-truth，task 状态进 git history 反而帮 debug "为什么 task 卡住"。
- 不需要新一张 SQLite 表或新一个 DB 文件。

不可恢复策略：进程 crash 后，所有 status=`running` / `queued` / `pending` 的 task 在 startup 时一律标记 `errored`（error="worker died before completion"）。理由是 LLM call 已经发出去拿不回来；让用户重 spawn 是最干净的语义。

### D5. Cancel 信号传播

每个 running task 在 runner 内有一份 `threading.Event`（不存 entity）。Cancel API 触发时：

1. 写 `cancel_requested_at` 到 entity，状态转 `cancelled`（如果还在 queued / pending），或保持 `running` 等 worker 自然退出。
2. `cancel_event.set()` — 通过 `_pause_stop.register_cancel_event` 已经定义的 contract，传到 `process_user_turn(cancel_event=...)`。
3. `process_user_turn` 已经把 cancel_event bridge 进 asyncio.Event（`agent_loop` 调用），LLM provider stream 会在下一个 chunk 检查到 cancel 然后中断。
4. BashTool / 其他 subprocess：复用 `_pause_stop.kill_active_runtime`（已有）。Tool 层的 cancel 是合作式：每个 `@agentic_function` 的 pre-invocation hook 检查 `is_cancelled`（已有），下一个 tool call 入口会 raise `CancelledError`。
5. 兜底 timeout：cancel 后 30 秒 worker 还没退出，runner 把 entity 标 `cancelled`（error="cancel timed out, worker may be stuck")，然后 detach worker thread（不强杀，等 GC）。

工具自身原子操作（比如一个 `Write` 写一半）不中断，等当前 atomic 完成再退出。

### D6. Task ↔ session 的关系

一个 task 永远跑在**一个** parent session 上。跨 session 在概念上等于 merge / attach（已有），不在 task 范畴。

`task.parent_session_id` 就是 sub-agent `process_user_turn(session_id=...)` 用的那个 — 跟 `run_agent_turn` 现在的行为一致。sub-agent 的输出落地为该 session 的一个 branch（或新 root，看 `context_mode`），所以 session repo 既存了 task entity 又存了 task 产出，自洽。

### D7. Task ↔ sub-agent 的关系

`/task` 不再同步阻塞。所有 spawn 都走 task entity。语义切换：

- agent-facing tool `task(prompt, ...)` 默认变成 **async** — 立刻返回 `task_id`，不阻塞主对话。LLM 拿到 id 后选择继续干别的，或者立刻调 `await_task(task_id)` 复刻旧的同步语义（D15）。
- 兼容旗子：`/task --sync` (或 `wait=True` 参数) 在 tool 层包一层 `spawn → await`，对 LLM 透明地同步返回结果。
- 旧的 `_task_impl` 的实现仍保留 `run_agent_turn` 调用，但走 runner 入口。

`/spawn` (用户在 chat 输入 `/spawn label: prompt`) 也走同一条 spawn API，区别只是 caller 是 WS handler 而非 LLM。

### D8. Task ↔ ContextCommit 的关系

attach pointer (`function="attach"` 节点) 一直由 `_run_spawn` / `_task_impl` 在 sub-agent 跑完后写。改造后：

- spawn 时立刻写一个 **placeholder attach card** （`function="attach"`，`extra.attach.task_id = <task_id>`，`extra.attach.status = "running"`），content="(running)"。`source_commit_id` 留空。
- task 完成时 runner update 同一个 attach card 节点：填 `head_id` / `source_commit_id` / 替换 content 为 `final_text`，`status` 改 `completed` / `cancelled` / `errored`。
- generator 看到 `status="running"` 的 attach 节点：跳过展开（不进 commit items），只在 UI 显示卡片占位。看到 `status="completed"` 走现有 attach 展开路径（见 `context-attach-merge.md` 场景 B）。

这样 LLM 在 task 跑完前再触发新 turn 不会看到半成品 attach 内容，但用户能看到 spinner。

### D9. WS API

新增四个 ws action（参考 `ws_actions/` 现有命名）：

- `spawn_task` — `{action, session_id, prompt, description, agent_id?, context?, wait?}` → 立刻回 `{task_id, status, parent_msg_id}`。
- `list_tasks` — `{action, session_id?, status_filter?, limit?}` → `{tasks: [...]}`。无 session_id 等于全局列。
- `get_task` — `{action, task_id}` → 单条 entity + 当前 head_id / 部分 result（如果 running）。
- `cancel_task` — `{action, task_id}` → `{task_id, status}`（同步返回当前 status，不等 worker 退出）。

广播事件：

- `task_created`、`task_status` (queued / running / completed / cancelled / errored)、`task_progress`（可选，未来加 token 进度）。
- 复用现有 `running_task` 广播但加 `task_id` 字段（UI 兼容）。

### D10. agent-facing 工具

agent 用三件套：

- `spawn_task(prompt, description, agent_id?, context?, wait=False)` → 返回 `task_id`（wait=False）或最终 result（wait=True）。包装 `runner.submit(...)`。
- `await_task(task_id, timeout=None)` → 阻塞调用线程直到完成 / cancelled / timeout，返回 `{status, result_text, head_id, error}`。LLM 在并发场景调它来收尾。
- `cancel_task(task_id, reason?)` → `{ok, status}`。

变体 `await_tasks([id1, id2, ...], mode="all"|"any", timeout)` 用于 plan mode 的 wait_all（D14）。三件套加 wait_all = 四个工具，但 plan-mode 不暴露 wait_all 给普通 agent —— 普通 agent 只能 await 单个 id（避免乱用）。

### D11. UI 表达

- 右侧 panel 增一个 **Tasks** tab（紧挨现有 Branches / Context Commits panel）。列出当前 session 的所有 task entity：spinner + subject + status + 创建时间。
- 点开 task：跳到对应的 attach card（chat 里已有 placeholder）→ checkout 它的 head_id branch（如果 completed）。
- 每个 attach card 自带 status badge：`running` / `done` / `cancelled` / `error`。完成后行为同现在。
- 全局 sidebar 显示一个总计数徽章（多少 running）：复用现有 `running_task` 机制。

### D12. 错误恢复

- worker 抛异常：runner 捕获 → 状态 `errored` → `error` 字段填 `f"{type}: {msg}"` → attach card status 同步更新 → 广播 task_status 事件。
- 主进程 crash：参考 D4。startup hook 扫 `tasks.json`，把所有非终止态记为 `errored`。
- pool shutdown：进程关闭时 wait 5 秒；超时的 task 标 `errored` ("worker pool shutdown")。
- 同一 task_id 不允许重 spawn（spawn API idempotent on task_id 但默认是 mint 新 id）。

### D13. 测试边界

unit test 不跑真 LLM。需要的 seam：

- `runner.submit(req, *, sync_fn=...)` —— 把 `process_user_turn` 替成 fake 同步函数（接收 cancel_event，返回 fake `TurnResult`）。
- entity store 用 in-memory `MockTaskStore`（dict 而非 git，但同样接口）。
- 状态机测试矩阵：每对合法转移一个 case，每对非法转移一个 reject case。

集成测试覆盖：spawn → await → completed 一路、spawn → cancel mid-flight、spawn → worker raise → errored、多 task FIFO 排队、crash recovery 把 running 标 errored。

### D14. Plan mode 集成

plan agent 产出一个 plan（一组 sub-task spec），exit_plan_mode 完成时：

- plan 工具 / executor agent 接收 spec list（每条含 `description` + `prompt` + 可选 `agent_id`）。
- 顺序调 `spawn_task` 拿 N 个 task_id。
- 调 `await_tasks(ids, mode="all")` 等全部完成。
- 拿到结果后由 executor 综合（写一条 user-visible summary，或自动触发 `merge`）。

并发上限就是 D3 的 pool size — plan 列 10 个 task、pool 4 个，会有 6 个停在 `queued`，UI 显示排队。

### D15. 向后兼容

`/task` 在 chat 里继续是用户输入入口，行为不变（用户看到的是 attach card + 完整结果），但底层走 task entity。

LLM-facing `task(prompt, ...)` tool 提供两个语义：

- `wait=True` (默认，向后兼容)：内部 spawn + await + 把 result_text 当 return value 给 LLM。LLM 视角等同今天。
- `wait=False`：返回 `task_id`，LLM 自己决定何时 await。新代码 / plan mode 用。

切换标记藏在 tool 签名里，老 prompt 不改也能跑。当工具能力广播给 LLM 的描述里说明两种模式 + 推荐用法。

---

## Part 2. 每种场景按维度过一遍

### 场景 A：单个 sync `/task`（现状基线）

最常见情况：LLM 调 `task(prompt="探查 X")`，希望阻塞拿到结果。改造后行为不变，但底层走 task entity。

| 维度 | 设计 |
|---|---|
| **D1 entity** | spawn 时创建 entity（含 prompt / agent_id / context_mode = inherit），`parent_task_id=None`，`wait=True` |
| **D2 状态机** | 仍走完整 pending → queued → running → completed |
| **D3 worker pool** | submit 到 pool；pool 满则等 queued（同步语义下 LLM 会感知一点延迟但不变结果） |
| **D4 持久化** | 完整流程：每次状态转移写 tasks.json + git commit |
| **D5 cancel** | 用户 stop session → cancel 事件传到 task → sub-agent loop 中断；最终 status=`cancelled`，tool 返回部分输出 + `[cancelled]` 标记 |
| **D6 session 绑定** | parent_session = caller 的 session（不变） |
| **D7 sub-agent** | tool wrapper 内部 spawn + await：对 LLM 调用现场零变化 |
| **D8 ContextCommit** | placeholder attach card 短暂存在（毫秒到秒级，因为同步等结果），完成后立即更新；UI 几乎看不到 running 状态 |
| **D9 WS API** | tool 调用走 in-process API（不必经 WS）；UI 仍能通过 list_tasks 看到 |
| **D10 agent tool** | `task(...)` 默认就是 wait=True，覆盖 99% 既存代码路径 |
| **D11 UI** | Tasks panel 闪一下；attach card 直接出现完成态 |
| **D12 错误** | worker 抛错 → status=`errored` → tool 拿到 `[task error] ...` 字符串（保留现有错误格式） |
| **D13 测试** | unit：mock runner，验证 spawn + await 两次顺序 call；integration：真跑一个 trivial agent |
| **D14 plan mode** | 不适用（这是单 task） |
| **D15 兼容** | tool 签名不变，老代码 0 改 |

---

### 场景 B：单个 async task（agent 主动选 async）

LLM 决定干个长活，先 spawn 拿 id，回头再 await 或 cancel。

| 维度 | 设计 |
|---|---|
| **D1 entity** | spawn 时创建，wait=False；entity 立刻写盘 |
| **D2 状态机** | spawn 返回时通常已经 `queued` 或 `running`，对 LLM 透明 |
| **D3 worker pool** | submit 不阻塞 caller thread；caller LLM 继续下一个 tool call |
| **D4 持久化** | 同 A |
| **D5 cancel** | LLM 调 `cancel_task(id)` 或用户 UI cancel；两条路径一样（都进 runner.cancel） |
| **D6 session 绑定** | 同 A |
| **D7 sub-agent** | spawn / await 解耦：LLM 在两个 tool call 之间可以读文件、搜代码等 |
| **D8 ContextCommit** | placeholder attach card 在 spawn 那一 turn 就写出，状态=running；后续 turn LLM 看到 ContextCommit 里这块仍是占位（generator 看 status=running 不展开） |
| **D9 WS API** | spawn_task → 返回 task_id；UI 立刻看到 Tasks panel 增一行 |
| **D10 agent tool** | `spawn_task` 返回 `task_id` 给 LLM；后续 `await_task(task_id)` 拿结果 |
| **D11 UI** | Tasks panel running 行 + sidebar 总计数 + attach card with status badge |
| **D12 错误** | 同 A，但 LLM 是在 await 时才感知 error（也可能在 await 之前用 get_task 查到） |
| **D13 测试** | unit：spawn 返回 task_id 后状态= queued/running；await 后状态正确转移 |
| **D14 plan mode** | 是 plan mode 的基础 building block |
| **D15 兼容** | 新工具，老 prompt 不会触发 |

---

### 场景 C：并发 N 个 async task（plan mode）

plan agent 列出 5 个调研任务，spawn 5 个 task，调 `await_tasks(ids, mode="all")` 等齐。

| 维度 | 设计 |
|---|---|
| **D1 entity** | 5 个 entity，`parent_task_id` 都指向 plan agent 当前的 turn (`parent_msg_id`)，便于 list_tasks 按 plan 分组 |
| **D2 状态机** | pool size=4 时，4 个 → running，1 个 → queued；先完成的转 completed，queued 的开跑 |
| **D3 worker pool** | 关键场景。FIFO 公平；pool 满时 spawn 立即返回 task_id，状态=`pending`/`queued`，await_tasks 自动等 |
| **D4 持久化** | 每个 task 自己一行 tasks.json；每次状态转移 commit。可以期望一次 plan 有 ~10-20 个 git commit |
| **D5 cancel** | `cancel_task(id)` 单个；plan 整体撤销时 plan agent 自己遍历 cancel 所有 children（也可以加个 `cancel_tasks(parent_msg_id=...)` 批量 API 当 future） |
| **D6 session 绑定** | 5 个 task 全部跑在同一个 parent_session；落地后是 5 个并列 branch（branch label = task description）|
| **D7 sub-agent** | 5 个并发 sub-agent 同时跑，靠 thread pool 隔离；ContextVar (`current_session_id`) 是 thread-local，互不干扰 |
| **D8 ContextCommit** | 5 个 placeholder attach card 一字排开挂在 plan agent 的 fork point；完成顺序无关，UI 各自 update。后续 LLM turn 看到的 ContextCommit 里这 5 块是 attach 展开，按 `context-attach-merge.md` 场景 C 处理 |
| **D9 WS API** | spawn 5 次 + 1 次 await_tasks（包一层服务端等聚合，避免 LLM 多次轮询）|
| **D10 agent tool** | plan agent 用 `spawn_task` ×5 + `await_tasks(mode="all")` ×1 |
| **D11 UI** | Tasks panel 5 行；其中 1 行 queued 状态有时钟 icon。完成的逐个翻 completed |
| **D12 错误** | 部分失败：await_tasks 收齐所有终止态后返回 list，每条带自己的 status / error；plan agent 自己决定 partial 还是 retry |
| **D13 测试** | 关键覆盖 pool backpressure：spawn 6 个 task 但 pool=2，断言第 3-6 个停在 queued 直到前两个完成 |
| **D14 plan mode** | 这就是 plan mode 主要场景 |
| **D15 兼容** | 新 API，老代码无影响 |

---

### 场景 D：长时间 task + cancel

agent spawn 一个 30 分钟的 deep research task，10 分钟后用户在 UI 点 Stop 或 agent 自己想撤。

| 维度 | 设计 |
|---|---|
| **D1 entity** | 跟 B 一样；cancel 时填 `cancel_requested_at` |
| **D2 状态机** | running → cancelled（或如果 worker 在 timeout 内未退则 forced cancelled） |
| **D3 worker pool** | thread 一直 occupy 到 worker 真的退出；pool slot 在 worker function return 后释放 |
| **D4 持久化** | cancel 请求立刻 commit；worker 退出后再 commit 一次（最终状态） |
| **D5 cancel** | 这是设计核心。cancel_event.set() → (a) `process_user_turn` 内 asyncio.Event 触发 → agent_loop 在下一个 stream chunk break → LLM call 中止；(b) `is_cancelled(session)` hook 让下一个 `@agentic_function` raise CancelledError；(c) BashTool 通过 `kill_active_runtime` 杀子进程；(d) 30 秒兜底 timeout 强转 status |
| **D6 session 绑定** | 不变 |
| **D7 sub-agent** | sub-agent loop 拿到 cancel 后走 dispatcher 现有的 cancelled 分支：placeholder 已经 insert，error 折进同一行 → status=cancelled，部分输出落盘 |
| **D8 ContextCommit** | attach card 状态从 running → cancelled；content 写部分输出 + `[cancelled at T]` 标记；generator 看 cancelled 也可以选择性展开（初版：不展开 cancelled，只显示 marker）|
| **D9 WS API** | cancel_task 立刻返回（不等 worker），UI 显示 "cancelling..." 状态；worker 真退出时再一条 task_status 广播 |
| **D10 agent tool** | LLM 可以调 `cancel_task(id)`；后续 `await_task(id)` 立刻返回 cancelled 终态 |
| **D11 UI** | Stop 按钮已有；点击触发 cancel_task；spinner 变成 spinner + dim 颜色直到 worker 退出 |
| **D12 错误** | cancel timeout: 30s 后强转 status 但保留 worker thread；记 warn 日志；UI 提示 "task may still be running in background"  |
| **D13 测试** | 关键测试：fake sync_fn 故意忽略 cancel_event 30 秒，断言 runner 在 30s 后强转 cancelled 状态 |
| **D14 plan mode** | plan agent 可能在 partial 完成时主动 cancel 剩下的 queued task（节省 budget）|
| **D15 兼容** | sync /task 默认 wait=True 时，用户 stop session 会同时 cancel sub-agent + 父 turn（已有行为，不变） |

---

## Part 3. 现状 vs 目标的差距

| 能力 | 现状 | 目标 | 差距 |
|---|---|---|---|
| Task entity | 无 (只有 in-mem `_running_tasks` for spinner) | 完整 entity + 持久化 | 大 |
| 状态机 | 隐式 (函数返回 = 完成) | 显式 5 态 + 转移规则 | 大 |
| spawn 立刻返回 | 阻塞调用 | 立刻返回 task_id | 大 |
| 查询接口 | 无 | get_task / list_tasks | 大 |
| Cancel | 整 session 级 (`_cancel_events`)，无 task 粒度 | 单 task cancel | 中 (基础设施已在) |
| Worker pool | 每 session 一个 daemon thread (`_execute_in_context`) | 统一 ThreadPoolExecutor | 中 |
| 持久化 | `_running_tasks` 内存 dict (refresh 丢) | session repo tasks.json + git | 中 |
| Plan mode 并发 | 无 (`/task` 同步串行) | spawn N + await_all | 大 |
| attach card placeholder | 完成后才写 | spawn 时即写 with running 状态 | 中 |
| Agent tool | 仅 `task` (同步) | spawn / await / cancel 三件套 | 大 |
| UI Tasks panel | 无 (只有 sidebar spinner) | 独立面板 | 中 (前端) |
| 跨进程 task | 无 | 暂不做 (Part 6) | — |
| 重试 / 优先级 | 无 | 暂不做 (Part 6) | — |

---

## Part 4. 改动清单

按依赖顺序：

| 步骤 | 文件 | 主要改动 |
|---|---|---|
| 1 | `openprogram/agent/task/types.py` (新建) | `TaskStatus` enum + `Task` dataclass (D1) + 转移规则 helper |
| 2 | `openprogram/agent/task/store.py` (新建) | `TaskStore` 接口；落 `tasks.json` 在 session repo (D4)；同时实现 `MockTaskStore` 给测试用 |
| 3 | `openprogram/agent/task/runner.py` (新建) | `TaskRunner` 单例：`submit / cancel / get / list`；持有 `ThreadPoolExecutor` (D3) + `_cancel_events` (D5)；startup hook 标 orphan task errored (D12) |
| 4 | `openprogram/agent/sub_agent_run.py` | 改造：拆出 `_run_one(task: Task, *, cancel_event)` 包装 `process_user_turn`；新加 `submit_agent_task(...)` 异步入口；保留 `run_agent_turn(...)` 但内部走 `runner.submit(...).result()` |
| 5 | `openprogram/agent/task/agent_tools.py` (新建) | `@function` 实现 `spawn_task / await_task / cancel_task / await_tasks`，绑定到 toolset (D10) |
| 6 | `openprogram/webui/ws_actions/task.py` (新建) | 4 个 handler 对应 D9；注册到 `ws_actions/__init__.py` |
| 7 | `openprogram/webui/_execute/__init__.py::_run_spawn` | 改用 `submit_agent_task`；写 placeholder attach card 时带 task_id + status=running |
| 8 | `openprogram/context/commit/generator.py` | 处理 attach 节点时检查 `extra.attach.status`：running / cancelled / errored 不展开，只占位 (D8) |
| 9 | `openprogram/functions/tools/task/task.py` | `_task_impl` 内部改走 `submit_agent_task` + 默认 wait=True；wait=False 时返回 task_id |
| 10 | `web/components/right-sidebar/tasks-panel.tsx` (新建) | UI 表达 (D11)；订阅 `task_status` ws 事件 |
| 11 | `web/components/chat/messages/attach-card.tsx` | 渲染 status badge (running / done / cancelled / error) |
| 12 | `openprogram/agent/dispatcher.py::process_user_turn` | 启动时检查 `OPENPROGRAM_TASK_WORKERS` 并初始化 runner 单例（idempotent） |
| 13 | Tests | unit：state machine、runner submit + cancel + crash 恢复；integration：spawn → await、并发 N、cancel mid-flight (D13) |
| 14 | `docs/design/runtime/async-task-lifecycle.md` | 本文档 |

---

## Part 5. 关键不变式（实施时校验）

1. **终止态唯一**：每个 task entity 最终必有 completed / cancelled / errored 之一，不存在永久 running（pool shutdown / crash recovery 必转 errored）。
2. **状态单调**：状态机每条边只走一次。一旦 completed 不会再 → cancelled，不允许 running → pending。
3. **cancel 可达**：cancel API 返回后，30 秒内 entity 必出现 cancelled / errored 终态（强制 timeout 兜底）。
4. **placeholder 一致**：spawn 写的 attach card 跟 entity 同步状态；entity 状态变化必同步 update card。
5. **持久化 idempotent**：crash 后 reload，未完成 task 的状态确定为 errored；同 task_id 永不复活。
6. **session 绑定不可变**：task 创建后 `parent_session_id` 不变；同一 task 不跑两个 session。
7. **并发安全**：runner 内 `_tasks` + `_cancel_events` map 全程持锁；状态读写不竞争。
8. **测试可控**：runner 必须接受可注入的 `sync_fn` / `store`，不依赖真 LLM 也能跑全状态机。

---

## Part 6. 不在本设计范围

- **跨进程 task**：当前所有 worker 在主进程内。分布式 / multi-host 留给后续，需要 message broker。
- **Task 优先级 / SLA**：FIFO 即可，没有高优先级抢占。后续按需加 priority queue。
- **Resume / 续跑**：cancelled / errored task 不能"接着跑"。用户 retry 等于新 spawn 一个 task。
- **Task 重试策略**：runner 不自动 retry；上层 agent / plan 自己决定。
- **跨 session task**：一个 task 只绑一个 session。跨 session 用 attach / merge。
- **DAG-shaped task 依赖**：当前 `await_tasks(mode="all"|"any")` 已经够 plan mode；显式 DAG / pipeline 留给后续。
- **Task 输出流式订阅**：初版只在 task 完成时拿 final_text。中途订阅 stream（让父 agent 看到 sub-agent 边想边说）留给后续。
- **资源配额**：单 user 同时 task 数 / token 上限不在本设计，需要先有 multi-tenant 模型。
