# 指示点系统

聊天界面各处的状态 / 活动指示点统一为一个类 `.indicator-dot`，
再配以尺寸、颜色、动画的修饰类。统一成一个类是对齐的前提：各处
各画各的点时，尺寸和形状会逐渐分叉，header 的 `●` 字形叠在 body
元素之上就无法对齐在同一条垂直线上——字形带有字符盒的左侧边距
（left side-bearing），而元素没有。

## 被它取代的那些点

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

分叉出现在四个维度上：盒宽（6 / 7 / 10 / 12.8）、形式（字形 vs
元素）、动画周期（1.2 / 1.4 / 1.5s），以及各处独立却做同一件事的
CSS 类。

## 系统

**外层盒始终是 14px 字号下 `●` 字形的宽度
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

## 类的对应关系

四个遗留类到统一类的对应：

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

调用点（8 处 JSX，2 个 CSS 文件）：

- `web/components/chat/messages/execution-dag/index.tsx`（header `●`）
- `web/components/chat/messages/runtime-block.tsx`（header `●` + pending body）
- `web/components/chat/messages/tool-card.tsx`（2× header `●`）
- `web/components/chat/messages/message-list.tsx`（pending 气泡）
- `web/components/chat/messages/assistant-bubble.tsx`（嵌套的 pending）
- `web/components/chat/messages/attach-card.tsx`（状态点）
- `web/components/chat/top-bar/index.tsx`（provider 状态）
- `web/app/styles/chat/indicator-dot.css`（及 `web/app/styles/chat/` 下各组件文件）

## 附录：实现状态

已实现。四个遗留类已删除，八处调用点全部改用 `.indicator-dot`。
