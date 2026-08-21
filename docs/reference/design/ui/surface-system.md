# Surface system

The UI splits into two **surface contexts**. Each surface has its
own interaction language so the eye can tell at a glance which
"layer" of the app it is hovering: navigation vs content. These
rules apply in **light and dark** themes. Light-theme tokens are
the ones that usually go wrong (white fill on a pale rail).

## The two surfaces

```
─────────────────────────────────────────────────────────────────
surface        background tone           where it lives
─────────────────────────────────────────────────────────────────
deep           `--bg` /                  left sidebar, right
               `--bg-secondary`          sidebar (branches /
                                         worktrees / mini-DAG)
─────────────────────────────────────────────────────────────────
panel          slightly lifted           chat stream, settings
               `--bg-surface` /          panes, dialog content,
               `--bg-tertiary`           function-card grid,
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
items, branch entries, function favourites, and the same row used
in a content-pane rail (MCP `drawio` / `linear` / `+ Add server`).
They should NOT behave like buttons:

- no border, no outline, no fill in the idle state
- hover / selected → switch background to a **visible grey**
  (``--bg-hover`` / ``--bg-selected``), text stays in
  ``--text-primary`` or ``--text-secondary``
- never fill selected rows with ``--bg-input``. In light theme
  that token is white; on a light-grey rail the selected row
  washes out and looks faded
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

Header **tab pills** on a manage page (Abilities / Programs /
Plugins / Skills) are the one bright exception: the selected tab
uses ``--bg-input`` so it reads like the search box (lighter, not
darker). That fill is **only** for those pills. Do not copy it
onto sidebar rows or MCP server rows.

## One list-row recipe

Sidebar nav (`+ New chat`, Agents, Abilities, History, Scheduler)
and content-pane list rows (MCP `drawio` / `linear` / `+ Add server`)
share **one** box. Do not invent a second height, padding, radius,
or selected fill for the rail on the right.

```
property     token / value
─────────────────────────────────────────────────────────────────
height       `--ui-list-h` → `--ui-button-h` → 30px
padding      6px 8px
gap          12px
radius       `--ui-list-radius` (10px)
idle         transparent, 1px transparent border if needed for
             box-sizing only
hover        `--bg-hover`
selected     `--bg-hover`  (same grey, never white / `--bg-input`)
```

`+ Add server` is the same row as `+ New chat`: a normal list
row, slightly muted text. Not italic, not a different add-style.

Implementation: `.ui-list-item` in `apps/web/app/styles/base.css`
is the source. MCP `.serverItem` must match those metrics (prefer
composing `.ui-list-item` over a parallel recipe).

## Size system — two sets, same height

Every interactive primitive picks ONE of two size sets. There is
no sm / md / lg ladder inside a set — once you choose list vs
button, height and radius are locked. CSS variables in
`apps/web/app/styles/base.css`:

```
set         height               radius             css tokens
─────────────────────────────────────────────────────────────────
list        30 px                10 px              --ui-list-h
                                                    --ui-list-radius
─────────────────────────────────────────────────────────────────
button      30 px (same as       10 px              --ui-button-h
            list)                                   --ui-button-radius
─────────────────────────────────────────────────────────────────
```

List used to be 32px and button 30px. That split is retired:
sidebar rows, MCP tab pills, and MCP server rows must sit on the
same 30px rhythm. `--ui-list-h: var(--ui-button-h)`.

Both sets share the same 10 px radius — the Claude shape language
puts list rows and small buttons at 10 px and reserves 12 px
(`--radius-lg`) for cards and panels.

Why no in-set variants: when the design lets one slot pick from
sm / md / lg, every author negotiates with the design instead of
following it, and the sizes drift apart. Two fixed sets is
enforceable.

Backward-compat for `Button`: `size="sm" | "lg" | "icon-sm"` are
kept as aliases for existing call sites, but they resolve to the
same height as `default`. The token names are the source of
truth.

A header row (search + tab pills + icon buttons) must share one
vertical center. A 1–2 px height mismatch between those controls
is a bug, not a variant.

## Inputs, selects, and borders

Inputs and dropdowns share **one single-layer 1px** edge
(`border: 1px solid var(--border)`, fill `--bg-input`).

- Do not stack a 2px `:focus-visible` halo on that 1px edge.
  After a native `<select>` closes and focus remains, the stacked
  ring reads as a double frame.
- Hover must **keep** the 1px border. Setting `border-color:
  transparent` on hover makes the box look like it vanished
  (MCP catalog buttons had this).
- Do not invent a second input chrome per page. Settings,
  dialogs, plugins, and MCP editors use the same 1px +
  `--bg-input` treatment.

## Dialogs

Dialogs **fade** in and out only (about 300ms). No slide from
the top, no snap-close. Motion is opacity, not translate.

## Settings rows

Settings pages (General, Memory, System, and the rest) use one
two-column row:

- **left**: name, left-aligned. Description stays in this column
  and does not run into the control
- **right**: the control / value, right-aligned
- left and right are isolated columns. Do not stack the label
  above the input
- status chips (`LIVE`, `NEXT START`, …) sit to the **left** of
  their control, never mixed left/right across rows

## Button variant guidance

`apps/web/components/ui/button.tsx` already exposes the two main
patterns:

**No borders on Button-derived actions.** Every Button variant is
border-less in both idle and hover state. The surface lift
between deep / panel already separates layers; an explicit
``border-input`` on top of that adds visual noise to dense rows.

Form fields are not Buttons. They keep the 1px edge above.

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
  `variant="outline"` (subtle grey hover) or `ghost`. When a
  header secondary action needs to read as a real control next
  to the search box, give it the same 1px `--border` as the
  search field (not a borderless outline that looks like naked
  text).
- **Deep surface — sidebar rows** → don't use the Button
  primitive. Use `.ui-list-item` / `nav-classes.ts`.
- **Destructive** (Delete, Remove, Force) →
  `variant="destructive"`. Red-text default, red fill on hover.

The failure mode to watch for is `outline` used where `default`
belongs. `outline` is what shadcn looks like out of the box, so
authors reach for it by reflex, and primary actions end up with
the muted hover-accent treatment instead of the brand fill.

## Don'ts

- Don't invent a new hover / selected / border treatment per
  page. One recipe, then reuse it. A new look needs a line in
  this file first.
- Don't introduce a new pill background colour without listing it
  here first. Flavours in budget: deep grey hover, panel, brand
  fill, and the header-tab `--bg-input` exception above.
- Don't put brand-coloured fills on the deep surface — the
  contrast against the rail makes a brand pill look like an
  alert, not a click target.
- Don't use white / `--bg-input` as the selected fill on sidebar
  or content-pane **list rows**. Light theme washes out.
- Don't give MCP server rows (or any other content-pane list) a
  different height, padding, or selected fill than sidebar nav.
- Don't add the SHIFT-on-hover (translate-y, scale-105) effect
  on either surface. We rely on background swap alone; motion
  inside dense rows reads as jitter, not feedback.
- Don't add ``border`` / ``ring`` / ``outline`` to Button-derived
  components. The surface lift already separates them from the
  background.
- Don't stack a 2px focus glow on a 1px input/select edge.
- Don't drop a control's 1px border on hover.
- Don't slide dialogs. Fade only.
- Don't let a settings description overflow into the right-hand
  control column, or stack label above control.
