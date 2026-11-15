# DAG renderer

Self-contained SVG renderer for the session context DAG (PyCharm-style
conversation history view). It paints into `#historyPanel .history-body`,
hosted by the chat pane's graph perspective
(`apps/web/components/chat/dag/dag-view.tsx`) — see
`docs/reference/design/runtime/dag/rendering.html`. Imported for side
effects by `apps/web/components/app-shell.tsx` via `./index`, which
exports `renderHistoryGraph` / `repaintBranchTags` /
`setHistoryContextRange` / `refreshHistoryContextRange` /
`enterExclusiveCoverageMode` (imported by `../conversations.ts`,
`../chat-handlers.ts`, `app-shell.tsx` and `components/chat/dag/dag-view.tsx`).

This module replaced and removed the pre-split monolithic
`apps/web/lib/runtime-bridge/history-graph.ts` (~1700 lines). Current code
imports from `./dag` directly.

## Module layout

```
dag/
├── README.md                  (this file)
├── index.ts                   public entry
├── types.ts                   GNode / layout constants
├── paint-gate.ts              cheap signatures and render gating
├── pipeline.ts                main render() pass + layout + emit loop
├── passes/
│   ├── collapse-runtime-pairs.ts        legacy user/asst runtime pair fold
│   ├── merge-runs.ts                    fold tool wrapper into run-node
│   ├── demote-decoration-cards.ts       suppress lane fork from LLM-triggered cards
│   ├── fold-summaries.ts                elide the range a compaction capsule covers
│   ├── project-programs.ts              project top-level Programs into overview
│   ├── thread-nodes.ts                  Program-thread node classification
│   └── thread.ts                        Program-thread ownership and folding
├── layout/
│   ├── build-tree.ts                    flat list → parent/children
│   ├── depth.ts                         row index (prefers backend _depth)
│   ├── assign-lanes.ts                  column index (prefers backend _lane)
│   └── geometry.ts                      coordinates and graph bounds
├── interaction/
│   ├── view/                            the camera
│   │   ├── canvas.ts                    pan / zoom / fit, lattice, one paint a frame
│   │   ├── gestures.ts                  wheel, pinch, pan drag, selection-box drag
│   │   ├── wheel.ts                     mouse wheel vs trackpad vs pinch
│   │   ├── far-labels.ts                zoomed-out label culling
│   │   └── minimap.ts                   the corner map
│   ├── node/                            one node under the pointer
│   │   ├── nodes.ts                     click / dblclick / contextmenu wiring
│   │   ├── tooltip.ts                   hover / detail card lifecycle and position
│   │   ├── tooltip-content.ts           pure card content model
│   │   └── inspector.ts                 verb menu + raw JSON layer
│   └── selection/                       many nodes at once
│       ├── selection.ts                 selection state, hit-testing, the box
│       ├── node-delete.ts               delete, widening, undo
│       ├── keys.ts                      shortcuts
│       └── key-sheet.ts                 whether the shortcut sheet is open
├── render/
│   ├── nodes.ts                         node tiles, status and coverage marks
│   ├── tiles.ts                         icon tile per node kind (presentTool for calls)
│   ├── agents.ts                        agent dots and activity bubbles
│   ├── edges.ts                         conv / fork / attach / spawn edges, belts
│   ├── visibility.ts                    the coverage fill (is-covered)
│   ├── seat.ts                          reads a drawn group's translate(x,y) back
│   ├── shapes.ts                        SVG primitives, branch colour, text width
│   └── labels/
│       ├── captions.ts                  the text beside each node
│       └── badges.ts                    branch-name badges, fold counts
└── store/
    └── globals.ts                       module-level state (HEAD, collapsed set, ...)
```

The rest of the module lives beside its kind of file, each in its own
folder: the React parts in `apps/web/components/chat/dag/` (the host
`dag-view.tsx`, the rail `dag-tools.tsx`, the shortcut sheet
`dag-key-sheet.tsx`), the styles in `apps/web/app/styles/dag/`
(`graph/` for the drawing, `panels/` for what floats over it), the tests
in `apps/web/tests/dag/`. No folder holds more than about eight files;
when one would, it splits by what its files are about.

`pipeline.ts` still owns the render-context closure (`pos()`,
`stableLeafOfNode`, `cinfo`, `internalSet`, …) and passes it into the
`render/` drawers as arguments. Reifying it into one object would
shorten those signatures; nothing depends on it happening.

## Node kinds and tiles

See `docs/reference/design/runtime/dag/rendering.html` §4 for the full schema.
Every node is the same rounded icon tile; `tileFor` (`render/tiles.ts`)
picks its icon and tone:

| node kind                      | role            | function field   | icon / tone          |
|--------------------------------|-----------------|------------------|----------------------|
| ROOT                           | `user`          | —                | house / root         |
| `user_msg`                     | `user`          | —                | user-rounded / user  |
| `llm_reply`                    | `assistant`/`llm` | —              | agent's character / branch colour |
| `function_call` (code)         | `tool`          | `bash`, `gui_agent`, …| `presentTool` |
| sub-agent spawn                | `tool`          | spawn            | sub-agent's character / branch colour |
| branch-referencing             | `tool`          | `attach`, `merge` | link, pull request / plan |
| compaction summary             | `assistant`/`llm` | — (has `covers_ids`) | archive / memory |

ROOT is special-cased by `display=root`. There are no anchor / placeholder /
scaffold nodes: a function call is a single `role=tool` node hanging
off its caller via `caller`. Backend `graph_layout/filter.py`
strips all `display=runtime` rows before the frontend sees them, so
the renderer never has to fold or hide synthetic cards.

## Edge kinds

| edge        | source attribute | drawn how                                  |
|-------------|------------------|--------------------------------------------|
| conv chain  | `predecessor`    | solid coloured S-curve (branch colour)     |
| sub-call    | `caller`         | same S-curve, treated as conv parent for branch-op nodes |
| spawn       | `function="agent"` → attach pointer chain → sub-branch conv root | dot-dash in the **child branch's lane colour** (rendering.md §3) |
| reference   | `attach_ref` on `function="attach"`/`"merge"` | dashed marching-ants (CSS animation) |

## Pass pipeline (top of `pipeline.ts`'s `render()`)

```
flat GNode[]
  → mergeRuns                       passes/merge-runs.ts
  → collapseRuntimePairs            passes/collapse-runtime-pairs.ts
  → demoteDecorationCards           passes/demote-decoration-cards.ts
  → projectTopPrograms              passes/project-programs.ts
  → snapshot stable leafOfNode      layout/build-tree + assign-lanes
  → foldSummaries                   passes/fold-summaries.ts
  → buildThreadModel                passes/thread.ts
  → buildTree + assignDepth + assignLanes
  → emit SVG
       conv-edges → attach-refs → spawn-edges → nodes → branch-tags
  → attachCanvas (transform now, the rest on the next frame)
  → _recomputeVisibility (the coverage fill)
```

State that survives across calls (`_threadOpen`, `_summaryExpanded`,
`_lastSignature`, `_lastGraph`, the per-session canvas view) lives in
`store/globals.ts`.

## Keeping a paint cheap

A capture redraws the whole SVG, and gestures fire faster than the
screen refreshes. The rules that keep both cheap
(`docs/reference/design/runtime/dag/rendering.html` §7):

* Gestures move the view in the store; `applyView` paints once a frame.
  Work bound to the scale runs only when the scale or the graph changes.
* Never read a size (`clientWidth`, `getBoundingClientRect`, `getBBox`)
  straight after swapping DOM in: read first, write after, or wait a
  frame. `attachCanvas` sets the transform and defers the rest.
* Whatever is a pure function of its inputs is kept: icons
  (`render/tiles.ts`), tool presentations, text widths
  (`render/shapes.ts`), the minimap's dots and colours.
* Positions of drawn groups come from `render/seat.ts`, not ad-hoc
  regexes.

## Known corner cases (locked in by tests + manual checks)

The pass pipeline above is what handles each of these. If you're
adding a new pass, check that you don't regress any of these:

* **LLM-called `gui_agent` displays as a reply child square**
  (issue #137). The code (`role=tool`) call hangs off the reply via
  `caller`, so it renders one tier right of the reply.
  `demoteDecorationCards` keeps the next user turn on the same lane
  so it doesn't visually fork.

* **Manually-triggered function runs display as a main-lane square.**
  The code call's `caller` is ROOT, so it sits directly under the
  ROOT diamond on the trunk (lane 0, one tier in), same colour as the
  trunk.

* **Sub-calls don't dim** (issue #129). A node outside the context
  range keeps its solid tile (`.history-node.out-of-context` in
  `app/styles/dag/graph/nodes.css`); only edges fade.

* **Top-level `gui_agent` square auto-collapses** (issue #136).
  `applyCollapse` always starts `role=tool` clusters with ≥1
  caller-edge kid collapsed; the user can click to expand.

* **Decoration nodes never fork the trunk** (issue #137). See
  `demoteDecorationCards` — the runtime card itself is pinned to the
  parent's lane, the next non-runtime sibling is promoted onto that
  lane (and its conv subtree + caller subtree get re-stamped too).

* **Sub-process disk write + cache invalidate timing** is handled in
  the backend (`webui/_graph_layout.py`); the front-end just trusts
  whatever lane/depth/tier the backend emits.

## Corner cases pending future work

(empty — add here when a new edge case surfaces)
