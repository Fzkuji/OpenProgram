# Session-DAG v2 — closing the model/implementation gaps

Status: Decision 1 IMPLEMENTED (e1293cc0, 2026-08-02; old session data wiped
at cutover). Decisions 2-4 approved, pending implementation.

Decision 1 implementation notes (semantics fixed during landing):
- The session's first node and explicit root forks carry the sentinel
  `predecessor="ROOT"` (not empty) — retrying the first message creates a
  legitimate ROOT-level sibling that the append invariant must admit.
- Besides the two designed exceptions, ask_user answer nodes and compaction
  (`summary_`/`k_`) nodes are exempt from the invariant until Decision 4
  legalises compaction.
- `get_branch` on a spawn branch now stops at the spawn root (previously it
  leaked into the parent branch via the caller edge). Matches the clean-
  context spawn semantics; the chat view of a spawn branch no longer shows
  parent history. Companion to `session-dag.md` (v1 model, still authoritative for
everything not amended here) and the two audits of 2026-08-02 (context-cost
audit + paper-readiness audit). Each section states the problem, the decision,
and the rejected alternative.

The v1 story is "the session DAG is the single source of truth; every LLM call's
context is a rendering of one path through it." Four gaps break that story
today:

| # | Gap | Kind |
|---|-----|------|
| G1 | `predecessor` lives in metadata; writers can forget it, readers can't rely on it | implementation debt |
| G2 | `render_context` ignores edges; branch isolation is a post-hoc set intersection in engine.py | implementation debt |
| G3 | The system prompt (and memory prefetch) never enters the DAG; three assemblies disagree | design blank |
| G4 | Compaction violates the model's own axioms (orphan summary roots, `k_` clones, smuggled role) | design conflict |

## Decision 1: `predecessor` becomes a schema field; spawn becomes a store primitive

**Problem.** The conversational edge is a `metadata["predecessor"]` key.
`Graph.from_dict` silently drops a top-level `predecessor`; three spawn entry
points each remember (or forget) to pass `spawn_caller`; `get_branch` compensates
with a three-tier guessing chain (predecessor → caller → seq stitching).

**Decision.**
- `Call` gains a top-level `predecessor: str | None`. Serialization writes it
  top-level only — no metadata mirror. There is NO backward compatibility:
  pre-v2 session data is wiped wholesale at cutover (user decision,
  2026-08-02) rather than migrated, so no fallback read path exists.
- Write-side invariant, enforced in the store's append path: every ROOT-level
  conversational node MUST carry a predecessor, except (a) the session's first
  node and (b) spawn branch roots. A violating append raises; it does not
  silently produce a ROOT fork.
- `SessionStore.spawn_branch(caller_node_id, *, source, name=...)` becomes the
  single way to open a spawn branch: it creates the branch root
  (predecessor=None, caller=caller_node_id, metadata.source), registers the
  head, and returns the branch handle. The three existing entry points
  (`task.py`, `agent/task/runner.py`, `agent_collab/message_branch.py`) call it
  instead of hand-assembling nodes. New spawn call sites cannot get the edge
  wrong because they never touch edges.
- `get_branch` walks edges only. The caller/seq guessing chain is deleted
  outright (no legacy flag): a walk that cannot proceed raises with the
  offending node id. `list_branches`' main-tip walk is likewise edge-pure.

**Rejected:** keeping metadata storage with a validating linter. Validation
after the fact cannot un-corrupt a mislinked branch; the field must be
first-class so the type system and the append path enforce it.

## Decision 2: path selection moves into the rendering primitive

**Problem.** `render_context(graph, head_seq, ...)` selects by seq window +
expose only. Engine then intersects the result with `get_branch` output and
patches leaks (placeholder exclusion, in-branch caller re-admission) by hand.
The paper's central claim — "context is a rendering of one path" — is
implemented as "context is a rendering of a time window, minus what a second
pass throws out".

**Decision.**
- New signature: `render_context(graph, head_id, frame_entry_seq, render_range)`.
  The primitive walks the predecessor chain from `head_id` to the root, then
  admits, for every node on that spine: its caller-subtree filtered by the
  frame/expose rules (unchanged from v1). Seq ordering remains the sort key but
  no longer the membership test.
- Membership rule, stated once: **a node is in the rendering iff its nearest
  ROOT-level ancestor (via caller) is on the predecessor spine of `head_id`,
  and frame/expose admit it.** This single sentence replaces the engine-side
  intersection, the placeholder exclusion walk, and the caller re-admission
  patch, all of which are deleted.
- `engine._build_messages_from_dag` shrinks to: resolve head → call the
  primitive → hand nodes to `render_dag_messages`. No set algebra.
- The primitive is pure: no disk writes. Large-node spill-to-disk moves out of
  the read path (see Decision 4's render manifest).

**Rejected:** keeping selection in the engine and "documenting it". Two layers
each owning half of the membership rule is exactly what produced the leak
patches; artifact reviewers will read the primitive first.

## Decision 3: the constant prefix enters the DAG; one assembly, one budget

**Problem.** v1 Decision 6 ordered a project-wide unified system prompt but
never landed: dispatcher assembles and sends one prompt, engine budgets a
different (never-sent, 1.7k-token) one, exec runtime assembles a third. Memory
prefetch is appended to the system prompt per call, which changes the prefix
every turn and destroys cross-turn provider cache on the entire message
history (the single largest avoidable cost found by the context audit).

**Decision.**
- **One assembler.** `context.build_system_prompt(agent_profile, tools, mode)`
  becomes the only producer. Dispatcher stops self-assembling
  (`_with_tool_runtime_prompt` folds into the assembler as a layer); exec
  runtime calls the same assembler with its profile; the budget counts the
  exact string that ships. A test pins assembler-output == wire-output.
- **The prompt is recorded, not implied.** Whenever the assembled prompt's
  hash changes (session start, toolset change, plan-mode toggle), the store
  appends a `role=code` node `name="context/system_prompt"`, caller=ROOT,
  output=the full text, on the current branch. Rendering pins the latest such
  node on the spine as the wire system message. Replay of any historical call
  now reproduces the prompt that was actually sent — v1 could not.
  No fourth role is introduced; `context/*` names are reserved and hidden from
  the chat transcript view (same mechanism that hides `summary_` nodes today).
- **Memory prefetch moves out of the system prompt.** Prefetched memory is
  rendered as a prefix block inside the *current user node's* wire message and
  stored in that node's metadata (`memory_prefetch`), so (a) the system prompt
  and tools segment is byte-stable across turns — history cache-hits again —
  and (b) replay sees exactly what the model saw. The block is not aged; it
  dies with its turn like any other user content.

**Rejected:** treating the system prompt as out-of-band config forever. That
is v1's status quo; it makes the "single source of truth" claim false and
budget/replay unfixable.

## Decision 4: compaction becomes a legal graph rewrite; rendering becomes replayable

**Problem.** `insert_summary_node` creates a parentless summary root (violates
single-connectedness), clones the kept tail as `k_` copies (violates
append-only), and smuggles a system role through metadata. Rendering applies
aging/truncation policies *at read time* with disk side effects, so the same
graph renders differently on different days — the "we can replay any call"
claim is false.

**Decision.**
- **Summary nodes join the chain.** A summary node is `role=llm`,
  `name="context/summary"`, `predecessor = predecessor of the first node it
  covers`, `metadata.covers = [first_seq, last_seq]`. The head moves to a node
  whose predecessor is the summary node. No `k_` clones: the kept tail is not
  copied — rendering, walking the spine through the summary node, skips nodes
  whose seq falls inside `covers` and keeps everything after. Compaction is
  thereby append-only: two appended nodes (summary + new head link), zero
  clones, zero orphans, and the old spine remains intact as a sibling branch
  for rollback exactly as before.
- **Aging becomes ratchet + recorded.** The TAIL_TURNS boundary advances only
  at turn commit (never mid-turn), and each llm node records
  `metadata.render_manifest = {policy_version, aged_before_seq, spilled: [...]}`
  at the moment the call is made. Replaying a call = rendering with the
  manifest's policy, not today's. This also fixes the cache-prefix breakage of
  a per-call rolling boundary (context audit #6).
- **Spill-to-disk moves to the write path.** A node larger than the threshold
  is spilled when it is *recorded* (once, deterministic), not when it happens
  to be rendered. The read path becomes side-effect-free, which Decision 2
  already requires.
- **One pipeline, loud failures.** The DAG render is the only context
  pipeline. The commit-chain and legacy-microcompact fallbacks are deleted;
  if the render raises, the turn fails visibly with the error, and the
  decision log records it. (The Tier-3 dead-code bug survived precisely
  because a silent fallback hid it.)

**Rejected:** legalising `k_` clones by documenting them. Clones create a
second id space that every consumer (UI, Context tab, replay) must translate;
`covers` gives the same semantics with zero duplication.

## Consequences for the paper

- The method section can state the membership rule of Decision 2 verbatim.
- Claim "non-destructive, replayable rendering" becomes true (manifests,
  write-path spill, append-only compaction).
- Claim "single source of truth" becomes true (Decision 3 puts the last
  out-of-band context into the graph).
- E4 (branch/spawn experiments) measures enforced semantics, not heuristics.

## Implementation order

1. Decision 1 (schema + spawn primitive) — everything else depends on reliable
   edges. Includes migration-on-read + write invariant + tests.
2. Decision 2 (path-native rendering) — delete engine set algebra.
3. Decision 3 (one assembler, prompt nodes, prefetch relocation) — independent
   of 1/2, can proceed in parallel; contains the dominant cost fix.
4. Decision 4 (compaction rewrite, manifests, single pipeline) — last, on top
   of 1+2.

Each step lands with the existing unit suite green plus new tests pinning the
invariants (append rejection, membership rule, assembler==wire, replay
byte-equality on a recorded session).
