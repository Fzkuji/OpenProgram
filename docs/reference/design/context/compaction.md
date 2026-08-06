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

## 7. Invariants and their tests

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

Implemented today:

- Summary node shape of §2 including `covers_ids` (persister writes it; the
  legacy `covers` seq interval is still written alongside and still read by
  the dead elision path — both to be removed).
- Rolling `_last_summary_id` / `_last_summary_text` chaining (§1), including
  the summariser's `previous_summary` input.
- Graph contract of §6: active-only `covers_ids`, `superseded_summary` flag,
  capsule fold/expand placement per rendering.md §9.
- Compaction head snapshot/restore inside the persister (interim form of §5's
  append rule).

Not yet implemented — the refactor batch:

- **§3 segment substitution in `render_context`.** Today the spine walk never
  reaches the summary node and the covers-based elision never fires: a
  compacted branch still renders every covered turn raw and omits the summary
  entirely. This is the core gap; compaction currently has no effect on what
  the model reads.
- **§4 step 1.** `trigger_compaction` and auto/reactive compact feed the raw
  `get_branch` walk to the cut finder; re-compaction therefore re-summarises
  raw turns instead of the previous summary + tail, and produces duplicate
  coverage.
- **§4 step 4 coverage extension** (`covers_ids` = old segment + newly eaten
  turns) — falls out of fixing step 1.
- **§5 append rule.** `append_message` still auto-advances HEAD
  unconditionally; the persister compensates with snapshot/restore.
- **§5 mirror read-only.** `_save_session → save_meta` still forwards the
  mirror's `head_id` into `SessionStore.update_session`, and the mirror's
  transcript rows (including compaction markers) flow back through
  `save_messages`. This is the phantom head-move path.
- **`covers` seq-interval removal** (write in persister, read in
  `context/nodes.py`, exemption in the store append invariant) once §3 lands
  on `covers_ids`.
