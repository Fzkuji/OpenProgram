# Web UI

Web UI 界面 — 界面系统、指示点、附件处理、聊天轮次视觉，以及 GUI-agent 上下文流转。

- [`invariants.md`](invariants.md) — 跨模块 UI 不变量清单（改相关模块前先过一遍）
- [`chat-turn-visual-spec.html`](chat-turn-visual-spec.html) — 聊天轮次视觉规范（执行时间线 + 手动函数运行 + 消息导航；可交互示例，唯一权威）
- [`interaction-feedback.md`](interaction-feedback.md) — 交互反馈 0ms 规则（乐观状态先行，数据后补）
- [`state-layer.md`](state-layer.md) — Web 状态层：每个会话一个 store 实例，真正共享的数据留全局
- [`built-in-browser.html`](built-in-browser.html) — 内置浏览器主页、浏览器 profile 导入、紧凑 History 与四入口新建 pane
- [`composer-interaction-modes.md`](composer-interaction-modes.md) — Composer 交互模式
- [`attachment-handling.md`](attachment-handling.md) — 附件处理：附件怎么进模型上下文（[已渲染](attachment-handling.html)）
- [`chat-attachments.html`](chat-attachments.html) — 聊天附件双向流转：聊天流里显示成什么、agent 怎么把文件交回来、可读文件怎么点开
- [`gui-agent-context.md`](gui-agent-context.md) — GUI agent 上下文流转
- [`indicator-dots.md`](indicator-dots.md) — 指示点
- [`surface-system.md`](surface-system.md) — Surface 系统
- [`web-styles.md`](web-styles.md) — Web 样式组织（一个组件一个文件，目录对齐组件树）
