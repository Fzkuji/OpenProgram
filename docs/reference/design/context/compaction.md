# Context Compaction

Compaction keeps a long conversation inside the model's context window by
replacing the oldest turns with one LLM-written summary. This document is the
single authority on how compaction stores its result, how the result changes
what the model reads, how it composes with branches and with repeated
compaction, and which invariants protect it. The DAG's visual treatment of a
summary (the capsule) is specified in
[dag/rendering.md §9](../runtime/dag/rendering.md); this document defines the
data and semantics that rendering consumes.

## 1. The model: a rolling summary, exactly one active

A session has at most **one active summary** at a time. Compacting again does
not stack a second summary on top of the first: the summariser receives the
previous summary text as input, absorbs it, and produces a replacement. The
session's `extra_meta._last_summary_id` names the active summary;
`extra_meta._last_summary_text` carries its text for the next chaining. Every
older summary node stays on disk as an inert relic, flagged for the graph as
`superseded_summary` and never consulted again.

The next LLM request therefore always has the shape

```
[system prompt] [active summary] [kept tail, verbatim] [new user message]
```

— one summary, never a stack, followed by the turns it did not eat.

## 2. Data model: an append-only stand-in

Compaction writes exactly one node and mutates nothing:

| Field | Value |
|---|---|
| `id` | `summary_<hex>` |
| `role` | `llm`, `name = "context/summary"` |
| `output` | `[Previous conversation summary]\n<text>` |
| `predecessor` | predecessor of the FIRST covered node (the splice point) |
| `metadata.covers_ids` | ordered ids of the exact chain nodes it replaces |
| `metadata.compaction` | `true` |

Rules that follow from append-only:

- **No clones.** The kept tail keeps its ids and predecessors untouched. A
  cloned tail would mint a second id space every consumer must translate.
- **No edge rewrites.** The covered nodes stay on the chain exactly as
  written; the first kept node still points at the last covered node. The
  pre-compaction view is always reconstructible by ignoring the summary.
- **No head movement.** Compaction is a pure insert. HEAD stays on the branch
  tip it was on; the summary changes what a *render* of that branch produces,
  not which branch is active.
- **Ids, not seq intervals.** `covers_ids` is the record of what was
  summarised. A seq interval cannot express this in a DAG — seqs of sibling
  branches interleave, so any interval sweep drags dead forks into the
  coverage and its answer changes when HEAD moves. The interval form
  (`metadata.covers = [first_seq, last_seq]`) does not exist in this design;
  nothing reads or writes it.
- **`covers_ids` is a contiguous chain segment of real turns.** It never
  contains another summary node. When a re-compaction eats "the previous
  summary plus k more turns", the new node's `covers_ids` is the previous
  segment extended by those k turns' ids — coverage is always expressed in
  terms of the underlying conversation, and the old summary is retired via
  `_last_summary_id`, not via nesting.

## 3. The rendering rule: segment substitution

`render_context` (context/nodes.py) is the one place that decides what the
model reads, for chat and for `runtime.exec` alike. Compaction enters it as a
single rule:

> Let S be the session's active summary and L = `covers_ids(S)`, a contiguous
> segment of a conversation chain. When rendering from head H: **if every node
> of L lies on H's predecessor spine, drop L from the rendering and admit S at
> L's position** (S's own splice point — its `predecessor` — puts it exactly
> where the segment began). Otherwise render the spine raw.

Properties this buys, each of which is a requirement, not a side effect:

- **The summary reaches the prompt.** S is admitted by rule, not by hoping the
  spine walk stumbles onto a node nothing points to. The rendered id list for
  a compacted branch is `[ROOT, S, kept tail…]`.
- **Branch isolation is automatic.** A fork whose spine does not contain the
  whole covered segment — a retry from inside the covered range, a dead
  sibling from the same era — fails the ⊆ test and renders raw. Its context
  was never compacted, and it does not inherit a summary of turns it never
  had.
- **HEAD-independence of storage.** Checking out any branch, at any time,
  yields a deterministic rendering from data alone. No render result depends
  on where HEAD happened to be when something else ran.
- **Superseded summaries are invisible here.** Only the active summary is
  consulted; relics never elide anything.

The same rule, stated over the same `covers_ids`, drives the DAG's capsule
fold — the graph shows the folded capsule on exactly the branches whose
context carries the summary, and shows raw turns on branches that render raw.
One fact, two projections.

## 4. The compaction pipeline

`trigger_compaction` (manual `/compact`), auto-compact (budget ≥ 80% before a
turn) and reactive compact (provider overflow error) all run the same
`engine.compact` pipeline:

1. **Input is the rendered view, not the raw chain.** The history handed to
   the cut finder is exactly what the model currently reads: active summary
   first (if any), then the kept turns. Feeding the raw predecessor walk here
   re-summarises turns the previous summary already ate and produces a second
   summary with identical coverage.
2. **Cut.** `find_cut_index` picks the split so the kept tail fits
   `keep_recent_tokens` (default from budget policy), snapping forward to a
   user-turn boundary; the first element(s) of the rendered view — the
   previous summary, if present — always land on the covered side.
3. **Summarise.** The summariser writes the new summary from the covered
   slice, chaining `previous_summary` so nothing already summarised is lost.
4. **Persist.** One node, as specified in §2. The new `covers_ids` = previous
   segment (if a summary was covered) extended with the newly covered turns'
   ids. `_last_summary_id` / `_last_summary_text` move to the new node.
5. **Events.** `compaction_started` / `compaction_finished` (or
   `compaction_failed`) broadcast over the session channel; the finished event
   carries `summary_id`, counts and token deltas. Fewer than 4 history
   messages short-circuits with a user-visible `local_command` notice.

## 5. HEAD integrity

Compaction was one of several writers that could move HEAD as a side effect.
The design allows exactly one mover:

- **Single writer.** `SessionStore.set_head` is the only way HEAD changes,
  and it is called only by explicit user-facing moves: send-turn advance,
  retry/edit fork, checkout, rewind, branch delete. Compaction, session
  load, worker restart, model switch and meta saves never call it.
- **Append advances HEAD only on chain extension.** `append_message` moves
  HEAD to the new node only when the node's `predecessor` equals the current
  HEAD — the natural "conversation grew" case. Any other insert (a summary
  splice, a side-branch write, a relic) leaves HEAD alone. This replaces the
  old unconditional auto-advance plus per-caller snapshot/restore
  compensation.
- **Spawned turns never move HEAD.** A same-session sub-agent turn
  (task / message_branch) runs with `TurnRequest.advance_head=False`: the
  spawn branch opens without registering itself as head, and every write the
  inner dispatcher makes (branch root, placeholder, reply, finalize, error)
  is head-neutral. The transcript follows HEAD, so a stolen head switched
  the user's window to the agent's conversation mid-run and mixed the two
  dialogues. Cross-session sends still advance the target session's own
  head — there the turn IS that conversation growing.
- **The turn's head policy is one object.** `dispatcher/turn_writer.py`'s
  `TurnWriter` performs every chain write a turn makes and alone applies
  `advance_head`. The invariant is structural: inside the dispatcher
  package, `set_head` / `update_session(head_id=…)` appear only in that
  file (plus the manual function-run path in `forced_tool.py`, a
  user-initiated move by definition).
- **Mirrors are read-only.** The webui keeps an in-memory `conv` mirror
  (messages + head) for display. It is hydrated FROM the store and never
  written back: `save_meta` carries no `head_id`, and display-side rows that
  the mirror accumulates (e.g. the summary marker appended when a
  `compaction_finished` event is rendered into the transcript) can never
  become the store's head or new store rows. The store is upstream of the
  mirror, always, in both directions of a restart.

## 6. What the graph shows

Defined in [dag/rendering.md §9](../runtime/dag/rendering.md); the wire
contract from this side:

- The active summary row carries `covers_ids` — verbatim from
  `metadata.covers_ids`, extended with the caller subtrees of covered turns
  (a covered turn folds together with its tool calls), minus ids that no
  longer exist.
- Superseded summary rows carry `superseded_summary: true` and no
  `covers_ids`.
- The graph builder does no seq arithmetic and no head-dependent filtering;
  everything it says about coverage restates `covers_ids`.

## 7. Extension points

The rolling-single-summary policy matches the reference tools (Claude Code,
Codex CLI, Gemini CLI) and keeps the prompt-cache prefix stable. It is a
policy, not a property of the storage: every alternative compaction scheme
differs only in *which summaries count as active* (a policy field) and *how
the renderer substitutes them* (the §3 rule). The append-only stand-in node
is common to all of them, so switching schemes never migrates data:

- **Segmented summaries** (several compaction nodes kept live): N summary
  nodes covering disjoint chain segments; §3 applies per summary and the
  trunk carries N capsules. Replace `_last_summary_id` with an active set.
- **Nested summaries** (a summary of summaries): relax the "`covers_ids`
  names real turns only" rule to admit summary ids, and make substitution
  recursive.
- **External-memory schemes** (summary retrieved on demand instead of
  inlined): the node is stored identically; only the renderer stops inlining
  it.

Beneath all of these sits the contract that survives even a fully arbitrary
context — one assembled by retrieval, cross-branch selection, or any future
policy rather than a spine walk:

1. **The DAG is the ledger, not the context.** Nodes record what happened,
   append-only; context is a deterministic *view function* over them.
   Changing how context is built changes the view function, never the data.
   The renderer already deviates from the pure chain today (`render_range`,
   `expose`, attach/merge, memory prefetch) — each deviation is data, not
   hidden state.
2. **Provenance is mandatory.** Whatever the view function produces, the ids
   whose content actually entered a call's prompt are stamped on that call
   (`reads`). Replay, audit and the graph's per-node context marking depend
   on this record — not on the view function staying simple.

A summary node is the first instance of a *view node* — a node that stands in
for other content in renders. Retrieved memory snippets, injected documents
and cross-session references generalise it; the capsule's visual grammar
(stand-in in place, expand to see the original) is the generic treatment for
the class. No generic view-composition framework is built ahead of a concrete
second scheme.

## 8. Invariants and their tests

| Invariant | Where enforced / tested |
|---|---|
| After compaction, rendering the active branch yields `[ROOT, S, kept tail…]` — covered ids absent, S present | render_context tests; scenario suite |
| Compaction never moves HEAD | persister tests; scenario suite |
| A branch not containing the full covered segment renders raw | render_context branch-isolation tests |
| Re-compaction input contains no already-covered raw turns; new `covers_ids` extends the old segment | compaction pipeline tests |
| At most one row per session carries `covers_ids` on the wire; older summaries arrive `superseded_summary` | `test_graph_builder_covers.py` |
| `covers_ids` never names a node off the summarised chain (dead forks stay out) | `test_graph_builder_covers.py` |
| HEAD survives: worker restart, session load, model switch, meta save | scenario suite (`test_dag_mutation_scenarios.py`) |
| Store round-trip: no mirror row or mirror head ever writes back into the store | webui persistence tests |

The scenario suite runs these flows end-to-end on a real `SessionStore`
(chat → fork → checkout → compact → chat → compact → chat → restart-load),
checking head and rendering after every step — the class of cross-module
side-effect bug involved here does not show up in unit tests of the parts.

## Implementation status

Every section above is implemented:

- §2 node shape and §4 pipeline — `context/persistence.py`
  (`insert_summary_node`, `covered_chain_ids`, `rendered_history`); the
  `covers` seq interval no longer exists anywhere.
- §3 segment substitution — `render_context` in `context/nodes.py`
  (`active_summary`, `summary_covers_ids`).
- §5 HEAD integrity — the chain-extension append rule in
  `store/session/session_store.py`; `webui/persistence.py` `save_meta` strips
  `head_id` unconditionally and `save_messages` is gone; the CLI turn path
  writes rows through `db.append_message`.
- §6 graph contract — `webui/graph_builder.py`.
- §8 invariants — `tests/unit/test_compaction_covers.py`,
  `tests/unit/test_graph_builder_covers.py`,
  `tests/integration/test_dag_mutation_scenarios.py`.
