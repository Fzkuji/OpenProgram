# Indicator dot system

Status / activity dots are scattered across the chat UI in four
incompatible forms — different sizes, different shapes (Unicode
glyph vs DOM element), different breathing animations, different
CSS classes. The mismatch is most visible when a header `●`
glyph stacks above a body `.pending-pulse` element: the glyph
carries a character-box left side-bearing, the element doesn't,
and the two dots fail to line up on the same vertical column.

This document proposes a unified system. The first step (sized
slot equal to the `●` glyph advance width) is implemented; the
rest is staged for follow-up.

## Inventory of existing dots

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

Inconsistencies:

- box width (6 / 7 / 10 / 12.8)
- form (glyph vs element)
- animation period (1.2 / 1.4 / 1.5s)
- per-site CSS classes that all do the same job

## Target system

One class `.indicator-dot` with modifier classes for size,
colour, and animation. The **outer box is always the width of
the `●` glyph at 14px font (~12.8px)**, so dots align with header
glyphs and across rows without per-call-site nudges. The visual
disc is painted by `::before` centred inside, which keeps layout
stable while the optional scale animation runs.

```css
/* Outer box = ● glyph advance width at 14px font. ::before paints
   the visual disc centred inside. Layout slot stays stable while
   the optional scale animation runs. */
.indicator-dot          { display:inline-block; position:relative;
                          vertical-align:middle; width:12.8px; height:12.8px; }
.indicator-dot::before  { content:""; position:absolute;
                          inset:var(--dot-inset, 1.5px);
                          border-radius:50%;
                          background:var(--dot-color, var(--accent-blue)); }

/* Sizes
     md (default) — 10×10 disc inside 12.8×12.8 box, matches ● glyph
     sm           — 6×6 disc inside 10×10 box, for compact badges  */
.indicator-dot.sm       { width:10px; height:10px; }
.indicator-dot.sm::before { inset:2px; }

/* Colours — override --dot-color. */
.indicator-dot.--ok     { --dot-color: var(--accent-green); }
.indicator-dot.--warn   { --dot-color: var(--accent-yellow); }
.indicator-dot.--err    { --dot-color: var(--accent-red); }
.indicator-dot.--neutral{ --dot-color: var(--accent-blue); }

/* Animations — apply to ::before so the layout box doesn't jitter. */
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

## Migration plan

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

Touched files (8 JSX call sites, 2 CSS files):

- `web/components/chat/messages/execution-dag/index.tsx` (header `●`)
- `web/components/chat/messages/runtime-block.tsx` (header `●` + pending body)
- `web/components/chat/messages/tool-card.tsx` (2× header `●`)
- `web/components/chat/messages/message-list.tsx` (pending bubble)
- `web/components/chat/messages/assistant-bubble.tsx` (nested pending)
- `web/components/chat/messages/attach-card.tsx` (status dot)
- `web/components/chat/top-bar/index.tsx` (provider status)
- `web/app/styles/chat.css`, `web/app/styles/detail.css`

## Status

- **Step 1 (done)** — `.pending-pulse` outer box widened to 12.8px
  to match the `●` glyph slot; visual disc moved to `::before` so
  the existing scale animation no longer perturbs layout. This
  fixes the immediate misalignment between `Function call` header
  and `Running…` body without touching the other dot variants.

- **Step 2 (deferred)** — add `.indicator-dot` class + migrate the
  four call sites. Cosmetic, no behaviour change; do when the
  next round of UI polish makes it worthwhile.
