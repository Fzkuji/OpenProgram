# Branch Collaboration (Communication · Service · Merge)

> Branches in the DAG are more than parallel universes — they **collaborate**: one
> branch sends a message to another, one branch does work on behalf of another, and
> the results of two branches merge into one. This document defines the three
> collaboration modes and how their nodes relate to the graph.
>
> Prerequisites: the edge model (caller + predecessor) is covered in `dag/overview.md`;
> the authoritative spec for layout and edges is in `rendering.md`.

## 1. What a branch is, and what collaborating means

A **branch** is a `(session_id, head_id)` pair — the same abstraction within a session
and across sessions. Branching itself is not a special operation: checkout moves HEAD,
and the next user turn naturally becomes a sibling.

Three things branches can do to each other:

| Mode | Meaning |
|---|---|
| **communication** | branch A delivers a message into branch B and optionally waits for B's reply |
| **service** | branch A dispatches sub-branch B to do a piece of work, and B's result flows back into A |
| **merge** | two or more branches converge into one continuation |

Merging and embedding are backed by one engine: `merge_branches` writes N attach
pointers plus one merge assistant node, with `commit_parents = [target prior, *peers]`
(multi-parent). Attach is the embedding primitive it builds on — one branch's content
enters another point as an attach pointer that expands into an
`[Attached from "label"]` block.

Edge visual rules live in `rendering.md` section 3 (color = branch, line style =
type; the line-style table; communication lines hidden by default). This document keeps
no copy of them.

## 2. Three Collaboration Modes

### Mode 1: Inter-branch messaging (communication)

**Scenario**: branch A's LLM wants to ask branch B's LLM something, or push a piece of information to B.

**Mechanism**: the agentic tool `send_to_branch`:

```
send_to_branch(target_branch, message) -> the other side's reply (optional wait)
```

- `target_branch`: the target branch's head_id (or branch name)
- `message`: the content to send
- Behavior: append a user node at the end of the target branch (`source="from_branch"`, annotated with the source branch); the target branch's LLM sees it on its next turn and replies; optionally wait synchronously for the other side's reply and return it to the caller.

**DAG drawing**: from the initiating branch's LLM node, a **communication line**
(dotted `1 5`, distinct from other line styles) points at the newly added user node on
the target branch. The line's color uses the **target branch's lane color** (color
always = branch, type is conveyed only by line style). This line is a "communication
edge," not a caller/predecessor structural edge — it is used only for rendering and does
not enter lane/depth computation.

> On the data side: the new user node on the target branch has predecessor = the target branch tip (normal conversation chain), plus `metadata.from_branch = the initiating branch's node id`, from which the render layer draws the communication dashed line.

### Mode 2: Sub-branch serving the main branch (dispatch → merge-back)

**Scenario**: main branch A dispatches a sub-branch B to do something (look something up / run a tool), and B hands the result back to A when done.

**Mechanism**: this is the "branch version" of the `/task` sub-agent, built on spawn + attach:
1. A's LLM calls `spawn_branch(task)` → creates sub-branch B (forks a new lane); B runs independently
2. When B finishes, its tip is embedded back into A via **attach**: an attach pointer points at B's tip and expands into an `[Attached from "B"]` block that enters A's context
3. On its next turn, A's LLM sees B's output and continues

**DAG drawing**: the spawn edge (dash-dot, task node → sub-branch root) plus the
attach_ref dashed line (sub-branch tip → attach node) express this. Sub-branch B is an
independent lane (per the layout rules, starting at A's lane rightmost column +1, with
its own vertical line).

### Mode 3: Branch merge (convergence)

**Scenario**: two branches each produced their own results, and they merge into one. This is the case that determines how the merge node is drawn.

**Two kinds of merge** (both offered by MergeModal):
- **equal merge**: N branches are peers, and the merge produces a **new merge node** as the new tip. The merge node has N parents (convergence).
- **attach-into-★ (in-place merge)**: pick one base branch; the rest attach into base, and base continues downward without producing a standalone merge node (this is the multi-peer version of Mode 2).

**Data model of the merge node**:
- the merge is a `role=assistant` node (the LLM synthesizes a reply from each branch's output)
- its `predecessor` = the base branch's tip (the main conversation chain parent)
- each additional "branch being merged in" is expressed via an **attach pointer node**: one attach pointer per peer (`predecessor=target_head`, `attach.head_id=peer tip`)
- `commit_parents = [target prior commit, *peer commit ids]` (multi-parent, for provenance)

**DAG drawing**: `rendering.md` scenario 10 is authoritative. The merge node shape
is a **double ring ◎**, the graph's unique convergence shape, and it lands in the base
branch lane — the post-merge mainline continues base. The attach pointer node itself is
not drawn in the viewport; only the convergence line is.

## 3. The messaging tool

```python
@function(name="send_to_branch")
def send_to_branch(target_branch: str, message: str, wait_reply: bool = False) -> str:
    """Send a message to another branch.
    target_branch: target branch head_id or branch name
    message: content
    wait_reply: if True, synchronously wait for the target branch's LLM reply and return it; if False, just deliver
    """
```

Design points:
- append a user node at the end of the target branch: `predecessor=target branch tip`, `source="from_branch"`,
  `metadata.from_branch=caller node id`
- `wait_reply=True`: trigger a turn on the target branch, wait for the assistant reply, return its text
- safety: sending a message is a side effect (writing into another branch); in attended mode it is interceptable by the policy layer (hooks into the event layer `tool.before`, see the proactive design)
- DAG: the render layer reads `metadata.from_branch` to draw the cross-branch communication dashed line (its own line style, distinct from attach/spawn)

## 4. Open questions

1. **whether send_to_branch waits synchronously for a reply**: deliver by default (async) or wait for a reply (sync)? Leaning toward making it a parameter.
2. **the boundary between communication and merge**: send_to_branch delivers a single message vs merge converges an entire branch — do we need a "send multiple times, then merge" combined workflow?
3. **attended interception**: should inter-branch messaging and auto-merge require user confirmation by default (cross-branch side effects)?

## 5. Related Code

| Item | Location |
|---|---|
| merge engine | `openprogram/agent/internals/_merge.py` `process_merge_turn` |
| merge WS action | `openprogram/webui/ws_actions/merge.py` |
| merge UI | `apps/web/components/right-sidebar/branches/merge-modal.tsx` |
| attach parsing | `openprogram/webui/ws_actions/branch.py` `_attach_info` |
| DAG edges | `apps/web/lib/runtime-bridge/dag/render/edges.ts` |
| DAG shapes | `apps/web/lib/runtime-bridge/dag/render/shapes.ts` |
| layout (merge node lane) | `openprogram/webui/graph_layout/{lane,__init__}.py` |
| send_to_branch tool | to be created under `openprogram/programs/tools/` |
| verification | `scripts/dag_dump.py` |

## Appendix: Implementation Status

| Capability | State | Location |
|---|---|---|
| fork (branching) | implemented | `message-actions.tsx` branch() / checkout |
| branch abstraction (`(session_id, head_id)`) | implemented | `ws_actions/merge.py` |
| merge backend (attach pointers + merge node + multi-parent commit) | implemented | `ws_actions/merge.py` + `agent/_merge.py` |
| merge UI (equal merge vs attach-into-★, plus a merge instruction) | implemented | `merge-modal.tsx` |
| attach (embedding) | implemented | `_merge.py` + `branch.py` `_attach_info` + generator |
| attach edges (attach_ref dashed line, source tip → attach node) | implemented | `dag/render/edges.ts` |
| worktree merge (a separate mechanism: git worktree ff-only file merge) | implemented | `worktree-item.tsx` |
| merge node drawing (shape, lane, convergence lines) | specified in `rendering.md` scenario 10 | `dag/render/shapes.ts`, `dag/render/edges.ts` |
| inter-branch messaging (`send_to_branch`) | not yet built | to be added |
| sub-branch service chain (spawn_branch → attach merge-back) | partial: /task sub-agents exist; the merge-back still needs wiring | reuses merge |
