# Surface system (dark mode)

The dark-mode UI splits into two **surface contexts**. Each
surface has its own interaction language so the eye can tell at a
glance which "layer" of the app it is hovering: navigation vs
content.

## The two surfaces

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

The lift between **deep** and **panel** is intentional — it
substitutes for an explicit border / shadow on the chat content
column, so the bubble area reads as a separate sheet floating
above the navigation.

## Interaction language per surface

Mouse interaction never draws an outer focus ring on buttons. Keyboard focus
on buttons uses a small brightness change without an outline or box-shadow.
The top tab strip is the exception: its `role="tab"` targets use each theme's
own `--focus-ring`, which is lighter in dark themes and darker in light themes.

### Deep surface (sidebars)

Components on the deep surface are **list rows** — conversation
items, branch entries, function favourites. They should NOT
behave like buttons:

- no border, no outline, no fill in the idle state
- hover / selected → switch background to a **slightly lighter
  grey** (``--bg-hover`` / ``--bg-selected``), text stays in
  ``--text-primary`` or ``--text-secondary``
- avoid the brand-coloured glyph treatment except for the very
  small status / activity indicators (``.indicator-dot``)

Rationale: the sidebar is dense and frequently scanned. A field
of brand-coloured pills makes it loud and visually competes with
the content column. Greying-on-hover keeps the layer calm and
still gives the click target enough feedback.

### Panel surface (chat content + dialogs)

Components on the panel surface ARE buttons / pills / cards:

- they sit on a lifted background, so a "ghost outline" pattern
  reads cleanly
- idle state — ``--bg-surface`` background, ``--text-primary``
  text or brand-coloured text for primary actions
- hover — fill with the brand colour, swap text to its contrast
  pair (``--text-on-accent``)
- the inverted hover is what makes the chain of "actions" feel
  like one design family — the user knows that the colour
  shift is universally the "this is going to do something"
  affordance

## Button variant guidance

`apps/web/components/ui/button.tsx` already exposes the two main
patterns:

**No borders.** Every Button variant is border-less in both idle
and hover state. The surface lift between deep / panel already
separates layers; an explicit ``border-input`` on top of that
adds visual noise to dense rows and looks dated against the
slightly-lifted ghost-pill convention this app uses everywhere
else (function-card grid, attach card, fn-form pills).

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

Pick per surface:

- **Panel + primary action** (Run, Save, Test, Apply, Check) →
  `variant="default"`. Brand-coloured text by default, brand
  filled on hover. This is what most chat / settings / function
  dialog actions should use.
- **Panel + secondary action** (Cancel, Close, Reset, Browse) →
  `variant="outline"` (subtle grey hover) or `ghost`.
- **Deep surface — sidebar rows** → don't use the Button
  primitive. Use plain anchors / divs styled by
  `sidebar.module.css`, since the row IS the interaction.
- **Destructive** (Delete, Remove, Force) →
  `variant="destructive"`. Red-text default, red fill on hover.

The failure mode to watch for is `outline` used where `default`
belongs. `outline` is what shadcn looks like out of the box, so
authors reach for it by reflex, and primary actions end up with
the muted hover-accent treatment instead of the brand fill.
Correcting it is per-call-site work, because only a human can say
whether a given action is primary or secondary.

## Size system — two sets, no in-set variants

Every interactive primitive picks ONE of two size sets. There is
no sm / md / lg ladder inside a set — once you choose list vs
button, height and radius are locked. CSS variables in
`apps/web/app/styles/base.css`:

```
set         height               radius             css tokens
─────────────────────────────────────────────────────────────────
list        32 px                10 px              --ui-list-h
                                                    --ui-list-radius
─────────────────────────────────────────────────────────────────
button      30 px (slightly      10 px              --ui-button-h
            shorter than list)                      --ui-button-radius
─────────────────────────────────────────────────────────────────
```

Why button shorter than list: a pill on the panel surface should
not visually outweigh the sidebar rows it sits next to. Both sets
share the same 10 px radius — the Claude shape language puts list
rows and small buttons at 10 px and reserves 12 px
(`--radius-lg`) for cards and panels.

Why no in-set variants: when the design lets one slot pick from
sm / md / lg, every author negotiates with the design instead of
following it, and the sizes drift apart. Two fixed sets is
enforceable.

Backward-compat for `Button`: `size="sm" | "lg" | "icon-sm"` are
kept as aliases for existing call sites, but they resolve to the
same height as `default`. The token names are the source of
truth.

## Don'ts

- Don't introduce a new pill background colour without listing it
  here first. Three flavours (deep, panel, brand-fill) is the
  budget.
- Don't put brand-coloured fills on the deep surface — the
  contrast against near-black makes a brand pill look like an
  alert, not a click target.
- Don't add the SHIFT-on-hover (translate-y, scale-105) effect
  on either surface. We rely on background swap alone; motion
  inside dense rows reads as jitter, not feedback.
- Don't add ``border`` / ``ring`` / ``outline`` to Button-derived
  components. The surface lift already separates them from the
  background; a border on top of the lift reads as a stacked
  alert dialog or a focus halo, not a quiet click target.
