# DAG renderer

Self-contained SVG renderer for the right-rail mini-DAG (PyCharm-style
conversation history view). Imported for side effects by
`web/components/app-shell.tsx` via `./index`; exposes
`renderHistoryGraph` / `repaintBranchTags` / `setHistoryContextRange`
/ `refreshHistoryContextRange` / `recomputeHistoryVisibility` /
`setHistoryHighlightMode` / `getHistoryHighlightMode` on `window.*`
(consumed by `../conversations.ts` and the WebSocket handlers).

This module replaced the pre-split monolithic
`web/lib/runtime-bridge/history-graph.ts` (~1700 lines). That file now
re-exports from here for back-compat; new code should import from
`./dag` directly.

## Module layout

```
dag/
├── README.md                  (this file)
├── index.ts                   public entry + window bridges
├── types.ts                   GNode / HighlightMode / constants
├── shapes.ts                  SVG primitives, branch colour, shape helpers
├── tooltip.ts                 node hover tooltip (collapsed → expanded)
├── pipeline.ts                main render() — the giant pass+layout+emit loop
├── passes/
│   ├── collapse-runtime-pairs.ts        legacy user/asst runtime pair fold
│   ├── merge-runs.ts                    fold tool wrapper into run-node
│   ├── demote-decoration-cards.ts       suppress lane fork from LLM-triggered cards
│   └── apply-collapse.ts                user/auto subtree collapse
├── layout/
│   ├── build-tree.ts                    flat list → parent/children
│   ├── depth.ts                         row index (prefers backend _depth)
│   ├── assign-lanes.ts                  column index (prefers backend _lane)
│   └── tier.ts                          placeholder for tier helpers (currently inline)
├── render/
│   ├── nodes.ts                         (reserved — node draw still inline in pipeline)
│   ├── edges.ts                         (reserved — edge draw still inline in pipeline)
│   ├── visibility.ts                    white-fill + chat-scroll/mutation sync
│   └── interaction.ts                   click / dblclick / checkout / scroll-to
└── store/
    └── globals.ts                       module-level state (HEAD, collapsed set, ...)
```

`render/nodes.ts` and `render/edges.ts` are currently empty stubs. The
node-/edge-drawing logic is tightly coupled to the render-context
closure (`pos()`, `stableLeafOfNode`, `cinfo`, `internalSet`, etc.)
and lives inline in `pipeline.ts`. A future cleanup pass should
extract it once that context is reified into an object — but the goal
of this reorganisation was zero behaviour change, so the giant
`render()` body stays in one place.

## Node kinds and shapes

See `docs/design/runtime/dag-node-model.md` for the full schema. Quick
reference:

| node kind                      | role            | function field   | shape          |
|--------------------------------|-----------------|------------------|----------------|
| ROOT                           | `user`          | —                | diamond        |
| `user_msg`                     | `user`          | —                | circle         |
| `llm_reply`                    | `assistant`/`llm` | —              | triangle       |
| `function_call` (code)         | `tool`          | `bash`, `gui_agent`, …| square    |
| branch-referencing             | `tool`          | `attach`, `merge`/`task`| square_outline |

ROOT is special-cased by `display=root` → diamond. Every other node's
shape comes from its `role`. There are no anchor / placeholder /
scaffold nodes: a function call is a single `role=tool` node hanging
off its caller via `called_by`. Backend `graph_layout/filter.py`
strips all `display=runtime` rows before the frontend sees them, so
the renderer never has to fold or hide synthetic cards.

## Edge kinds

| edge        | source attribute | drawn how                                  |
|-------------|------------------|--------------------------------------------|
| conv chain  | `parent_id`      | solid coloured S-curve (branch colour)     |
| sub-call    | `caller`         | same S-curve, treated as conv parent for branch-op nodes |
| spawn       | `function="task"` → attach pointer chain → sub-branch conv root | dot-dash grey |
| reference   | `attach_ref` on `function="attach"`/`"merge"` | dashed marching-ants (CSS animation) |

## Pass pipeline (top of `pipeline.ts`'s `render()`)

```
flat GNode[]
  → mergeRuns                       passes/merge-runs.ts
  → collapseRuntimePairs            passes/collapse-runtime-pairs.ts
  → demoteDecorationCards           passes/demote-decoration-cards.ts
  → snapshot stable leafOfNode      layout/build-tree + assign-lanes
  → applyCollapse                   passes/apply-collapse.ts
  → buildTree + assignDepth + assignLanes
  → emit SVG
       conv-edges → attach-refs → spawn-edges → nodes → branch-tags
  → wire chat-scroll / mutation / resize sync
  → first _recomputeVisibility (+ rAF / 250ms / 700ms catch-up)
```

State that survives across calls (per-session `_collapsed`,
`_seenCollapsible`, `_lastSignature`, `_lastGraph`, the various
"wired?" latches) lives in `store/globals.ts`.

## Known corner cases (locked in by tests + manual checks)

The pass pipeline above is what handles each of these. If you're
adding a new pass, check that you don't regress any of these:

* **LLM-called `gui_agent` displays as a reply child square**
  (issue #137). The code (`role=tool`) call hangs off the reply via
  `called_by`, so it renders one tier right of the triangle.
  `demoteDecorationCards` keeps the next user turn on the same lane
  so it doesn't visually fork.

* **Manually-triggered function runs display as a main-lane square.**
  The code call's `called_by` is ROOT, so it sits directly under the
  ROOT diamond on the trunk (lane 0, one tier in), same colour as the
  trunk.

* **Sub-calls don't dim** (issue #129). The "out-of-context"
  override forces `task` / `attach` / `merge` to read as in-context
  regardless of what the context engine says, and `_recomputeVisibility`
  propagates visibility through `_internalOwner` so a visible owner
  lights up its whole sub-call subtree.

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
