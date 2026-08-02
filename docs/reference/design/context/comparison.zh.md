# 上下文成分对比 —— 参考项目 vs 我们（按三层组织）

> 本文把参考项目喂给 LLM 的上下文成分，跟我们的 L0/L1/L2 设计逐层对比。这是一份对比材料，设计本身见 [`composition.md`](composition.md)。
>
> Hermes 自己也是**三层、按稳定度划分**，与我们 L0/L1/L2 是同一套组织原则——
> 它把三层叫作 `stable / context / volatile`：
> - `stable`  = 身份 + 工具指导 + 技能 + 模型/平台/环境 hints  → 我们的 **L0**
> - `context` = caller system_message + 上下文文件（AGENTS.md 等） → 我们的 **L1 项目层**
> - `volatile`= 记忆快照 + USER.md + 外部记忆 → 我们的 **L1 项目记忆 / L2**
>
> 其余项目（opencode / claude-code / openclaw / pi-mono）的上下文成分都比 Hermes 少，与我们持平或更少。

图例：✓=有，-=无，△=零散有但没归层。

---

> **层内也按稳定度排序**：越稳定越靠前、越常变越靠后——缓存前缀匹配，层内顺序同样影响命中。下面每张表的 `#` 列就是**该层内从前到后的 wire 顺序**。历史这类每轮追加的内容排在所在层的最后。排序参考 Hermes 的 `stable_parts` append 顺序加上我们的缓存原则。

## L0 系统级（配好不动）

层内序：身份类（最稳）→ 指导块 → 工具/技能 → 环境信息（相对会变，靠后）。

| # | 成分 | hermes | claude-code | 其余 | 我们 |
|:--:|---|:--:|:--:|:--:|---|
| 1 | 整体身份 | ✓ | ✓ | pi ✓ | ✓ L0（identity） |
| 2 | inline agent prompt | ✓ | ✓ | — | ✓ L0（inline_prompt） |
| 3 | **工具强制（act-don't-ask）** | ✓ | - | - | ✓ L0（tool_enforcement，恒定） |
| 4 | **模型特定操作指导** | ✓ | - | - | ✓ L0（model_guidance，按 provider） |
| 5 | **平台渲染格式（多渠道）** | ✓ | - | - | ✓ L0（platform_format，按 channel 参数） |
| 6 | computer-use 指导 | ✓ | - | - | -（仅该工具启用时适用） |
| 7 | 技能索引 | ✓ | - | pi ✓ | ✓ L0（skills_index） |
| 8 | 工具 + MCP schema | ✓ | ✓ | oc/oclaw ✓ | ✓ L0 |
| 9 | 全局/用户级记忆 | ✓ | - | - | ✓ L0（memory_global） |
| 10 | 环境信息（OS / shell / 远程后端） | ✓ | - | - | ✓ L0（environment: OS/shell；cwd 另由 tool-runtime 负责） |
| 11 | 当前日期（日粒度，缓存友好） | ✓ | - | pi ✓ | ✓ L0（current_date，日粒度） |

> 排序理由：身份/指导/工具是配好绝不动的，放最前；环境信息（OS/后端/日期）虽然同样整会话稳定，但比身份更接近会变——换机器、隔一天就变——所以放 L0 末尾。

---

## L1 会话/项目级（跟项目/会话走，会变）

层内序：项目固定信息（换项目才变，最稳）→ 会话绑定 → 安全检测 → **历史（每轮追加，最后）**。

| # | 成分 | hermes | claude-code | 其余 | 我们 |
|:--:|---|:--:|:--:|:--:|---|
| 1 | 项目身份（AGENTS.md / .cursorrules） | ✓ | ✓ | oclaw ✓ | ✓ L1 |
| 2 | **Prompt 注入检测**（在 1 加载进 prompt 前扫描） | ✓ | - | - | ✓ L1（pi_shield + detect_injection_patterns） |
| 3 | 上下文文件截断策略（约束 1 的大小） | ✓ | - | - | ✓ L1（workspace_files 截断，MAX_WORKSPACE_CHARS=8000） |
| 4 | 项目级记忆 | ✓ | - | - | ✓ L1 |
| 5 | **用户档案 USER.md** | ✓ | - | - | ✓ L1（user_profile，由 workspace_files 调 read_user_md 加载） |
| 6 | 工作目录 cwd | ✓ | - | pi ✓ | ✓ L1 |
| 7 | 是否在 git 仓库 | ✓ | - | - | ✓ L1（git_repo_flag） |
| 8 | session_id / model / thinking / tier | ✓ | - | - | ✓ L1 |
| 9 | deferred tools catalog | - | - | - | ✓ L1 |
| 10 | **历史消息（结果）+ 工具调用记录** | ✓ | - | - | ✓ L1（每轮追加，排最后） |

> 排序理由：项目固定信息（AGENTS.md / 项目记忆 / USER.md / cwd / 绑定）换项目才变，放前面；**历史每轮追加、最不稳，放 L1 最后**。注入检测与截断策略紧挨它们守护的项目文件，所以 2、3 紧跟 1。

---

## L2 任务级（用完即弃，本次）

层内序：本次处境/环境（相对稳）→ 本次输入 → 本次输出规格 → 时间戳（最末）。

| # | 成分 | hermes | claude-code | 其余 | 我们 |
|:--:|---|:--:|:--:|:--:|---|
| 1 | 本次处境 situation（在哪个函数/调用栈/第几步） | ✓(_situational) | - | - | ✓ L2（situation + call_path，step 6a/6b） |
| 2 | **Git 分支 / status**（本次环境快照） | △(git root) | - | - | ✓ L2（git_status，L2 order=20） |
| 3 | **todo 列表 / 任务计划 / 进度** | - | ✓(todo 工具) | - | ✓ L2（todo_progress，读 _TODOS） |
| 4 | token 预算提示 | - | - | - | - |
| 5 | per-turn memory prefetch（本次检索的材料） | ✓ | - | - | ✓ L2 |
| 6 | 本次用户输入 + 附件 | ✓ | ✓ | ✓ | ✓ L2 |
| 7 | 输出格式 / schema | - | ✓ | - | ✓ L2 |
| 8 | 输出契约 output_contract | - | - | - | ✓ L2（在 _situational_prefix 中） |
| 9 | timestamp | ✓ | - | pi ✓ | ✓ L2（每次必变，最末） |
| — | Kanban 多 agent 协调 | ✓ | - | - | -（Hermes 多 agent 特有） |

> 排序理由：处境/环境/todo 是本次但相对成型的，放前面；用户输入与输出规格在中段；timestamp 每次必变，放最末。

---

## 我们不带的成分

以下成分在某个参考项目里存在，我们这边没有对应物，因为我们没有对应功能：computer-use 指导、Nous 订阅指导、Kanban 多 agent 协调、Hermes profile 机制、外部记忆提供者。[`composition.md`](composition.md) 的注册模型给每一项都留了位置——真做了对应功能，注册一个 `ContextComponent` 即可，框架不改。

同样不带的还有 token 预算提示，参考项目也都没有。
