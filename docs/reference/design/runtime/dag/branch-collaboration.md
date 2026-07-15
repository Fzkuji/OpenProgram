# Branch Collaboration Design Doc (Communication · Service · Merge)

Status: **draft (pending discussion; user authorized continued progress before going to sleep)** · Created: 2026-06-29

> Goal: make different branches in the DAG more than just "parallel universes" — let them **collaborate**: one branch sends a message to another, one branch does work on behalf of another, and the results of two branches merge into one. This doc takes stock of the current state (much of it is already implemented), fills the gaps, and defines how merge/communication nodes are drawn in the DAG viewport.
>
> Prerequisites: the edge model (caller + predecessor) is covered in `session-dag.md`; the authoritative spec
> for layout and edges is in `dag-rendering.md` (includes 12 scenarios).

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

## 1.5. Edge Visual Rules

> Handed off to `dag-rendering.md` section 3 (the color=branch, line-style=type
> orthogonality iron rule + the line-style table + communication lines hidden by
> default). This doc no longer maintains a copy.

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

## 3. DAG Drawing of the Merge Node

> Handed off to `dag-rendering.md` scenario 10. The two former "to confirm" items are
> now ruled (2026-07-10): merge node shape = **double ring ◎** (the graph's unique
> convergence shape); merge node lane = **lands in the base branch lane** (Option A, the
> post-merge mainline continues base). The existing ruling that the attach pointer node
> is not drawn in the viewport (only the convergence line is) is likewise recorded in
> scenarios 10/11.

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

1. ~~merge node shape~~ **Ruled**: double ring ◎ (`dag-rendering.md` scenario 10).
2. ~~post-merge lane~~ **Ruled**: lands in the base branch lane (Option A).
3. **whether send_to_branch waits synchronously for a reply**: deliver by default (async) or wait for a reply (sync)? Leaning toward making it a parameter.
4. ~~cross-branch communication line style~~ **Ruled**: dotted `1 5`, target branch's lane color, hidden by default and shown on hover (`dag-rendering.md` section 3).
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
