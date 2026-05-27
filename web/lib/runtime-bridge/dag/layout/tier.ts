/**
 * Layout step: tier (call-stack depth) handling.
 *
 * ``_tier`` is computed entirely on the backend in
 * ``webui/_graph_layout.py`` and arrives pre-populated on every node.
 * The front-end uses it in two places only:
 *
 *   1. ``pos(n)`` in ``render/nodes.ts`` adds ``tier * COL_W`` to the
 *      x coordinate so sub-call clusters hang off to the right.
 *   2. ``passes/merge-runs.ts`` shifts tier on the surviving cluster
 *      when a tool wrapper is folded away (see ``tierShift`` there).
 *
 * No standalone helper function lives here yet — this file exists to
 * mark where future tier-related logic should land (e.g. a future
 * "shift all tiers down by N when the root cluster is collapsed"
 * pass would live in passes/ and call into helpers here).
 */

export {};
