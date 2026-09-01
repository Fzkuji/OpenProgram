# TUI 会话记录渲染与交互

> 本文是 Ink TUI 会话记录显示与交互的设计：工具调用如何渲染、输出如何折叠与
> 展开、命令与键位如何声明、运行中途的提问如何呈现。配套文档：
> [user-input-requests.md](../runtime/operations/user-input-requests.md)
> （运行中途的提问；其 TUI 呈现层在本文中规定）。

## 1. 目标

会话记录应做到：工具调用折叠但不丢失信息、提供 ctrl+o 的展开视图、结构化
渲染 diff、agent 忙碌时排队消息，以及给出用户能发现并覆盖的键位绑定。

## 2. 起点

TUI 底座提供了内置的 hermes-ink 单元格网格渲染器（带鼠标追踪与 ScrollBox）、
4 套可实时预览的主题、显示 tokens / context% / cache / permission mode 的
BottomBar、命令面板（ctrl+k）、fish 风格自动建议、`@file` 补全、按会话保存
的草稿，以及 account 与 channel 流程。本设计要补的缺口集中在会话记录本身：

- 工具输出硬性折叠在 6 行（`Turn.tsx` MAX_LINES），没有任何展开手段——没有
  键位绑定，没有 verbose 模式。
- 工具参数只有一行截断显示；没有按工具区分的渲染，每个工具看起来都一样。
- 没有 diff 渲染；`/diff` 直接输出原始的 `git diff` 文本。
- 流式文本显示原始 markdown 源码，等最终渲染完成时画面会跳动
  （`Turn.tsx:123`）。已提交文本在全量重绘的渲染器下按挂载的文本段和终端
  列宽进行 memoize。
- `follow_up_question` / `approval_request` 信封在 `ws/client.ts` 中已有类型
  定义，但被无声丢弃，导致 agent 的提问超时、`ask` permission mode 无法触达
  （shift+tab 只在 bypass↔auto 之间循环）。
- `/resume` 仅从 role + content 重建会话记录，工具历史丢失
  （`useWsEvents.ts:326-336`）。
- 工具结果按工具名匹配到调用（服务端流事件不带 call id），并发的同名调用会被
  错误归属。
- 忙碌即输入被锁定（`submitText` 直接返回）；没有消息排队。
- `ui/` 套件（ModalProvider / Confirm / Form / MultiSelect / Toast）已构建，
  但只被 `--demo` 界面使用；REPL 仍在跑一个 24 状态的 `pickerKind` 枚举。

## 3. 会话记录渲染

信息密度沿用 Claude Code 的做法（指向
`references/claude-code-leaked/src` 的文件指针见 references 文档）。

1. **工具渲染器接口。** 每个工具获得一组渲染钩子（use-line / progress /
   result / error），共用同一个外壳：状态圆点（`⏺` 排队时暗、运行时闪烁、
   完成为绿、错误为红）、加粗的工具名、括号内的参数摘要，结果缩进在 `⎿`
   边槽之下。两个字形承载全部工具状态；没有方框。
2. **3 行截断**，配 `… +N lines (ctrl+o to expand)`，另加两处细化：只隐藏
   1 行时就直接显示它；超大输出先按字符数预截断，再估算剩余行数。
3. **量化的单行摘要**，每个工具一条，而非省略号截断：`Read 52 lines`、
   `Added 5 lines, removed 2 lines` 后接 diff、`Found 8 files`，子运行用
   `Done (12 calls · 48k tokens · 2m 10s)`。
4. **ctrl+o 打开冻结快照的会话记录界面**——一个独立的 Screen 状态，而非原地
   展开。它冻结消息列表，把所有内容重新渲染为展开形态，页脚给出退出提示；
   在其中按 ctrl+e 则完全不截断地显示全部内容。
5. **复合 spinner 行**：`✻ verb… (esc to interrupt · 42s · ↓ 3.2k tokens)`，
   带渐进式宽度门控。spinner 与 token 统计已各自存在，这一步是把它们合并。
6. **忙碌时排队的消息**：运行期间打字会把消息加入队列（暗色，显示在输入框
   上方），↑ 可调回编辑，队列在 turn 之间清空。

diff 渲染自己写：行号、增删着色、3 行上下文，供 edit 风格的工具结果和
`/diff` 使用。markdown 通过 marked-terminal 渲染并按 turn memoize——在每帧
全量重绘的渲染器下这一点很关键。

渲染器本身不更换。内置的 hermes-ink 已经有鼠标追踪和 ScrollBox，换成
OpenTUI 在这里没有收益。

## 4. 命令与交互架构

交互结构沿用 opencode（指向
`references/opencode/packages/opencode/src` 的指针见 references 文档）。

7. **一份命令注册表作为唯一事实来源。** 每条命令只声明一次（name / title /
   category / keybind / slash-name / enabled），这份声明驱动键位绑定、ctrl+k
   面板、斜杠命令，以及页脚里的实时按键提示。单一张表正是让注册表与处理器
   不再彼此漂移的东西——`/branch` 已实现但未列出、`/memory` 已列出但是空壳。
8. **键位定义表 + 用户覆盖。** 默认值与描述只声明一次，并据此生成配置
   schema，契合现有的 schema 驱动设置设计。未知按键报错；`"none"` 表示禁用。
9. **问题与权限提示替换输入框**，通过一个三选一的槽位（Prompt |
   QuestionPrompt | ApprovalPrompt），而非弹出模态框。会话记录保持可见且可
   滚动，esc 保持单一明确的语义。这就是 user-input-requests.md 的 TUI 呈现
   层，处理 `follow_up_question` 和 `approval_request`，并让 `ask`
   permission mode 可触达。
10. **行内危险确认**，用于具破坏性的 picker 操作：再按一次即确认，该行变红，
    取代嵌套的确认层。

REPL 的 picker 从 `pickerKind` 枚举迁移到 ModalProvider/Form 套件；该改动是
机械的，可以按 picker 逐个进行。

## 5. 服务端支持

设计中有两处需要服务端目前未发送的数据：

- 工具流事件带上 `call_id`，TUI 便按 id 而非按名字匹配结果，并发的同名调用
  归属正确。
- `conversation_loaded` 携带工具块，`/resume` 得以恢复工具历史。

两者都会触及 `_event_parsing.py` 与 dispatcher 的事件发射，与事件总线的
工作共享同一片代码。

## 6. 约束

- 单元格网格渲染器每帧重绘全部内容，因此上述更重的按 turn 渲染依赖于对
  markdown 与 diff 输出的 memoize。
- ctrl+o 作为全局按键是安全的：终端流控用的是 ctrl+s 与 ctrl+q。

## 附录：实现状态

调研已完成，实现正在进行。Markdown 输出按每个已挂载文本段的 streaming
状态、文本和终端列宽进行 memoize，因此另一个流式 turn 更新时，不会再次解析
未变化的已提交文本；终端尺寸变化时，依赖列宽的 Markdown 仍会重新渲染。其余
工作按以下顺序进行：

- **P0 —— 会话记录密度**（纯 TUI，无服务端改动）：工具渲染外壳、3 行截断、
  ctrl+o 会话记录界面和 diff 组件仍待实现。验收标准是：
  一次混合工具的运行读起来是一条条两行的条目，ctrl+o 显示全部内容，一次
  edit 显示带颜色的 diff，冗长的 bash 输出折叠并给出准确的 +N 计数。
- **P1 —— 交互**：忙碌时排队的消息与 ↑ 编辑、复合 spinner/状态行、命令注册
  表统一、带 `~/.openprogram` 覆盖的键位定义表，以及由同一张表生成的 `?`
  快捷键帮助。
- **P2 —— 依赖服务端的部分**：工具流事件中的 `call_id`、
  `conversation_loaded` 中的工具块、输入槽位里的问题/批准提示，以及 REPL
  picker 的迁移。
