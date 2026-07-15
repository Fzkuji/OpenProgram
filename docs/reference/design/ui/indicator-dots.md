# 指示点系统

状态 / 活动指示点散落在聊天界面各处，存在四种互不兼容的形式
——尺寸不同、形状不同（Unicode 字形 vs DOM 元素）、呼吸动画
不同、CSS 类不同。当一个 header 的 `●` 字形叠在 body 的
`.pending-pulse` 元素之上时，这种不一致最为明显：字形带有字符盒的
左侧边距（left side-bearing），而元素没有，于是这两个点无法对齐在
同一条垂直线上。

本文档提出一套统一的系统。第一步（尺寸槽位等于 `●` 字形的前进宽度，
advance width）已实现；其余部分留作后续推进。

## 现有指示点清单

```
class                          size     form        animation          uses
─────────────────────────────────────────────────────────────────────────────
.pulse (character ●)           ~12.8 box glyph      opacity 1.5s       inline-tree-header
                               10 disc                                 (Function call, Thinking,
                                                                       Tool call) — 4 sites
.pending-pulse                 10×10    element     scale 1.4s         Running… / Agent is
                                                                       thinking… — 3 sites
.status-dot[.ok/.warn/.err]    7×7      element     none               top-bar provider state
.attach-card-status-dot        6×6      element     opacity 1.2s       attach card
```

不一致之处：

- 盒宽（6 / 7 / 10 / 12.8）
- 形式（字形 vs 元素）
- 动画周期（1.2 / 1.4 / 1.5s）
- 各处独立的 CSS 类，却都在做同一件事

## 目标系统

统一为一个类 `.indicator-dot`，并配以用于尺寸、颜色和动画的
修饰类（modifier class）。**外层盒始终是 14px 字号下 `●` 字形的宽度
（约 12.8px）**，因此指示点既能与 header 字形对齐，也能跨行对齐，
无需在每个调用处单独微调。可见的圆盘由居中的 `::before` 绘制，
这样在可选的 scale 动画运行时布局仍保持稳定。

```css
/* 外层盒 = 14px 字号下 ● 字形的前进宽度。::before 在内部居中绘制
   可见圆盘。在可选的 scale 动画运行时布局槽位保持稳定。 */
.indicator-dot          { display:inline-block; position:relative;
                          vertical-align:middle; width:12.8px; height:12.8px; }
.indicator-dot::before  { content:""; position:absolute;
                          inset:var(--dot-inset, 1.5px);
                          border-radius:50%;
                          background:var(--dot-color, var(--accent-blue)); }

/* 尺寸
     md (默认)    — 12.8×12.8 盒内放 10×10 圆盘，匹配 ● 字形
     sm           — 10×10 盒内放 6×6 圆盘，用于紧凑徽章  */
.indicator-dot.sm       { width:10px; height:10px; }
.indicator-dot.sm::before { inset:2px; }

/* 颜色 — 覆盖 --dot-color。 */
.indicator-dot.--ok     { --dot-color: var(--accent-green); }
.indicator-dot.--warn   { --dot-color: var(--accent-yellow); }
.indicator-dot.--err    { --dot-color: var(--accent-red); }
.indicator-dot.--neutral{ --dot-color: var(--accent-blue); }

/* 动画 — 应用到 ::before，使布局盒不会抖动。 */
.indicator-dot.pulse-opacity::before {
  animation: indicatorPulseOpacity 1.5s ease-in-out infinite;
}
.indicator-dot.pulse-scale::before {
  animation: indicatorPulseScale 1.4s ease-in-out infinite;
}
@keyframes indicatorPulseOpacity { 0%,100%{opacity:1} 50%{opacity:.4} }
@keyframes indicatorPulseScale   { 0%,100%{transform:scale(.85);opacity:.9}
                                   50%   {transform:scale(1.15)} }
```

## 迁移计划

```
old                                        new
─────────────────────────────────────────────────────────────────────────────
<span className="pulse">●</span>           <span className="indicator-dot pulse-opacity"/>
                                           (drop the ● glyph; CSS draws the disc)
<span className="pending-pulse" />         <span className="indicator-dot pulse-scale"/>
<span className="status-dot" />            <span className="indicator-dot sm"/>
<span className="attach-card-status-dot"/> <span className="indicator-dot sm pulse-opacity"/>

CSS — drop  .pulse, .pending-pulse, .status-dot[.ok/.warn/.err],
            .attach-card-status-dot
```

涉及的文件（8 处 JSX 调用点，2 个 CSS 文件）：

- `web/components/chat/messages/execution-dag/index.tsx`（header `●`）
- `web/components/chat/messages/runtime-block.tsx`（header `●` + pending body）
- `web/components/chat/messages/tool-card.tsx`（2× header `●`）
- `web/components/chat/messages/message-list.tsx`（pending 气泡）
- `web/components/chat/messages/assistant-bubble.tsx`（嵌套的 pending）
- `web/components/chat/messages/attach-card.tsx`（状态点）
- `web/components/chat/top-bar/index.tsx`（provider 状态）
- `web/app/styles/chat.css`、`web/app/styles/detail.css`

## 状态

- **第 1 步（已完成）** — `.pending-pulse` 的外层盒加宽到 12.8px，
  以匹配 `●` 字形的槽位；可见圆盘移到 `::before`，使现有的 scale
  动画不再扰动布局。这在不触碰其他指示点变体的前提下，修复了
  `Function call` header 与 `Running…` body 之间的即时错位问题。

- **第 2 步（已完成）** — 新增 `.indicator-dot` 类，带有尺寸 /
  颜色 / 动画修饰类；四个遗留类已删除，八处调用点已迁移完成。
  参见 commit `40cef5a2`。
