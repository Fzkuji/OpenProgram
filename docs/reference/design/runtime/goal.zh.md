# 会话目标 —— /goal 续轮循环

会话目标是存在会话 meta 里的一个条件；只要它处于 active 且未达成，dispatcher 就在每个完整轮次之后继续发起续轮。设计收敛自 Claude Code 的 `/goal`、Codex goals 与 OpenHands `run_goal`：外层循环包在整轮之外、判定来源与干活模型分离、硬性防失控规则。

## 循环位置

循环在 `process_user_turn` 内部（`openprogram/agent/dispatcher/__init__.py`）。原 `process_user_turn` 的函数体改名为 `_process_turn_once`；公开入口先完整跑一轮——持久化、agent loop、finalize（phase 6/7）、idle 标记、result 事件——然后把请求和它的 `TurnResult` 交给 `continue_goal_turns`（`openprogram/agent/goal/`）。

位置带来的结果：

- 所有调用方自动继承——webui 的 `_execute/chat.py`、channels、走 dispatcher 的 CLI 路径、task runner 的 follow-up 投递都调用 `process_user_turn`，谁都不需要感知 goal。
- 每个续轮就是一个普通轮次：`continue_goal_turns` 调 `_process_turn_once`（绝不调 `process_user_turn`，循环因此不可能嵌套），请求由 `dataclasses.replace` 构造——`source="goal_continue"`、`user_text="[goal] 未达成：<原因>。继续。"`、新的 `user_msg_id`、`branch_from=INHERIT_PARENT`；模型/权限设置从触发请求继承。工具同样继承，但强制加一项：续轮是无人值守的自主工作，`_tools_with_forced_web_search` 在继承的本轮工具 override 上叠加 `web_search`（`None` 变成 dict 意图 `{"enabled": true, "web_search": true}`，dict 意图加 `web_search: true`，显式名单追加名字）。只对本轮生效——会话持久化的 `web_search` 设置不动——判定轮的 `DECISION_TOOLS` 保持只读巡查集不变。续轮有自己的持久化、git 提交与压缩，构造方式对照 task runner 的 follow-up（`agent/job/runner.py`）。
- 不用 `agent_loop` 的轮内 follow-up 机制：goal 续轮是会话级事件，必须像用户消息一样经得起 worker 重启、压缩和分支操作。
- goal 机制中任何位置的崩溃都在包装层被接住并返回已完成轮次的结果——goal 循环可以失败，用户轮次的结果不能因此丢失。

## 设定目标：规格完善

`/goal <text>` 把用户那句话原样存进 `text`，并立即在后台启动**规格完善**步骤（`_start_spec_refinement` → `refine_goal_spec`，daemon 线程——设定永不等它）。完善是一个同会话 spawn 的 agent 轮，走 goal 模块里的内部函数 `refine`（`openprogram/programs/functions/agentic/goal/`——与判定同模块，刻意**不加** `@agentic_function`，函数面板保持只有一个 `goal` 条目；prompt 即 `refine` 的 docstring）。该 agent 配巡查工具加搜索（`read`、`glob`、`grep`、`list`、`bash`、`web_search`），可以看一眼工作目录理解任务语境。它把一句话扩写成完整规格——可逐条核验的达成标准（形式指标加过程要求，如"必须先读来源 X、Y 再写 Z 部分""引用逐条核实"）、明确的边界（不做什么）、判定时逐条核对的验收清单——输出严格 JSON `{"spec": str, "checklist": [str, …]}`（3–12 条短句、每条独立可核验、与目标文本同语言；解析清洗为非空字符串并在 20 条截断；纯 prose 回文仍算有效规格，只是清单为空）。

**参考锚定——参考是下限，不是风格建议。** 任何 goal（不限于论文）都可以带参考锚：目标点名或暗示了一个可比的现有作品——或能搜到同类的成熟作品——完善步骤就去读它，翻译成可数的验收指标；判定则逐项核对交付物在每个指标上**达到或超过**参考。完善工具集为此加入了 `web_search`。

| goal 类型 | 参考锚 | 提取的指标（示例） |
|---|---|---|
| 文献综述 | 该领域一篇已发表综述（用户给的或搜到的） | 节数与每节篇幅、引用总数、逐篇标注比例、有无分类框架图/对比表 |
| 代码功能 | 现有实现 / 竞品库 | 覆盖的功能清单、处理的边界情况、测试覆盖形态 |
| 文档 / 页面 | 上一版或竞品页面 | 覆盖的章节、每主题深度、每概念的示例数 |
| 没给参考也搜不到 | — | 跳过——完善不许凭空发明参考 |

只锚可数结构不够——交付物可以章节数、引用数全对标,读起来仍是分点笔记。完善因此还要提取**文体锚**(参考如何组织论述)和**核实深度规则**,都写成可核验条目:

| 锚定维度 | 条目示例 |
|---|---|
| 文体(文字类交付) | "正文以连贯段落论述为主,列表行占正文行 10% 以下"、"每个主要章节 ≥ N 词连贯论述"、"图数量对标参考" |
| 核实深度 | "引用真实性只认抽样回查(随机抽若干条打开或搜索),不认写作方自己的'已核实'记录" |

判定侧强制（不限于锚定）：只要 spec 里有可核验项——checklist、可数阈值、必须存在的文件、必须通过的命令——裁判**必须**先用工具逐项取证再答 `met=true`；带锚的 goal 还要打开参考确认逐项达到或超过。干活 agent 的"我已完成……"叙事只能用来判那些工具核不了的项。对"来源/引用真实"类条目,裁判要自己随机抽样——逐条打开或搜索并核对被引事实;抽样失败(作品不存在、编号错位、名字是编的)即判该条目不成立,无论转写里怎么声称。这堵住了提前收工的主要漏洞——只写在首条任务消息里的质量要求几轮压缩后就从会话视图里消失了，而锚写在裁判每轮都重读的 spec 里。

成功时规格落进 `goal["spec"]`（原文 `text` 永不改动），`goal.update` 事件带上它，并以 `local_command` 系统行插进对话——用户能看到系统把目标理解成了什么，理解偏了就 `/goal clear` 后重新 `/goal`。此后判定按 `spec` 评（没有 spec——完善还在跑或已失败——回退用 `text`）。失败一律 **fail-open 但绝不静默**：回文解析不出或 spawn 挂掉记日志、目标保持无 spec，并在会话里发一条系统消息告知"判定只按原始一句话核对"——不阻塞设定，也不影响与完善并行启动的首轮。`refine_goal_spec` 在完善轮返回后重读 goal，竞态的 `/goal clear` 或替换目标不会被过期规格覆盖。完善轮与所有同会话 spawn 一样 `source="agent_spawn"`、`advance_head=False`——既不触发 goal 循环也不抢会话 head。

## 判定：判定者与干活者分离

`evaluate_goal` 返回 `("met" | "unmet" | "needs_user" | "judge_failure",
reason, question)`。

**一个判定 agent** —— 判定点是单一的 `goal` agentic 函数（`openprogram/programs/functions/agentic/goal/`）：框架吃自己的狗粮，docstring 即判定 prompt，可从函数面板单独运行（面板上只有这一个条目）。判定只有这一种，只有它说完成才算完成。每次判定是一个同会话 spawn 的 agent 轮（`run_agent_turn`，`advance_head=False`，经 `spawn_caller` 锚在被判定的那轮上，图上画成子 agent 方块），输入是目标文本加会话的**压缩上下文视图**——`rendered_history`，与干活模型读到的形态一致：有 active summary 就先放摘要，再接保留轮次的尾部（最后 8 条消息内容加每条 assistant 行持久化的工具块，逐字段截断，总量约 24k 字符封顶；摘要不会被封顶截掉）。判定 agent 配有巡查工具（`bash`、`read`、`grep`、`glob`、`list`——没有 edit/apply_patch/task，判定不得修改任何东西、不得再 spawn agent），要不要去工作目录核查由它自己决定，prompt 不强制。它必须输出严格 JSON `{"met": bool, "reason": str, "need_user": bool, "question": str}`——目标带验收清单时还要加逐项布尔列表 `"checklist"`（见下节）；回文不合法或轮次失败时 `goal.py` 在同次判定内重试一次。

**"要不要停下来问用户"的决定在同一次判定里，分两种模式并带限频。** 判定 prompt 携带会话的在场/无人值守模式（`agent/attended.py`，作为 `attended` 参数传进函数；面板手动跑默认在场）。*在场*——有人看着——允许对"确实难以替用户决定的事"设 `need_user=true`：待批准的不可逆/破坏性操作、缺凭据/资源、决定方向的歧义、无法恢复的反复失败，或猜错会浪费大量轮次的选择。*无人值守*抬高门槛：暂停必须是罕见的，且严重性是**对象**的属性、不是操作类别的属性——"是删除""不可逆"本身永远不是暂停理由。裁判被要求用工具查实际风险（打开目录、看内容、判断可否再生）：查明是测试/缓存/可再生数据就自己决定并记录；只有查实的严重后果（用户自己的文档、未推送的工作、生产数据、真金白银、影响他人）、拿不到的凭据/资源、或 goal 文本自己点名要批准的操作才可暂停。方向歧义、反复失败这类要求它思考周全后自选最合理方案、写清决定和理由继续。prompt 策略之上，循环在代码层强制硬性限频：**1 小时内最多问 1 次**（goal state 里的 `last_question_at`，`QUESTION_MIN_INTERVAL_SECONDS`）。窗口内再来 `needs_user` 裁决不暂停——降级为续轮，续轮 prompt 说明提问额度已用、要求自选最合理方案并写清决定和理由。时间戳跨恢复保留（答完一个问题不重置这一小时）。这把"该不该打扰用户"放进每轮本来就要跑的那次新上下文判定里——零额外调用，也不依赖干活模型自己的克制。`need_user=true` 但 question 为空视为普通未达成。

**无人值守遇到无法决定的事——完整决策链。** 没人看着时，"决定不了"只有两条出路：

| | 能决定但拿不准 | 真正无法继续 |
|---|---|---|
| 示例 | "综述引用格式用 IEEE 还是 ACM？" · 某测试连挂 3 次 · 两种文件布局都说得通 · 删除一个裁判查过、确认只有可再生测试/缓存数据的目录 | API key 缺失/过期 · 查实真正不可恢复的数据（用户文档、未推送的工作、生产数据）· 花真钱 · goal 文本自己点名要批准的操作 |
| 处理 | 自己决定：选最合理方案，**把决定和理由写进会话记录**，继续 | 挂起：状态置 `waiting_user`，不再发续轮（不空转、不烧预算） |
| 呈现方式 | 决定记录在该轮输出里 | 系统消息 "[goal] 需要你的确认才能继续：…" + goal 徽标"等你回答" |
| 如何解除 | 已解除——运行继续 | 用户下一条真实消息就是回答；循环转回 `active`，照常判定 |
| 强制手段 | 判定 prompt 禁止为这些暂停，**且**工具层把 `ask_user_question` 从所有无人值守轮次里扣掉（`agent/attended.py` 的 `denied_ask_tools`）——就算 prompt 失守也问不出去 | 只有判定给出 `need_user=true` 才走到这里；每小时限频仍然生效 |

不变量：无人值守的运行绝不靠猜闯过不可逆操作，也绝不在无人回答的问题上空转——要么决定并记录，要么挂起等人。已知上限：挂起的问题只在会话内可见，还没有主动推送渠道，用户下次打开界面才会发现暂停。

## 验收清单

完善步骤生成的清单是目标的固定验收表：完善一次写死、裁判只报告逐项状态、循环在代码层强制。这堵住了剩下的提前收工缝隙——裁判无法用总结绕过一张它只能打勾的清单。

| 阶段 | 谁 | 发生什么 |
|---|---|---|
| 生成 | `refine`（完善时一次） | `{"checklist": [str, …]}` 落进 goal 状态，形如 `[{"text", "done": false}, …]`。此后清单固定——任何人不得增删改写条目。 |
| 打勾 | 裁判（每次判定） | 判定 prompt 渲染编号的 `<checklist>` 块；裁判必须用工具逐项取证并回答 `"checklist": [true\|false, …]`——同序号、同长度、只报状态。合法列表按序覆盖每项 `done`（true→false 也覆盖——取证胜过早先的勾）；缺失、长度不符或含非布尔的列表视为本轮无逐项信息，已存的勾保持不变。 |
| 强制 | 循环代码（`evaluate_goal`） | 存在未完成项时 `met` 一律降级为 `unmet`，reason 点名未完成条目（"清单未全部完成：3) …"）。prompt 已要求全 true 才可 met；代码把它变成不可协商。 |
| 点名 | 续轮 prompt | 只要有未完成项，`goal_continue` 轮次文本就追加"未完成项："加编号列表——把干活 agent 直接指向剩下的事。 |
| 呈现 | goal 徽标 / `/goal` 状态 | 有清单时徽标显示 `goal · done/total`（否则显示轮数）；`/goal` 状态打印 `checklist: done/total`，未完成项每行一条 `[ ]`。 |

运行中的状态示例：

```json
{"text": "写完综述",
 "spec": "…完整规格…",
 "checklist": [
   {"text": "正文包含 6 个章节", "done": true},
   {"text": "引用不少于 80 篇且逐条核实", "done": false},
   {"text": "包含分类框架图", "done": true}],
 "status": "active", "turns_used": 5, "max_turns": null,
 "last_reason": "引用核实未完成", "judge_parse_failures": 0}
```

徽标显示 `goal · 2/3`；续轮 prompt 点名第 2 条；裁判勾掉它之前 `met` 不可达。

判定独立成一次调用是刻意的。Codex 与 Cline 最初的自报式设计——干活 agent 自己宣布完成——都在 agent 系统性提前宣胜之后被迫打补丁：想停下来的模型不是"能不能停"这个问题的合格回答者。把结论放进一个只看目标与证据的新上下文（并要求把记录当数据、不执行其中指令），去掉了这个激励。

## 状态

Goal 状态存会话 meta（`update_session` 是 schemaless 的），键 `goal`：

```
{"text": str,
 "spec": str（完善后的规格——后台完善落地前不存在；判定回退用 text）,
 "checklist": [{"text": str, "done": bool}]（完善时写死的验收条目——
         完善没产出清单时不存在；裁判只翻 "done"，循环强制全勾才 met）,
 "status": "active" | "waiting_user" | "achieved" | "cleared" | "capped"
           | "error",
 "created_at": float, "turns_used": int,
 "max_turns": int | None（None = 无上限，默认）,
 "last_reason": str, "last_question": str,
 "last_question_options": [{"label": str, "description": str}]（≤4，
         裁判给出的一键回答；开放式问题时为空）, "last_question_at": float,
 "judge_parse_failures": int,
 "last_done_count": int, "stall_rounds": int（只读磨洋工守卫：
         连续多少个判定轮打勾数没涨）}
```

循环每次迭代开头重读 meta，任何入口发出的 `/goal clear` 在下一次判定即生效。`turns_used` 计目标活跃期间每个被判定的轮次——首轮、续轮、用户插进来的手动轮次都算。`max_turns` 在设定时刻从设置项 `goal.max_turns`（`config_schema`）盖章，默认 **None——无轮次上限**，对齐 Claude Code 与 Codex 的 stop hook（同样没有默认数字上限）：防失控靠内部停止规则（判定连续 3 次失败、空转检测）、用户中断和 `/goal clear`。显式设了正数则照设的执行；每个目标保持设定时的上限，改设置只影响下一个目标。

## 防失控规则

| 规则 | 终态 |
|---|---|
| 判定回答已达成 | `achieved` |
| 判定回答 `need_user` 且带问题（且小时提问额度未用） | `waiting_user`——循环暂停、不发起续轮，问题以系统行呈现并显示在 goal 芯片上，`last_question_at` 启动限频计时。`goal_continue` 轮永远不能作为回答；下一个真实用户轮把目标翻回 `active`（那条消息就是回答），随后照常判定。等待不消耗预算（除已跑完的那轮）。`/goal clear` 同样能清掉等待中的目标。窗口内的 `needs_user` 裁决降级为续轮（见上）。 |
| `turns_used` 到达 `max_turns`（仅当显式设了上限） | `capped` |
| 判定连续失败 3 次（同次判定内两次解析失败或轮次失败算一次失败；解析成功清零计数） | `error` |
| 某个 `goal_continue` 轮零工具调用且目标仍未达成——空转 | `error` |
| 清单打勾数连续 3 个续轮不涨——只读磨洋工（每轮调工具但交付物不动） | `error` |
| 用户解除 | `cleared` |
| 轮次失败，或取消已置位（`cancel_event` / `run_control.is_cancelled`） | 循环退出，状态保持 `active` |

最后一行是刻意的：取消与 provider 失败只暂停循环而不消耗目标，因为续轮共享调用方的取消 token——续轮是普通一轮，Stop 按钮本来就够得到它。

单次迭代内的顺序：met 最先胜出（最后一轮不带工具调用也把目标做成了，算成功而不是空转），然后是判定失败计数，然后空转检查，再是清单无进展检查，最后是轮数封顶。

goal 会话与 `turn.stop` 闸门分工明确：有 goal（active 或 waiting）的会话永远不进 `continue_stop_hook_turns`——它的 goal 循环是唯一停止决策者，外部干预只有 `/goal clear`。`turn.stop` 闸门是**无** goal 会话的扩展点（见 `docs/reference/design/proactive/event-layer.zh.md`）。

## 事件与各入口

每次状态变化和每次续轮前的进度都经 `_emit_goal_update` 发出：dispatcher `on_event` 流上的 `chat_response` 信封 `{"type": "goal_update", "session_id", "goal": {…}}`，加上经 webui server 的顶层 `goal_update` WS 广播（尽力而为——没有 server 时如纯 CLI 或测试即为 no-op）。

- **Web**：`session_loaded` 携带 goal（`ws_actions/session.py`）用于冷加载；输入框上方的 `GoalChip`（`web/components/chat/goal-chip.tsx`）据此加实时 `goal_update` 帧（经 `use-ws` 的兜底 `op:ws-message` 事件送达）渲染 `◎ goal · N/M`（共用 `useSessionGoal` hook）。`status === "waiting_user"` 期间，输入框顶部**向上长出问题面板**（`composer/modes/question/question-panel.tsx`，与 `ask_user_question` 的 ask/confirm 决策共用——真实 ask 优先）：单行徽标（"goal · 等你回答"加 Target 图标）、问题文本、带说明的选项 pill。其余一切原地不动——textarea、底栏、env-chip 行不移不变；转写区底部留白跟随 composer 实测高度（`--main-composer-height` CSS 变量，ResizeObserver），最后一条消息永不被盖住。点 pill 或在正常输入框打字都走正常聊天发送路径——和手打一条消息完全等价，恢复规则无需任何特殊处理（真实 ask 则改走 `question_reply`）。面板由 goal 状态驱动，挂起期间刷新页面会重新出现。输入框里敲的 `/goal …` 由 `ws_actions/chat.py` 的本地 builtin 分支在后端执行：查状态/解除的回文以 `local_command` 信封返回、渲染为临时 system row；设定则把本轮文本替换为目标指令并落入正常轮次流程。
- **命令注册表**：`/goal` 是 `builtin` 层带可调用 handler 的命令（`registry.register_shared_builtins`），因此出现在 `/api/commands` 且任何宿主都能解析。Rich REPL 在自己进程里用 marker 动作遮蔽它（`cli/repl/handlers.py:_handle_goal`）：本地打印，并把设定形式的首轮送进 `process_user_turn`——REPL 裸 `rt.exec` 的轮次跑法绕过 dispatcher，永远到不了循环。

## 实现状态

按上文实现。已知上限：web 记录里的 `local_command` 回文不持久化（与 REPL 控制台打印对齐）；两个入口同时在一个会话跑轮次时判定可能各跑一次——`turns_used` 每迭代重读所以封顶仍成立，加上别处的会话级串行（composer 锁、follow-up 锁）使竞态实际不可达。
