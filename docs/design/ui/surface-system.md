# 表面系统（深色模式）

深色模式 UI 分为两个**表面上下文**。每个表面拥有各自的
交互语言，使眼睛能一眼分辨出当前悬停在应用的哪个
"层"：导航层还是内容层。

## 两个表面

```
─────────────────────────────────────────────────────────────────
surface        background tone           where it lives
─────────────────────────────────────────────────────────────────
deep           near-black ``--bg`` /     left sidebar, right
               ``--bg-secondary``        sidebar (branches /
                                         worktrees / mini-DAG)
─────────────────────────────────────────────────────────────────
panel          slightly lifted greyish   chat stream, settings
               ``--bg-surface`` /        panes, dialog content,
               ``--bg-tertiary``         function-card grid,
                                         attach card, runtime
                                         blocks
─────────────────────────────────────────────────────────────────
```

**deep** 与 **panel** 之间的抬升是有意为之的——它替代了
聊天内容列上显式的边框 / 阴影，使气泡区域看起来像一张
漂浮在导航之上的独立纸面。

## 各表面的交互语言

### Deep 表面（侧边栏）

deep 表面上的组件是**列表行**——会话项、分支条目、
函数收藏。它们不应当表现得像按钮：

- 闲置状态下无边框、无描边、无填充
- 悬停 / 选中 → 背景切换为**略浅的灰色**
  （``--bg-hover`` / ``--bg-selected``），文本保持
  ``--text-primary`` 或 ``--text-secondary``
- 避免使用品牌色字形处理，唯一例外是极小的状态 /
  活动指示器（``.indicator-dot``）

理由：侧边栏密集且被频繁扫视。一片品牌色胶囊会显得
喧闹，并在视觉上与内容列竞争。悬停变灰让这一层保持
克制，同时仍为点击目标提供足够的反馈。

### Panel 表面（聊天内容 + 对话框）

panel 表面上的组件就是按钮 / 胶囊 / 卡片：

- 它们位于抬升的背景之上，因此"幽灵描边"模式
  能干净地呈现
- 闲置状态——``--bg-surface`` 背景，``--text-primary``
  文本，主操作则用品牌色文本
- 悬停——以品牌色填充，文本切换为其对比配对色
  （``--text-on-accent``）
- 这种反转式悬停让一连串"操作"感觉像同一个设计
  家族——用户知道这种颜色变化在各处都统一表示
  "这会执行某个动作"的可供性

## 按钮变体指南

`web/components/ui/button.tsx` 已经暴露了两种主要
模式：

**无边框。** 每个 Button 变体在闲置和悬停状态下都没有
边框。deep / panel 之间的表面抬升已经分隔了各层；在此
之上再加显式的 ``border-input`` 会给密集行增添视觉噪声，
并且与本应用在其他各处（function-card 网格、attach 卡片、
fn-form 胶囊）使用的轻抬升幽灵胶囊约定相比显得过时。

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

- **Panel + 主操作**（Run、Save、Test、Apply、Check）→
  `variant="default"`。默认为品牌色文本，悬停时品牌色
  填充。大多数聊天 / 设置 / 函数对话框操作都应使用它。
- **Panel + 次要操作**（Cancel、Close、Reset、Browse）→
  `variant="outline"`（淡灰悬停）或 `ghost`。
- **Deep 表面——侧边栏行** → 不要使用 Button 原语。
  使用由 `sidebar.module.css` 设样式的普通锚点 / div，
  因为行本身就是交互。
- **破坏性操作**（Delete、Remove、Force）→
  `variant="destructive"`。默认红色文本，悬停时红色填充。

## 当前审计（2026-05-28）

- 20 处调用使用 `variant="outline"`，8 处 ghost，3 处
  secondary，3 处 destructive，**1 处 default**。
- 这一分布清晰地说明了问题：大多数作者下意识地选用
  outline（它在视觉上是 shadcn 的默认值），于是各处的
  主操作得到的是低调的"悬停强调"模式，而非品牌色填充。
- 把那 20 个真正代表主操作的 outline 按钮迁移到
  `default` 是下一个具体步骤——这留作单独的一轮处理，
  因为每个调用点都需要由人来判断它属于主操作还是
  次要操作。

## 尺寸系统——两套，套内无变体

每个交互原语在两套尺寸中选其一。套内没有
sm / md / lg 的阶梯——一旦你选定 list 还是 button，
高度和圆角就被锁定。CSS 变量位于
`web/app/styles/base.css`：

```
set         height               radius             css tokens
─────────────────────────────────────────────────────────────────
list        32 px                6 px               --ui-list-h
                                                    --ui-list-radius
─────────────────────────────────────────────────────────────────
button      30 px (slightly      8 px               --ui-button-h
            shorter than list)                      --ui-button-radius
─────────────────────────────────────────────────────────────────
```

为什么 button 比 list 矮：panel 表面上的胶囊在视觉上
不应当压过它旁边的侧边栏行。略大的圆角（8 对 6）在不
比拼高度的前提下区分了二者。

为什么套内无变体：当设计允许一个槽位从 sm / md / lg
中挑选时，每位作者都会开始与设计讨价还价；结果就是
我们在 `Button` 上看到的那次审计（5 个尺寸选项，分布
混乱）。两套固定尺寸是可强制执行的。

`Button` 的向后兼容：`size="sm" | "lg" | "icon-sm"` 作为
别名为现有的 33 + ... 处调用点保留，但它们解析到与
`default` 相同的高度。token 名称才是唯一可信来源。

## 禁止事项

- 不要在未先于此处列出的情况下引入新的胶囊背景色。
  三种风味（deep、panel、品牌填充）就是预算上限。
- 不要在 deep 表面使用品牌色填充——它与近黑背景的
  对比会让品牌色胶囊看起来像一条警报，而非点击目标。
- 不要在任一表面添加悬停时位移（translate-y、scale-105）
  效果。我们仅依赖背景切换；密集行内的运动会被读作
  抖动，而非反馈。
- 不要给 Button 派生组件添加 ``border`` / ``ring`` /
  ``outline``。表面抬升已经把它们与背景分隔开；在抬升
  之上再加边框会被读作一个堆叠的警报对话框或聚焦光晕，
  而非一个安静的点击目标。
