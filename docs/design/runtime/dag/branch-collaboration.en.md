# Branch Collaboration Design Doc (Communication · Service · Merge)

Status: **draft (pending discussion; user authorized continued progress before going to sleep)** · Created: 2026-06-29

> Goal: make different branches in the DAG more than just "parallel universes" — let them **collaborate**: one branch sends a message to another, one branch does work on behalf of another, and the results of two branches merge into one. This doc takes stock of the current state (much of it is already implemented), fills the gaps, and defines how merge/communication nodes are drawn in the DAG viewport.
>
> Prerequisites: the edge model (caller + predecessor) is covered in `session-dag.md`; layout rules are in
> `dag-layout-algorithm.md` (includes the 7-scenario spec.html).

## 1. Current State (verified via codegraph)

The **data layer and backend for branch collaboration mostly already exist**; what's missing is mainly ① a tool for one branch to proactively send a message to another, and ② how merge/attach nodes are drawn in the DAG viewport.

| Capability | State | Location |
|---|---|---|
| fork (branching) | ✅ Already present. checkout moves HEAD; the next user turn naturally becomes a sibling | `message-actions.tsx` branch() / checkout |
| branch abstraction | ✅ A "branch" = a `(session_id, head_id)` pair, unified within and across sessions | `ws_actions/merge.py` |
| **merge** | ✅ Backend already implemented. `merge_branches` action → `process_merge_turn`, writes N attach pointers + one merge assistant node, `commit_parents=[target prior, *peers]` (multi-parent) | `ws_actions/merge.py` + `agent/_merge.py` |
| merge UI | ✅ MergeModal: equal merge (produces a new tip) vs attach-into-★ (in place) + a merge instruction | `merge-modal.tsx` |
| **attach (embedding)** | ✅ One branch's content embedded into another point as an attach pointer; expands into an `[Attached from "label"]` block | `_merge.py` + `branch.py` `_attach_info` + generator |
| attach edges | ✅ The DAG already draws the attach_ref dashed line (source tip → attach node) | `dag/render/edges.ts` |
| worktree merge | ✅ A separate mechanism (git worktree ff-only file merge) | `worktree-item.tsx` |
| **inter-branch messaging** | ❌ **Missing.** There is no tool for "branch A's LLM to proactively send a message to branch B" | To be added |
| **DAG drawing of merge nodes** | ⚠️ The data exists (multi-parent), but how the viewport draws the convergence of multiple parents is undefined | Defined in this doc |
| **sub-branch service mode** | ◐ Partial. /task sub-agents exist, but the merge-back of "sub-branch finishes and hands the result back to the main branch" needs to be wired up | Reuses merge |

**Conclusion**: the merge "engine" is already built (merge_branches + attach + multi-parent commits). This doc mainly does three things: (A) define how merge/attach nodes are drawn in the DAG; (B) add an inter-branch messaging tool; (C) wire "sub-branch service → result merge-back" into a complete chain.

## 1.5. Edge Visual Rules (color = branch, line style = type, orthogonal)

**Iron rule: color always denotes "branch identity," never "edge type."** Each lane has one color (see `dag/types.ts` `LANE_COLORS`), and any edge uses the lane color of the branch it **belongs to / points at**. **Never** assign a fixed special color to a particular edge type (e.g. gray for spawn, gold for communication) — that conflicts with "color = branch" and makes it impossible to tell whether the branch changed or the edge type changed.

**Edge types are distinguished only by line style (dash pattern / thickness):**

| Edge type | Line style | Color |
|---|---|---|
| same-branch edge (parent→child) | solid | that branch's lane color |
| retry fork (user manual retry/rewrite) | dashed `5 4` | that branch's lane color |
| LLM proactively creates a branch (spawn_branch) | dash-dot `4 2 1 2` | child branch's lane color |
| inter-branch communication (send_to_branch) | dotted `1 5` | target branch's lane color |
| merge convergence (merge) | thick solid `2.4px` | peer branch's lane color |
| attach merge-back (result embedded back) | long dashes `4 4` | source branch's lane color |

Effect: the same line style appears in different colors on different branches (the color tells you "which branch"); on the same branch, different edge types share the same color and are told apart by dashes (the line style tells you "what relationship"). The two dimensions are orthogonal.

### Default visibility: only communication lines are hidden by default

| Edge type | Default |
|---|---|
| same-branch call (solid) | **always shown** |
| retry fork (dashed) | **always shown** |
| spawn_branch / create_branch branch creation (dash-dot) | **always shown** |
| merge convergence (thick solid) | **always shown** |
| attach merge-back (long dashes) | **always shown** |
| **send_to_branch communication (dotted)** | **hidden by default; shown only on hovering the node/branch** |

Rationale: structural lines (how this branch came to be, merge/merge-back) are limited in number and form the skeleton — keeping them always shown stays legible. **Communication lines can be very numerous** (branches message each other frequently), and showing them all permanently turns into a tangle — so they're hidden by default and surface only when you hover the relevant node/branch, revealing that node's communication lines. Implementation: communication lines attach hover-visibility on render (CSS class + node hover trigger), and structural lines are unaffected.

## 2. Three Collaboration Modes

### Mode 1: Inter-branch messaging (communication)

**Scenario**: branch A's LLM wants to ask branch B's LLM something, or push a piece of information to B.

**Mechanism**: add an agentic tool `send_to_branch`:

```
send_to_branch(target_branch, message) -> the other side's reply (optional wait)
```

- `target_branch`: the target branch's head_id (or branch name)
- `message`: the content to send
- Behavior: append a user node at the end of the target branch (`source="from_branch"`, annotated with the source branch); the target branch's LLM sees it on its next turn and replies; optionally wait synchronously for the other side's reply and return it to the caller.

**DAG drawing**: from the initiating branch's LLM node, draw a **communication line** (dotted `1 5`, distinct from other line styles) pointing at the newly added user node on the target branch. The line's color uses the **target branch's lane color** (see "Edge Visual Rules" above — color always = branch, type is conveyed only by line style). This line is a "communication edge," not a caller/predecessor structural edge — it is used only for rendering and does not enter lane/depth computation.

> On the data side: the new user node on the target branch has predecessor = the target branch tip (normal conversation chain), plus `metadata.from_branch = the initiating branch's node id`, from which the render layer draws the communication dashed line.

### Mode 2: Sub-branch serving the main branch (dispatch → merge-back)

**Scenario**: main branch A dispatches a sub-branch B to do something (look something up / run a tool), and B hands the result back to A when done.

**Mechanism**: this is the "branch version" of the `/task` sub-agent, reusing the existing spawn + attach:
1. A's LLM calls `spawn_branch(task)` → creates sub-branch B (forks a new lane); B runs independently
2. When B finishes, its tip is embedded back into A via **attach** (the existing attach mechanism: an attach pointer points at B's tip and expands into an `[Attached from "B"]` block that enters A's context)
3. On its next turn, A's LLM sees B's output and continues

**DAG drawing**: the existing spawn edge (dash-dot, task node → sub-branch root) + the attach_ref dashed line (sub-branch tip → attach node) already express this. Sub-branch B is an independent lane (per the layout rules, starting at A's lane rightmost column +1, with its own vertical line).

### Mode 3: Branch merge (convergence)

**Scenario**: two branches each produced their own results, and they merge into one (the most critical case, which determines how the merge node is drawn).

**Two kinds of merge** (already in MergeModal):
- **equal merge**: N branches are peers, and the merge produces a **new merge node** as the new tip. The merge node has N parents (convergence).
- **attach-into-★ (in-place merge)**: pick one base branch; the rest attach into base, and base continues downward without producing a standalone merge node (this is the multi-peer version of Mode 2).

**Data model of the merge node** (already implemented):
- the merge is a `role=assistant` node (the LLM synthesizes a reply from each branch's output)
- its `predecessor` = the base branch's tip (the main conversation chain parent)
- each additional "branch being merged in" is expressed via an **attach pointer node**: one attach pointer per peer (`predecessor=target_head`, `attach.head_id=peer tip`)
- `commit_parents = [target prior commit, *peer commit ids]` (multi-parent, for provenance)

## 3. DAG Drawing of the Merge Node (the core definition of this doc)

A merge is the **only** place in the DAG where "multiple lines converge into one node" (everything else fans out tree-like). Drawing:

```
Before merge (two branches):     After merge:
col0   col2(fork vert) col3      col0        col3
◇ROOT                         ◇ROOT
│                             │
●─user                        ●─user
   │                             │
   ▲─llm    ┊                    ▲─llm    ┊
            ┊  ●─user'(fork)              ┊  ●─user'
            ┊     │                       ┊     │
            ┊     ▲─llm'                  ┊     ▲─llm'
                                          │        ╲
                                          ●─◆ merge ←──┘ (two lines converge)
                                            (new tip, multi-parent)
```

**Rules**:
1. **merge node shape**: distinguish it with a special shape (suggested: **double ring / solid diamond with a crossbar**) so it is recognizable at a glance as a "convergence point," different from the ordinary assistant triangle.
2. **merge node's column (lane)**:
   - equal merge: the merge is the new mainline tip, returning to the **base branch's lane** (usually the merged main branch's lane, or a freshly opened "post-merge mainline"). Preference: merge into base's lane so that the post-merge mainline continues base.
   - each merged peer branch draws a **convergence line** (solid or thick dashed) angling from the peer's tip into the merge node (similar to how a git merge commit converges two parent lines).
3. **convergence line**: from each peer tip → merge node, taking a polyline that "goes vertical to the merge row first, then enters horizontally/diagonally," colored with the peer branch's lane color (so you can see "which branch this line comes from").
4. **attach pointer node**: the attach pointer used by a merge is a `display=runtime` temporary node (`merge_temp=true`), **not drawn as a standalone node in the viewport** (it would be noise) — the convergence line alone expresses "this branch was merged in." filter.py already filters out `display=runtime`.

**To confirm**: which lane the merge node lands in after the merge —
- Option A: land in the base branch lane (the post-merge mainline continues base, other branches "converge into" base) — leaning toward this
- Option B: open a new lane (the merge product forms its own new mainline)

## 4. Inter-branch Messaging Tool (new, to be implemented)

```python
@function(name="send_to_branch")
def send_to_branch(target_branch: str, message: str, wait_reply: bool = False) -> str:
    """Send a message to another branch.
    target_branch: target branch head_id or branch name
    message: content
    wait_reply: if True, synchronously wait for the target branch's LLM reply and return it; if False, just deliver
    """
```

Implementation points:
- append a user node at the end of the target branch: `predecessor=target branch tip`, `source="from_branch"`,
  `metadata.from_branch=caller node id`
- `wait_reply=True`: trigger a turn on the target branch, wait for the assistant reply, return its text
- safety: sending a message is a side effect (writing into another branch); in attended mode it should be interceptable by the policy layer (hooks into the event layer `tool.before`, see the proactive design)
- DAG: the render layer reads `metadata.from_branch` to draw the cross-branch communication dashed line (a new line style, distinct from attach/spawn)

## 5. Implementation Steps (after user confirmation)

| Step | What to do | Verify |
|---|---|---|
| 1 | DAG drawing of the merge node: special merge node shape + peer convergence lines (lane color) | construct a merge session, dag_dump + check the convergence in the browser |
| 2 | post-merge merge node lane assignment (Option A: land in base lane) | same as above |
| 3 | `send_to_branch` tool + from_branch metadata | after the tool call, a user node appears on the target branch |
| 4 | communication dashed line rendering (read from_branch) | check the cross-branch dashed line in the browser |
| 5 | wire up the sub-branch service chain (spawn_branch → attach merge-back) | A dispatches B; when B finishes, A sees the result |

## 6. Design Decisions to Discuss

1. **merge node shape**: double ring? solid diamond with a crossbar? or some other shape that reads as "convergence" at a glance?
2. **post-merge lane**: land in the base branch lane (continue the mainline) or open a new lane? Leaning toward base.
3. **whether send_to_branch waits synchronously for a reply**: deliver by default (async) or wait for a reply (sync)? Leaning toward making it a parameter.
4. **cross-branch communication line style**: distinct from attach (dashed) and spawn (dash-dot) — what line style/color?
5. **the boundary between communication and merge**: send_to_branch delivers a single message vs merge converges an entire branch — do we need a "send multiple times, then merge" combined workflow?
6. **attended interception**: should inter-branch messaging and auto-merge require user confirmation by default (cross-branch side effects)?

## 7. Related Code (touched during implementation)

| Item | Location |
|---|---|
| merge engine | `openprogram/agent/_merge.py` `process_merge_turn` |
| merge WS action | `openprogram/webui/ws_actions/merge.py` |
| merge UI | `web/components/right-sidebar/branches/merge-modal.tsx` |
| attach parsing | `openprogram/webui/ws_actions/branch.py` `_attach_info` |
| DAG edges (attach_ref/spawn already present; add merge convergence line + communication line) | `web/lib/runtime-bridge/dag/render/edges.ts` |
| DAG shapes (add merge node shape) | `web/lib/runtime-bridge/dag/shapes.ts` |
| layout (merge node lane) | `openprogram/webui/graph_layout/{lane,__init__}.py` |
| new tool send_to_branch | create under `openprogram/functions/tools/` |
| verification | `tools/dag_dump.py` |
