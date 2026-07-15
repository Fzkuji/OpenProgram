# 上下文成分对比 —— 参考项目 vs 我们（按三层组织）

Status: **查漏记录** · Created: 2026-06-24

> 把参考项目喂给 LLM 的上下文成分，跟我们的 L0/L1/L2 设计对比，找出**我们漏了什么**。
> 本文只做对比，不改设计。
>
> **印证**：Hermes 自己也是**三层、按稳定度分**，跟我们 L0/L1/L2 是同一思路——
> 它叫 `stable / context / volatile`：
> - `stable`  = 身份 + 工具指导 + 技能 + 模型/平台/环境 hints  → 我们的 **L0**
> - `context` = caller system_message + 上下文文件（AGENTS.md 等） → 我们的 **L1 项目层**
> - `volatile`= 记忆快照 + USER.md + 外部记忆 → 我们的 **L1 项目记忆 / L2**
>
> 所以章法用三层即可。下面按 L0/L1/L2 分别对比，漏的成分直接落在它该在的层里。
> 其余三个项目（opencode / claude-code / openclaw / pi-mono）上下文成分都比 Hermes
> 少、和我们持平或更少；漏的几乎全来自 Hermes。

✓=有，-=无，△=零散有但没归层。

---

> **层内也按稳定度排序**：越稳定越靠前、越常变越靠后（缓存前缀匹配，层内顺序同样
> 影响命中）。下面每张表的 `#` 列就是**该层内从前到后的 wire 顺序**。历史这类每轮追加
> 的东西排在它所在层的最后。排序参考 Hermes 的 `stable_parts` append 顺序 + 我们的
> 缓存原则。

## L0 系统级（配好不动）

层内序：身份类（最稳）→ 指导块 → 工具/技能 → 环境信息（相对会变，靠后）。

| # | 成分 | hermes | claude-code | 其余 | 我们 | 漏？ |
|:--:|---|:--:|:--:|:--:|---|---|
| 1 | 整体身份 | ✓ | ✓ | pi ✓ | ✅ L0 已实现 | — (identity) |
| 2 | inline agent prompt | ✓ | ✓ | — | ✅ L0 已实现 | — (inline_prompt) |
| 3 | **工具强制（act-don't-ask）** | ✓ | - | - | ✅ L0 已实现 | — (tool_enforcement, 恒定) |
| 4 | **模型特定操作指导** | ✓ | - | - | ✅ L0 已实现 | — (model_guidance, 按 provider) |
| 5 | **平台渲染格式（多渠道）** | ✓ | - | - | ✅ L0 已实现 | — (platform_format, 按 channel 参数) |
| 6 | computer-use 指导 | ✓ | - | - | ✗ | 漏·低（仅该工具启用时） |
| 7 | 技能索引 | ✓ | - | pi ✓ | ✅ L0 已实现 | — (skills_index) |
| 8 | 工具 + MCP schema | ✓ | ✓ | oc/oclaw ✓ | ✓ L0 | — |
| 9 | 全局/用户级记忆 | ✓ | - | - | ✅ L0 已实现 | — (memory_global) |
| 10 | 环境信息（OS / shell / 远程后端） | ✓ | - | - | ✅ L0 已实现 | — (environment: OS/shell; cwd 另由 tool-runtime) |
| 11 | 当前日期（日粒度，缓存友好） | ✓ | - | pi ✓ | ✅ L0 已实现 | — (current_date, 日粒度) |

> 排序说明：身份/指导/工具是配好绝不动的，放最前；环境信息(OS/后端/日期)虽也整会话
> 稳定，但比身份"更接近会变"(换机器/隔天就变)，放 L0 末尾。
> L0 除 computer-use 指导（低优先）外全部已实现。

---

## L1 会话/项目级（跟项目/会话走，会变）

层内序：项目固定信息（换项目才变，最稳）→ 会话绑定 → 安全检测 → **历史（每轮追加，最后）**。

| # | 成分 | hermes | claude-code | 其余 | 我们 | 漏？ |
|:--:|---|:--:|:--:|:--:|---|---|
| 1 | 项目身份（AGENTS.md / .cursorrules） | ✓ | ✓ | oclaw ✓ | ✓ L1 | — |
| 2 | **Prompt 注入检测**（在 1 加载进 prompt 前扫） | ✓ | - | - | ✅ L1 已实现 | — (pi_shield + detect_injection_patterns) |
| 3 | 上下文文件截断策略（约束 1 的大小） | ✓ | - | - | ✅ L1 已实现 | — (workspace_files 截断, MAX_WORKSPACE_CHARS=8000) |
| 4 | 项目级记忆 | ✓ | - | - | ✓ L1 | — |
| 5 | **用户档案 USER.md** | ✓ | - | - | ✅ L1 已实现 | — (user_profile, 由 workspace_files 加载 read_user_md) |
| 6 | 工作目录 cwd | ✓ | - | pi ✓ | ✓ L1 | — |
| 7 | 是否在 git 仓库 | ✓ | - | - | ✅ L1 已实现 | — (git_repo_flag) |
| 8 | session_id / model / thinking / tier | ✓ | - | - | ✓ L1 | — |
| 9 | deferred tools catalog | - | - | - | ✓ L1 | — |
| 10 | **历史消息（结果）+ 工具调用记录** | ✓ | - | - | ✓ L1 | —（每轮追加，排最后） |

> 排序说明：项目固定信息（AGENTS.md / 项目记忆 / USER.md / cwd / 绑定）换项目才变，
> 放前面；**历史每轮都追加，最不稳，放 L1 最后**——这正是你说的"不断变的历史往后放"。
> 注入检测/截断策略紧挨它们守护的项目文件（2、3 紧跟 1）。
> L1 全部已实现。

---

## L2 任务级（用完即弃，本次）

层内序：本次处境/环境（相对稳）→ 本次输入 → 本次输出规格 → 时间戳（最末）。

| # | 成分 | hermes | claude-code | 其余 | 我们 | 漏？ |
|:--:|---|:--:|:--:|:--:|---|---|
| 1 | 本次处境 situation（在哪函数/调用栈/第几步） | ✓(_situational) | - | - | ✅ L2 已实现 | — (situation + call_path, step 6a/6b) |
| 2 | **Git 分支 / status**（本次环境快照） | △(git root) | - | - | ✅ L2 已实现 | — (git_status, L2 order=20) |
| 3 | **todo 列表 / 任务计划 / 进度** | - | ✓(todo 工具) | - | ✅ L2 已实现 | — (todo_progress, 读 _TODOS) |
| 4 | token 预算提示 | - | - | - | ✗ | 漏·低 |
| 5 | per-turn memory prefetch（本次检索的料） | ✓ | - | - | ✓ L2（现状错塞 system） | — |
| 6 | 本次用户输入 + 附件 | ✓ | ✓ | ✓ | ✓ L2 | — |
| 7 | 输出格式 / schema | - | ✓ | - | ✓ L2 | — |
| 8 | 输出契约 output_contract | - | - | - | ✅ L2 已实现 | — (在 _situational_prefix 中) |
| 9 | timestamp | ✓ | - | pi ✓ | ✓ L2 | —（每次必变，最末） |
| — | Kanban 多 agent 协调 | ✓ | - | - | ✗ | 漏·低（Hermes 多agent 特有） |

> 排序说明：处境/环境/todo 是"本次但相对成型"的，放前；用户输入+输出规格在中段；
> timestamp 每次必变放最末。
> L2 剩余未实现：token 预算提示（低）。

---

## 漏项汇总（按优先级，供决定补哪些）

**✅ 已实现**
- L0 整体身份（identity）
- L0 inline agent prompt（inline_prompt）
- L0 工具强制 act-don't-ask（tool_enforcement）
- L0 模型特定操作指导（model_guidance）
- L0 平台渲染格式（platform_format, 按 channel 参数）
- L0 技能索引（skills_index）
- L0 全局/用户级记忆（memory_global）
- L0 环境信息（environment: OS/shell）
- L0 当前日期（current_date, 日粒度）
- L1 Prompt 注入检测（pi_shield + detect_injection_patterns）
- L1 USER.md 用户档案（user_profile, 由 workspace_files 加载 read_user_md）
- L1 git 仓库归位（git_repo_flag）
- L2 本次处境 situation + call_path（_situational_prefix + _compute_call_path）
- L2 todo 列表/进度（todo_progress, 读 _TODOS）
- L2 output_contract（在 _situational_prefix 中作为 `Your output:` 行）
- L1 上下文文件截断策略（workspace_files 截断, MAX_WORKSPACE_CHARS=8000）
- L2 git 分支/status（git_status, L2 order=20）

**低（vendor 特有 / 专用，多半不补）**
- computer-use 指导 / Nous 订阅 / Kanban 多agent / Hermes profile / 外部记忆提供者

---

## 下一步
决定补哪些后，把它们写进 `context-composition.md` 对应层 + §三'' 对照表。本文查漏完成
后可删或留作追溯。
