# Web 迁移交接 — legacy CSS 收尾

新会话直接说「读 web/MIGRATION.md,继续」即可。

分支:`phase3-message-flip`。本地:`web` 跑在 `:3000`,backend `:8109`。
开发流程:改源码 → `cd web && npm run build` → 在仓库根
`OPENPROGRAM_WEB_PORT=3000 python -m openprogram worker restart`。

---

## 已完成 — legacy JS 全部迁移

`public/js/` 整个目录已删除。原 11 个 legacy 文件全部迁到 `web/lib/`:

```
chat/chat.js + chat-ws.js + init.js → lib/chat-handlers.ts
shared/conversations.js             → lib/conversations.ts
shared/providers.js                 → lib/providers.ts
shared/programs-panel.js            → lib/programs-panel.ts
shared/scrollbar.js                 → lib/scrollbar.ts
shared/helpers.js                   → lib/helpers.ts
shared/ui.js                        → lib/ui.ts
shared/state.js                     → lib/state.ts
shared/history-graph.js             → lib/history-graph.ts
```

迁移模式:每个 TS 模块 export 给 TS 调用方,同时 `window.*` 桥接(因
inline-onclick HTML 和模块间互调仍依赖)。`app-shell.tsx` 顶部按
state→helpers→ui→providers→programs-panel→history-graph 顺序做
side-effect import;`useWS` import chat-handlers + conversations。
`SHARED_JS` 已空,app-shell 的 shared-script fetch/inject 逻辑现在
跑空数组(可顺手删掉)。

WebSocket 连接 + 25 种消息分发都在 `lib/use-ws.ts`,在
`__sharedScriptsReady` resolve 后才 connect。

全部 build 过、浏览器实测过(发普通 chat / `/run` / 加载历史会话 /
切分支 / DAG 渲染 / fn-form / code modal)。

---

## 还剩 — legacy CSS → 组件级 module

`app/styles/*.css`(约 2500 行全局 CSS,经 `app/styles.css` →
`app/globals.css` 导入):

```
01-base.css       270  :root tokens / reset / 基础排版
02-sidebar.css    137  侧栏
03-settings.css    96
05-chat.css      1324  聊天区(最大)
06-detail.css     199  右栏 detail
08-dropdown.css   163
09-right-dock.css  304
```

目标:拆进各 React 组件 co-located 的 `*.module.css` / Tailwind。

**关键约束**:
- `01-base.css` 的 `:root` token(`--bg-*` / `--accent-*` / `--text-*`
  / 几何量)是全局的,**保留为全局**(所有组件 module 依赖这些 CSS
  变量)。只迁组件专属的类规则。
- `html { font-size: 14px }` 使 Tailwind rem 缩放 0.875×,组件里
  arbitrary px 值要锁死。
- 这是纯样式重构、零功能变化,但回归风险高 —— 每迁一个文件要逐页
  视觉核对(chat / settings / programs / chats / memory),别一次性
  全拆。
- 很多类名被 TS 模块生成的 HTML 字符串引用(如 `history-graph.ts` 的
  `.history-node` / `.history-edge`,`ui.ts` 的 `.detail-section` /
  `.code-modal-*`,`conversations.ts` 的 `.provider-icon-letter`)。
  这些类不能改名,只能搬位置;或者保留为全局。

建议顺序:先 03-settings / 02-sidebar / 08-dropdown(小、独立),
再 06-detail / 09-right-dock,最后 05-chat(最大)。01-base 基本
原样保留(只是 token + reset)。

---

## 注意的坑

- `git add` 明确列文件,别 `git add -A`。
- 移除文件时确认同时 `git rm` + 删引用,别留悬空条目(404)。
