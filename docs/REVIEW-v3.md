# Review of `CONTEXT-v3`

## 1. Is v3 clear and complete enough?

`v3` is clearer than `v1` and `v2` at the conceptual level. The core idea is easy to grasp: each agentic function owns a context node, child calls build a tree, and later LLM calls can read prior sibling summaries.

It is not complete enough as an implementation spec yet. The main gaps:

- Rule 2 is confusing: "pass parent Context by reference, same object" conflicts with `parent_ctx.child(...)`. The real model seems to be "pass a parent handle; child creates its own node linked to that parent".
- The `Context` schema is too small for the behavior shown later. `Traceback` and the examples already assume fields that do not exist: `parent`, call args, status, start/end time, duration, maybe an id/order index.
- Lifecycle is undefined: when is a child attached, when is it marked complete, how are exceptions captured, and how are retries represented?
- Persistence is underspecified. `ctx.input = {"image": img}` is not obviously serializable to JSONL.

So: good design note, not yet a tight spec.

## 2. Does `sibling_summaries()` make sense for parallel functions?

It makes sense for sequential siblings. It does not fully solve parallel context passing.

Problems:

- "previous siblings" assumes a stable order. In parallel execution, completion order is nondeterministic.
- If siblings share the same parent object concurrently, `children` becomes a shared mutable structure with race and ordering issues.
- Direct sibling visibility creates hidden coupling. A child can behave differently depending on which other branch finished first.

Recommended rule:

- `sibling_summaries()` should expose immutable summaries of completed siblings only.
- For true fan-out/fan-in parallelism, do not treat it as live sibling-to-sibling communication. Let the parent join the branches, aggregate their outputs, then pass a merged summary/result to downstream children.

In short: good for sequential steps, insufficient as the only mechanism for parallel branches.

## 3. Is the Level system (`trace` / `detail` / `summary` / `result`) well-defined?

Not yet.

What is clear:

- `trace`: full debug view
- `detail`: input/output view
- `summary`: one-line human/LLM summary
- `result`: return-value-shaped output

What is still ambiguous:

- Does `level` control what is stored, what is exposed, or both? That distinction matters a lot. If default is `summary`, do we lose trace data entirely?
- Whose level applies during `sibling_summaries()`? The sibling's declared level, the caller's requested level, or both?
- Is the ladder monotonic? Can every `trace` be deterministically reduced to `detail`, `summary`, and `result`?
- What happens when `output` is huge? `result` is described as "only return value", but a return value may still be too large for context passing.

I would restore the split from `v2`: one field for recording/internal fidelity, one field for exposure. A single `level` is too overloaded.

## 4. Any practical issues when implementing this?

Yes.

- Async/thread safety: shared parent context mutation needs a concurrency model.
- Ordering: children need deterministic ordering by call sequence, not just append-on-finish.
- Token growth: `sibling_summaries()` needs a budget, truncation rule, or rolling compaction.
- Summary generation: who writes the summary? template, function author, or LLM? Without this, `summary` quality will be inconsistent.
- Errors: `error: str` is too weak. You likely need status plus structured exception info.
- Retries/loops: repeated calls to the same child name need attempt ids or sequence numbers.
- Media handling: images/screenshots should be stored by reference/path/artifact id, not inline in generic `input`.

## 5. What's missing?

Minimum missing pieces before implementation:

- `parent` reference
- call args / params
- `status` (`running` / `success` / `error`)
- timing (`start`, `end`, `duration`)
- stable child order / id / attempt id
- explicit summary field or summarizer hook
- separate record-vs-expose policy
- token-budget / pruning policy
- media serialization rules
- defined parallel join semantics

## Bottom Line

`v3` is a better explanation of the idea, but it over-compresses the execution model. The main thing to fix is to make the runtime semantics precise again: node lifecycle, exposure policy, ordering, and parallel behavior. Without those, two implementations of `v3` will likely behave differently in important ways.
