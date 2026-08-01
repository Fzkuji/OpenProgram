# Task: implement `graphlib_mini.py`

Implement, without importing networkx:

  - `topo_sort(graph) -> list` — Kahn's algorithm over a dict
    node -> list of successors. Deterministic: at every step emit the
    alphabetically smallest node whose in-degree has reached zero
    (i.e. keep the ready set globally sorted, not a plain FIFO).
    Raise `ValueError("cycle")` on a cycle.
  - `shortest_path(graph, a, b) -> list | None` — BFS, node names as
    the tie-break, `None` when unreachable, `[a]` when `a == b`.
  - `components(graph) -> list[list]` — connected components treating
    edges as undirected, each component sorted, list sorted by first
    element.

`test_graphlib_mini.py` is the spec. Do not edit it.
Run `python -m pytest -q` until green.
