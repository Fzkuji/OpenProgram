# DAG Rendering Spec (Layout · Edges · Legend · Default Visibility)

> How the session graph draws: where each node goes, what each edge looks like,
> and what the user sees by default. **This document is the authoritative
> implementation standard** — write the layout code to match it, and when
> something breaks, check against it. For the data semantics (nodes, the two
> edges) see `dag/overview.md`; this document only covers the drawing.
>
> Every rule comes with an example. **The SVG scenario figures in
> `dag-layout-spec.html` are authoritative** (13 scenes: 1–7 base layout, 8 merge,
> 9 cross-branch messaging, 10 spawn dispatch & merge-back, 11 execution-subtree
> aggregation, 12 status & badge legend, 13 badge anchoring & collision). The ASCII
> figures in this file are a text-mode digest, equivalent to the html; on conflict
> the html wins.

---

## Where the graph lives: a center perspective

The graph is one of the chat pane's two **perspectives**, not a side panel. Each
center tab is either on the conversation transcript or on the context graph, and
the pair of controls at the pane's top-right switches between them — a
perspective toggle plus a `…` menu of session actions, following Obsidian's
per-pane controls. The perspective is per tab, so parking one session on the
graph leaves the others on their transcripts.

Giving the graph the full column width is the point: a branchy session needs
lanes, tiers and branch badges that a 288px rail truncates.

| Piece | Where |
|---|---|
| Perspective state | `CenterTab.dagView` (`web/lib/state/center-tabs-store.ts`) — not persisted; a reload opens on the transcript |
| Controls | `web/components/chat/view-controls.tsx` |
| Graph host | `web/components/chat/dag-view.tsx` — renders `#historyPanel` + `.history-body`, the elements `pipeline.ts` and `render/visibility.ts` select |
| Perspective swap | `.center-pane-chat[data-center-view]` in `web/app/styles/chat.css` |

Both surfaces stay mounted and swap by `display`: the renderer paints into the
host on every capture regardless of which perspective is showing, so unmounting
would blank the graph until the next one. The host's `ResizeObserver`
(`_wirePanelResize`) is what re-flows the layout — a perspective switch, a
split-view drag and a window resize all reach it the same way.

Clicking a node in the graph fills the right sidebar's Details / Context views;
those views stay in the sidebar because they read one selected node, not the
whole session.

### The composer belongs to the pane, not to the transcript

**The perspective swap hides `#chatArea`, the transcript scroll box — never
`#chatView`.** The composer is a singleton portalled into `#composer-mount`
inside `#chatView` and anchored `bottom: 0` against it, so hiding that ancestor
would take the composer down with the transcript, and mounting a second one
would fork the draft, the run state and the model row into two copies of what
the user reads as one input box. Both perspectives therefore share the one
composer instance, and the graph replaces only the scroll area above it.

You can send a message from the graph, and the node it produces appears on the
next capture — the send path is the transcript's send path, unchanged, so
nothing about the graph perspective has to know it is showing.

The graph takes the transcript's place by **covering the pane**, not by taking
its slot in the column: `#chatView` keeps its `flex: 1` and its full height, so
the composer's `bottom: 0` still lands on the pane's bottom edge, and stacking
order does the rest — the graph sits under the composer's `z-index: 5`. Shrinking
`#chatView` instead would drag the input box up the pane.

`.dag-view` reserves the composer's strip as `padding-bottom`, the way the
transcript reserves it on `.chat-messages`, so the deepest nodes never sit
behind the input box.

### Branch strip

A single wrapping row of pills above the canvas, one per active branch
(`web/components/right-sidebar/branches`, `variant="chips"`). Each chip carries
the branch's lane colour as a dot and its name; the HEAD chip is outlined and
badged. Click a chip to check that branch out; hover reveals rename and delete.

The chip and the sidebar's list row are **the same component** under two
layouts, so checkout, rename and delete are one implementation and cannot drift
between the two surfaces. Only the box differs — a pill sized to its content
instead of a stretched row. Merge and attach stay with the list layout: picking
two branches and starring a base needs vertical room the strip does not have,
and the strip's question is the narrower "which branch am I on".

The graph already draws the branch structure, so the strip does not repeat it.
It is capped at three rows; past that it scrolls, leaving the canvas the bulk
of the pane.

---

## 0. First, "what to draw": two granularities, only the conversation layer by default

A session graph has two kinds of node, an order of magnitude apart in count:

| Layer | Nodes | Question it answers | Magnitude |
|---|---|---|---|
| **Conversation layer** | ROOT, user, llm replies, spawn branch roots, merge, **manually-invoked top-level function nodes** (the user's explicit action — the code node behind a fn-form/run card) | What shape the session has: how many turns, how many branches, who spawned whom | single digits ~ dozens |
| **Execution layer** | code (tool call) and its internal sub-calls | What one turn did internally | can reach dozens in a single turn |

**Default visibility rule: the Viewport lays out only the conversation layer.** If an
llm node has an execution subtree (code nodes hanging off it via `caller`), it is
collapsed into a `⚒N` count badge next to the node (N = direct + transitive
sub-calls). Click the badge and that turn's execution subtree expands and enters
layout; click again to collapse. Expansion state is remembered per node and cleared
when switching sessions.

```
Default (conversation layer):    Click ⚒9 to expand that turn:
◇ROOT                          ◇ROOT
├ ○你好                        ├ ○你好
│ └ △回复                      │ └ △回复
├ ○查天气                      ├ ○查天气
│ └ △回复 ⚒9                   │ └ △回复
                               │     ├ ■bash
                               │     ├ ■web_fetch
                               │     └ ■…(9 total)
```

Rationale: execution-layer information already has a better presentation in the chat
stream (each turn's execution-tree card, the Executions page). The Viewport's job is
to let you see the session structure at a glance; 50 tool squares laid out flat would
drown the 8 structural nodes — a real weather session with 66 nodes, 50+ of them code,
looked exactly like that.

> The other two views — chat stream and call tree — are unaffected: the chat stream
> lays out top-level turns by seq with function nesting folded; the Executions /
> execution-tree card expands fully along caller. Same data, three projections.

---

## 1. A node's position = (column, row)

- **Column (horizontal) = lane start column + tier indent**
- **Row (vertical) = depth**

### lane — which branch it belongs to

**Count the branches and hand out column numbers 0, 1, 2… in order of appearance; no
gaps, no judging which is "the trunk."**

A branch = one conversation chain (user → llm → user → …). Three events produce a new
branch:

| Event | New branch's root | Attachment |
|---|---|---|
| retry / rewrite a turn | the forked-off user / llm node | shares predecessor with the replaced node |
| spawn (task / message_branch dispatch) | the `source=agent_spawn` user node | caller = the initiating node, predecessor empty |
| the new mainline from a merge | the merge node itself | lands in the base branch lane (see scenario 8), no new lane opened |

**Branches are packed by actual column occupancy**: the columns a branch occupies = from
its start column to the deepest column of its subtree; the next branch starts at the
previous branch's actually-occupied rightmost column +1, with no overlap.

### tier — how many columns to indent within a branch

**The conversation layer is fixed by role; the execution layer increases by caller
depth.** Two rules, each governing one layer, so they never conflict. A spawn root
counts by the conversation-layer rule: it is a conversation-layer user, tier=1, and its
caller pointing at a deep node only determines where the spawn edge is drawn from, not
its own indent.

| Node | Layer | tier |
|---|---|---|
| ROOT | — | 0 |
| user (incl. spawn branch root, hand-back node) | conversation | 1 |
| llm reply, merge | conversation | 2 |
| code (tool / function call) | execution | 3 |
| a deeper call inside the execution layer | execution | caller's tier +1 |

### depth — which row

Rows are allocated by a **preorder walk of the structural parent tree**: every visible
node takes its own row, and a subtree pushes the siblings below it down by however many
rows it occupies. Rows are not "hops to root" — that would stack all children of one
parent on a single row. Two exceptions keep their anchor's row because they grow
sideways, not down: a fork sibling sits on the **same row** as the sibling it rewrites
(scene 3), and a spawn branch root sits on the **same row as the spawn call node**
(scene 10) — unless a second spawn root already claimed that row, in which case it takes
the next free one and brings its lane with it, because a sub-agent capsule is a pill
carrying a name and two of them side by side are unreadable (§12). Cross-session spawns
land as the target session's own conversation chain (lane 0), not a side branch
(scene 12).

---

## 2. Three global layout rules

**① Square grid**: `COL_W == ROW_H`, child nodes strictly at the parent's lower-right
corner (45°).

**② Strict alignment + compaction**: nodes land on grid intersections; **empty rows shift
up to fill, empty columns shift left to fill, no empty rows or columns are kept.** This
applies to every visibility change: execution subtree collapse/expand, branch folding,
visibility filtering — once collapsed, the rows and columns it occupied must be freed
immediately. **Corollary: any "placeholder box" violates this rule** — the running state
is expressed by the node's own stroke (see the legend), not by drawing a dashed
placeholder node.

**③ Branches don't overlap**: see the lane rule.

---

## 3. Edges: color = branch, line style = type (orthogonal)

Each lane has one color (`dag/types.ts` `LANE_COLORS`). Any edge uses the lane color of
the branch it belongs to / points at; **never give a category of edge a fixed color.** Type
is conveyed only by line style:

| Edge type | Line style | Color | Default |
|---|---|---|---|
| same-branch parent→child | solid | this branch's color | shown |
| retry fork bridge | dashed `5 4` | this branch's color | shown |
| spawn edge (initiating node → branch root) | dash-dot `4 2 1 2` | child branch's color | shown |
| merge convergence (peer tip → merge node) | thick solid 2.4px | peer branch's color | shown |
| attach merge-back (source tip → embed position) | long dashes `4 4` | source branch's color | shown |
| inter-branch communication (send_to_branch) | dotted `1 5` | target branch's color | **shown only on hover** (numerous; always-on would smear) |

---

## 4. Node legend: shape = role, stroke = status

**Shape**: ◇ ROOT · ○ user · △ llm · ■ code · ◉ merge (solid circle with a hole, the graph's unique
"convergence" shape) · ▭ compaction summary (a capsule, the graph's only wide shape — §9).

**status mapping** — status is drawn on the node itself, never as a separate dashed
placeholder box:

| status | Drawing |
|---|---|
| success | default stroke |
| running | same-shape dashed stroke + breathing-opacity animation |
| error | red stroke + `!` badge at the upper right |
| cancelled | whole node grayed 50% |

**Badges** (attached to the node, no grid cell of their own):

| Badge | Meaning |
|---|---|
| `⚒N` (right of an llm node) | a collapsed execution subtree, N sub-calls; click to expand |
| `×N` (right of a code node) | N isomorphic siblings produced by a loop, folded (pure display) |
| `↗` (top-right corner) | marked on **both sides** of a cross-session spawn: the branch root in the target session (caller lives in another session's graph, hangs on ROOT here, tooltip "spawned from <source session>"); and the initiating node in the source session (tooltip "dispatched to <target session>" — otherwise the dispatch leaves no trace in its own graph). Click jumps to the peer session (implementation may come later). **Cross-session only**: a same-session spawn has both ends in the graph and the dash-dot edge already expresses the relationship (scene 10) — no ↗ there; the mark stands in for the edge that cannot be drawn, and is not a generic spawn decoration |

---

## 5. Branch-name badge

- **Anchoring**: **directly below the branch's deepest currently visible node**, one row
  down. In the default (folded) view that is the last conversation-layer node; when the
  execution subtree is expanded the badge follows the bottom-most expanded node and
  moves back up on collapse. Branch membership = lane (expanded execution nodes share
  their turn's lane).
- **Edge avoidance**: only when an edge crosses the anchor cell (the descending line of
  an expanded execution subtree, or the conversation continuing) does the badge shift
  half a column left — a badge never sits on an edge. Expanding/collapsing the execution
  layer only toggles this half-column shift; it never changes the anchor node.
- **Collision**: judged on **measured pixel boxes** (badge backing width = measured text
  width + padding, not grid cells). Short names almost never collide across the grid
  spacing; long names (branches auto-named from a message or task description) on
  same-row anchors do overlap — the later one (by branch order) slides down one row
  until there's no collision.
- **Source**: badges come ONLY from `list_branches` — **active** branches (bright,
  clickable to checkout). **Merging erases the name** (git semantics): a merged-in
  branch no longer draws a badge; its name moves into the ◉ merge node's tooltip
  (like a merge commit message recording provenance). The name data in session meta
  is kept on disk.
- Styling follows the HEAD label (`--bg-hover` rounded background, 9px text, backing sized
  to the measured text width).

---

## 6. Scenarios (SVG authority in spec.html, 13 scenes)

| # | Scene | Key points |
|---|---|---|
| 1–7 | Base layout (single turn / multi-turn / retry / tool indent / manual function / composite / collapse shift-left) | Scenario 4's tool indent shows as a ⚒N badge in the default view (scene 11); the indented squares appear only after expansion |
| 8 | merge (multi-parent convergence) | ◉ solid circle with a hole, lands on the base branch lane, peer merge-in thick solid lines (peer lane color); attach pointer nodes are not drawn, only the lines |
| 9 | cross-branch messaging (send_to_branch) | dotted `1 5`, target branch color, hidden by default / shown on hover; a from_branch user node lands at the target branch tail |
| 10 | spawn dispatch → attach merge-back | spawn edge dash-dot `4 2 1 2` (child branch color); the child branch's first node sits on the **same row** as the spawn node, own lane, tier=1; merge-back long dash `4 4` from the child tip back to its embed position on the main branch (the chat stream renders it as the Spawned card, display order moved ahead — see `ui/invariants.md` rule 9) |
| 11 | execution-subtree default aggregation | see §0: collapsed to a ⚒N badge by default, click to expand into layout, collapse reclaims rows/cols per rule ②; expansion state is per-branch independent |
| 12 | status & badge legend | see §4: status drawn on the node's own stroke, no placeholder boxes; both sides of a cross-session spawn carry the ↗ corner mark |
| 13 | badge anchoring · avoidance · collision · merged | see §5: anchor directly below the branch's last conversation-layer node, half-column left shift only when an edge crosses the anchor cell, collision shifts down one row, merging erases the badge (provenance moves into the merge node's tooltip) |

**Send-back nodes and the switcher (semantic note, no dedicated layout scene)**: a
message_branch send-back (the child branch's answer returning to the initiator's lane
as a user node with `predecessor = the initiating node`) forms a fork whenever the user
also sent a message while waiting — **send-back nodes participate in the `< N/M >`
switcher** (they are genuine alternative continuations of the initiator's dialogue;
`source=from_branch` gets no agent_spawn-style isolation — see `ui/invariants.md`
rule 7).

**A sub-agent spawning again**: forbidden at the data layer
(`MAX_TASK_DEPTH=1` — only the main agent may task(); a spawned agent always
does the work itself, see `ui/invariants.md` rule 6). The renderer still
recurses per scene 10 as a fallback, so historical multi-generation delegation
chains (the worker branch's dash-dot edge starting from the sub-agent's reply
node, hanging under its lane structure) remain drawable.

## 7. Render pipeline (code map)

```
web/lib/runtime-bridge/dag/
  pipeline.ts        orchestration: passes → layout → edges → nodes → badges → visibility
  passes/            data transforms, applied in order:
    merge-runs.ts               merge consecutive runs of the same node
    collapse-runtime-pairs.ts   fold a legacy display=runtime user/assistant
                                wrapper pair into a single row (pre-`caller`-edge
                                schema left the wrapper with no chat content of
                                its own, so it duplicated the column)
    demote-decoration-cards.ts  re-stamp LLM-triggered runtime cards so a reply
                                with both a card child and a follow-up user turn
                                isn't mistaken for a fork (which would split the
                                figure into two lanes)
    apply-collapse.ts           fold execution subtrees, emit the ⚒N badge
  layout/            lane / depth (the implementation of section 1).
                     **tier is NOT computed here** — the backend computes it in
                     `openprogram/webui/graph_layout/tier.py` and ships it on the
                     node; the front end only consumes the value.
  render/edges.ts    the line-style table of section 3
  render/nodes.ts    the shapes + status strokes + badges of section 4
  render/badges.ts   the branch-name badge of section 5
  store/globals.ts   expansion state, lastGraph, signatures
```

The backend `openprogram/webui/graph_builder.py` produces the node array (including the
`branch_name` stamp, caller/predecessor), and `graph_layout/` does the lane/tier/depth
annotation — **tier specifically in `graph_layout/tier.py`**. Verification tool: `python tools/dag_dump.py <session_id>` prints
lane/tier/depth + an ASCII grid.

## 8. The white fill means context coverage

A node's white fill marks it as **part of what the next LLM call will carry**.
There is no mode to choose and no switch to find: whenever the graph is
showing, it owns its pane. A lone session tab gets the chat shell plus this
graph; two session tabs split into two `PeerSessionPane`s where the shell — and
therefore the graph — is not rendered at all. So there is never a transcript
beside the graph, "which bubbles are on screen" has no reading to give, and
coverage is simply what the fill means.

The covered set is served by `GET /api/sessions/{id}/context-range`: the active
branch walked back from head, stopping at the most recent compaction summary.
Nodes outside it draw dimmed. `enterExclusiveCoverageMode`
(`web/lib/runtime-bridge/dag/index.ts`) is what the host calls to fetch and
apply it.

### Coverage data shape

The same response carries, per node, the two degradations the context pipeline
applies to content it is still carrying:

```json
{
  "session_id": "…",
  "node_ids": ["…"],
  "count": 12,
  "nodes": [
    { "node_id": "…", "in_context": true, "aged": false, "spilled": true }
  ]
}
```

| Field | Meaning | Backend source |
|---|---|---|
| `in_context` | the node is in the covered set — always true for a row, since the list IS that set | `get_branch` |
| `aged` | a code node old enough that its result renders as a one-line stub | `openprogram/context/render.py::_aged_code_ids`, boundary from `context/aging.py` |
| `spilled` | the result was written to `large_nodes/` and the render only cites it | `metadata.spilled`, written by `context/spill.py::spill_if_large` |

Both flags come from the functions the real render pass calls, not from a
parallel reimplementation — **the graph never derives context semantics
itself**; it asks the backend and draws the answer.

### Drawing the two degradations

| State | Drawing |
|---|---|
| in context | white fill (the baseline) |
| `aged` | stroke dimmed to 40% opacity — reads as "still here, but only the gist" |
| `spilled` | `▤` at the node's upper LEFT |

`aged` dims **`stroke-opacity`, never `opacity`**: the white fill is the
coverage signal, and fading the whole node would fade that too, collapsing two
independent facts into one. The `▤` mark takes the upper left because the upper
right is spoken for (`!` error, `↗` cross-session spawn) and the lower right
holds the fold badge; it is drawn as `<text>` so `_applyVisibility`'s
"first shape child" scan cannot mistake it for the node body.

Compaction covers a range of nodes with a summary; those covered nodes simply
fall out of `node_ids`, so the white fill lands on the summary and nowhere in
the range behind it. §9 is what the graph then does with that range.

Refreshing: coverage is re-fetched whenever `context_stats` or
`compaction_finished` arrives (`chat-handlers.ts`), so the fill tracks the real
context without the frontend ever recomputing it. Note that `aged` and
`spilled` are **not** part of the render signature, so applying new coverage
busts it (`setLastSignature(null)`) — otherwise the repaint silently no-ops and
the graph keeps painting the previous answer.

Compaction interaction:

- `insert_summary_node` clones nothing (dag/overview.md §8): the summary is an
  ordinary `role=llm` chain member carrying `metadata.covers`, and the kept
  tail keeps its own ids and predecessors. The branch ids therefore ARE the
  ids the graph draws, so `/context-range` returns them directly — no
  translation layer, no second id space.
- The summary node is drawn like any other conversation node in every respect
  the data cares about; only genuinely synthetic bridges are filtered
  (`graph_layout/filter.py`). Its capsule is a shape, not a role — §9.
- After compaction the covered prefix falls out of the set, so the fill lands
  on the summary and the kept tail.
- `compaction_finished` must refresh the context range
  (`chat-handlers.ts`); coverage is event-driven like everything else — the
  frontend never computes context membership itself.
- Context ids that have no drawn node (e.g. `display=runtime`
  task-followup rows) are silently ignored: they are context, but not
  graph.

## 9. Compaction reads as one capsule, not fourteen dimmed turns

Dimming a covered range says the right thing about each node and the wrong
thing about the session: fourteen faint circles still cost fourteen rows, and
the eye still has to walk them to reach the live conversation. The summary is
one node that stands for all of them, so the graph draws it that way.

**The capsule.** A summary node is a rounded box on the trunk — the only wide
shape in the vocabulary, because it is the only node that speaks for more than
one turn. It is still an ordinary `role=llm` chain member (dag/overview.md §8):
the shape is a shape, not a fourth role.

**The pleats.** Up to three receding grey slivers off the capsule's right edge,
each shorter and fainter than the last. They are what lets the capsule hide a
range without the graph lying about it — the pill says "one node", the pleats
say "and a stack behind it" — and they disappear when the capsule opens,
because then the stack is drawn. Beside them the covered count (`▸ 14` folded,
`▾ 14` open) makes the fold self-describing.

**The fold.** The covered range is elided by default. Clicking the capsule
brings it back, clicking again folds it away. That state is view-only and never
persisted: it records how you are looking at the graph, not what the graph is,
and a fresh session starts folded.

**The ghosts.** An expanded range draws in grey outline with a dashed incoming
edge — readable, clickable, visibly not part of the next request. This is the
whole point of expanding: "did the summary actually capture what I said" is
answerable without the answer ever being confused for live context.

The white fill never lands on a covered node, in either state, because
`/context-range` does not list it. One fact, one source (§8).

### Where the interval comes from

The store writes `metadata.covers = [first_seq, last_seq]`. Seq orders the
graph but never leaves the store — every wire payload speaks ids — so
`webui/graph_builder.py` resolves the interval once, on the way out, and the
summary row carries `covers_ids`: the ids it stands in for, in seq order, with
the summary itself excluded (its own seq sorts just inside the range it names).

One field drives everything: the capsule shape, the fold, the pleat count, the
ghost marking, and the inspector's coverage row. The frontend does no seq
arithmetic and calls no second endpoint.

## 10. A failed turn stays visible as an archive, never as an alarm

A turn that ends in `status = error` is a terminal node; the retry forks off its
predecessor and the conversation continues on the new line (dag/overview.md).
The failed line is kept — that is the point of forking rather than rewinding —
but it can never re-enter context.

So once such a node is **off the HEAD chain**, it draws in the same grey as a
covered turn, and the inspector labels it `失败轮 · 已留档`. The two states look
alike because on the only axis the graph is about they *are* alike: on disk,
readable, and never in the next request.

The grey deliberately replaces the red `!` stroke §4 gives a live error. Red
means "this needs you now", and an archived line does not — the retry already
happened. The `!` glyph itself stays, so why the line ended is still legible.

Both halves of the test matter. `status` alone would grey the error you are
currently looking at, before you have retried it. Off-HEAD alone would grey
every sibling branch. The node has to be a failure *and* abandoned.

`status` is the store's own terminal marker, written by the turn machinery
(`runtime/execution/turn-cancellation.md` for the cancel case, which stays
`cancelled` and keeps its own 50% grey). The graph reads it; it never decides
it.

## 11. Click, right-click, double-click

The hover tooltip (§4's card) answers "what is this" while you sweep the graph.
These answer the questions you stop and ask.

**Click → inspector.** A popover beside the node: role, seq, id, token estimate,
`expose` level, coverage state, ~200 characters of content, and three actions
(copy content · raw JSON · fork from here). Raw JSON opens in the same card
shell rather than a modal — a modal would take the graph away to show you one
node from it. The token figure is `llm.output_tokens` when the node carries a
measurement and `chars/4` when it does not; the popover says which
(`tokens` vs `tokens（估）`) instead of dressing an estimate as a count.

The coverage row reads off the DOM flags the node drawer already stamped
(`data-ghost`, `data-failed`, `.out-of-context`), so the popover and the picture
beside it cannot disagree.

**Right-click → menu.** checkout to this branch · fork from this node · fork and
edit this message (user turns only) · copy node id · view raw JSON. Anchored at
the cursor, because a right-click is aimed and a menu that jumps to the node's
edge reads as a miss.

**Double-click a user turn → fork and edit.** The message text lands in the
composer with HEAD already back at its fork point. Other nodes keep the
checkout behaviour — there is nothing to edit on a reply or a tool result.

### Every action is an existing operation

Nothing here adds a verb to the protocol:

| Action | Route | Why that is the whole implementation |
|---|---|---|
| checkout | `POST /api/chat/checkout` | a pure HEAD move, exactly what the transcript's sibling navigator sends |
| fork from node | `POST /api/chat/checkout` | fork *is* checkout plus intent — a turn sent from a HEAD that already has children is by definition a sibling of them, which is how the transcript's "branch from here" button works too |
| fork and edit | `POST /api/chat/checkout` to the node's **predecessor**, then the text into the composer | the user edits and sends; that send is an ordinary send against the new HEAD, so it forks with no protocol change and no "send from node X" concept to keep alive. The predecessor is the fork point precisely because the edited message has to stand *beside* the original, not after it — the same shape `POST /api/chat/edit` produces |

The inspector and menu are built imperatively (`render/inspector.ts`) because
the graph is: they float over an SVG the renderer owns, keyed to node geometry,
outside React's tree.

**Legend.** A collapsible corner card names the shapes and the two greys
(`components/chat/dag-view.tsx`). It starts collapsed — the vocabulary is small
and learnable, so the legend is for the first few sessions rather than a
permanent fixture on the canvas.

## 12. A sub-agent reads as one named capsule, not as its whole transcript

A spawned agent runs a conversation of its own, and that conversation is
usually the larger half of the session. Drawn in full it buries the main lane
it hangs off: in the case this section was written from, two sub-agents
contributed 280 of a session's 309 nodes, and the four turns the user actually
had were four circles lost among them.

None of those nodes is what the parent turn carries, either. The parent sees
the sub-agent's *result*, through the attach pointer; its transcript is
reachable, not resident. So the default view draws what the parent knows — one
node — and keeps the rest a click away.

**The capsule.** A spawn branch root is drawn as the §9 pill with a second
outline inset inside it. The silhouette is shared on purpose: both shapes mean
"one node standing for many", which is the thing the reader has to recognise
first. The doubled stroke says the many are a different agent's chain rather
than a span of this one.

**The name.** Where a compaction capsule is labelled by count, a sub-agent
capsule is labelled by name — `▸ 后端架构 (14)`. "Whose branch is this" is the
question the fold has to answer, and a count is not an answer to it; the count
rides along in parentheses so the fold still says how much it is hiding. The
name comes from the label the runner stamps on the attach pointer that points
back at the branch (dag/overview.md §4), with the branch name as a fallback.
The inspector titles the same node `子 agent · <name>` instead of `user`, which
is what its role field says and not what the node is.

**The name is drawn inside the pill.** A compaction count is three characters
and can hang off the capsule's right edge; a name cannot. Two sub-agents spawned
by one turn sit a lane apart, and two names hung off their right edges print
through each other and through each other's bodies. So the pill is sized to its
own measured text and carries it centred inside, ellipsised past 180px — a
capsule is a glyph, not a paragraph the lane has to make room for. That width is
one number the layout also reads: it reserves the columns the pill occupies, it
sizes the canvas to the pill's right edge rather than to the point it is
anchored at, and every edge that can terminate on a capsule lands on that edge
instead of its centre, so no line crosses a name.

**Two capsules never share a row.** Because the pill is wide, the row exception
above yields to it: the first capsule keeps its call node's row, and a second one
that would land on the same row takes the next free row and brings its lane with
it (an expanded branch has to stay attached to its head). Rule ② still holds —
this is an ordering, not a reserved gap.

**The fold.** The branch is elided by default; clicking the capsule draws the
whole lane, clicking again folds it away. Nested sub-agents keep their own
capsules rather than disappearing into their parent's fold — otherwise there
would be no handle to open the inner one with. The state is view-only and never
persisted, exactly as in §9.

**HEAD is never folded away.** Checking out a sub-agent's lane keeps that lane
drawn even while every other capsule stays shut. A graph that hides where you
are standing is worse than a graph that draws too much.

The pass runs after the compaction fold (`passes/fold-spawn-branches.ts`), so
the two compose without either having to know the other ran: each owns one kind
of elision, and a node dropped by both is dropped once.

## Appendix: Implementation Status

The whole spec is implemented. Where each part lives:

| Spec item | Implementation |
|---|---|
| §0 execution-subtree aggregation | `passes/apply-collapse.ts`: any node with execution sub-calls starts folded; `render/nodes.ts` draws ⚒N (spawn-root subtrees exempt) |
| Rule ② corollary (no placeholder box) | `shapes.ts`: no `square_outline`; task renders as a plain square |
| §4 status on the stroke | `graph_builder` emits status; `nodes.ts` draws it on the stroke (running dashed+breathing / error red+! / cancelled grayed) |
| §5 badge anchoring | `render/badges.ts`: anchor at last conversation-layer node, half-column left shift when a line crosses the anchor cell, measured-pixel-box collision slides down one row |
| Scene 8 merge shape and lines | `shapes.ts` `merge_dot` (◉); `edges.ts` merge-in line peer-colored 2.4px solid |
| Scenes 8/10 attach pointer | backend filters it (display=runtime) + `graph_builder` stamps the ref onto the embed host (`attach_returns`); `edges.ts` draws the long-dash return line |
| §4 cross-session ↗ | `graph_builder` stamps `spawn_remote` (target side); `nodes.ts` draws ↗ (source-side `spawn_out` rendering is ready, awaiting a data source that stamps it) |
| §1 spawn root tier | `graph_layout`: tier=1 / same-row depth / new lane; `task_followup` without an attach pointer re-parents onto the receiving turn (`filter.py` fallback) |
| Composer shared by both perspectives | `chat.css` hides `#chatArea`, not `#chatView`; asserted by `web/scripts/check-center-tabs.mjs` |
| Branch strip | `BranchesPanel variant="chips"` + `BranchItem chip`; `.branches-strip` / `.branch-chip` in `chat.css` / `right-dock.css` |
| §8 coverage query | `routes/tree.py::_coverage_nodes` fills `/context-range`'s `nodes`; tested in `tests/unit/test_context_range_coverage.py` |
| §8 aged / spilled drawing | `render/nodes.ts` (stroke-opacity + `▤`), fed by `_coverageSet` in `store/globals.ts` |
| §9 `covers_ids` on the wire | `webui/graph_builder.py` resolves `metadata.covers` to ids; tested in `tests/unit/test_graph_builder_covers.py` |
| §9 capsule shape | `shapes.ts` `capsule` (keyed on `covers_ids`, tagged `data-shape` so `_applyShapeSize` leaves its geometry alone) |
| §9 fold + pleats + ghosts | `passes/fold-summaries.ts` (fold), `render/nodes.ts` (pleats, count, ghost stroke), `render/edges.ts` (dashed ghost edge), `_summaryExpanded` in `store/globals.ts`; executed by `web/scripts/check-dag-summary.mjs` |
| §10 archived failure | `render/nodes.ts::_isArchivedFailure` — `status=error` AND off the HEAD chain; grey overrides §4's red |
| §11 inspector / menu / fork & edit | `render/inspector.ts`, wired in `render/interaction.ts`; all three actions go through `POST /api/chat/checkout` |
| §11 legend | `DagLegend` in `components/chat/dag-view.tsx`, `.dag-legend` in `right-dock.css` |
| §12 sub-agent capsule | `shapes.ts` `spawn_capsule` (double stroke), `passes/fold-spawn-branches.ts` (fold + name resolution + HEAD exemption), `render/nodes.ts` (name label, `data-spawn*`), `_spawnExpanded` in `store/globals.ts`; executed by `web/scripts/check-dag-subagent.mjs` |
| §12 the name on the wire | `task/runner.py::_update_attach_card` stamps `attach.label` from the task; `ws_actions/session.py::_annotate_spawn_origin` carries it to the spawn root as `spawned_from.label`; tested in `tests/unit/test_task_attach_integration.py` |
