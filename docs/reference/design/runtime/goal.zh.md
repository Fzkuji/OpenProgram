# 会话目标 —— /goal 续轮循环

会话目标是存在会话 meta 里的一个条件；只要它处于 active 且未达成，dispatcher 就在每个完整轮次之后继续发起续轮。设计收敛自 Claude Code 的 `/goal`、Codex goals 与 OpenHands `run_goal`：外层循环包在整轮之外、判定来源与干活模型分离、硬性防失控规则。

## 循环位置

循环在 `process_user_turn` 内部（`openprogram/agent/dispatcher/__init__.py`）。原 `process_user_turn` 的函数体改名为 `_process_turn_once`；公开入口先完整跑一轮——持久化、agent loop、finalize（phase 6/7）、idle 标记、result 事件——然后把请求和它的 `TurnResult` 交给 `continue_goal_turns`（`openprogram/agent/goal.py`）。

位置带来的结果：

- 所有调用方自动继承——webui 的 `_execute/chat.py`、channels、走 dispatcher 的 CLI 路径、task runner 的 follow-up 投递都调用 `process_user_turn`，谁都不需要感知 goal。
- 每个续轮就是一个普通轮次：`continue_goal_turns` 调 `_process_turn_once`（绝不调 `process_user_turn`，循环因此不可能嵌套），请求由 `dataclasses.replace` 构造——`source="goal_continue"`、`user_text="[goal] 未达成：<原因>。继续。"`、新的 `user_msg_id`、`branch_from=INHERIT_PARENT`；模型/权限/工具设置从触发请求继承。续轮有自己的持久化、git 提交与压缩，构造方式对照 task runner 的 follow-up（`agent/task/runner.py`）。
- 不用 `agent_loop` 的轮内 follow-up 机制：goal 续轮是会话级事件，必须像用户消息一样经得起 worker 重启、压缩和分支操作。
- goal 机制中任何位置的崩溃都在包装层被接住并返回已完成轮次的结果——goal 循环可以失败，用户轮次的结果不能因此丢失。

## 判定：两态，判定者与干活者分离

`evaluate_goal` 返回 `("met" | "unmet" | "needs_user" | "judge_failure",
reason, question)`。

**确定性谓词** —— `goal.check` 非空时作为 shell 命令执行（`subprocess.run(shell=True)`），cwd 是会话工作目录（`project_workdir_for` 回落 `session_workdir_for`，与 agent 自己轮次的解析一致），120 秒超时。退出 0 即达成；否则输出尾部（最后 2000 字符）成为原因。零 LLM 成本，对模型乐观免疫。

**LLM 判定** —— 无谓词时，用会话配置的模型（profile + 会话覆盖，经 `internals/_model_tools` 解析；provider 注册表的 `fast` 是同一模型的速度档，不是更便宜的判定模型，因此没有独立判定模型可选）发起一次无工具调用。输入是目标文本加活跃分支的尾部渲染：最后 8 条消息内容加每条 assistant 行持久化的工具块，逐字段截断，总量约 24k 字符封顶。尾部渲染写在 `goal.py` 里而不是复用 `render_session_transcript`，因为现成渲染保头弃尾——对判定最近进展是错误的一端。判定者必须输出严格 JSON `{"met": bool, "reason": str, "need_user": bool, "question": str}`；解析失败在同次判定内重试一次。确定性谓词永不请求用户——它的裁决携带空 question。

**"要不要停下来问用户"的决定放在验证步骤里。** `need_user=true` 只允许四种情形（写死在系统提示里）：待批准的不可逆/破坏性操作；缺关键凭据/资源、拿不到无法推进；目标存在决定方向的歧义、猜错会浪费大量轮次；同一失败反复出现且无法自行恢复。其余一律继续。这把"该不该打扰用户"放进每轮本来就要跑的那次新上下文判定里——零额外调用，也不依赖干活模型自己的克制（正在干活的模型同样不是"该不该打扰用户"的合格回答者）。`need_user=true` 但 question 为空视为普通未达成。

**停止裁决要经过主动核实。** 尾部判定只读得到干活模型自己的叙述——既没有手段核查声称，也逃不出转录的框架；多喂转录两个问题都解决不了。而错误的代价是不对称的：错误的"继续"多花一轮，错误的"停止"（假完成、不必要的打断）才昂贵。所以尾部判定给出的 `met` 和 `needs_user` 在生效前要过第二道：起一个同会话 spawn 的 agent 轮（只配巡查工具——`bash`、`read`、`grep`、`glob`、`list`；`advance_head=False`；锚在被核实的那轮上，图上画成子 agent 方块），给它目标和声称、**不给转录**，让它自己去工作目录取证——跑测试、读文件、看产物。查实 → 按核实证据停止；查不实 → 裁决改为未达成，差距（"核实未通过：…"）作为下一续轮的理由。核实自身失败则回退信任尾部判定（核实故障不能卡死循环）。设了检查命令的目标全程跳过这一步——命令本身就是证据。普通"继续"裁决零新增开销。

判定独立成一次调用是刻意的。Codex 与 Cline 最初的自报式设计——干活 agent 自己宣布完成——都在 agent 系统性提前宣胜之后被迫打补丁：想停下来的模型不是"能不能停"这个问题的合格回答者。把结论放进一个只看目标与证据的新上下文（并要求把记录当数据、不执行其中指令），去掉了这个激励，也让确定性模式可以原位替换它。

## 状态

Goal 状态存会话 meta（`update_session` 是 schemaless 的），键 `goal`：

```
{"text": str, "check": str,
 "status": "active" | "waiting_user" | "achieved" | "cleared" | "capped"
           | "error",
 "created_at": float, "turns_used": int, "max_turns": int,
 "last_reason": str, "last_question": str, "judge_parse_failures": int}
```

循环每次迭代开头重读 meta，任何入口发出的 `/goal clear` 在下一次判定即生效。`turns_used` 计目标活跃期间每个被判定的轮次——首轮、续轮、用户插进来的手动轮次都算。`max_turns` 在设定时刻从设置项 `goal.max_turns`（`config_schema`，默认 20）盖章，改设置只影响下一个目标，不影响进行中的。

## 防失控规则

| 规则 | 终态 |
|---|---|
| 检查命令通过 / 判定者回答已达成 | `achieved` |
| 判定者回答 `need_user` 且带问题 | `waiting_user`——循环暂停、不发起续轮，问题以系统行呈现并显示在 goal 芯片上。`goal_continue` 轮永远不能作为回答；下一个真实用户轮把目标翻回 `active`（那条消息就是回答），随后照常判定。等待不消耗预算（除已跑完的那轮）。`/goal clear` 同样能清掉等待中的目标。 |
| `turns_used` 到达 `max_turns` | `capped` |
| 判定连续失败 3 次（同次判定内两次解析失败算一次失败；解析成功清零计数） | `error` |
| 某个 `goal_continue` 轮零工具调用且目标仍未达成——空转 | `error` |
| 用户解除 | `cleared` |
| 轮次失败，或取消已置位（`cancel_event` / `run_control.is_cancelled`） | 循环退出，状态保持 `active` |

最后一行是刻意的：取消与 provider 失败只暂停循环而不消耗目标，因为续轮共享调用方的取消 token——续轮是普通一轮，Stop 按钮本来就够得到它。

单次迭代内的顺序：met 最先胜出（最后一轮不带工具调用也把目标做成了，算成功而不是空转），然后是判定失败计数，然后空转检查，最后是轮数封顶。

## 事件与各入口

每次状态变化和每次续轮前的进度都经 `_emit_goal_update` 发出：dispatcher `on_event` 流上的 `chat_response` 信封 `{"type": "goal_update", "session_id", "goal": {…}}`，加上经 webui server 的顶层 `goal_update` WS 广播（尽力而为——没有 server 时如纯 CLI 或测试即为 no-op）。

- **Web**：`session_loaded` 携带 goal（`ws_actions/session.py`）用于冷加载；输入框上方的 `GoalChip`（`web/components/chat/goal-chip.tsx`）据此加实时 `goal_update` 帧（经 `use-ws` 的兜底 `op:ws-message` 事件送达）渲染 `◎ goal · N/M`。输入框里敲的 `/goal …` 由 `ws_actions/chat.py` 的本地 builtin 分支在后端执行：查状态/解除的回文以 `local_command` 信封返回、渲染为临时 system 行；设定则把本轮文本替换为目标指令并落入正常轮次流程。
- **命令注册表**：`/goal` 是 `builtin` 层带可调用 handler 的命令（`registry.register_shared_builtins`），因此出现在 `/api/commands` 且任何宿主都能解析。Rich REPL 在自己进程里用 marker 动作遮蔽它（`_cli_chat/handlers.py:_handle_goal`）：本地打印，并把设定形式的首轮送进 `process_user_turn`——REPL 裸 `rt.exec` 的轮次跑法绕过 dispatcher，永远到不了循环。

## 实现状态

按上文实现。已知上限：web 记录里的 `local_command` 回文不持久化（与 REPL 控制台打印对齐）；两个入口同时在一个会话跑轮次时判定可能各跑一次——`turns_used` 每迭代重读所以封顶仍成立，加上别处的会话级串行（composer 锁、follow-up 锁）使竞态实际不可达。
