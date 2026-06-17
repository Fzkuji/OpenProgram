# TUI upgrade — research notes (2026-06)

Raw multi-agent research output backing [tui-upgrade.md](tui-upgrade.md).
Three reports: current-state audit, Claude Code study, opencode study.

---

# OpenProgram Ink TUI 全面审计报告

审计对象：`cli/`（入口 `cli/src/index.tsx`，主屏 `cli/src/screens/REPL.tsx`）。下文所有相对路径均以 `/Users/fzkuji/Documents/LLM Agent Harness/OpenProgram/cli/src/` 为根（除非另注明）。

## 0. 架构总览

- **不是 stock ink**：`runtime/ink/` 是一份 vendored 的 cell-grid 渲染器分支（代码注释自称 "hermes-ink"，见 `components/Messages.tsx:8`），约 100 个文件，含完整的 yoga 布局（`runtime/native-ts/yoga-layout/`）、alt-screen、SGR 鼠标追踪、bracketed paste、OSC 11 背景色探测、`ScrollBox`、`RawAnsi`、selection、focus 等。每帧全量重绘 cell grid，所以没有 `<Static>`，换主题/resize 直接整树重渲染。stock ink@7 被刻意排除（`runtime/index.ts:37-40`）。
- **三层结构**（`ui/index.ts:1-17` 注释明确）：Layer 0 = runtime 原语（Box/Text/ScrollBox/useInput）；Layer 1 = `ui/` 组件库（Shell/Modal/Form/Toast…）；Layer 2 = screens（REPL、Demo）。
- **启动链路**：Python 侧 `openprogram/cli_ink.py` 找到运行中的 worker（`worker.{pid,port}`），首次自动 `npm install + build` 出 `cli/dist/index.js`，然后以 `OPENPROGRAM_WS=ws://127.0.0.1:<port>/ws` 启动 Node 进程。`index.tsx:10-27` 解析 `--ws` / `--demo`，建 `BackendClient` 并连接，`render(<ThemeProvider><REPL/></ThemeProvider>, { exitOnCtrlC: false })`。
- **REPL.tsx（~520 行）是状态中枢**：约 40 个 useState（transcript、streaming turn、picker 状态、channel 绑定流程暂存、provider 账号面板、token 统计 per-conv…），WS 事件经 `screens/repl/useWsEvents.ts` 注册，picker 渲染经 `screens/repl/pickerRouter.tsx` 分发。

## 1. 组件清单与职责

### components/（业务组件）

| 组件 | 职责 |
|---|---|
| `Turn.tsx` (200 行) | 单条消息渲染：`TurnRow` 按 role 分发到 `UserRow` / `AssistantRow` / `SystemRow`；内含 `ToolRow`、`TextSegment`。详见 §2 |
| `Messages.tsx` | transcript 列表：Welcome banner + committed turns + streaming turn，纯 map，无虚拟化 |
| `TranscriptViewport.tsx` | 包 `ScrollBox`（stickyScroll 钉底），用 `inputEmitter.prependListener` 抢先消费滚动键：滚轮 ±3 行、PgUp/PgDn ±(viewport−2)、Ctrl+U/D 半屏、Home/Ctrl+G 顶、End/Ctrl+Shift+G 底（`TranscriptViewport.tsx:60-97`） |
| `BottomBar.tsx` | 单行状态栏。左侧：permission mode（`▸▸ bypass/auto/ask` 着色）、tools on/off、`✦`thinking effort、上下文相关 hint（exit pending / esc to stop / ctrl+r search）；右侧：连接状态、agent、model、session id+live 圆点、↑↓ token、context 占用百分比（>65% 黄 >85% 红，启发式估计加 `~`）、cache 命中率、working 标签。按列宽（60/72/86/96/100/112 cols）渐进降级 |
| `PromptInput/PromptInput.tsx` (470 行) | 输入框：单行视口（多行显示成 `↵ `），slash 菜单、`@file` 补全菜单、↑↓ 历史、fish 式 autosuggest、per-session 草稿持久化（`utils/draftStore.ts` → `~/.agentic/tui_drafts.json`）、declared cursor。详见 §3 |
| `PromptInput/PromptInputHelpMenu.tsx` | slash 命令下拉（窗口化 8 行，名字+描述+footer 提示） |
| `PromptInput/FileMenu.tsx` | `@` 文件补全下拉（8 行窗口） |
| `Picker.tsx` | 通用列表选择 overlay：type-to-filter、↑↓ 循环、enter/esc；用 `RawAnsi` 单 Yoga leaf 渲染、按终端高度自适应降级（去 spacer→去 footer→缩行数，`Picker.tsx:74-80`） |
| `LineInput.tsx` | 单行文本输入卡片（label/hint/mask），无光标移动（只能尾部追加/退格） |
| `SettingsPanel.tsx` (258 行) | `/config` 的 schema 驱动设置面板：分组、toggle（space）、enum（←→）、number/text（enter 进编辑 buffer）、status 行（enter 跳转 action）、type-to-filter、滚动窗口、live/next_start 标注。值由 server `settings` / `setting_result` envelope 回写 |
| `Spinner.tsx` | 活动行：braille 帧 + verb +（elapsed s）+ detail（流速 chars/s 或工具入参截断） |
| `Welcome.tsx` | 开屏仪表盘：agent/model + programs/skills/agents/sessions/functions/applications/tools/providers/channels 计数 8 宫格，5 档响应式布局（`getWelcomeLayout`，`Welcome.tsx:49-68`） |
| `ChannelActivityFeed.tsx` | BottomBar 上方最多 3 行的"非当前会话"环境活动：`◐/✓ wechat:nick « 用户话 » 回复尾部`，30s 过期 |
| `ThemePicker.tsx` | 主题选择（auto/dark/dark-dim/light/light-dim），光标移动即全 UI live preview，enter 保存 esc 回滚（`ThemePicker.tsx:60-80`） |

### ui/（kit 组件库）

| 组件 | 职责 | 实际使用情况 |
|---|---|---|
| `Shell.tsx` | 根框架，`mode="alt"`（alt-screen + 高度钉满）或 inline；内置 ToastProvider + ModalProvider + esc-pop-modal handler | REPL/Demo 都用 |
| `ModalProvider.tsx` | modal 栈（push/replace/pop/clear），`ModalHost` 只渲染栈顶 | **REPL 挂了 `<ModalHost/>`（REPL.tsx:471）但没人 push；只有 Demo.tsx 在用** |
| `Confirm.tsx` | Yes/No 二选（包 Select，destructive 可 `defaultYes={false}`） | **仅 Demo.tsx 使用** |
| `Select.tsx` | kit 名义下的 Picker thin wrapper | 仅 Demo |
| `Input.tsx` | LineInput thin wrapper（validate 参数是占位 no-op） | 仅 Demo |
| `MultiSelect.tsx` | 多选（space 勾选/enter 提交/filter），RawAnsi 实现 | 仅 Demo |
| `Form.tsx` | 多步向导（steps + skipWhen + ctx.next/back/cancel），专为替代 pickerKind 状态机而设计（文件头注释明说） | **仅 Demo；REPL 仍是 24 态 pickerKind 枚举** |
| `ToastProvider.tsx` | 临时通知气泡（默认 4s 消失），`ToastHost` 挂在 REPL.tsx:474 | 仅 Demo 调 `toast.show` |
| `ScrollView.tsx` | ScrollBox wrapper（统一键位/sticky） | REPL 用自己的 TranscriptViewport，未用 |
| `Alert.tsx` / `Card.tsx` / `layout.tsx`（Stack/Row/Spacer/Center）/ `hooks.ts`（useBreakpoint 等） | 视觉原语 | 基本只有 Demo 用 |

**结论：ui/ kit 是建好但未迁移的"第二套体系"**。REPL 的全部交互仍走 legacy `pickerKind` 路径（REPL.tsx:467-470 的注释承认 "As screens migrate to ModalProvider.push()…"）。

### screens/repl/（REPL 拆分件）

- `pickerRouter.tsx` (396 行)：`buildPickerNode(ctx)` 按 pickerKind 分发——settings / commands（Ctrl+K 调色板）/ model / branch / agent / effort / channel 六态链 / register 两态 / acct 七态 / context_search(+results) / theme / resume。
- `pickers/channel.tsx` (374 行)：/channel 六步流程（选 channel → 选 account → 选绑定方式 → 输 peer id → 覆盖确认 → QR 等待卡片）。覆盖确认用的是两项 Picker 而不是 kit Confirm（channel.tsx:298-357）。
- `pickers/providerAccounts.tsx` (357 行)：任意 provider 的账号管理面板（list/activate/deactivate/rename/remove/rotate/add），claude-code 走 code-paste，其他 provider 走 `providerLoginFlow.tsx`。
- `pickers/providerLoginFlow.tsx` (195 行)：**TUI 里唯一真正的"后端中途向用户提问"循环**——REST 轮询 `/api/providers/{id}/login/poll`，收到 `waiting+prompt` 就渲染一个内联输入（可 secret 掩码），enter 后 `submitLogin` 送回（providerLoginFlow.tsx:91-96, 146-153）。
- `useWsEvents.ts` (398 行)：WS envelope → state 的总 dispatcher（latest-ref 模式）。
- `wsHandlers/handleChatResponse.ts` (307 行)、`handleChannelTurn.ts`、`streamingHelpers.ts`：见 §2/§4。

## 2. 一轮对话如何渲染（Turn.tsx 细读）

**数据模型**（`Turn.tsx:6-40`）：`Turn = { id, role, text, blocks?, tools?, streaming? }`，`TurnBlock = text | tool`。blocks 按模型产出顺序交错排列（文本→工具→文本），legacy `tools` 数组只作 fallback。

**用户消息**（`UserRow`, Turn.tsx:103-118）：逐行渲染，首行 `> ` 前缀，整行套 `colors.user.bg` 背景色块。

**assistant 文本**（`TextSegment`, Turn.tsx:120-142）：
- 非流式：`renderMarkdown(text)`（`utils/markdown.ts` = marked + marked-terminal，模块加载时一次性 `marked.use(markedTerminal())`，解析失败回退原文，约 100ms import 成本注释里写明）。
- **流式时跳过 markdown**（Turn.tsx:37-39, 123 `streaming ? text : renderMarkdown(text)`）——streaming 阶段用户看到的是裸 `**bold**` 源码，result 落地后才整段重渲染，存在视觉跳变。
- 只有**第一个 text block** 带绿色 `● ` 前导 glyph（AssistantRow，Turn.tsx:160）；纯工具 turn 不加 glyph。
- 注意：`renderMarkdown` 在 `TextSegment` 里每次 render 都重跑、没有 memo——cell-grid 渲染器每帧重绘整树，长 transcript 下每个 committed turn 的 markdown 每帧都重新 parse。

**工具调用**（`ToolRow`, Turn.tsx:42-101）：
- 一行头部：状态 glyph（`◌` running / `●` done / `✗` error，着 `colors.tool.*`）+ **工具名**（bold）+ ` · ` + 入参字符串（muted、`truncate-end` 单行截断——入参本身是 server 给的字符串，无结构化渲染）。
- 结果预览：固定显示前 **6 行**（`MAX_LINES = 6`，Turn.tsx:74），首行 `└ ` 前缀，超出部分折叠成 `(+N more lines)` 灰字。**没有任何展开手段**——非交互、无 keybind、阈值不可配。
- 工具结果归属匹配（`handleChatResponse.ts:152-179`）：`tool_result` 事件**按工具名从后向前找最后一个 status==='running' 的同名块**就地更新——server 的 stream_event 不带 tool call id，并发同名工具会错配。

**流式管线**（`streamingHelpers.ts` + `handleChatResponse.ts`）：
1. `chat_response{type:'stream_event', event:{type:'text', text}}` → `upsertStreamingText`：delta 追加到最后一个 text block（没有就新建 streaming Turn）；Activity verb 置 "Streaming"，累计 `streamedChars` 算 chars/s 流速显示在 Spinner。
2. `event:{type:'tool_use', tool, input}` → `appendStreamingTool` 推一个 running 工具块；Spinner verb 变 `Calling <tool>`，detail 取入参前 50 字。
3. `event:{type:'tool_result'}` → 如上回填 result + done/error。
4. `type:'result'` → `finalizeStreamingTools`（残余 running 全标 done）→ streaming Turn 落入 committed（保留 block 顺序）→ Activity 清空；若该轮 >5s 且 /bell 开着，响铃 `\x07`（handleChatResponse.ts:211-217）。
5. `type:'status'` → 折叠进 Spinner verb；`type:'error'` → system 行；`type:'context_stats'` → token/window per-conv，并顺带 REST 拉一次 `/api/sessions/{id}/tokens` 取 cache 统计（handleChatResponse.ts:269-274）。
6. **conv_id 不等于当前会话的 chat_response 全部改道 ChannelActivityFeed**（routeForeignConv，handleChatResponse.ts:89-147），不污染当前 transcript。

**历史加载丢工具**：`conversation_loaded` 只 map `role+content` 成纯文本 Turn（useWsEvents.ts:326-336）——/resume 回来的 transcript **看不到任何工具调用记录**。

## 3. 快捷键全表

### 全局（REPL.tsx:211-254，永远活跃）

| 键 | 行为 |
|---|---|
| Ctrl+C ×2（800ms 窗口） | 退出；第一次按只在 BottomBar 显示 "press ctrl+c again to exit"（REPL.tsx:199-231） |
| Shift+Tab | permission mode 在 **bypass ↔ auto** 间循环（注释明说"只循环不需要 approval UI 的模式"——`ask` 键盘不可达，REPL.tsx:234-237） |
| Ctrl+K | 命令调色板（SLASH_COMMANDS 的 Picker）；仅当无 picker 且非 streaming |
| Esc | 仅用于关 `channel_qr_wait`（其余 esc 由各 picker 自己消费） |

### transcript 滚动（TranscriptViewport.tsx:60-97，prepend 抢占，任何时候有效）

滚轮上下 ±3 行；PgUp/PgDn ±(视口−2)；Ctrl+U / Ctrl+D 半屏；Home 或 Ctrl+G 到顶；End 或 Ctrl+Shift+G 到底。stickyBottom 钉底，上滚解钉。

### PromptInput（无 picker 时，PromptInput.tsx:218-370）

| 状态 | 键 | 行为 |
|---|---|---|
| busy | Esc | 取消当前轮（发 `{action:'stop'}`）；其余键全吞 |
| 任意 | Ctrl+R | 打开跨会话 context search（带当前草稿） |
| @file 菜单开 | ↑↓ / Tab 或 Enter / Esc | 选条目 / 插入 `@path ` / 删掉 @token 关菜单 |
| slash 菜单开 | ↑↓ / Tab / Enter | 选命令 / 补全 `/cmd ` / 运行 |
| 普通 | Enter | 提交；**Alt(meta)+Enter** 插入换行 |
| 普通 | Esc | 清空输入 |
| 普通 | ↑ / ↓ | 历史回溯/前进（500 条，`~/.openprogram/cli-history`） |
| 普通 | ← / → | 移光标；行尾 → 或 **Ctrl+E** 接受 autosuggest |
| 普通 | Backspace/Delete | 删一个字符（无 word-delete） |
| 普通 | 可打印字符 | 插入；bracketed paste 作为一整块 input 进来（runtime use-input 注释 + App.tsx 启用 bracketed paste） |
| 框 focus | Tab | 被 `onKeyDownCapture` preventDefault 吞掉（防焦点跳走，PromptInput.tsx:431-435） |

### 各 overlay

- **Picker**（Picker.tsx:60-68）：打字过滤、↑↓ 循环、Enter 选、Esc 取消、Backspace 改 filter。
- **SettingsPanel**（SettingsPanel.tsx:115-166）：↑↓ 导航；toggle 行 Enter/Space 翻转；enum 行 ←→ 循环；number/text 行 Enter 进编辑 buffer（Enter 存 Esc 弃）；status/action 行 Enter 关面板并跑对应 slash 命令；打字过滤；Esc 先清 filter 再关面板。
- **LineInput**：Enter 提交、Esc 取消、Backspace；无光标移动。
- **ThemePicker**（ThemePicker.tsx:60-80）：↑↓ 即时 preview 全 UI、Enter 保存、Esc 回滚。
- **ProviderLoginFlow**（providerLoginFlow.tsx:138-163）：Esc 中止（cancel 后端 session）；有 prompt 时 Enter 提交答案、退格、打字。
- **MultiSelect**（kit，仅 Demo）：Space 勾选、↑↓、Enter 提交、Esc。
- **Shell ModalEscHandler**（Shell.tsx:43-51）：modal 栈非空时 Esc pop 栈顶。

**没有的键**：Ctrl+L 清屏、Ctrl+D 退出、Ctrl+A/E 行首尾（Ctrl+E 被 autosuggest 占用）、word 级移动/删除、工具输出展开键、transcript 全屏/外部 pager、keybinding 自定义（设计文档明确 deferred）。

## 4. 与后端的连接

**传输 = 单条 WebSocket + 少量 REST 侧信道，无 SSE / 轮询主路径。**

- `ws/client.ts` `BackendClient`：连 `OPENPROGRAM_WS`（默认 `ws://127.0.0.1:18109/ws`，`--ws` 可覆盖）。断线指数退避重连（首连前 50ms 基数、连过后 200ms，上限 5s，client.ts:352-357）；未连通时请求进队列，open 后 flush（client.ts:363-369）。`onState` 把 connecting/connected/disconnected 推给 BottomBar。
- **出站 action**（client.ts:13-52 + 散落的 `as never` 调用）：`chat`（带 conv_id/agent_id/text/tools/thinking_effort/permission_mode）、`stop`、`stats`、`sync`、`list_models`/`switch_model`、`list_agents`/`set_default_agent`、`list_conversations`/`load_conversation`、`search_messages`、channel 系（`list_channel_accounts`/`add_channel_account`/`remove_channel_account`/`list_channel_bindings`/`attach_session`/`detach_session`/`list_session_aliases`）、branch 系（`list_branches`/`checkout_branch`/`rename_branch`/`auto_name_branch`/`delete_branch`/`load_session`）、`get_settings`/`set_setting`、`browser`。
- **入站 envelope → UI 映射**（useWsEvents.ts:105-378）：

| envelope | UI 行为 |
|---|---|
| `chat_ack` | 设置 conversationId |
| `chat_response` | §2 的流式管线（status/stream_event/result/error/context_stats）；外会话改道活动 feed |
| `stats` | Welcome 数据 + 默认 model/agent |
| `agents_list` / `models_list` / `model_switched` | picker 数据、BottomBar model |
| `conversation_loaded` | 重建 transcript（纯文本）、恢复 per-session settings（tools/effort/permission_mode） |
| `history_list` / `conversations_list` | /resume picker 数据 |
| `branches_list` + `branch_*` | /branch picker；结构变更自动重拉 |
| `settings` / `setting_result` | SettingsPanel 行 + 保存反馈 |
| `channel_accounts` / `channel_account_added` / `channel_bindings` / `session_aliases` / `session_alias_changed` | channel 流程数据 + "你覆盖了 X" 警示 |
| `qr_login`（qr_ready/scanned/confirmed/done/expired/error） | QR 状态机 → ASCII QR 卡片 → done 自动 attach_session |
| `search_results` | 打开 context_search_results picker |
| `channel_turn` | 当前会话直接 append 两条 turn；外会话进活动 feed（handleChannelTurn.ts） |
| `browser_result` / `error` / `pong` | system 行 / 错误行 / 忽略 |

- **REST 侧信道**（`utils/backend.ts` `backendBase()`）：`/api/sessions/{id}/tokens`（每轮一次 cache 统计）、`/api/providers/{id}/fetch-models`、`/api/providers/{id}/login/{start,poll,submit,cancel}`、accounts CRUD（`utils/providerAccounts.ts`）。
- **端口默认值不一致的小 bug**：`backend.ts:16` 回退 18109，但 `commands/handler.ts:257` 和 `wsHandlers/handleChatResponse.ts:291` 各自硬编码回退 `http://127.0.0.1:8765`（backend.ts 文件头注释自己点名了这个问题）。

## 5. 已有的交互式提问能力

- **kit 通路（Confirm + ModalProvider + Toast）存在但在 REPL 中零使用**——只有 `screens/Demo.tsx`（`--demo` 启动的组件橱窗）在 push Confirm/Form/MultiSelect。REPL 真实的破坏性确认（channel 绑定覆盖）是用两项 Picker 手写的（channel.tsx:298-357）。
- **Picker/LineInput 的触发场景**：全部是**用户主动发起**的 slash 命令流程（/model /agent /resume /branch /effort /theme /channel /login /config /search、Ctrl+K、Ctrl+R）。
- **"运行中途向用户提问"现状**：
  1. **Provider 登录流程有**：`providerLoginFlow.tsx` 通过 REST poll 收到 `waiting+prompt` 即渲染内联问答输入（支持 secret），这是 TUI 内唯一的后端驱动问答循环——但只服务登录。
  2. **agent/函数运行中的提问没有**：服务端已实现 `ask_user` → `follow_up_question` envelope（`openprogram/webui/server.py:253`，队列阻塞 300s 等答案），且 TUI 的类型定义里声明了该类型（`ws/client.ts:62`），**但 `handleChatResponse.ts` 没有任何分支处理它**，也没有把答案送回去的 action 调用。agent 中途问问题在 TUI 端会沉默超时。`cancelled` / `tree_update` 同样是"类型里有、处理器里无"。
  3. **工具审批没有**：`permission_mode` 三态走协议（chat 请求带上），但 TUI 默认 `'bypass'`（REPL.tsx:134），Shift+Tab 只在 bypass↔auto 间切，**`ask` 模式没有对应的审批 prompt UI**，等于死路。

## 6. 短板清单（对照现代 agent TUI 应有能力）

**完全缺失**
1. **工具输出展开/transcript 展开**：固定 6 行折叠、无任何展开 keybind（对照 Claude Code 的 Ctrl+O transcript mode）。工具入参也是单行截断不可看全。
2. **diff 渲染**：无着色 diff 组件；`/diff` 只是把 `git diff` 原文当 system 行 dump、60 行截断（handler.ts:572-601）。Edit/Write 类工具调用没有任何结构化 diff 视图。
3. **agent 中途提问 / 工具审批**：见 §5 — `follow_up_question` 不处理、`ask` 模式无 UI。这是与"现代 agent TUI"差距最大的一项。
4. **todo / plan / subagent 显示**：无任何对应组件或事件处理。
5. **thinking 内容显示**：只有 effort 档位上 BottomBar；thinking 流不渲染。
6. **图片/附件**：bracketed paste 只支持文本；无图片粘贴、无附件发送。`@file` 仅插入路径文本。
7. **busy 时排队消息**：`submitText` 直接 `if (busy) return`（PromptInput.tsx:206），不能像 Claude Code 那样排队下一条。
8. **多行编辑**：单行视口（换行显示成 `↵ `），无真正多行编辑器、无 `$EDITOR` 外跳；LineInput 连光标移动都没有。
9. **会话内搜索**：Ctrl+R 是跨会话 FTS 注入草稿，当前 transcript 没有 find。
10. **keybinding 自定义 / 快捷键帮助页**：设计文档明确 deferred；提示全靠各处 footer 文案。

**做了一半 / 有隐患**
11. **ui/ kit 迁移停滞**：ModalProvider/Confirm/Form/MultiSelect/Toast/Alert/ScrollView 全部只在 Demo 使用；REPL 仍是 24 态 `pickerKind` 枚举 + 巨型 ctx 对象（pickerRouter.tsx:50-110），正是 Form.tsx 文件头注释声称要消灭的形态。
12. **流式 markdown 跳变**：streaming 显示裸 markdown 源码，result 后整段重排（Turn.tsx:123）。且 `renderMarkdown` 无 memo，全量重绘渲染器下长 transcript 每帧重 parse。
13. **工具结果按名字回填**会错配并发同名调用（handleChatResponse.ts:152-179，无 tool call id）。
14. **/resume 丢工具历史**（useWsEvents.ts:326-336 只取 role+content）。
15. **registry/handler 不同步**：`/branch`、`/aliases`、`/web`、`/exit` 在 handler 里实现但不在 `SLASH_COMMANDS`（不出现在补全菜单）；反之 `/memory /mcp /doctor /review /compact` 在菜单里但是 stub（handler.ts:612-621 直接劝退回 shell）。
16. **REST 回退端口不一致**（18109 vs 8765，见 §4）。
17. **`/cost`** 只是指一下 BottomBar，没有真实成本明细。
18. **`validate` prop（ui/Input.tsx）**、Select 的 `disabled` option 都是声明了的 no-op 占位。

**已经做得不错的**（避免误判为短板）：滚动回看（ScrollBox + sticky + 全套滚动键）、主题（4 主题 + auto OSC-11 探测 + live preview）、状态栏（token/context%/cache/连接态/权限态）、命令调色板、fish autosuggest、@file 补全、per-session 草稿持久化、双击 Ctrl+C 退出、长轮响铃、多账号管理与 OAuth 登录全程内嵌、channel 绑定全流程含覆盖确认、外会话活动 feed、`/export` markdown 导出、`/copy` 剪贴板（pbcopy/xclip/OSC52 兜底）。

## 7. docs/design/cli/ 设计文档现状

| 文档 | 内容 | 落地程度 |
|---|---|---|
| `cli-redesign.md` (141 行) | schema 驱动设置（`config_schema.py` 单一 SettingSpec 注册表）+ TUI SettingsPanel + Ctrl+K palette + config get/set + 容器 verb 帮助统一 | **文档头部自标 "Status: implemented (2026-06)"**。已验证落地：SettingsPanel.tsx 存在且 `/config` 接 `get_settings`/`set_setting` WS action（handler.ts:603-610）；Ctrl+K palette 在 REPL.tsx:240-243；P0 的"Ports 可在 TUI 改 + next-start 标注"由 SettingsPanel 的 `apply: 'next_start'` 渲染兑现。唯一明确 deferred：keybind 编辑器（文档 §4 末尾） |
| `slash-commands.md` (356 行) | 统一 slash 命令登记表设计：L0 built-in → L1 plugins → L2 MCP prompts → L3 skills → L4 user `~/.openprogram/commands/*.md` → L5 project，markdown+frontmatter 格式、`$ARGUMENTS`/`$0..$9`/`!`cmd``/`@`path`` 模板、prompt/local/fork 执行模式 | **未落地**：TUI 的 `registry.ts` 仍是 42 条硬编码数组，无 user/project/skill/MCP 命令源，无 frontmatter 加载器，无命名空间消歧 UI |
| `slash-commands-references.md` (282 行) | 上文的参照调研（claude-code/opencode/openclaw/hermes 的实现笔记） | 纯参考 |
| `cli-naming.md` (92 行) | Python CLI 的 noun-first verb-last 语法规范 | 规范类，与 TUI 无直接关系 |
| `config-write-safety.md` / `drop-run-command.md` / `ports.md` | config 写安全、去掉 /run、端口管理 | Python CLI 侧议题 |

与 auto-memory 中 "CLI/TUI redesign (designed, pending)" 的记录相比，cli-redesign.md 的 P0-P2 实际已基本完成；**真正 pending 的大块是 slash-commands.md 的统一命令表**，以及本报告 §5/§6 指出的（不在任何设计文档中的）agent 交互缺口：`follow_up_question` 处理、`ask` 审批 UI、工具输出展开、diff 渲染、kit 迁移。
---

# Claude Code 终端 TUI 设计调研报告

信息源：
- **A. 泄露版源码**（结构完整、版本较新，已含 keybindings/teams/tasks 子系统）：`/Users/fzkuji/Documents/LLM Agent Harness/OpenProgram/references/claude-code-leaked/src`（下文以 `src/` 简写；文件为 react-compiler 编译产物但带 inline sourcemap，可读）
- **B. 新版行为**：本机 `claude` v2.1.175（`/Users/fzkuji/.local/share/claude/versions/2.1.175`，Bun 编译 Mach-O，已做 strings 挖掘）+ 官方文档 `https://code.claude.com/docs/en/interactive-mode`（已抓取）

---

## 1. 信息密度设计：默认折叠什么、展开什么

### 1.1 总原则
每个 tool call 默认渲染为 **两行**：一行 tool use 头（`⏺ ToolName(摘要参数)`），一行结果摘要（`  ⎿  结果`）。完整内容只在两个地方出现：verbose 模式（`--verbose`/config）和 transcript 模式（ctrl+o）。

### 1.2 结果输出截断：3 行 + "… +N lines (ctrl+o to expand)"
核心实现 `src/utils/terminal.ts`：
- `MAX_LINES_TO_SHOW = 3`（terminal.ts:7）— 工具结果折叠态只显示前 3 行（按终端宽度做 ANSI-aware 折行后计数，宽度 = columns - 10 padding）。
- 超出部分渲染 `… +${N} lines (ctrl+o to expand)`（`renderTruncatedContent`，terminal.ts:71；hint 文案来自 `src/components/CtrlOToExpand.tsx`）。
- **细节 1**：如果折叠后只剩 1 行没显示，直接把那行显示出来，不显示 "+1 lines"（terminal.ts:45-55）——避免提示行比内容还贵。
- **细节 2**：性能预截断——只处理 `MAX_LINES * wrapWidth * 4` 个字符，剩余行数用字符数估算，防止 64MB 输出卡死渲染（terminal.ts:83-90）。
- **细节 3**：子 agent 输出内部、虚拟列表内不重复显示 "(ctrl+o to expand)" hint（CtrlOToExpand.tsx:13 的 `SubAgentContext`）。

### 1.3 工具行自身的截断
- Bash 命令折叠态最多 **2 行 / 160 字符**，超出加 `…`（`src/tools/BashTool/UI.tsx:26-27`）；verbose 显示完整命令。
- 工具结果若是 JSON 行，≤10KB 时自动 pretty-print（`src/components/shell/OutputLine.tsx` 的 `tryJsonFormatContent`），并把 JSON 里的 URL 转成终端超链接。

### 1.4 collapsed vs verbose(ctrl+o) 两种形态
- **collapsed**：工具行 + 3 行结果摘要；连续 Read/Grep/Glob/MCP 调用整组折叠成一行（见 §2.8）；子 agent 只显示最近 3 条进度（`src/tools/AgentTool/UI.tsx:34`，`MAX_PROGRESS_MESSAGES_TO_SHOW = 3`）。
- **ctrl+o transcript**：不是简单展开，而是切换到独立 screen（`src/screens/REPL.tsx:571` `Screen = 'prompt' | 'transcript'`），**冻结**当前消息列表（REPL.tsx:1324 `frozenTranscriptState`），所有消息以 `isTranscriptMode=true` 重渲染：子 agent 显示 Prompt/全程嵌套 transcript/Response，MCP 折叠组展开。底部 footer：`Showing detailed transcript · ctrl+o to toggle · ctrl+e to show all`（REPL.tsx:318-336 `TranscriptModeFooter`）。transcript 内再按 **ctrl+e** 才显示"全部内容"（无任何截断）。
- 新版（2.1.175）transcript 是完整 pager：`/` 搜索（带 indexing 提示、`n/N` 跳转、右侧 `当前/总数` badge）、`[` 把全文打到终端原生 scrollback、`v` 用 $EDITOR 打开、`?` 快捷键帮助面板、`{`/`}` 跳上/下一条用户消息、vim 滚动键（binary strings 中的 `TranscriptHelpMenu`；文档 interactive-mode 同步确认）。

---

## 2. 每类工具的定制渲染

工具渲染是接口化的：每个工具实现 `renderToolUseMessage` / `renderToolUseProgressMessage` / `renderToolResultMessage` / `renderToolUseRejectedMessage` / `renderToolUseErrorMessage` / `renderToolUseQueuedMessage` / `renderToolUseTag`（`src/Tool.ts:562-678`）。统一外壳 `src/components/messages/AssistantToolUseMessage.tsx`：`{状态点}{粗体工具名}({参数摘要}){tag}`；结果行经 `src/components/MessageResponse.tsx` 统一加 `  ⎿  ` 前缀（2 空格 + ⎿ + 2 空格，5 字符 gutter）。

状态点（`src/components/ToolUseLoader.tsx`）：运行中 = ⏺ 闪烁；排队 = ⏺ 暗色常亮；成功 = ⏺ 绿色；出错 = ⏺ 红色。

| 工具 | tool use 行 | 进行中 | 结果（collapsed） | verbose/transcript |
|---|---|---|---|---|
| **Bash** | `Bash(command)`，≤2行/160字符；`sed -i` 命令特判显示为文件路径（BashTool/UI.tsx:99-103）| `Running…` → ShellProgressMessage（输出尾部 + 已耗时/总行数/字节数）；下方 hint `(ctrl+b to run in background)`，tmux 下显示 `ctrl+b ctrl+b (twice)`（UI.tsx:71）| stdout 前 3 行；stderr 红色；空输出显示 `(No output)` 或 `Done`；后台任务显示 `Running in the background (↓ manage)`；`Shell cwd was reset to…` 警告单独拆出来 dim 显示（BashToolResultMessage.tsx）| 完整输出 |
| **Edit** | `Update(path)` / `Create(path)`（userFacingName 区分，FileEditTool/UI.tsx:25-45）| — | `Added N lines, removed M lines` + **行号 gutter 的结构化 diff**（上下文 3 行，`src/utils/diff.ts:9` `CONTEXT_LINES=3`；添加/删除整行底色 `diffAdded/diffRemoved` + 词级高亮 `diffAddedWord`，`src/utils/theme.ts`）；`style:'condensed'` 时只显示统计行（FileEditToolUpdatedMessage.tsx）| diff 全量 |
| | 错误特判：`File has not been read yet` → dim 的 `File must be read first`；找不到文件 → 红色 `File not found`，不抛吓人的原始错误（FileEditTool/UI.tsx:renderToolUseErrorMessage）| | | |
| **Write** | `Write(path)` | — | `Wrote N lines to path` + 语法高亮的前 **10 行**（`FileWriteTool/UI.tsx:27` `MAX_LINES_TO_RENDER=10`）+ `… +x lines (ctrl+o to expand)` | 全文 |
| **Read** | `Read(path)`；verbose 加 `· lines 12-50`；PDF 加 `· pages 1-5`（FileReadTool/UI.tsx）| — | 一行：`Read **N** lines` / `Read image (2.3MB)` / `Read **N** cells` / `Read PDF (…)`；缓存命中显示 dim 的 `Unchanged since last read` | 同左（Read 从不展开内容——内容在模型侧）|
| **Grep/Glob** | `Grep(pattern)` | — | `Found **N** files` / `Found **N** matches across **M** files`（GrepTool/UI.tsx:45），非 verbose 截断列表 + ctrl+o hint | 文件列表全量 |
| **TodoWrite** | **不渲染工具行**（`userFacingName() = ''`，TodoWriteTool.ts）——todo 更新直接反映到 spinner 区的任务清单，全部完成时自动清空列表 | — | 清单见 §4.4 | — |
| **Task（子 agent）** | `Task(description)` + agent 颜色 tag | 最近 3 条进度行；连续 read/search 合并为 `Searching for 2 patterns, reading 5 files…`；运行中可 ctrl+b 转后台 | `Done (**N** tool uses · 48.2k tokens · 2m 10s)` + `(ctrl+o to expand)`（AgentTool/UI.tsx:renderToolResultMessage）；后台启动显示 `Backgrounded agent (↓ manage · ctrl+o expand)` | `Prompt:`（markdown）→ 完整嵌套子 transcript → `Response:` |
| **WebSearch** | `WebSearch("query")` | `Searching: {query}` → `Found N results for "query"`（progress 流式更新）| `Did N searches in 4s`（WebSearchTool/UI.tsx）| verbose 额外显示 allowed/blocked domains |
| **WebFetch** | url（verbose 加 prompt）| `Fetching…` | `Received **12.3KB** (200 OK)` | + 返回正文全文 |
| **MCP 工具** | 新版默认把连续 MCP 调用折叠为一行 `Called slack 3 times`（文档 interactive-mode：ctrl+o 展开；binary 中 `Queried {server} N times`）| | | |

### 2.8 连续读/搜折叠组（主线程也做）
`src/components/messages/CollapsedReadSearchContent.tsx` + `src/utils/collapseReadSearch.ts`：连续的 Read/Grep/Glob/LS/REPL/MCP/memory 调用合并为一行动词短语：`Searched for **2** patterns, read **5** files, queried slack **3** times`（进行中用现在时 `Searching… reading…` + 末尾 `…`）。进行中在下方 `⎿` 行实时滚动显示当前文件名/搜索词，每条 hint 最少停留 700ms 防闪烁（`MIN_HINT_DISPLAY_MS = 700`）。组内有 PreToolUse hook 时追加 `⎿ Ran 2 PreToolUse hooks (0.3s)`。

---

## 3. 快捷键全表（以 2.1.175 + 官方文档为准）

默认绑定源码：`src/keybindings/defaultBindings.ts`（支持 `~/.claude/keybindings.json` 按 context 覆盖；ctrl+c/ctrl+d 保留不可重绑，`src/keybindings/reservedShortcuts.ts`）。新版 binary 提取的绑定表与文档核对如下：

**全局（Global）**

| 键 | 动作 |
|---|---|
| `ctrl+c` | 中断运行；空闲时第一次清空输入、第二次退出 |
| `ctrl+d` | 退出（EOF）|
| `ctrl+o` | **切换 transcript 详细视图**（含展开 MCP 折叠）|
| `ctrl+t` | 切换任务（todo）清单显示 |
| `ctrl+r` | 反向搜索输入历史；再按 ctrl+r 翻旧匹配；`ctrl+s` 切换范围（本会话/本项目/全部项目）；Tab/Esc 接受、Enter 直接执行、ctrl+c 取消（HistorySearch context）|
| `ctrl+l` | 重绘屏幕（新版中 Chat context 的 ctrl+l 为 `chat:clearInput`，`cmd+k` 清屏）|
| `ctrl+x ctrl+k` | 杀掉所有后台子 agent（3 秒内按两次确认）|
| `ctrl+shift+o` | teammate 预览切换（swarm）|

**对话输入（Chat）**

| 键 | 动作 |
|---|---|
| `esc` | **打断 Claude**（保留已完成的工作）|
| `esc esc` | 输入有内容→清空（存入历史可↑找回）；输入为空→打开 **Rewind** 菜单 |
| `shift+tab`（或 alt+m）| 循环 permission mode：default → acceptEdits → plan →（auto/bypass 若启用）|
| `ctrl+b` | 把前台 bash/agent 全部转后台（Task context；tmux 用户按两次；新版另有 `ctrl+x ctrl+b` 别名）|
| `enter` / `ctrl+j` / `\`+enter / shift+enter（部分终端）/ option+enter | 提交 / 换行 |
| `↑/↓`（或 ctrl+p/n）| 多行内先移光标，到边界后翻历史；**忙碌时 ↑ 可编辑排队消息** |
| `ctrl+g` 或 `ctrl+x ctrl+e` | 用 $EDITOR 编辑当前 prompt |
| `ctrl+s` | 暂存（stash）prompt |
| `ctrl+_` / `ctrl+-` | 撤销输入 |
| `ctrl+v`（win: alt+v）| 粘贴剪贴板图片，插入 `[Image #N]` chip |
| `meta+p` | 切换模型（不清空输入）|
| `meta+t` | 切换 extended thinking |
| `meta+o` | 切换 fast mode |
| `ctrl+a/e/k/u/w/y`, `alt+b/f/y` | readline 行编辑全套（文档 Text editing 表）|
| 按住/点按 `space` | 语音输入（启用后）|

**前缀字符**：`/` 命令+skill 补全菜单；`!` shell 模式（直接执行、输出入上下文、Tab 从历史 `!` 命令补全、esc/backspace 退出）；`@` 文件路径补全。**`#` 记忆前缀已在新版移除**（binary 中 0 处 "memorize"；文档 Quick commands 表只有 / ! @ 三个）。

**Transcript 模式内**：`q`/`esc`/`ctrl+c` 退出；`ctrl+e` show all/collapse；`/` 搜索、`n/N` 跳转；`[` 打印到 scrollback；`v` 在编辑器打开；`?` 帮助；`j/k ↑↓` 滚动、`ctrl+u/d` 半页、`space/b` 整页、`g/G` 顶/底、`{`/`}` 上/下一条用户消息。

**对话框（Confirmation/Select）**：`y/n/enter/esc`；`↑↓/j/k/ctrl+n/p` 导航；`tab` 切字段；`space` 多选切换；`shift+tab` 在权限对话框里循环模式（= 直接选 auto-accept）；`ctrl+e` 展开权限解释。

---

## 4. 状态/反馈系统

### 4.1 Spinner 状态行
`src/components/Spinner.tsx` + `src/components/Spinner/SpinnerAnimationRow.tsx`，单行复合格式：

```
✻ Cogitating… (esc to interrupt · 42s · ↓ 3.2k tokens · thinking)
```

- **动词**：优先级 = override 消息 > 当前 in-progress todo 的 `activeForm`（如 "Running tests"）> 从 ~300 个趣味动词随机挑一个（`src/constants/spinnerVerbs.ts`：Clauding/Brewing/Combobulating…，用户可在 settings 里 replace/append）。
- **括号内 byline 按剩余宽度渐进显示**（progressive width gating）：esc to interrupt → 计时器 → `↓ N tokens`（输出 token 估算 = 字符/4）→ `thinking` / `thought for Ns`（thinking 状态最少显示 2s 防跳变）。
- 3 秒无新 token spinner 变红（stalled 检测）；reduced-motion 设置可关动画。
- **Tip 行**（spinner 下方 dim）：>30s 且没用过 /btw → `Tip: Use /btw to ask a quick side question…`；>30min → `Tip: Use /clear to start fresh…`；有 todo 时优先显示 `Next: {下一个 pending 任务}`。

### 4.2 上下文余量
`src/components/TokenWarning.tsx:166`：常态 dim `{N}% until auto-compact`；超阈值变 warning/error 色 `Context low ({N}% remaining) · Run /compact to compact & continue`。2.1.175 binary 同款文案（另支持 DISABLE_COMPACT 分支）。

### 4.3 Footer（输入框下方）
`src/components/PromptInput/PromptInputFooter*.tsx`：左侧 = 权限模式 pill（见 §6.3）+ `shift+tab to cycle` hint（空间不够自动隐藏）；中间 = 后台任务/teammate pills（↑↓ 选中、enter 打开详情，Footer context 键位）；右侧 = PR badge（`PR #446`，下划线颜色表 review 状态，60s 刷新）。输入为空时 placeholder 显示 prompt suggestion（来自 git 历史/对话延续，Tab 接受）。`? for shortcuts` 打开帮助菜单（`PromptInputHelpMenu.tsx`：列出 ! / @ /btw、undo、stash、外部编辑器、图片粘贴、模型切换、todos、transcript、verbose 等全部快捷键）。

### 4.4 Todo 清单呈现
`src/components/TaskListV2.tsx`：
- 图标语义：completed = `✔`（success 绿）+ **删除线**（TaskListV2.tsx:313 `strikethrough={isCompleted}`）；in_progress = `◼`（claude 品牌色）+ 粗体；pending = `◻` dim；被阻塞任务追加 `› blocked by 3,4`。
- 显示数量随终端高度自适应（`rows<=10` 不显示，否则 `min(10, rows-14)`；文档说新版默认显示 5 条）；溢出折叠为 `… +2 pending, 1 completed`。
- 截断时优先级：30 秒内刚完成的（`RECENT_COMPLETED_TTL_MS=30_000`，让用户看见勾掉的瞬间）> in_progress > pending（未被阻塞的优先）> 老的 completed。
- 位置在 spinner 正下方，ctrl+t 切换 `expandedView==='tasks'`；in_progress 任务的 activeForm 直接当 spinner 动词。

### 4.5 后台任务通知
- bash 转后台：结果行变 `Running in the background (↓ to manage)`，输出写文件由 Read 工具取回。
- 后台任务完成 → 以 task-notification 形式排进消息队列显示，**最多 3 行**，溢出合成一条 `+N more tasks completed`（`src/components/PromptInput/PromptInputQueuedCommands.tsx:30` `MAX_VISIBLE_NOTIFICATIONS=3`）。
- 新版 Session recap：离开 3 分钟后回来，自动显示一行会话摘要。

---

## 5. 中断与转向

- **esc 单击**：`chat:cancel` 打断当前 turn，已生成内容保留并标注 `Interrupted`（`src/components/InterruptedByUser.tsx`）；spinner 中的 `(esc to interrupt)` 持续提示这是安全操作。
- **esc esc**：输入非空 = 清空草稿（进历史）；输入为空 = 打开 **Rewind** 对话框（`src/components/MessageSelector.tsx`）。新版引导文案：`Double-tap esc to rewind the code and/or conversation to a previous point in time`（binary strings）。
  - 列出历史用户消息（j/k 导航，每条显示时间 + 该点以来的文件变更统计 `3 files changed +45 -12`，无快照的标 `⚠ No code restore`）。
  - 选中后二级菜单（MessageSelector.tsx:93-105）：`Restore code and conversation` / `Restore conversation` / `Restore code` / `Summarize from here`（可附加 context 的内联输入框）/ `Never mind`，下方动态解释每个选项的后果，附警告 `⚠ Rewinding does not affect files edited manually or via bash.`。
- **打字排队（queued messages）**：Claude 忙碌时输入直接排队，渲染在输入框上方（复用消息组件、dim 显示，`PromptInputQueuedCommands.tsx`）；placeholder 前 3 次提示 `Press up to edit queued messages`（`usePromptInputPlaceholder.ts:54`）——↑ 取回排队消息继续编辑。队列在 turn 间自动 flush 进对话。
- **/btw 旁路提问**：不打断主 turn、无工具、不进历史，answer 出现在 overlay（space/enter/esc 关、c 复制、f fork 成新会话）。

---

## 6. Permission 询问 UI

### 6.1 对话框外形
`src/components/permissions/PermissionDialog.tsx`：**只有顶边框**的圆角横线（borderLeft/Right/Bottom=false）+ 标题（粗体、`permission` 主题色，如 `Bash command` / `Bash command (unsandboxed)` / `Edit file`）+ dim 副标题 + 内容区。不是全包围大盒子——视觉重量很轻。内容区显示工具特定预览：Bash 显示完整命令 + Haiku 生成的命令描述；Edit/Write 显示完整 diff/内容预览；WebFetch 显示 URL。`ctrl+e` 展开"为什么询问"的规则解释（Confirmation context `confirm:toggleExplanation`）。

### 6.2 选项结构（Bash 为例，`src/components/permissions/BashPermissionRequest/bashToolUseOptions.tsx`）
1. `Yes`（可带内联输入 `and tell Claude what to do next`）
2. `Yes, and don't ask again for: [可编辑的命令前缀输入框，如 npm run:*]` —— **don't-ask-again 规则是用户可现场编辑的 prefix**，不是死按钮；或显示建议规则标签（haiku 生成）
3. `No`（内联输入 placeholder：`and tell Claude what to do differently`）—— 拒绝即转向，esc 同义

文件类（FilePermissionDialog）有 `Yes, allow all edits during this session` / `Yes, allow reading from {dir}`；ExitPlanMode 有 `Yes, auto-accept edits` / `Yes, and bypass permissions` / `Yes, clear context and …`（shift+tab 直接选 auto-accept，ExitPlanModePermissionRequest.tsx:267）。新版问题句式统一为 `Do you want to make this edit to {file}?` / `Do you want to proceed?`。

### 6.3 模式 pills（footer 常驻）
binary 提取：default 无 pill；`⏵⏵ accept edits on`（autoAccept 色）；`⏸ plan mode on`（planMode 色）；`⏵⏵ auto mode on`（warning 色）；`Bypass Permissions`（error 红）。固定文案 + 固定符号，shift+tab 循环。

### 6.4 AskUserQuestion 多选题卡片
`src/components/permissions/AskUserQuestionPermissionRequest/`：
- 多问题时顶部是 **tab 栏**（`QuestionNavigationBar.tsx`：`← Q1 | Q2 | ✔ Submit →`，当前 tab 至少占一半宽度、其余压缩，左右箭头切换）。
- 每题 Select（单选）或 SelectMulti（space 勾选）；选项可带 description 与 preview 面板（`PreviewQuestionView.tsx`）。
- 永远追加 `Other` 自由文本项（QuestionView.tsx:208-209，placeholder "Type something"）；新版强制带 **Skip 按钮**（binary："AskUserQuestion always includes a Skip button and a free-text input box"）。
- esc = "respond to Claude directly"（把控制权还给输入框），ctrl+g 可把答案丢进外部编辑器写长文。

---

## 7. 值得 OpenProgram 抄的 Top 10（按适用性排序）

1. **工具渲染接口化**（`Tool.ts:562-678`）：每个工具实现 `renderToolUse / Progress / Result / Rejected / Error / Queued` 六个钩子，外壳统一管状态点、参数括号、⎿ gutter。OpenProgram 的 @agentic_function/内置工具天然适配这套协议，是 TUI 升级的地基——先定接口再谈样式。
2. **"3 行 + … +N lines (ctrl+o to expand)" 截断**（terminal.ts:71）连同两个细节：剩 1 行直接显示、超大输出按字符预截断估行数。低成本高回报，OpenProgram 的 bash/函数输出可直接照抄。
3. **两行语法 + 状态点颜色语言**：`⏺`（排队 dim / 运行闪烁 / 成功绿 / 失败红）+ `  ⎿  ` 结果缩进。整个 UI 只靠 2 个字形表达全部工具状态，没有边框没有盒子，信息密度极高。
4. **ctrl+o = 冻结快照式 transcript 屏**而非原地展开：消息列表冻结后整树以 transcript 模式重渲染，底部 footer 提示退出/`ctrl+e` show all，新版再加 `/` 搜索和 `[` dump-to-scrollback。比"逐条点开"模型简单且可达性强。
5. **每类工具的"结果一句话"**：Read→`Read 52 lines`、Edit→`Added 5 lines, removed 2 lines`+diff、Write→前 10 行高亮、Grep→`Found 8 files`、子 agent→`Done (12 tool uses · 48k tokens · 2m)`、WebFetch→`Received 12KB (200 OK)`。摘要全部是**量词**（行/文件/token/时长），不是省略号截断——这是折叠不丢信息的关键。
6. **连续读/搜折叠组**（CollapsedReadSearchContent）：把工具风暴折成 `Searching for 2 patterns, reading 5 files…` 一行 + 实时 ⎿ hint（700ms 最短停留）。OpenProgram 的 DAG/pipeline 执行大量并行函数调用，这是最值得抄的密度技术。
7. **spinner 复合状态行 + todo 联动**：`✻ 动词… (esc to interrupt · 时长 · ↓ tokens · thinking)` 按宽度渐进降级；动词取自当前 in-progress todo 的 activeForm，下一行 `Next: …`；todo 清单 ✔删除线/◼粗体/◻dim、终端高度自适应、刚完成的 30s 内优先可见。
8. **esc 打断 / esc esc Rewind**：打断保留已完成工作并明示；Rewind 列出历史消息 + 每点的文件 diff 统计，恢复选项区分代码/对话/摘要并解释后果。OpenProgram 有 session 存储，可以先做"仅恢复对话"的子集。
9. **忙碌时输入排队 + ↑ 编辑队列**：排队消息 dim 渲染在输入框上方，placeholder 教学 3 次后消失；后台完成通知限 3 行 + `+N more`。解决"agent 跑着不敢打字"的核心体验问题，实现成本低。
10. **permission 对话框语法**：轻量顶线框 + 工具特定预览（命令全文/完整 diff）+ 三段式选项（Yes / Yes-and-don't-ask-again-带可编辑规则输入框 / No-带反馈输入框）+ footer 固定文案模式 pills（`⏵⏵ accept edits on`）。"don't ask again 即现场编辑规则" 和 "拒绝即附带转向指令" 两个点尤其值得抄。

另两个不进 Top10 但备查的点：键位系统是 **context 分层 + 用户 JSON 可覆盖 + 保留键校验**（`src/keybindings/`，OpenProgram TUI 重设计已计划 schema-driven settings，可同构）；`#` 记忆前缀在新版已删除，证明前缀字符要克制（现存仅 `/` `!` `@`）。
---

# opencode TUI 设计调研报告（OpenProgram TUI 升级参照）

## 0. 重要前提更正：Go + bubbletea TUI 已不存在

任务假设的 `packages/tui`（Go + bubbletea）在当前 仓库快照中**已被删除**：git 历史中 commit `96bdeb3c7 "OpenTUI is here (#2685)"` 引入新 TUI，随后 `f68374ad2 "DELETE GO BUBBLETEA CRAP HOORAY"` 删除了整个 Go 实现。

现行 TUI 位于 `references/opencode/packages/opencode/src/cli/cmd/tui/`（约 160 个文件），技术栈是 **TypeScript + solid-js + OpenTUI**（`@opentui/core` / `@opentui/solid` / `@opentui/keymap`，见 `packages/opencode/package.json`）。OpenTUI 是 SST 自研的终端渲染器，提供 flexbox `<box>`、`<scrollbox>`（带 sticky scroll）、`<markdown>`（流式）、`<diff>`（split/unified）、`<code>`（tree-sitter wasm 高亮）、`<textarea>/<input>`、鼠标事件、文本选区/剪贴板等 JSX 内建元素。

这一变化对 OpenProgram 反而是利好：**参照对象从 Go/bubbletea 变成了与 React/Ink 同构的 reactive-JSX 终端 UI**，组件结构、context/provider 模式几乎可以直接平移；差距主要在 Ink 渲染器能力（无鼠标、无内建 scrollbox/markdown/diff）。

进程模型也值得注意（`cli/cmd/tui/thread.ts:115-260`）：TUI 跑在主线程，**server 跑在同进程的 Bun Worker** 里；默认传输是 worker RPC 桥接的假 `fetch`（`url: "http://opencode.internal"`，`thread.ts:201-211`）+ RPC 事件订阅，只有显式 `--port` 时才走真 HTTP+SSE。另有 `attach.ts` 命令可以连远程 server（HTTP + SSE + basic auth）。即：**UI 层完全只依赖 "HTTP API + 事件流" 这个抽象**，与 OpenProgram 的 Python 后端 + SSE/WS 架构同形。

---

## 1. 整体布局架构

### 1.1 根布局（`app.tsx`）

`app.tsx:942-980` 的根节点是一个占满终端的 `<box flexDirection="column">`：

- 主区 `flexGrow={1}`：`<Switch>` 按 route 渲染 `Home` 或 `Session`，外加 plugin 注册的自定义 route；
- 底部 `flexShrink={0}`：plugin slot `app_bottom` / `app`；
- `StartupLoading` 覆盖层在 ready 前显示。

route 是自研的极简 RouteProvider（`context/route.tsx`），route 数据只有 `{type: "home"} | {type: "session", sessionID} | {type: "plugin", id}` 三种。**没有多 tab、没有嵌套路由**——整个 app 任意时刻只有一个主视图。

整个 app 用约 15 层 context provider 嵌套组装（`app.tsx:200-263`）：keymap → args → exit → kv → toast → route → tui-config → SDK → project → sync → theme → local → dialog → prompt 等，每个关注点一个 provider。

### 1.2 Session 视图布局（`routes/session/index.tsx:1095-1291`）

```
┌──────────────────────────────────────────┬───────────────┐
│  scrollbox（消息流，stickyScroll bottom） │   Sidebar     │
│                                          │  (固定 42 列)  │
│                                          │  标题/share    │
│                                          │  Context tokens│
│                                          │  Todo          │
│  ──────────────────────────────────────  │  LSP           │
│  底部固定区（flexShrink=0），三选一：       │  MCP           │
│   PermissionPrompt | QuestionPrompt |    │  修改文件       │
│   Prompt（输入框，含自带状态 footer）       │  版本 footer    │
└──────────────────────────────────────────┴───────────────┘
```

关键点：

- 消息区是 `<scrollbox stickyScroll stickyStart="bottom">`（`index.tsx:1118-1135`），流式输出时自动贴底，用户上滚则脱离 sticky；滚动条可开关。
- **Sidebar 响应式**：`wide = width > 120` 时作为固定 42 列右栏；窄屏手动打开时变为绝对定位 overlay，底色 `RGBA(0,0,0,70)` 半透明遮罩（`index.tsx:1268-1287`）。sidebar 状态是 `"auto" | "hide"` 持久化在 KV。
- 内容宽度统一计算：`contentWidth = width - (sidebar?42:0) - 4`（`index.tsx:237`），通过 session context 下发给所有 part 渲染器（折叠阈值、diff split/unified 都依赖它）。
- **没有独立的全局状态栏**——状态信息分散在 Prompt 组件自带的上下两行（见 §6）和 Home footer plugin。
- permission/question 出现时 `visible()` 为 false，**输入框整体被替换**而不是弹窗（`index.tsx:1203-1204, 1232-1263`）。

### 1.3 Plugin slot 布局系统

内部 feature 也走 plugin slot 机制（`feature-plugins/`、`plugin/slots.tsx`）：sidebar 的 Context/Todo/LSP/MCP/Files 各自是一个 internal plugin，向 `sidebar_content` slot 按 `order` 注册（如 `feature-plugins/sidebar/context.tsx:49-58` order=100，lsp.tsx order=300）。slot 有三种模式：追加、`single_winner`、`replace`（外部插件可整体替换 prompt 或 footer，`routes/session/index.tsx:1242-1262`）。布局可扩展点全部显式命名：`home_logo/home_prompt/home_footer/sidebar_title/sidebar_content/sidebar_footer/session_prompt/session_prompt_right/app_bottom` 等。

---

## 2. 消息与 tool call 渲染

### 2.1 消息结构

数据模型是 `message → parts[]`，part 类型 `text | tool | reasoning | file | compaction`。`AssistantMessage`（`index.tsx:1410-1499`）逐 part 用 `PART_MAPPING = {text, tool, reasoning}`（`index.tsx:1501-1505`）动态分发，并在消息末尾渲染一行元信息：`▣ Build · claude-sonnet-4-5 · 32s`（agent 色块 + 模式 + 模型 + 用户消息→完成的耗时 + `interrupted` 标记，`index.tsx:1471-1496`）。

`UserMessage`（`index.tsx:1304-1408`）：左侧 `┃` 竖线 border，颜色 = 当前 agent 颜色；hover 变底色；点击打开 DialogMessage（复制/编辑/fork 该消息）；附件渲染为 `img/pdf/txt` 彩色 MIME 徽章；排队中的消息显示反色 `QUEUED` 徽章（`index.tsx:1390-1393`）。

视觉语言核心是 `SplitBorder`（`component/border.tsx`）：所有"块"（用户消息、tool block、错误、permission 面板）统一是**左侧粗竖线 `┃` + panel 底色**，没有完整边框盒子。

### 2.2 两级工具渲染原语：InlineTool / BlockTool

这是 tool 渲染的关键抽象（`index.tsx:1752-1889`）：

- **InlineTool**：单行，`icon + 文本`。未完成时显示 `~ pending文案`（如 "Reading file..."）或 spinner；完成后变灰；被拒绝时整行**删除线**（通过匹配 error 字符串 `rejected permission` 等判断，`index.tsx:1784-1790`）；正在等 permission 时变 warning 色（`index.tsx:1769-1773`）。还有一个巧妙细节：用 `renderBefore` 钩子检查前一个兄弟元素高度，**连续的单行工具之间不留空行，块与单行之间留一行**（`index.tsx:1802-1823`）。
- **BlockTool**：左竖线块，标题行 `# 描述`，运行中标题变 spinner，可传 `onClick` 做展开/折叠，hover 变色，错误附在块尾。

### 2.3 每种工具的具体显示

| 工具 | 形态 | 内容（代码位置） |
|---|---|---|
| shell | BlockTool | 标题 `# {description} in {workdir}`；`$ command` + 输出，**默认折叠 10 行**，"Click to expand/collapse"（`index.tsx:1891-1947`） |
| edit | BlockTool | 标题 `← Edit path`；`<diff>` 内建元素渲染，**宽 >120 自动 split 双栏否则 unified**（可配 `diff_style: stacked`），行号、word wrap 可切换、主题化 diff 配色 + **LSP diagnostics 错误前 3 条附在 diff 下**（`index.tsx:2135-2186, 2327-2348`） |
| write | BlockTool | `<code>` + 行号 + diagnostics（`index.tsx:1949-1980`） |
| apply_patch | 每文件一个 BlockTool | 标题区分 `# Created / # Deleted / # Moved a → b / ← Patched`；删除只显示 `-N lines`（`index.tsx:2188-2262`） |
| read | InlineTool `→` | `Read path [offset=.., limit=..]`，附 `↳ Loaded xxx` 列出连带加载的文件（`index.tsx:1994-2027`） |
| glob/grep | InlineTool `✱` | `Grep "pattern" in path (N matches)`（结果计数来自 metadata） |
| webfetch / websearch | InlineTool `% / ◈` | URL / `Provider "query" (N results)` |
| task（子 agent） | InlineTool `│` + spinner | **实时嵌套进度**：`General Task — 描述`、第二行 `↳ 当前工具 title`、完成后 `└ N toolcalls · 时长`；点击**导航进子 session**（`index.tsx:2059-2133`） |
| todowrite | BlockTool `# Todos` | TodoItem 列表 |
| question | BlockTool | 已答的 Q/A 对 |
| 其他/未知 | InlineTool `⚙` | `toolname [key=value, ...]`（`input()` 把原始参数里的标量序列化成摘要，`index.tsx:2350-2357`）；输出默认隐藏，可全局开 `session.toggle.generic_tool_output` 后变 3 行折叠 BlockTool（`index.tsx:1714-1750`） |

折叠策略统一走 `util/collapse-tool-output.ts`：`maxLines` + `maxChars` 双重截断加 `…`。另有全局 `showDetails` 开关：关掉后**已完成的 tool 整体隐藏**，只看对话文本（`index.tsx:1627-1631`）。

### 2.4 流式文本与 markdown

- 文本 part 直接交给 OpenTUI 内建 `<markdown streaming={true} conceal syntaxStyle tableOptions={{style:"grid"}}>`（`index.tsx:1599-1617`），渲染器自己处理增量 reparse；代码块用 tree-sitter wasm 高亮（解析器清单在 `packages/opencode/parsers-config.ts`，md/js/ts 内建）。`conceal` 是可切换的"代码修饰隐藏"。
- thinking/reasoning part（`index.tsx:1507-1597`）：默认折叠成**单行** `+ Thinking: 首行摘要 · 时长`（布局永不跳动），点击展开为 muted 色 markdown；`time.end` 到达时标题翻成 `Thought`。
- 流式更新机制：SSE 事件进入 `context/sdk.tsx:46-72` 的**16ms 批量队列**（`batch()` 合并 emit，避免每 token 一次渲染），再写入 `context/sync.tsx` 的 solid store（`reconcile`/binary-search 增量更新），组件细粒度响应。

---

## 3. 快捷键与 leader-key 系统

### 3.1 三层结构

1. **Definitions 表**（`config/keybind.ts:47-232`）：约 150 个键位，每个 = `keybind(默认值, 描述)`，一处声明同时生成：用户配置 schema（`KeybindOverrides`，`keybind.ts:237-244`）、help/which-key 描述、默认绑定。
2. **CommandMap**（`keybind.ts:248-405`）：配置键名（`session_new`）→ 运行时命令名（`session.new`）的映射，配置面与命令面解耦。
3. **命令注册**（`keymap.tsx` + `@opentui/keymap`）：组件通过 `useBindings(() => ({mode, commands, bindings}))` 声明式注册命令（带 `title/category/slashName/slashAliases/hidden/suggested/enabled`）和绑定，组件卸载自动注销。命令同时是：快捷键目标、command palette 条目、slash 命令（`useCommandSlashes`，`keymap.tsx:243-273`）——**一份数据三个入口**。

### 3.2 键表（默认值精选，全表见 `config/keybind.ts:47-232`）

| 类别 | 键 | 功能 |
|---|---|---|
| leader | `ctrl+x`（可改） | leader 前缀，超时 2000ms（可配 `leader_timeout`） |
| 全局 | `ctrl+c,ctrl+d,<leader>q` 退出；`ctrl+p` command palette；`ctrl+z` suspend | |
| session | `<leader>n` 新建；`<leader>l` 列表；`<leader>1..9` 快速槽位切换；`<leader>g` timeline；`<leader>c` compact；`<leader>x` 导出；`<leader>y` 复制；`<leader>u/r` undo/redo；`escape` 中断 | |
| 模型/agent | `<leader>m` 模型列表；`f2/shift+f2` 最近模型轮换；`tab/shift+tab` agent 轮换；`ctrl+t` variant 轮换；`<leader>a` agent 列表 | |
| 视图 | `<leader>b` sidebar；`<leader>t` 主题；`<leader>s` status；`<leader>h` conceal；`<leader>e` 外部编辑器 | |
| 消息滚动 | `pageup/pagedown`、`ctrl+alt+u/d` 半页、`ctrl+g/home` 顶、`ctrl+alt+g/end` 底 | |
| 输入框 | 完整 emacs 风格（`ctrl+a/e/k/u/w`、`alt+f/b`、undo/redo、select 系列），约 45 条（`keybind.ts:156-195`） | |
| dialog | `up/ctrl+p`、`down/ctrl+n`、`return`、`escape`；MCP 开关 `space` | |
| diff viewer | `tab/n/p/b/s/d/v/E/?` 一套独立键域（`keybind.ts:62-74`） | |
| which-key | `ctrl+alt+k` 开关面板，`ctrl+alt+←/→` 切组（`keybind.ts:221-231`） | |

绑定值语法很灵活：逗号多绑定 `"ctrl+c,ctrl+d"`、leader 模板 `"<leader>q"`、对象形式 `{key, preventDefault, fallthrough}`、`"none"/false` 禁用（`keybind.ts:9-34`）；输入别名归一化 `enter→return, esc→escape`（`keymap.tsx:100-114`）。

### 3.3 用户可配置

可以。`~/.config/opencode/tui.json`（`config/tui-schema.ts:64-78`）：`keybinds`（全部 150 项可覆写）、`leader_timeout`、`theme`、`mouse`、`scroll_speed/scroll_acceleration`、`diff_style`、`attention`（声音/通知）、`plugin`。未知键名直接报错（`keybind.ts:434-443`）。

### 3.4 mode 栈与 which-key

- keymap 有 **mode 栈**（`keymap.tsx:41-98`）：base → 打开 dialog 时 push `"modal"`（`ui/dialog.tsx:78-82`）、question 时 push `"question"`，绑定按 mode 过滤，自动避免弹层下的全局键泄漏。
- **which-key 面板**（`feature-plugins/system/which-key.tsx`，608 行）：`ctrl+alt+k` 常驻显示当前 mode 下所有可达绑定，按 category 分组成 tabs，多列自适应；支持 dock（挤压布局）/overlay（浮层）两种形态；**leader 按下后 pending 序列自动弹出预览**（`pendingAutoVisible`，which-key.tsx:196-198）。
- help dialog 本身极简（`ui/dialog-help.tsx`，40 行）：只提示"按 ctrl+p 看所有命令"——**发现性职责全部交给 command palette + which-key**。
- UI 中所有键位提示都是活的：`useCommandShortcut("session.redo")`（`keymap.tsx:229-237`）反查当前实际绑定并格式化显示，改键后提示同步变。

---

## 4. dialog / overlay 系统

### 4.1 通用 Dialog 框架（`ui/dialog.tsx`）

- `DialogProvider` 持有一个 `stack`（实际上 `replace()` 永远先清空再放一个——**浅栈**，避免弹层套娃），`clear()`、`setSize("medium"|"large"|"xlarge")`（宽 60/88/116，`dialog.tsx:22-26`）。
- 渲染：全屏遮罩 `RGBA(0,0,0,150)` + 顶部 1/4 处居中面板，zIndex 3000；**点遮罩关闭、点面板 stopPropagation**（`dialog.tsx:28-63`），有"拖选文本不算点击关闭"的处理。
- 打开时记住当前 focus 元素，关闭后 `refocus()` 还原（`dialog.tsx:84-100`）；esc/ctrl+c 关闭。
- mode 栈 push `"modal"` 使底层键位失效。

### 4.2 DialogSelect：所有列表弹层的统一基座（`ui/dialog-select.tsx`，559 行）

特性：标题 + 过滤输入框 + 分组列表 + 底部 action 栏。

- fuzzysort 模糊过滤，title 权重 2 倍于 category（`dialog-select.tsx:126-134`）；
- category 分组表头、`flat` 模式（过滤时打平显示并把 category 移到行尾 footer）；
- 当前值 `current` 显示 `●` 标记并初始定位居中；
- 鼠标/键盘双输入消歧：过滤导致布局移动时合成 mousemove 不会抢走键盘 selection（`dialog-select.tsx:137-143`）；
- **actions 机制**：传 `{command, title, onTrigger}`，自动从 keymap 反查键位渲染成底部 `delete ctrl+d` 提示，按键即对选中项执行（`dialog-select.tsx:235-330`）；
- 高度上限 = 半屏。

### 4.3 各弹层

全部建立在 DialogSelect 上，文件即清单（`component/dialog-*.tsx`）：

- **session 切换**（`dialog-session-list.tsx`）：服务端搜索（150ms debounce 的 createResource）、Pinned 分组、按日期分组（Today/日期）、运行中 session 行首 spinner、快速槽位数字 gutter、`ctrl+d` **二次按键确认删除**（第一次按行变红显示 "Press ctrl+d again to confirm"，`dialog-session-list.tsx:183-199, 244-291`）、`ctrl+r` 改名、`ctrl+f` pin。
- **model 选择**（`dialog-model.tsx`）：Favorites / Recent / 按 provider 分组三段；Free 徽章；`ctrl+f` 收藏、`ctrl+a` 跳 provider 连接；选完若模型有 variants 自动接 DialogVariant 弹层（`dialog-model.tsx:133-146`）。
- **theme 选择**（`dialog-theme-list.tsx`）：`onMove` 实时预览主题。
- agent / mcp（space 开关）/ skill / status / provider connect / org switch / variant / export options / rename（输入型）等。
- **command palette**（`component/command-palette.tsx`）：keymap 注册表里 `namespace: "palette"` 且可达的命令全集，无过滤时先显示 `suggested` 段（命令可声明 `suggested: boolean | () => boolean`）。
- **promise 风格的简单弹层**：`DialogConfirm.show(dialog, title, msg) → Promise<boolean|"skip">`、`DialogAlert.show(...)`，用于更新提示等流程化场景（`app.tsx:893-931`）。
- timeline / fork-from-timeline / message 弹层（session 内跳转、fork、编辑历史消息）。

注意：**permission 和 question 不走 dialog**，它们占据输入框位置（理由可推断：弹层可被 esc 误关，而 permission 必须显式选择；同时保持消息流可滚动可见）。

---

## 5. permission / 确认机制（含跨进程协议）

### 5.1 服务端（`src/permission/index.ts`）

请求-阻塞-应答模型，与 OpenProgram 的 Python 后端可一一对应：

1. 工具执行中调用 `ctx.ask({permission, patterns, always, metadata})`（如 edit 工具 `src/tool/edit.ts:98-106`，metadata 里直接带 `diff`，**UI 渲染所需数据在 ask 时一次性给齐**）。
2. `Permission.ask`（`permission/index.ts:171-211`）先对 ruleset 求值（配置规则 + 已批准规则，wildcard 匹配，`allow/deny/ask` 三态）：deny → 直接抛 `DeniedError`（附相关规则给模型看）；全 allow → 直接放行；否则创建 `Deferred`，登记进 pending map，**publish bus 事件 `permission.asked`（完整 Request：id/sessionID/permission/patterns/metadata/always/tool{messageID,callID}）**，然后 `await deferred`——工具协程就地挂起。
3. 客户端通过 REST `POST /permission/:requestID/reply {reply: "once"|"always"|"reject", message?}`（`src/server/routes/instance/httpapi/groups/permission.ts:31-43`；另有 `GET /permission` 列出 pending 用于重连恢复）。
4. `reply`（`permission/index.ts:213-269`）：
   - `reject`：fail deferred 为 `RejectedError`，或带 message 时为 `CorrectedError(feedback)`——**错误 message 直接成为返回给模型的工具错误文本**（"The user rejected permission ... with the following feedback: ..."，`permission/index.ts:87-93`）；并且**级联拒绝同 session 的其余 pending**。
   - `always`：把 `request.always` 里的 pattern 写入项目级 approved 规则（SQLite `PermissionTable` 持久化），并**自动放行其余 pending 中已被新规则覆盖的请求**。
   - `once`：只 resolve 这一个。
5. `permission.replied` 事件广播，所有客户端同步移除。

### 5.2 传输层

bus 事件 → 全局事件流（SSE 或 worker RPC）→ TUI `context/sdk.tsx`（16ms 批量）→ `context/sync.tsx:138-173` 按 sessionID 维护 `permission[sessionID]: Request[]` 有序数组（binary search 插入/删除，幂等 reconcile）。**UI 不存在"等待回复"的本地状态——pending 列表本身就是状态**，TUI 重启/attach 后用 `GET /permission` 即可恢复。

### 5.3 TUI 呈现（`routes/session/permission.tsx`）

- session 路由读取 `permissions()`（含子 session 聚合，`index.tsx:195-198`），有 pending 时第一条渲染 `PermissionPrompt`，**替换输入框位置**，一次只处理一个（队列依次弹出）；footer 同时显示 `△ N Permissions` 计数（`routes/session/footer.tsx:63-68`）。
- 主面板（`permission.tsx:194-441`）：warning 色左竖线块，头部 `△ Permission required` + 工具专属标题行（icon + 摘要），body 按 permission 类型定制——`edit` 是**可滚动的完整 diff**（`permission.tsx:31-91`），shell 是 `$ command`，task/webfetch/websearch/glob/grep/read/external_directory 各有摘要，未知工具 fallback `Call tool X`。
- 选项三个按钮：**`Allow once` / `Allow always` / `Reject`**；`left/right/h/l` 移动、enter 确认、**esc = reject**、鼠标可点；`ctrl+f` 全屏展开（Portal 到近全屏看大 diff，`permission.tsx:629-718`）。
- 选 `always` 进入**二段确认**：明确列出将被永久放行的 pattern 列表（"This will allow the following patterns until OpenCode is restarted"，`permission.tsx:141-178`）。
- 选 `reject` 且是子 agent session 时进入 **RejectPrompt**：textarea 让用户写"该怎么做"，作为 `message` 提交 → 模型收到 CorrectedError 反馈（`permission.tsx:444-523`）。
- 对应的工具行在消息流里同步变 warning 色（等待中）或删除线（被拒）。

### 5.4 Question 机制（伴生设计，`routes/session/question.tsx`）

模型主动提问（question 工具）走同样的 ask/reply 事件模式，UI 是多问题 tab 向导：单选直接提交、多选 + confirm tab、每题支持 custom 自由文本，reject 可整体拒绝。与 permission 共用"替换输入框"的位置和服务端 Deferred 模式。

---

## 6. 状态反馈

### 6.1 输入框自带状态行（核心状态栏，`component/prompt/index.tsx`）

输入框上沿一行：`Agent名 · 模型名 (provider) · variant`（agent 名带 agent 色，shell 模式显示 "Shell"，`prompt/index.tsx:1560-1596`）。

输入框下沿一行（`prompt/index.tsx:1625-1779`）：

- **busy**：agent 色的动画块 spinner（40ms 帧，`createFrames({style:"blocks"})`，颜色取自当前 agent，`prompt/index.tsx:1444-1466`）+ 右侧 `esc interrupt`，**按一次 esc 变 "esc again to interrupt"，1.5s 内再按才真正中断**（双击防误触，`prompt/index.tsx:358-489`）。
- **retry**：错误摘要（>80 字截断，可点击弹全文）+ `[retrying in 3s attempt #2]` 实时倒计时（`prompt/index.tsx:1641-1696`）。
- **idle**：右侧 `tokens数 (上下文百分比) · $成本`——token 取最后一条 assistant 消息的 input+output+reasoning+cache，百分比按模型 context limit 算，cost 是 session 累计（`prompt/index.tsx:334-352`）；无用量时显示 `tab agents` / `ctrl+p commands` 快捷键提示。
- 状态源是服务端推的 `session_status[sessionID]: idle|busy|retry`（`prompt/index.tsx:150`），不是本地猜测。

### 6.2 sidebar 与 footer

- sidebar Context 块：`N tokens / N% used / $X.XX spent`（`feature-plugins/sidebar/context.tsx:37-46`）。
- sidebar LSP：每个 server 一行 `• id root`，绿/红状态点，>2 个可折叠（`feature-plugins/sidebar/lsp.tsx`）；MCP 同理（connected 计数 + failed 标红）。
- Home footer：`目录:git分支 + ⊙ N MCP (/status) + 版本号`（`feature-plugins/home/footer.tsx`）。
- session footer（子 agent 视图用）：目录、`△ N Permissions`、`• N LSP`、`⊙ N MCP`、`/status` 提示（`routes/session/footer.tsx:52-89`）。
- 详细状态集中在 `/status` 弹层（DialogStatus）而非常驻——状态栏只放计数和红绿灯。

### 6.3 其他反馈通道

- **Toast**（`ui/toast.tsx`）：右上角浮层，variant 色（info/success/warning/error）左右边线，单条替换式，自动消失；`toast.error(err)` 快捷方法。
- **attention 系统**（`attention.ts` + `config/tui-schema.ts:36-62`）：permission/question/done/error/subagent_done 各自可配提示音 + 桌面通知 + 音量 + 音效包。
- **终端标题**随 session 标题更新 `OC | <title>`（`app.tsx:352-375`），可开关。
- spinner 全局尊重 `animations_enabled` KV，关动画时退化为静态 `⋯`（`component/spinner.tsx:15`）。

---

## 7. 值得抄的 Top 10（按对 OpenProgram 的适用性排序）

OpenProgram 现状：React/Ink TUI（`cli/src/`，已有 ModalProvider/Picker/BottomBar/PromptInput）+ Python 后端 + SSE/WS。排序原则：协议/数据模型类（与渲染器无关，可直接平移）优先，重渲染器能力的靠后。

1. **Permission 的 ask→Deferred→reply 协议 + once/always/reject 三选 + 拒绝带反馈**（`permission/index.ts:171-269`）。Python 侧即 `asyncio.Future` + pending dict + `permission.asked/replied` 事件 + REST reply 端点；`always` 落盘 pattern 规则并级联放行 pending；reject 的 message 直接变成给模型的工具错误文本（CorrectedError）。`GET /permission` 列表使 UI 重连即恢复。这是整套调研里**含金量最高且与渲染器完全无关**的设计。

2. **命令注册表三位一体**（`keymap.tsx:243-273`、`app.tsx:458-823`）：每个命令一份声明（name/title/category/keybind/slashName/aliases/hidden/suggested/enabled），同时驱动快捷键、command palette、slash 命令、UI 内活键位提示（`useCommandShortcut`）。OpenProgram 的 pickers/slash 目前是分散注册的，统一成数据表后 palette 和 help 都是免费的。

3. **Keybind Definitions 表 + CommandMap 解耦 + 全量可配置**（`config/keybind.ts`）：默认值与描述一处声明，自动生成用户配置 schema，未知键报错，`"none"` 禁用，`<leader>` 模板。与 MEMORY 里 CLI/TUI redesign 的 schema-driven settings 方向完全吻合，可作为 keybinds 部分的直接模板。

4. **InlineTool/BlockTool 两级工具渲染 + per-tool 定制 + 折叠策略**（`index.tsx:1752-1889, 2350-2357`）：未知工具自动降级为 `icon tool [k=v,...]` 单行摘要；shell 10 行折叠、generic 3 行折叠（`collapseToolOutput`）；`showDetails` 一键隐藏全部已完成工具；被拒工具删除线、等 permission 工具变色。Ink 下没有鼠标点击，折叠展开改键驱动即可，其余照搬。

5. **permission/question 占据输入框位置而非弹窗**（`routes/session/index.tsx:1232-1263`）：输入区是一个三态槽（Prompt | PermissionPrompt | QuestionPrompt），消息流保持可见，esc 语义明确（=reject）。Ink 实现零障碍，比 modal 方案更稳。

6. **session_status 驱动的状态行**（`prompt/index.tsx:1625-1779`）：busy/retry/idle 由服务端事件直接给（含 retry 的 next 时间戳和 attempt 数，UI 只做倒计时），token/context%/cost 常驻右下，**esc 双击中断**。OpenProgram 已有 BottomBar，主要是把状态来源改为服务端显式 status 事件。

7. **SSE 16ms 批量 flush + 有序增量 store**（`context/sdk.tsx:46-72`、`context/sync.tsx:133-210`）：事件队列按帧合并后一次性 batch 更新，store 按 id binary-search 插入/reconcile 保证幂等。Ink/React 下用 `unstable_batchedUpdates`/单次 setState 同理——这是流式 token 下 TUI 不卡的关键工程点。

8. **DialogSelect 通用选择器的交互细节**（`ui/dialog-select.tsx`、`dialog-session-list.tsx:183-199`）：fuzzy 权重过滤、分组/打平双模式、`current` ● 标记并居中、actions 自动渲染键位提示、**危险操作行内二次确认（行变红 "press again to confirm"）而不是再弹一层**。OpenProgram 已有 Picker，可按这份清单补齐。

9. **leader key + which-key 自动预览**（`config/keybind.ts:43-48`、`feature-plugins/system/which-key.tsx`）：leader 解决 Ink 下快捷键空间不足的问题（终端可用组合键远少于 GUI）；leader 按下后 pending 面板自动弹出所有后续键，发现性由系统保证而不是文档。实现量中等，价值高。

10. **sidebar 响应式 + 状态红绿灯分层**（`routes/session/index.tsx:229-237, 1268-1287`、`feature-plugins/sidebar/*`）：>120 列固定右栏（tokens/cost/todo/LSP/MCP/改动文件），窄屏 overlay；状态栏只放计数+颜色点，详情进 `/status` 弹层。配套可抄 `contentWidth` 单点计算下发的模式，所有折叠/换行阈值共享一个宽度真相。

**一个反向结论**：opencode 的 markdown 流式渲染、diff split 视图、鼠标支持、sticky scrollbox 都来自 OpenTUI 渲染器内建能力，Ink 没有对等物。若 OpenProgram 想达到同级的 diff/markdown 体验，选项是（a）在 Ink 内自写 ANSI 渲染（成本高），或（b）评估直接采用 `@opentui/solid`/`@opentui/react`（OpenTUI 有 react 适配）替换 Ink——上面 1-9 条设计与该决策正交，可先行落地。