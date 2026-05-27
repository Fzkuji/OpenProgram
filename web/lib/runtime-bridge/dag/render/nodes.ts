/**
 * Renderer: node SVG drawing.
 *
 * The node-drawing logic currently lives inline inside ``render()`` in
 * ``../pipeline.ts`` (the third ``Object.keys(tree.byId).forEach``
 * block, the one that emits ``<g class="history-node">``). It is
 * tightly bound to the rest of the render pass — same ``pos()``
 * closure, same ``stableLeafOfNode`` capture, same ``cinfo`` /
 * ``internalSet`` / ``internalOwner`` reads — so splitting it out
 * cleanly requires defining a new "render context" parameter object.
 *
 * This file is reserved for that future split. For now it intentionally
 * exports nothing; the README + this docstring track where the code
 * lives so the next reorganisation pass knows what to move here.
 */

export {};
