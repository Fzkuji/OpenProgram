# Indicator dot system

Status and activity dots across the chat UI are one class,
`.indicator-dot`, with modifier classes for size, colour, and
animation. A single class is what keeps dots aligned: when each
site draws its own dot, sizes and forms drift apart, and a header
`●` glyph stacked above a body element never lines up on the same
vertical column — the glyph carries a character-box left
side-bearing and the element does not.

## The dots this replaced

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

Four axes drifted: box width (6 / 7 / 10 / 12.8), form (glyph vs
element), animation period (1.2 / 1.4 / 1.5s), and a separate CSS
class per site all doing the same job.

## The system

**The outer box is always the width of
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

## Class mapping

The four legacy classes map onto the unified one as follows:

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

Call sites (8 JSX, 2 CSS files):

- `apps/web/components/chat/messages/execution-dag/index.tsx` (header `●`)
- `apps/web/components/chat/messages/runtime-block.tsx` (header `●` + pending body)
- `apps/web/components/chat/messages/tool-card.tsx` (2× header `●`)
- `apps/web/components/chat/messages/message-list.tsx` (pending bubble)
- `apps/web/components/chat/messages/assistant-bubble.tsx` (nested pending)
- `apps/web/components/chat/messages/attach-card.tsx` (status dot)
- `apps/web/components/chat/top-bar/index.tsx` (provider status)
- `apps/web/app/styles/chat/indicator-dot.css` (and sibling per-component files under `apps/web/app/styles/chat/`)

## Appendix: Implementation Status

Implemented. The four legacy classes are gone and all eight call
sites use `.indicator-dot`.
