# Research: what data model to use for agent execution traces — choosing span

Type: research / route selection (not a design doc; it does not cover how to implement, only "why take this route + the state of the field")
Date: 2026-06-19

## One-line conclusion

For the execution trace of a single agent task run (user messages, LLM calls, tool/function calls, nesting, loops),
**adopt the span data model** (id + parent_id + start/end + attributes + status, with parent_id linking them into a tree).
This is 15 years of industry consensus in the observability field, and the entire LLM-agent tracing community has already converged on it.
Our existing `Call` + `called_by` is already a half-baked span — the direction is right, we just need to straighten it out according to the span spec,
**without bringing in a heavyweight OTel SDK** — only align the data shape + attribute naming (`gen_ai.*`), preserving future interoperability.

## Why this needs research (our problem)

An agent system is essentially "a large model running code as an interpreter": the user gives a task → the large model repeatedly calls functions/tools →
the functions in turn call the large model (nesting + recursion). We need to record "what actually ran this time." The sticking points:
- **An LLM call must be one kind of node**; it must not split into two kinds depending on whether it was "triggered by the user" or "triggered by a function."
- **Calls are nested** (parent→child, with returns); **loops are siblings** (siblings under the same parent, not one calling the other).
- We need to be able to draw both the **chat line** (the time flow) and the **call tree** (the nesting).

This is exactly the problem the observability field solved long ago — one request passes through multiple services, with nested calls, and needs to be traced.
The shape is completely isomorphic to our agent.

## State of the field

### What field is this

**observability**, sub-field **distributed tracing**.
The three pillars: metrics (numeric statistics) / logs / **traces**. Spans live inside traces —
one trace = one span tree, one span = one operation with a start and end.

### History: it fragmented, then merged into a single standard

| Time | Event |
|---|---|
| 2010 | Google **Dapper** paper, defines span |
| 2012 | Twitter open-sources Zipkin |
| 2015 | Uber builds Jaeger; OpenTracing standard |
| 2018 | Google/Microsoft also create OpenCensus (**two standards fighting**) |
| 2019 | The two merge into **OpenTelemetry (OTel)**, the standards war ends |
| 2021 | OTel tracing spec v1.0 stabilizes |
| 2023-24 | OTel becomes the second most active CNCF project (after Kubernetes) |

What this means for a cautious selector: **this field has already been shaken out** (there were once competing standards), and OTel is what survived. It is not a new thing, not a gamble.

### Is OTel real consensus — yes

A CNCF project, built jointly by **AWS / Google / Microsoft / Datadog / Splunk / Honeycomb / Grafana / Dynatrace**.
These are companies that compete with each other, yet they maintain the same standard together — this is the strongest signal of "a real standard rather than hype."

### Competitors — almost all use span

| Solution | Uses span? |
|---|---|
| Commercial APM (Datadog / New Relic / Honeycomb / Lightstep) | All do, and are natively compatible with OTel spans; they differ only in storage/query |
| Chrome Trace / Perfetto (Google's other system) | Different lineage (browser/Android performance), but **also that "timed nested interval" span shape** |
| eBPF tracing (Pixie / Cilium) | Different layer (kernel-level); what they produce is also spans — it is a collection method, not a competing model |
| "Use only flat logs, no span tree" (early Honeycomb / Stripe) | **The only genuinely different philosophy**, but even its chief proponents later moved to span |

**For modeling "nested execution," span is whole-industry consensus, with no second credible model.**

### Decisive evidence: the LLM-agent tracing community has already converged on span

New tools built specifically for agent tracing all use span:

| Tool | Uses what |
|---|---|
| **OTel GenAI spec** | Official `gen_ai.*` attributes (model name, token counts…), with dedicated span conventions for LLM/tool/agent-step |
| **Langfuse** | observation tree (span/generation/event), natively ingests OTel spans |
| **Arize Phoenix** | Built directly on OTel (OpenInference conventions) |
| **LangSmith** (LangChain) | "run tree" — nested Runs with parent-child + start/end, **which is a span tree**, plus OTel interop |
| **OpenLLMetry / W&B Weave / Braintrust** | All OTel span |

For the problem we want to solve, they have all given the same answer: **one agent run = one span tree**.

## How the span model resolves our sticking points

```
span = { id, parent_id, name/kind, start, end, status, attributes, events[] }
```

| Our requirement | How span satisfies it |
|---|---|
| The large model is one node that does not change form | A span does not split based on "who called it" — this is an OTel iron rule; HTTP/internal-function/background-task are all spans, differing only in kind/attributes |
| Nested calls | `parent_id` points to the parent; the child interval nests inside the parent; return = span end |
| Loops are siblings | Multiple sibling spans under the same parent, ordered by time, with no parent-child relation between siblings — exactly "a loop is not a call" |
| Chat line + call tree | parent_id gives the tree; the start time gives the timeline; the same data, two views |
| Context references (reads) | Attached as the span's `events[]`, opening no extra child node and not polluting the tree |
| Async/background causality | OTel's `links` edge (we call it `caused_by`), for cases that are not strictly nested |

## Drawbacks of span (recorded honestly)

1. **fan-out overhead**: one span per small operation; with many agent loops the spans explode, requiring sampling/aggregation.
2. **The tree assumes clean parent-child**: when an agent has shared state, retries, or DAG flows (not a strict tree), the mapping is awkward — partially solved by `links`/`caused_by` edges; this is a known rough spot.
3. **token/cost/evaluation** are not native to span; they are attached via attributes (`gen_ai.*` is exactly what does this).

## Distance from the current state — very close

| Current state | span | Gap |
|---|---|---|
| `Call.id` | span id | Same |
| `called_by` | parent_id | Same (it is exactly this) |
| `role` | name/kind | Similar |
| `output` | status + attributes | Present |
| `seq` | start (ordering) | Roughly |
| `metadata.parent_id` (conversation order, hidden away) | siblings ordered by start, **this edge is not needed** | An extra one that should be deleted |
| `reads` (not yet enabled) | span events[] | Concept matches, implementation still to be filled in |

## Route recommendation

1. **Adopt the span data model** (id / parent_id / start-end / attributes / status).
2. **Align attribute naming toward OTel `gen_ai.*`** (model, token, cost), preserving the possibility of exporting to OTel in the future.
3. **Do not bring in a heavyweight OTel SDK** — borrow only the data shape, manage internal storage ourselves, and avoid binding to the SDK prematurely.
4. Straighten out the existing `Call` + `called_by` per the span spec: delete the conversation edge hidden in metadata (siblings ordered by time),
   straighten out the wire layer of role, attach reads as span events, and add a `caused_by` edge for async.

> To be verified (confirm the current version before citing): OTel's CNCF graduation status, and the stability tier of the GenAI semantic conventions — this area iterates fast.

## Relationship to the design doc

This document is a **selection study** (why go with span). The concrete data model + context retrieval + the implementation design for merging the two call paths
is in `docs/design/runtime/session-dag.md` (authoritative); the call-flow skeleton is in `agent-call-flow.md`.
