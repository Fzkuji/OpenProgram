# Web UI

Web UI 界面 — 界面系统、指示点、附件处理、聊天轮次视觉，以及 GUI-agent 上下文流转。

- [`invariants.md`](invariants.md) — 跨模块 UI 不变量清单（改相关模块前先过一遍）
- [`chat-turn-visual-spec.html`](chat-turn-visual-spec.html) — 聊天轮次视觉规范（执行时间线 + 手动函数运行 + 消息导航；可交互示例，唯一权威）
- [`interaction-feedback.md`](interaction-feedback.md) — 交互反馈 0ms 规则（乐观状态先行，数据后补）
- [`state-layer.md`](state-layer.md) — Web 状态层：每个会话一个 store 实例，真正共享的数据留全局
- [`center-tabs-and-split-layout.html`](center-tabs-and-split-layout.html) — 普通 tab 与复合分屏 tab 的生命周期、显示、持久化和跨窗口转移权威设计
- [`built-in-browser.html`](built-in-browser.html) — 内置浏览器主页、浏览器 profile 导入、紧凑 History 与四入口新建 pane
- [`browser-extensions.html`](browser-extensions.html) — 不支持 Chrome/Edge 扩展安装与管理的产品决策、保留的浏览器能力，以及旧扩展数据不执行也不自动删除的边界
- [`integrated-terminal.html`](integrated-terminal.html) — 真实 PTY 终端与本机 Claude Code 直接启动入口
- [`composer-local-attachment-paths.html`](composer-local-attachment-paths.html) — Composer 到模型上下文的本地附件路径保留规则
- [`composer-responsive-controls.html`](composer-responsive-controls.html) — Composer 响应式控件及紧凑状态交互契约
- [`composer-tool-profile-menu.html`](composer-tool-profile-menu.html) — Tools 操作与 profile 二级菜单行为
- [`programs-source-categories.html`](programs-source-categories.html) — Programs 分组与来源分类行为
- [`composer-interaction-modes.md`](composer-interaction-modes.md) — Composer 交互模式
- [`attachment-handling.md`](attachment-handling.md) — 附件处理：附件怎么进模型上下文（[已渲染](attachment-handling.html)）
- [`chat-attachments.html`](chat-attachments.html) — 聊天附件双向流转：聊天流里显示成什么、agent 怎么把文件交回来、可读文件怎么点开
- [`gui-agent-context.md`](gui-agent-context.md) — GUI agent 上下文流转
- [`indicator-dots.md`](indicator-dots.md) — 指示点
- [`surface-system.md`](surface-system.md) — Surface 系统
- [`theme-system.html`](theme-system.html) — 主题入口、完整 token 契约、组件消费与桌面浮层传播的权威设计
- [`settings-collapsible-columns.html`](settings-collapsible-columns.html) — 应用主侧栏与 Settings 分类栏的独立 49px 折叠；Provider 列表始终展开，搜索框通栏
- [`avatar-randomization.html`](avatar-randomization.html) — Agent/用户共用的头像选择器：先选类型，再进入同风格变体或字母字段
- [`web-styles.md`](web-styles.md) — Web 样式组织（一个组件一个文件，目录对齐组件树）
- [`window-state.md`](window-state.md) — 桌面窗口普通尺寸、最大化/全屏与标题栏缩放命中
- [`window-lifecycle.md`](window-lifecycle.md) — 一个主窗口：启动、Dock、二次启动共用同一次创建
