# Web 样式组织

Web 端 CSS 按组件树组织：一个组件（或一个紧致的关注点）一个文件，目录与
UI 自身结构对齐。调某个组件的样式，就打开以它命名的那一个文件。

## 布局

```
apps/web/app/styles/
  base.css          设计令牌 + 全局基元（不动，全局）
  themes/           主题令牌覆盖（不动，全局）
  chat/             每个聊天组件一个文件
    transcript.css bubbles.css execution-strip.css attach-card.css
    agent-branch-banner.css message-rail.css message-actions.css
    inline-tree.css stream-blocks.css turn-files-card.css file-diff.css
    …（共 25 个）
  dag/              DAG 渲染器每个关注点一个文件
    view-host.css canvas.css nodes.css edges.css badges.css
    tooltip.css inspector.css hud.css dag-flash.css
  right-dock/       右侧栏各面板
    view-host.css bookmarks.css branches-panel.css web-history.css
    detail.css
```

`apps/web/app/styles.css` 按拆分前的层叠顺序（base → chat → detail 时代文件 →
right-dock/dag）导入整棵树，特异性平局的判定与拆分前完全一致。

## 规则

- **一个组件一个文件。** 文件只写它命名的那个组件的样式。每个文件开头
  一段注释：这是哪个组件、对应的 tsx/ts 源路径。
- **归属跟着组件树走。** DAG 节点样式属于 `dag/nodes.css`，因为绘制器在
  `lib/runtime-bridge/dag/render/nodes.ts`；聊天气泡样式属于
  `chat/bubbles.css`。新组件开新文件，不往旧文件里追加段落。
- **真正全局的留在全局。** 令牌与主题在 `base.css` / `themes/`；聊天列
  骨架（`.main`、`#chatView`）是 `chat/layout.css`——唯一有意跨组件的
  文件，名字点名了用途。
- **module CSS 保持 module CSS。** 已用 `*.module.css` 的组件（输入框）
  维持原样；本树只覆盖全局样式层。
- 对样式源做断言的守卫脚本读对应的组件文件（`check-dag-subagent.mjs`
  读 `dag/nodes.css` 校验 HEAD 光晕关键帧）。
