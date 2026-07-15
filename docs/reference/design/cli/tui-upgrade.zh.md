# TUI 升级：会话记录渲染与交互

状态：proposed（2026-06）—— 调研完成，尚未开始实现。
配套文档：[user-input-requests.md](../runtime/operations/user-input-requests.md)（运行中途的提问；其 TUI 呈现层落在本文档）。

## 目标

把 Ink TUI 的会话记录显示与交互提升到 Claude Code 和 opencode 的水平：工具调用折叠但不丢失信息、ctrl+o 展开的会话记录视图、结构化的 diff 渲染、忙碌时的消息排队，以及一套可被发现的键位绑定系统。

## 当前状态（审计摘要）

底座是扎实的：内置的 hermes-ink 单元格网格渲染器，带鼠标追踪 + ScrollBox，4 套主题可实时预览，功能丰富的 BottomBar（tokens/context%/cache/permission mode），命令面板（ctrl+k），fish 风格自动建议，@file 补全，按会话保存的草稿，以及完整的 account/channel 流程。缺失的部分集中在会话记录本身：

- 工具输出被硬性折叠在 6 行（`Turn.tsx` MAX_LINES），且**完全没有展开手段**——没有键位绑定，没有 verbose 模式。
- 工具参数只有一行截断显示；没有按工具区分的渲染（每个工具看起来都一样）。
- 任何地方都没有 diff 渲染；`/diff` 直接倾倒原始的 `git diff` 文本。
- 流式文本显示的是原始 markdown 源码，等最终渲染落地时画面会突然跳动（`Turn.tsx:123`）；`renderMarkdown` 在全量重绘的渲染器下未做 memoize。
- `follow_up_question` / `approval_request` 信封在 `ws/client.ts` 中已有类型定义，但**被静默丢弃**——agent 的提问会超时，`ask` permission mode 无法触达（shift+tab 只在 bypass↔auto 之间循环）。
- `/resume` 仅从 role+content 重建会话记录——工具历史丢失（`useWsEvents.ts:326-336`）。
- 工具结果是**按工具名**匹配到调用的（服务端流事件不带 call id）——并发的同名调用会被错误归属。
- 忙碌 = 输入被锁定（`submitText` 直接 return）；没有消息排队。
- `ui/` 套件（ModalProvider/Confirm/Form/MultiSelect/Toast）已构建好，但只被 `--demo` 界面使用；REPL 仍在跑一个 24 状态的 `pickerKind` 枚举。

## 我们借鉴什么，从哪里借鉴

来自 **Claude Code**（信息密度——文件指针见 references 文档，指向 `references/claude-code-leaked/src`）：

1. **工具渲染器接口。** 每个工具获得一组渲染钩子（use-line / progress / result / error），共用同一个外壳：状态圆点（`⏺` 排队-暗 / 运行-闪烁 / 完成-绿 / 错误-红）+ 加粗的工具名 + 括号内的参数摘要，结果缩进在 `⎿` 边槽之下。两个字形承载了全部工具状态；没有方框。
2. **"3 行 + `… +N lines (ctrl+o to expand)`" 截断**，并采纳其两处精细处理：如果只隐藏了 1 行，就直接显示它；对超大输出先按字符数预截断，再估算剩余行数。
3. **量化的单行摘要**，每个工具一条，而非省略号截断：`Read 52 lines`、`Added 5 lines, removed 2 lines` + diff、`Found 8 files`，子运行用 `Done (12 calls · 48k tokens · 2m 10s)`。
4. **ctrl+o = 冻结快照的会话记录界面**（一个独立的 Screen 状态，而非原地展开）：冻结消息列表，把所有内容重新渲染为展开形态，页脚带退出提示；在其中 ctrl+e = 全部显示（完全不截断）。
5. **复合 spinner 行**：`✻ verb… (esc to interrupt · 42s · ↓ 3.2k tokens)`，带渐进式宽度门控——spinner 和 token 统计我们已经有了，这一步是把它们合并。
6. **忙碌时排队的消息**：运行期间打字会把消息加入队列（暗色，显示在输入框上方），↑ 可调回编辑，队列在 turn 之间清空。

来自 **opencode**（交互架构——指针见 references 文档，指向 `references/opencode/packages/opencode/src`）：

7. **命令注册表作为唯一事实来源**：每条命令一份声明（name/title/category/keybind/slash-name/enabled），驱动键位绑定、ctrl+k 面板、斜杠命令，以及页脚里的实时按键提示。修复现有的注册表/处理器漂移（`/branch` 等已实现但未列出；`/memory` 等已列出但是空壳）。
8. **键位定义表 + 用户覆盖**：默认值 + 描述只声明一次，由此生成配置 schema（契合现有的 schema 驱动设置设计）；未知按键报错；`"none"` 表示禁用。
9. **问题/权限提示替换输入框**（一个三选一的槽位：Prompt | QuestionPrompt | ApprovalPrompt），而非弹出模态框——会话记录保持可见且可滚动，esc 的语义保持清晰。这就是 user-input-requests.md 的 TUI 呈现层的落地点。
10. **行内危险确认**（再按一次确认，该行变红），用于具破坏性的 picker 操作，取代嵌套的确认层。

明确**不**采纳：更换渲染器（OpenTUI）。我们内置的 hermes-ink 已经有鼠标追踪和 ScrollBox；markdown 通过 marked-terminal 落地；diff 我们自己写。更换渲染器不在本次范围内。

## 阶段

### P0 —— 会话记录密度（纯 TUI，无服务端改动）

- 工具渲染外壳：`⏺` 状态圆点 + 工具名 + 参数摘要，`⎿` 结果边槽；为 bash/read/write/edit/grep 提供按工具的渲染器 + 通用兜底（`tool [k=v, …]`）。
- 3 行截断，配 `… +N lines (ctrl+o to expand)` + 上述 1 行与超大输出的精细处理。
- ctrl+o 会话记录界面（冻结快照、全部展开、q/esc 退出、ctrl+e 全部显示；复用 TranscriptViewport 的滚动）。
- Diff 组件（行号、增删着色、3 行上下文）；供 edit 风格的工具结果和 `/diff` 使用。
- 按 turn memoize `renderMarkdown`（开销小，在全量重绘下收益大）。

验收：一次混合工具的运行读起来是一条条两行的条目；ctrl+o 显示全部内容；一次 edit 显示带颜色的 diff；冗长的 bash 输出折叠并给出准确的 +N 计数。

### P1 —— 交互

- 忙碌时排队的消息 + ↑ 编辑队列。
- 复合 spinner/状态行（verb · esc 提示 · 已耗时 · ↓ tokens）。
- 命令注册表统一（面板/斜杠/按键来自同一张表），以及带 `~/.openprogram` 覆盖的键位定义表；`?` 快捷键帮助由同一张表生成。

### P2 —— 修复与收敛（需要少量服务端改动）

- 服务端：在工具流事件中带上 `call_id`；TUI 按 id 匹配结果（修复并发同名的错误归属）。
- 服务端：`conversation_loaded` 携带工具块；`/resume` 恢复工具历史。
- 输入槽位里的问题/批准提示——实现 user-input-requests.md 的 TUI 侧（处理 `follow_up_question` 和 `approval_request`，让 `ask` permission mode 可触达）。
- 把 REPL 的 picker 从 `pickerKind` 枚举迁移到 ModalProvider/Form 套件（机械改动；按 picker 逐个择机进行）。

## 风险

- 单元格网格渲染器每帧重绘所有内容；P0 增加了更重的按 turn 渲染——memoization（markdown、diff）是 P0 的一部分，而非事后补救。
- ctrl+o 作为全局按键不得与终端流控冲突（它不会；冲突的是 ctrl+s/ctrl+q）。
- P2 的服务端改动会触及 `_event_parsing.py` / dispatcher 的事件发射——需与进行中的 event_bus 工作协调。
