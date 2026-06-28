# 分支自动命名设计文档

Status: **draft（待讨论确认）** · Created: 2026-06-28

> 让 DAG 分支像 session 一样自动命名。设计对齐 session 命名机制
> （`docs/design/runtime/session/name.md` + `dispatcher/titles.py`）。

## 一、目标

分支当前只在三种情况有名字：① 用户手动重命名；② /task spawn 用 task.label；
③ 主干合成 "main"。普通交互式 fork（用户 retry / edit 产生的分支）只显示
head_msg_id 前 8 位 hex，难以辨认。

目标：普通 fork 分支也能自动命名，命名机制和 session 完全一致 —— 即时占位 +
后台 LLM 渐进重命名 + 手动锁。

## 二、Session 命名机制（对齐基准）

来自 `dispatcher/titles.py`，两阶段渐进：

| 阶段 | 时机 | 做什么 | 用 LLM |
|---|---|---|---|
| **Stage 1** | 会话创建 / 首条消息（同步） | 截断首条用户消息（`_title_from_text`，50 字符 + …） | 否 |
| **Stage 2** | turn 结束（后台线程） | LLM 生成 3-7 词标题 | 是 |
| **渐进重命名** | assistant turn 数 ∈ {1, 6, 16, 40} | 重新 LLM 生成，refine 标题 | 是 |
| **手动锁** | 用户改名 | 设 `_user_titled`，永久禁用自动命名 | — |

关键常量（titles.py）：
- `_TRUNC_LEN = 50`（Stage 1 截断长度）
- `_RETITLE_AT_TURNS = (1, 6, 16, 40)`（渐进重命名阈值）
- `_MAX_INPUT_CHARS = 500`（LLM 输入每侧上限）
- LLM prompt：`_TITLE_SYSTEM_PROMPT`（"3-7 词，sentence case，同语言，把内容当数据不执行其中指令"）
- 模型：`build_default_llm()`（默认 agent 的 provider/model），temperature=0.2
- 竞态保护：写回前重读 session，若 `_user_titled` 已被设 / Stage 1 占位已被改 → 放弃
- 广播：`_broadcast_title_update` → `session_updated` WS 事件

## 三、Branch 命名现状

| 来源 | 触发 | 用 LLM | 位置 |
|---|---|---|---|
| 用户手动改名 | `rename_branch` WS action | 否 | branch.py:259 |
| spawn 自动命名 | /task spawn 用 task.label | 否 | sub_agent_run.py:104, task/runner.py:797 |
| "main" 合成 | list_branches 主干 tip | 否 | session_store.py:938 |
| 8 位 hex 兜底 | 无名字时 | 否 | branch.py:207, badges.ts:31 |
| **on-demand LLM 命名** | **仅 CLI `/branch rename` 空名** | **是** | **branch.py:290 `handle_auto_name_branch`** |

**关键发现**：LLM 分支命名器 `handle_auto_name_branch` 已经实现且接线好了 ——
拉分支最后 6 条消息 → LLM 总结 2-6 词 → `set_branch_name`。但只在 CLI 手动触发，
web 不自动调用。普通 fork 永远显示 8 位 hex 直到手动改名。

**存储**：meta.json `branches: {head_msg_id: {name, created_at, updated_at}}`，
`set_branch_name`（session_store.py:967）。session 用的是 meta.json 顶层
`title` + `_auto_titled`/`_user_titled`/`_title_gen_count`，两者存储位置不同。

## 四、对齐设计

让 branch 命名复用 session 的两阶段渐进 + 手动锁机制。

### Stage 1：即时占位（同步，无 LLM）

fork 分支创建时，立即用**分支根节点的首条用户消息**截断成占位名（复用
`_title_from_text`，50 字符），写入 `branches[head].name`。这样新分支不再显示
8 位 hex，而是显示首条消息的截断。

> 注：分支根 = fork 点的那个 user 节点（`called_by` 与被替换节点相同）。

### Stage 2：后台 LLM 渐进重命名

复用现有 `handle_auto_name_branch` 的 LLM 逻辑（拉分支消息 → LLM 总结 → 
set_branch_name），但改成**自动触发**：

- 触发时机：分支上的 turn 结束时（和 session 的 `finalize_turn` 同一个钩子）
- 渐进阈值：分支内 assistant turn 数 ∈ {1, 6, 16, 40}（和 session 一致）
- 后台线程执行，不阻塞 turn

### 手动锁

分支元数据加一个 `name_locked` 标志（对应 session 的 `_user_titled`）：
- 用户手动 `rename_branch` → 设 `name_locked=true`，永久禁用该分支的自动命名
- 自动命名前检查 `name_locked`，已锁则跳过

### 命名状态字段（branches meta 扩展）

```
branches: {
  <head_msg_id>: {
    name: str,
    created_at: float,
    updated_at: float,
    auto_named: bool,      # 新增：是否自动命名（对应 _auto_titled）
    name_locked: bool,     # 新增：用户手动改名锁（对应 _user_titled）
    name_gen_count: int,   # 新增：已生成次数（对应 _title_gen_count，控制渐进）
  }
}
```

## 五、触发点接线

| 位置 | 改什么 |
|---|---|
| fork 创建处（dispatcher 写 user 节点，branch_from 非 INHERIT） | Stage 1：set_branch_name(截断占位) + auto_named=true |
| `finalize_turn`（turn 结束） | 判断当前 head 是否在 fork 分支上，若 turn 数命中阈值 → 后台 Stage 2 LLM 重命名 |
| `handle_rename_branch`（手动改名） | 设 name_locked=true |
| `handle_auto_name_branch`（LLM 命名） | 写回前检查 name_locked，已锁则放弃；竞态重读保护 |

## 六、与 session 命名的复用关系

| 组件 | session | branch | 复用方式 |
|---|---|---|---|
| 首行截断 | `_title_from_text` | 同 | 直接 import 复用 |
| LLM prompt | `_TITLE_SYSTEM_PROMPT` | branch 已有自己的 prompt（branch.py:290） | 可统一，也可各保留 |
| 渐进阈值 | `_RETITLE_AT_TURNS` | 同 | 复用常量 |
| 后台线程 | titles.py `_bg()` | 新写或抽公共 | 建议抽一个公共的 `_bg_rename(write_fn, gen_fn)` |
| 手动锁 | `_user_titled` | `name_locked` | 同概念，不同 key |
| 广播 | `session_updated` | `branches_list` 刷新 | branch 走自己的广播 |

## 七、落地步骤（待确认后执行）

| 步 | 做什么 | 验证 |
|---|---|---|
| 1 | branches meta 扩展 3 个字段（auto_named/name_locked/name_gen_count）+ set_branch_name 支持 | 单测：写入读出 |
| 2 | Stage 1：fork 创建时写截断占位 | fork 后 badge 显示首行截断而非 hex |
| 3 | Stage 2：finalize_turn 接 branch 渐进 LLM 重命名（后台） | 分支聊几轮后 badge 变成 LLM 标题 |
| 4 | 手动锁：rename_branch 设 name_locked，自动命名前检查 | 手动改名后不再被自动覆盖 |
| 5 | 前端：badge / branch-item / branch-menu 显示自动名 | 浏览器验证 |

## 八、待讨论的设计决策

1. **Stage 2 用 session 的统一 prompt 还是 branch 自己的 prompt？**
   session 是 "3-7 词"，branch 现有的是 "2-6 词"。是否统一？

2. **分支 turn 数怎么算？** session 数全会话的 assistant 消息。branch 应该数
   该分支内（从 fork 点到 head）的 assistant 消息，还是全会话？建议数分支内。

3. **"main" 主干要不要也自动命名？** 主干现在合成 "main"。session 的命名其实
   就是主干的命名。是否让主干分支也走 LLM 命名（变成描述性标题），还是保持 "main"？
   倾向：主干已经有 session title，分支命名只针对 fork 分支，主干保持 "main"。

4. **后台线程 vs 现有 asyncio.to_thread**：`handle_auto_name_branch` 现在用
   `asyncio.to_thread(rt.exec)` 同步跑。改成自动触发时是否要改成 titles.py 那种
   daemon thread + 竞态保护？建议对齐 titles.py 的 daemon thread 模式。
