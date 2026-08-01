# Execution Trace Data Model — Choosing Span

> This document establishes why agent execution traces use the span data model:
> what the model is, what the alternatives are, and why span is the route.

## Conclusion

The execution trace of an agent task run — user messages, LLM calls,
tool/function calls, nesting, loops — uses the **span data model**: id +
parent_id + start/end + attributes + status, with parent_id linking spans into
a tree. Span is fifteen years of consensus in observability, and the LLM-agent
tracing community has converged on it. `Call` + `called_by` is already a span in
all but naming, so the work is alignment with the span spec rather than a
rewrite. No heavyweight OTel SDK is adopted; only the data shape and the
attribute naming (`gen_ai.*`) are aligned, which preserves interoperability.

## The problem

An agent system is a large model running code as an interpreter: the user gives
a task, the model repeatedly calls functions and tools, and the functions call
the model in turn (nesting plus recursion). The record must capture what
actually ran. Three requirements constrain the model:

- **An LLM call is one kind of node.** It must not split into two kinds
  depending on whether the user or a function triggered it.
- **Calls nest** (parent to child, with returns); **loops are flat** (siblings
  under one parent, not one calling the next).
- Both the **chat line** (time flow) and the **call tree** (nesting) must be
  drawable from the same record.

Observability solved this problem long ago: one request crosses several
services, with nested calls, and has to be traced. The shape is isomorphic to
the agent case.

## State of the field

### The field

**Observability**, sub-field **distributed tracing**. The three pillars are
metrics (numeric statistics), logs, and **traces**. Spans live inside traces:
one trace is one span tree, one span is one operation with a start and an end.

### History: fragmented, then unified

| Time | Event |
|---|---|
| 2010 | Google **Dapper** paper defines span |
| 2012 | Twitter open-sources Zipkin |
| 2015 | Uber builds Jaeger; OpenTracing standard |
| 2018 | Google/Microsoft create OpenCensus (two competing standards) |
| 2019 | The two merge into **OpenTelemetry (OTel)**; the standards war ends |
| 2021 | OTel tracing spec v1.0 stabilizes |
| 2023-24 | OTel becomes the second most active CNCF project, after Kubernetes |

For a cautious selector this matters: the field has already been through its
shakeout, competing standards included, and OTel is what survived. It is
neither new nor a bet.

### OTel is real consensus

A CNCF project built jointly by **AWS, Google, Microsoft, Datadog, Splunk,
Honeycomb, Grafana, and Dynatrace**. Companies that compete with each other
maintain the same standard together, which is the strongest available signal of
a real standard rather than hype.

### Alternatives — nearly all use span

| Solution | Uses span? |
|---|---|
| Commercial APM (Datadog / New Relic / Honeycomb / Lightstep) | All do, natively compatible with OTel spans; they differ only in storage and query |
| Chrome Trace / Perfetto | Different lineage (browser and Android performance), but the same timed-nested-interval shape as span |
| eBPF tracing (Pixie / Cilium) | Different layer (kernel level); what they produce is also spans — a collection method, not a competing model |
| Flat logs with no span tree (early Honeycomb, Stripe) | The one genuinely different philosophy, though its main proponents later moved to span |

For modeling nested execution, span is industry-wide consensus with no second
credible model.

### The LLM-agent tracing community has converged on span

Tools built specifically for agent tracing all use span:

| Tool | Model |
|---|---|
| **OTel GenAI spec** | Official `gen_ai.*` attributes (model name, token counts), with dedicated span conventions for LLM, tool, and agent-step |
| **Langfuse** | Observation tree (span/generation/event), natively ingests OTel spans |
| **Arize Phoenix** | Built directly on OTel (OpenInference conventions) |
| **LangSmith** (LangChain) | Run tree — nested Runs with parent-child plus start/end, which is a span tree, with OTel interop |
| **OpenLLMetry / W&B Weave / Braintrust** | OTel span |

They all answer the same question the same way: one agent run is one span tree.

## How span satisfies the requirements

```
span = { id, parent_id, name/kind, start, end, status, attributes, events[] }
```

| Requirement | How span satisfies it |
|---|---|
| The LLM node keeps one form | A span does not split by who called it — an OTel rule; HTTP, internal function, and background task are all spans, differing only in kind and attributes |
| Nested calls | `parent_id` points at the parent; the child interval nests inside the parent's; return is the span end |
| Loops are flat | Several sibling spans under one parent, ordered by time, with no parent-child relation between siblings — exactly "a loop is not a call" |
| Chat line plus call tree | parent_id gives the tree, start time gives the timeline; one dataset, two views |
| Context references (reads) | Attached as the span's `events[]`, opening no extra child node and leaving the tree clean |
| Async and background causality | OTel's `links` edge (named `caused_by` here), for cases that do not nest strictly |

## Drawbacks of span

1. **Fan-out cost.** One span per small operation; long agent loops produce many
   spans, so sampling or aggregation is needed.
2. **The tree assumes clean parent-child structure.** Shared state, retries, and
   DAG flows map awkwardly onto a strict tree. `links`/`caused_by` edges cover
   part of this; the rest is a known rough edge.
3. **Token, cost, and evaluation data are not native to span.** They attach as
   attributes, which is what `gen_ai.*` is for.

## Distance from the current model

| Current | span | Gap |
|---|---|---|
| `Call.id` | span id | Same |
| `called_by` | parent_id | Same edge |
| `role` | name/kind | Similar |
| `output` | status + attributes | Present |
| `seq` | start (ordering) | Approximately |
| `metadata.parent_id` (conversation order, stored in metadata) | siblings ordered by start; this edge is not needed | An extra edge to remove |
| `reads` (not yet enabled) | span events[] | Concept matches, implementation pending |

## Route

1. **Use the span data model**: id, parent_id, start/end, attributes, status.
2. **Align attribute naming with OTel `gen_ai.*`** (model, token, cost), keeping
   OTel export possible later.
3. **Do not adopt a heavyweight OTel SDK.** Borrow the data shape only, manage
   internal storage directly, and avoid binding to the SDK prematurely.
4. Align `Call` + `called_by` with the span spec: drop the conversation edge
   held in metadata (siblings order by time), settle the wire layer of `role`,
   attach reads as span events, and add a `caused_by` edge for async.

> To verify before citing, since this area iterates quickly: OTel's CNCF
> graduation status, and the stability tier of the GenAI semantic conventions.

## Relationship to the design doc

This is the selection study — why span. The concrete data model, context
retrieval, and the design that merges the two call paths live in
[`../runtime/dag/session-dag.md`](../runtime/dag/session-dag.md), which is
authoritative; the call-flow skeleton is in `agent-call-flow.md`.
