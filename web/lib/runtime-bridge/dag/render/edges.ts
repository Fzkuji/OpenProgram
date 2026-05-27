/**
 * Renderer: edge SVG drawing.
 *
 * Three edge kinds, all currently drawn inline inside ``render()`` in
 * ``../pipeline.ts``:
 *
 *   * ``parent_id`` (conv chain) — solid coloured curve, branch-coloured.
 *   * ``attach_ref`` (function="attach" / "merge") — dashed marching-ants
 *     overlay from the source branch tip to the attach-pointer node.
 *   * spawn — dot-dash neutral grey from a ``function="task"`` node
 *     to the conv root of the spawned sub-branch.
 *
 * Edge drawing reuses the same ``pos()`` / ``_branchColor`` /
 * ``_edgePath`` helpers as the node block, so splitting cleanly needs
 * the same render context. Reserved for a future split — see
 * ``./nodes.ts`` for the same situation.
 */

export {};
