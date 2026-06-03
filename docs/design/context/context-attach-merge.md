# Context 管理总览：常规 / Attach / Merge

> 配套 context commit chain 的总体设计 (`context-commit-chain.md`)。
> 本文统一描述三种情况下 LLM context 怎么管理：
> 1. **常规** 单分支聊天
> 2. **Attach** 把别的分支引用进当前对话（一个或多个）
> 3. **Merge** 多个分支聚合产出一个新 turn

---

## Part 1. Context 管理需要考虑的维度

不论哪种场景，凡是要往 LLM context 里塞东西或重组 context，都得想清楚下面这 13 件事。这是单一一张"checklist"，后面每个场景都按这个列表逐项填一遍。

### D1. 数据入口

新的内容怎么进入 ContextCommit 的 items 列表？
- 是 DAG 上新节点（user / assistant / tool / system）？
- 是引用别的分支的 ContextCommit？
- 是把多个 peer 的产物拼一段 user prompt 喂回 LLM？

不同入口决定了 generator 怎么把 raw 数据转成 ContextItem。

### D2. 初始 state / locked

item 进入 commit 时是什么状态？

- `state=full, locked=False` → 跑完规则流水线后再决定命运（默认）
- `state=full, locked=True` → 永远是全文，规则不动（罕见，仅 system prompt / anchor 用）
- 别的初始 state（aged / cleared / summarized）→ 不允许，规则单向收紧

### D3. Token budget

每个 commit 有一个总 budget（`budget_total`）和一个触发 summarize 的阈值（`budget_summarize_threshold`，一般是 budget_total 的 70-80%）。

- budget_total 多大？哪里读？
- 超 threshold 时是不是触发 LLM summarize？
- 多分支 merge 一次性引入 N 个 commit 加起来 token 数翻倍，budget 怎么不爆？

### D4. Tail window

尾部多少 turn 必须保留全文，不动？这是 `tool_aging` 规则的核心参数（默认 `tail_turns=3`）。

- tail 长度怎么定？
- attach / merge 引入的内容怎么算 tail？是按 commit 内 position 算，还是按时间算？

### D5. Tool aging（"microcompact" 行为之一）

老的 tool result 替成一段简短的 "stub" 字符串保留语义但省 token。

- 触发条件：tool result 距离当前 head 超过 tail_turns
- 替换策略：保留 tool name + 输入参数摘要 + result 的第一行 / 最后一行
- 影响：state full → aged + locked

### D6. Idle clearing（"microcompact" 行为之二）

更老的 tool result 干脆清空，替成固定占位符（`[Old tool result content cleared]`）。

- 触发条件：tool 节点 60 min idle（或更久）
- 优点：cache prefix 稳定，节省 token 更多
- state aged 或 full → cleared + locked

### D7. Summarize（"compact" 行为）

整个 commit 超 budget threshold 时，跑 LLM 把多条老 item 折成一条 summary item。

- 触发条件：`total_tokens > budget_summarize_threshold`
- 选哪些 item summarize：通常是最老的连续若干条 full / aged item
- 锚点机制：高价值节点（被新节点 cited、被钉选的）保留 full，其他折进 summary
- 影响：被合的 item state → summarized + locked，新增一条 state=summary 的 item

### D8. Dedup（跨 turn 去重）

同一个引用（attach pointer）在跨多 turn 时，会不会被重复展开？

- attach 跨 turn 展开恰好一次（首次出现的那个 turn）
- 之后 turn 顺着 parent commit copy items，已经存在 `attached_from=X` 的 item 不再重展开

### D9. 冻结时机

item 什么时候被锁住（locked=True）不再被规则动？

- 规则把它判定为 aged / cleared / summarized / summary 之一后，立即锁
- locked 之后跨 turn 通过 `_copy_item` 一路复制下去，永远不变

### D10. Regen 触发

什么操作让 generator 跑一次产出新 commit？

- 用户发新 chat → 一次新 turn → 一次 generate_commit
- attach / merge / spawn 的 ws action 内部也会触发新 turn
- 撤回（revert）操作 → 重置 head_id 后下个 turn 自动重新生成

### D11. Commit chain 形状

parent commit 怎么连：

- 单父：普通 turn / attach / spawn 之后，新 commit 只有一个 parent
- 多父：merge turn 的 commit 同时记录所有 peer 分支的 commit id 为 parent
- 多父用 `parent_ids: list[str]`，UI 时间线靠它画分叉

### D12. 可追溯字段

item 上有哪些 metadata 帮 UI / debug 区分这条 item 哪来的：

- `source_node_id`：对应 DAG 哪个节点（summary 用虚拟 sm_ 前缀）
- `attached_from`：如果是从 attach 展开来的，记 source 分支 commit id
- `state_set_at`：哪个 commit 把它的状态定下来
- `reason`：状态变化原因（"new" / "tail_window" / "idle_60min" / "attached_from:X" 等）

### D13. Fallback / 边界

- 数据缺失（旧数据没 `attached_from`、source commit 被 GC 掉）怎么走？
- source 分支还没生成过 ContextCommit 怎么办？
- attach 的 source 跨 session，对方 session 不可达怎么办？

---

## Part 2. 每种场景按维度过一遍

---

## 场景 A：单分支常规对话

最基础场景：用户在一个分支上一轮接一轮发消息。这是其他场景的 baseline。

| 维度 | 设计 |
|---|---|
| **D1 数据入口** | DAG 上当前 turn 新增的节点：user msg + 0..N 个 tool call + assistant reply |
| **D2 初始 state** | 全部 `state=full, locked=False` |
| **D3 Budget** | `budget_total` 从 provider 配置读（GPT-5.5 约 128k，留 chat + tool 余量后约 80k 给 history）；`summarize_threshold` = 70% × budget_total |
| **D4 Tail window** | 默认 `tail_turns=3`：最后 3 个 turn 的所有节点保留 full |
| **D5 Tool aging** | 超 tail 的 tool result → aged stub。stub = `[tool <name>] output: <第一行>... <最后一行>` |
| **D6 Idle clearing** | 超 60min 没动过的 aged tool result → cleared 固定占位符 |
| **D7 Summarize** | total_tokens > threshold 触发 LLM 摘要老 user/assistant turn → 折成一条 state=summary 的 item，被折的 turn state → summarized |
| **D8 Dedup** | 无需 dedup：每个 turn 的新节点天然只展开一次 |
| **D9 冻结** | 规则跑完即锁。后续 turn 通过 `_copy_item` 永久保留 |
| **D10 Regen** | 用户发 chat / retry / revert 都触发新 turn → 新 commit |
| **D11 Chain 形状** | 严格线性：`parent_ids=[prev_commit_id]` |
| **D12 可追溯字段** | `source_node_id` = DAG 节点 id；`reason` 由具体规则填（"new" / "tail_window" / "idle_60min" / "summarized_into:sm_xxx"） |
| **D13 Fallback** | 新 session 没有 parent commit → items 从空列表开始；新节点全部 state=full 进入 |

---

## 场景 B：Attach 一个分支

用户在 Branches panel 点 "Attach to"，或者 `/task` spawn 自动产生 attach pointer。语义：把 source 分支的 ContextCommit 在当前分支的下个 turn 里展开成一组 item。

| 维度 | 设计 |
|---|---|
| **D1 数据入口** | 当前分支 chain 上挂一个 function="attach" 节点，它的 metadata 含：`source_session_id` / `source_head_id` / `source_commit_id` / `label` / `manual`。generator 看到这个节点时，去 `load_commit(source_commit_id)` 加载源 commit，把它的 items 展开 |
| **D2 初始 state** | 展开后的每条 item 都 `state=full, locked=False`（跟原生 turn 平等），让规则流水线统一处理。**不预先 lock**——这是关键决策，attach 内容能被压缩 |
| **D3 Budget** | 同 D3 of A：commit 总 token 受 budget_total 约束。N 个 attach 一起进，加起来超 threshold 就触发 summarize |
| **D4 Tail window** | tail_turns 按 commit 内 item position 算（最后 3 个 turn 的位置）。attach 展开的内容如果落在 tail 内（很少见，只发生在 attach 后立刻 turn 完毕），保持 full；否则规则按位置老化 |
| **D5 Tool aging** | attach 展开的 tool item 跟原生 tool item 一样按 tail_turns 老化。`attached_from` 字段不豁免规则 |
| **D6 Idle clearing** | 同 D6 of A：attached tool item 超 60min idle 也会被清，但是 idle 时间从 source commit 那边的 wall-clock 算（item 自带 created_at 跨 commit 传递）|
| **D7 Summarize** | attach 引入的所有 item 跟原生 item 一起进 token 统计。超 threshold 时，summarize 规则照常工作，但选 item 时优先**保留 attach 边界**——不要让 summary 跨过 `attached_open` / `attached_close` marker（要么整段 attach 块都被折，要么都不折），避免 summary 内容里掺杂"半个 attach"难以解读 |
| **D8 Dedup** | parent_commit 已经有 `attached_from == source_commit_id` 的 item，跳过本次展开。这样 attach pointer 在 commit chain 上只展开一次（首次出现的那个 commit），之后 turn 通过 _copy_item 复制 |
| **D9 冻结** | 规则跑完后 locked，跟原生 item 一致 |
| **D10 Regen** | 写完 attach pointer 后必须 trigger 一次 ws `session_reload`，让 client 重新拉 chain；下一次用户发 chat 时，generator 看到这个 attach pointer 才会展开 |
| **D11 Chain 形状** | attach 不动 commit chain 形状：仍然是单父 `parent_ids=[prev_commit_id]` |
| **D12 可追溯字段** | 每条 attached item: `attached_from = source_commit_id`，`reason="attached"`；额外两条 marker item `attached_open` / `attached_close` 包起来，UI 渲染时方便圈出整段 |
| **D13 Fallback** | (a) attach pointer 没记 `source_commit_id`（旧数据）→ 走老路径：单条 user-role item，content 来自 attach.head_id 那条 assistant 的 raw output。(b) `source_commit_id` 写了但 load 不到（commit GC 掉了）→ 同 (a)。(c) attach 跨 session 但对方 session 不可达 → 同 (a)，外加在 marker 里标注 "(source unavailable)" |

---

## 场景 C：Attach 多个分支（同一 turn）

用户连续做了 N 次 attach（或者一个 turn 上同时挂了多个 attach pointer）。语义和数据结构跟 B 完全一样，但 budget 压力翻倍。

| 维度 | 设计 |
|---|---|
| **D1 数据入口** | N 个 attach pointer 节点；generator 遍历它们各自 load 各自的 source commit 展开 |
| **D2 初始 state** | 每个 attach 展开的 item 都 full + unlocked。N 个 attach 之间互不影响 |
| **D3 Budget** | 关键场景。假设 N=5、每个 attach 平均 2000 token、当前分支本身 4000 token：5×2000 + 4000 = 14000，远超 8k threshold → 必然触发 summarize。设计上 OK 让它触发，summary 规则会把最老的 attach 整段折成一条 summary item |
| **D4 Tail window** | tail 按 commit 内位置算，跟 attach 数量无关。多 attach 的位置都在 tail 之外（除非用户 attach 后立即不发 chat），所以都会被规则候选 |
| **D5 Tool aging** | 每个 attach 块里的 tool item 独立按 tail 老化 |
| **D6 Idle clearing** | 同 D6 of B：每条 item 看自己的 wall-clock idle 时间 |
| **D7 Summarize** | 多 attach 触发 summarize 时，规则按 commit 内位置从前往后逐个 attach 块整体折叠（保 attach 边界完整）。一般情况：先折最早 attach 进来的块，直到 total_tokens 回到 threshold 以下 |
| **D8 Dedup** | 每个 attach pointer 独立 dedup（按各自的 source_commit_id） |
| **D9 冻结** | 同 B：规则跑完锁住 |
| **D10 Regen** | 同 B |
| **D11 Chain 形状** | 同 B：单父 |
| **D12 可追溯字段** | 每条 item 标自己的 `attached_from`（不同 source_commit_id）；UI 端按 attached_from 分组渲染（每组一对 open/close marker） |
| **D13 Fallback** | 每个 attach 各自 fallback。可能 5 个里有 4 个走新路径、1 个走旧路径（混合使用没问题，因为 generator 是逐 pointer 处理） |

---

## 场景 D：Merge 多个分支

用户在 Branches panel 多选 N 个分支，点 Merge。语义：在 target session 上**触发一次新 LLM turn**，让它看到 N 个 peer 分支的 ContextCommit 视野，产出一个合并回复。merge 同时写一个 multi-parent ContextCommit 记录血统。

merge 跟 attach 的本质区别：merge 是**主动**触发 LLM 跑一次；attach 是**被动**stage 等下次 LLM 跑。所以 merge 的 attach 部分等价于"在 LLM 跑之前，给 target session 临时注入 N 个 attach 块"，复用 attach 的全部机制。

| 维度 | 设计 |
|---|---|
| **D1 数据入口** | (a) target session 当前 head 之后写入 N 个临时 attach pointer 节点，每个指向一个 peer 的末端 commit id。(b) 写一个 user msg 节点，content 是 merge instruction（"上面是 N 个并行分支的工作脚本。请整合成一个连贯答复... [用户的合并指令]"）。(c) 触发 `process_user_turn` 跑 LLM。 |
| **D2 初始 state** | N 个 attach 展开后的 item + merge instruction user msg + assistant 回复，都按常规 `full + unlocked` 进入 |
| **D3 Budget** | merge 最容易爆 budget。同 C 的分析：N=5、每个 5k token 就 25k，肯定超 threshold → 触发 summarize。但 merge 的特殊性：**这是当前 turn 的输入**，summarize 把老 attach 折掉后 LLM 看不到原文，可能影响质量。需要给 merge 模式一个更高的临时 budget（比如 budget_total × 1.5），或者允许 merge instruction 提示 LLM "我看到的是经过压缩的总结，重要细节请基于 base_peer" |
| **D4 Tail window** | N 个 attach 块跟 merge instruction 都属于"当前 turn"，理论上都在 tail 内。但因为 merge 的 prompt 紧跟在 attach 后面跑 LLM，tail 规则其实不太影响（规则在 turn 结束后才跑） |
| **D5 Tool aging** | 同 C |
| **D6 Idle clearing** | 同 C |
| **D7 Summarize** | 同 C。但 merge 模式有个额外考虑：`base_peer` 参数指定的那个 peer 是"主线"，它的 attach 块**优先保留 full**（locked=True），其他 peer 的可以正常被 summarize。这样 merge agent 一定能看到 base 的原文 |
| **D8 Dedup** | merge 是一次性操作，N 个 attach pointer 都是新的，没有 dedup 问题 |
| **D9 冻结** | merge 跑完后，所有 attach 引入的 item + merge 自己的 user msg + assistant 回复都按规则锁定 |
| **D10 Regen** | merge ws action 同步走完整 turn（LLM 跑、commit 落地、broadcast session_reload）。失败要回滚 attach pointer 写入避免脏数据 |
| **D11 Chain 形状** | **多父** commit：`parent_ids = [target_prev_commit, peer_1_commit, peer_2_commit, ...]`。这是 ContextCommit 唯一会产生多父的场景。UI 时间线靠 parent_ids 画出"N 个分支汇流"形状 |
| **D12 可追溯字段** | merge 引入的 attach item 仍然带 `attached_from`；merge 的 assistant reply 节点 metadata 加一个 `merged_from: [peer_session_id_or_head, ...]` 数组，方便回看 |
| **D13 Fallback** | (a) 任一 peer 没有 ContextCommit → 那个 peer 走 attach 的 fallback 路径（裸文本）。(b) base_peer 索引超出范围 → 降级为对称 merge（所有 peer 平等）。(c) 全部 peer 都 fallback → 等价于现在的"final_text 拼 prompt"路径，保底可用 |

---

## Part 3. 现状 vs 目标的差距

| 行为 | 现状 | 目标 | 差距 |
|---|---|---|---|
| 单分支常规 | 已实现 RULE_PIPELINE | 同 | 无 |
| Attach 注入内容 | 单条 user-role item（只末端 assistant content）| 完整展开 source commit | 大 |
| Attach dedup | 没有跨 turn dedup 概念 | 按 attached_from 字段 dedup | 中 |
| Multi-attach | 每个独立按当前裸文本路径处理 | 每个独立展开，规则统一压 | 大 |
| Merge | 拼 final_text 当 prompt | 改成 attach 展开路径 | 大 |
| base_peer 处理 | 在 prompt 文案里区分 base | base attach 块 locked=True 强保留 | 中 |
| 多父 commit | 已有 parent_ids 字段 + 合并逻辑 | 同 | 无 |

---

## Part 4. 改动清单

按依赖顺序：

| 步骤 | 文件 | 主要改动 |
|---|---|---|
| 1 | `openprogram/context/commit/types.py` | `ContextItem` 加 `attached_from: Optional[str]` 字段 + to_dict/from_dict |
| 2 | `openprogram/webui/ws_actions/branch.py::handle_attach_branch` | 写 attach pointer 时调 `load_commit_for_head(source_session, source_head)` 取 commit_id 存进 attach blob |
| 3 | `openprogram/context/commit/generator.py` | `_build_item_from_node` → `_build_items_from_node`（返 list）；处理 attach 节点的展开（D1+D8+D12）+ dedup check（D8） |
| 4 | `openprogram/context/rules/summarize.py` | summarize 选 item 时尊重 attach 边界（D7）|
| 5 | `openprogram/agent/_merge.py::process_merge_turn` | 拼 prompt 改用临时 attach pointer 路径；base_peer 处对应 attach block 标 locked（D7 of D）|
| 6 | `web/components/chat/messages/attach-card.tsx` | preview 改成多行 + 显示 token 估算（D12 渲染） |
| 7 | Tests | 单测覆盖 attach dedup / multi-attach budget / merge base_peer 保留 / fallback 路径 |

---

## Part 5. 关键不变式（实施时校验）

1. **入口同一**：attach / merge / 当前分支新 turn 进 commit 的所有 items 默认 `state=full, locked=False`，跑同一套 RULE_PIPELINE
2. **冻结后不变**：`source_commit_id` 一旦写入永远不变；source 分支未来变更不影响已 attach 的内容
3. **dedup 不漏**：同一 attach pointer 在 commit chain 上展开恰好一次
4. **可追溯**：attach / merge 进来的 item 必带 `attached_from` 标记
5. **fallback 不破**：旧数据没 `source_commit_id` 时仍能正常工作，向后兼容
6. **多父 commit 唯一来自 merge**：常规 turn 和 attach 都不会写多父 commit
7. **attach 边界完整**：summarize 不跨过 attach 块的 open/close marker

---

## Part 6. 不在本设计范围

- 用户可调"attach 引入粒度"（只引最近 N 条 / 整段引）→ 未来
- "Refresh attach 到 source 最新 commit"按钮 → 未来
- ContextCommit 的 GC 策略（attach 引用别人的 commit，被引的 commit 不能被 GC）→ 当前 commit store 没有 GC，先不考虑
- 跨 session attach 的权限模型 → 现在都是本地 session，无权限
- merge 失败时的部分回滚（attach pointer 写入了但 LLM 跑失败）→ 需要 transaction-like 处理，初版用 best-effort cleanup
