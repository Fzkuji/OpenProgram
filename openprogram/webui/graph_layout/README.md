# DAG layout pipeline

Backend layout for the History panel. Each chat session's nodes are
annotated with `_depth` (row), `_lane` (column), `_tier` (call-stack
depth) so the frontend renderer doesn't have to re-derive topology.

## Stages

Each stage is a small, single-purpose module — keep them under ~150
lines, swap implementations independently:

| File | Stage | Reads | Writes |
|---|---|---|---|
| `filter.py` | strip non-DAG noise (microcompact summaries) | raw graph | filtered graph |
| `topology.py` | build adjacency maps from the two edge types | filtered graph | conv_children + call_children dicts |
| `tier.py` | call-stack depth — peer via conv, +1 via caller | adjacency | `_tier` per node |
| `depth.py` | y-row — conv: int+1, sub-call: caller + 1 + k*step | adjacency + tier | `_depth` per node |
| `lane.py` | x-column allocation, trunk = lane 0, retries fork | adjacency + head | `_lane` per node |
| `reflow.py` | overlap detection + adaptive lane reassignment | (lane, depth, tier) | mutated `_lane` |

Entry point: `annotate_graph(graph_entries, head_id)` in `__init__.py`
runs the stages in order.

## Edge model recap

Two parent edges, mutually exclusive per node:

* **`parent_id`** — conversation edge (user/assistant chain). Retry
  siblings (multiple conv-children of one node) fork into new lanes.
* **`caller`** — sub-call edge (assistant → tool → sub-llm). Sub-calls
  inherit caller's lane + tier offset; **never** a branch tip.

See `docs/design/dag-edge-split.md` for the schema rationale.

## Adaptive reflow

Without reflow, two big sub-call clusters under different callers on
the same lane will visually overlap if their depth ranges intersect
(common when assistant N has 30 tools and assistant N+1 also has 30,
all stacked under tier-1 right of lane 0). `reflow.py` detects these
collisions in (effective_x, depth) space and pushes the later cluster
to a fresh lane.
