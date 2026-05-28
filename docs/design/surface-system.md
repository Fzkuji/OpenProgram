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

`web/components/ui/button.tsx` already exposes the two main
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

## Today's audit (2026-05-28)

- 20 call sites use `variant="outline"`, 8 ghost, 3 secondary,
  3 destructive, **1 default**.
- That distribution reads loud and clear: most authors reach for
  outline by reflex (it's the shadcn default visually), so
  primary actions everywhere are getting the muted "hover accent"
  pattern instead of the brand fill.
- Migrating those 20 outline buttons to `default` where they
  actually represent a primary action is the next concrete step
  — saved for a separate pass since each call site needs a
  human decision about primary vs secondary intent.

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
