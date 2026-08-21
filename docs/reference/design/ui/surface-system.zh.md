# 表面系统

UI 分为两个**表面上下文**。每个表面有各自的交互语言，眼睛能一眼分清当前停在哪一层：导航层还是内容层。这些规则同时约束**浅色和深色**主题。浅色主题最容易踩坑（浅灰侧栏上铺白底）。

## 两个表面

```
─────────────────────────────────────────────────────────────────
surface        background tone           where it lives
─────────────────────────────────────────────────────────────────
deep           `--bg` /                  左侧栏、右侧栏
               `--bg-secondary`          （branches / worktrees /
                                         mini-DAG）
─────────────────────────────────────────────────────────────────
panel          略抬升的                  聊天流、设置页、对话框、
               `--bg-surface` /          function-card 网格、
               `--bg-tertiary`           attach 卡片、runtime 块
─────────────────────────────────────────────────────────────────
```

**deep** 与 **panel** 之间的抬升是有意的——它替代聊天内容列上显式的边框 / 阴影，让气泡区读起来像一张浮在导航之上的纸。

## 各表面的交互语言

鼠标点按钮不画外圈焦点环。键盘聚焦普通按钮只轻微提高亮度，不用 outline 或 box-shadow。顶部 `role="tab"` 是唯一例外：用当前主题的 `--focus-ring`，深色更亮，浅色更深。

### Deep 表面（侧边栏）

deep 表面上的组件是**列表行**——会话项、分支、收藏，以及内容区里同一套行（MCP 的 `drawio` / `linear` / `+ Add server`）。它们不应当表现得像按钮：

- 闲置：无边框、无描边、无填充
- 悬停 / 选中：背景换成**看得出的灰色**（``--bg-hover`` / ``--bg-selected``），文字仍是 ``--text-primary`` 或 ``--text-secondary``
- 选中行**禁止**用 ``--bg-input`` 填充。浅色主题里这个 token 是白的，铺在浅灰侧栏上会发白、发淡
- 不用品牌色字形，唯一例外是极小的状态点（``.indicator-dot``）

理由：侧栏密、扫得勤。一片品牌色胶囊会吵，还会跟内容列抢视线。悬停变灰让这一层安静，点击目标仍有反馈。

### Panel 表面（聊天内容 + 对话框）

panel 表面上的组件就是按钮 / 胶囊 / 卡片：

- 坐在抬升背景上，"幽灵描边"能干净地呈现
- 闲置：``--bg-surface`` 底，``--text-primary`` 字，主操作用品牌色字
- 悬停：品牌色填充，字切到对比色（``--text-on-accent``）
- 这种反转式悬停让一串操作读起来是同一家人

管理页顶部的 **tab 胶囊**（Abilities / Programs / Plugins / Skills）是唯一的亮底例外：选中态用 ``--bg-input``，跟搜索框一样偏亮，而不是更深。这个填充**只给这些胶囊**。不要抄到侧栏行或 MCP 服务器行上。

## 列表行只有一套尺寸

侧栏导航（`+ New chat`、Agents、Abilities、History、Scheduler）和内容区列表行（MCP 的 `drawio` / `linear` / `+ Add server`）共用**同一只盒子**。不要给右边那列另起一套高度、内边距、圆角或选中底。

```
属性         token / 值
─────────────────────────────────────────────────────────────────
高度         `--ui-list-h` → `--ui-button-h` → 30px
内边距       6px 8px
间距         12px
圆角         `--ui-list-radius`（10px）
闲置         透明；需要时用 1px 透明边只为对齐盒模型
悬停         `--bg-hover`
选中         `--bg-hover`（同一灰，绝不用白 / `--bg-input`）
```

`+ Add server` 跟 `+ New chat` 是同一种行：普通列表行，字略淡。不要斜体，不要另做一种"添加"样式。

实现：`apps/web/app/styles/base.css` 的 `.ui-list-item` 是唯一来源。MCP 的 `.serverItem` 必须对齐这些数（优先 compose `.ui-list-item`，不要平行再写一套）。

## 尺寸系统——两套，高度相同

每个交互原语在两套尺寸里选一套。套内没有 sm / md / lg——选定 list 或 button 之后，高度和圆角就锁死。CSS 变量在 `apps/web/app/styles/base.css`：

```
set         height               radius             css tokens
─────────────────────────────────────────────────────────────────
list        30 px                10 px              --ui-list-h
                                                    --ui-list-radius
─────────────────────────────────────────────────────────────────
button      30 px（与 list 相同） 10 px              --ui-button-h
                                                    --ui-button-radius
─────────────────────────────────────────────────────────────────
```

以前 list 32px、button 30px。这套高低差已废止：侧栏行、MCP tab 胶囊、MCP 服务器行都走 30px。`--ui-list-h: var(--ui-button-h)`。

两套共用 10px 圆角。Claude 形状语言把列表行和小按钮放在 10px，12px（`--radius-lg`）留给卡片和面板。

为什么套内无变体：允许 sm / md / lg 之后，每位作者都会跟设计讨价还价，尺寸跟着分叉。两套固定尺寸才能强制执行。

`Button` 向后兼容：`size="sm" | "lg" | "icon-sm"` 仍指向 `default` 的同一高度。token 名称才是真相。

页头那一行（搜索 + tab 胶囊 + 图标按钮）必须同一垂直中线。控件之间差 1–2px 高度是 bug，不是变体。

## 输入框、下拉、边框

输入框和下拉共用**一层 1px** 边（`border: 1px solid var(--border)`，底 `--bg-input`）。

- 不要在这 1px 边上再叠 2px 的 `:focus-visible` 光晕。原生 `<select>` 收起后焦点还在，叠出来就是双层框。
- 悬停必须**保住**这 1px 边。`border-color: transparent` 会让框像消失了一样（MCP catalog 按钮踩过）。
- 不要每个页面另做一套输入框。设置、对话框、插件、MCP 编辑器都用同一套 1px + `--bg-input`。

## 对话框

对话框只做**淡入淡出**（大约 300ms）。不要从上往下滑动，不要突然消失。动的是透明度，不是位移。

## 设置行

设置页（General、Memory、System 以及其余）统一两列：

- **左**：名称左对齐。说明文字留在这一列，不要伸进右边控件
- **右**：控件 / 取值右对齐
- 左右隔开。不要把标签堆在输入框上面
- 状态标签（`LIVE`、`NEXT START` 等）放在对应控件的**左边**，不要一行左一行右

## 按钮变体指南

`apps/web/components/ui/button.tsx` 已经暴露主要模式：

**Button 派生动作无边框。** 每个 Button 变体在闲置和悬停时都没有边框。deep / panel 的表面抬升已经分层；再加 ``border-input`` 只会给密行添噪声。

表单控件不是 Button。它们保留上面的 1px 边。

```
variant     idle                              hover
─────────────────────────────────────────────────────────────────
default     bg-background + text-primary      bg-primary +
                                              text-primary-foreground
─────────────────────────────────────────────────────────────────
outline     bg-background + foreground        bg-accent +
                                              text-accent-foreground
─────────────────────────────────────────────────────────────────
ghost       transparent                       bg-accent +
                                              text-accent-foreground
─────────────────────────────────────────────────────────────────
secondary   subtle grey fill                  darkens slightly
─────────────────────────────────────────────────────────────────
destructive bg-background + text-destructive  bg-destructive +
                                              text-destructive-foreground
─────────────────────────────────────────────────────────────────
```

按表面选择：

- **Panel + 主操作**（Run、Save、Test、Apply、Check）→ `variant="default"`
- **Panel + 次要操作**（Cancel、Close、Reset、Browse）→ `variant="outline"` 或 `ghost`。页头次要按钮要跟搜索框并排读得出来时，给它和搜索框一样的 1px `--border`，不要无边框、看起来像裸文字
- **Deep 表面——侧栏行** → 不用 Button，用 `.ui-list-item` / `nav-classes.ts`
- **破坏性操作** → `variant="destructive"`

要警惕的失效：该用 `default` 的地方用了 `outline`。

## 禁止事项

- 不要每个页面另起一套悬停 / 选中 / 边框。一套配方，反复用。新样子先写进这份文件。
- 不要在未先于此处列出的情况下引入新的胶囊底色。预算内：deep 灰悬停、panel、品牌填充，以及上面的页头 tab `--bg-input` 例外。
- 不要在 deep 表面用品牌色填充。
- 不要用白色 / `--bg-input` 做侧栏或内容区**列表行**的选中底。浅色主题会发白。
- 不要给 MCP 服务器行（或任何内容区列表）另一套高度、内边距或选中底。
- 不要加悬停位移（translate-y、scale-105）。只换背景。
- 不要给 Button 派生组件加 ``border`` / ``ring`` / ``outline``。
- 不要在 1px 输入/下拉边上再叠 2px 聚焦光晕。
- 不要在悬停时丢掉控件的 1px 边。
- 不要让对话框滑动。只淡入淡出。
- 不要让设置说明伸进右侧控件列，也不要把标签堆在控件上面。
