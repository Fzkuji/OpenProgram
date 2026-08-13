// Guards the call-thread model (dag/rendering.md §12). Split like
// check-dag-summary: the thread pass and the geometry are EXECUTED,
// because they decide which nodes draw and where; the drawing side stays
// a source assertion, because SVG geometry needs a browser to mean
// anything.
//
// What it protects:
//   * one triangle = one turn's whole activity — followup replies and a
//     spawned agent's internal turns merge into their anchor;
//   * folded is folded: no thread items on screen, only a count on the
//     node's shoulder;
//   * open is open: every call a real node on the thread column, one
//     row per event in call order, agents recursing one column further;
//   * a fork branch runs parallel: sibling's row, two columns out,
//     dashed bridge.
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";

// ``@/x`` is the app's own tsconfig alias for ``web/x``; node has never
// heard of it, and the shape/geometry modules reach the palette through it.
const WEB_ROOT = new URL("../", import.meta.url);

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("@/")) {
      const base = new URL(specifier.slice(2), WEB_ROOT).href;
      const file = `${base}.ts`;
      const url = existsSync(fileURLToPath(file)) ? file : `${base}/index.ts`;
      return { url, shortCircuit: true };
    }
    if (specifier.startsWith(".") && !/\.[a-z]+$/.test(specifier)) {
      const base = new URL(specifier, context.parentURL).href;
      const file = `${base}.ts`;
      const url = existsSync(fileURLToPath(file)) ? file : `${base}/index.ts`;
      return { url, shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});

const { buildThreadModel, isSpawnRoot, isChainNode } =
  await import("../lib/runtime-bridge/dag/passes/thread.ts");
// Namespace import: ``_threadOpen`` is a live ``let`` that the setter
// REPLACES, so a destructured copy goes stale on the first reset.
const globals = await import("../lib/runtime-bridge/dag/store/globals.ts");
const { setThreadOpen, toggleThreadOpen } = globals;
const { computeGeometry } =
  await import("../lib/runtime-bridge/dag/layout/geometry.ts");
const { COL_W, ROW_H, PAD_X, PAD_Y } =
  await import("../lib/runtime-bridge/dag/types.ts");

const nodesSrc = readFileSync(
  new URL("../lib/runtime-bridge/dag/render/nodes.ts", import.meta.url), "utf8");
const shapesSrc = readFileSync(
  new URL("../lib/runtime-bridge/dag/shapes.ts", import.meta.url), "utf8");
const interactionSrc = readFileSync(
  new URL("../lib/runtime-bridge/dag/render/interaction.ts", import.meta.url), "utf8");
const inspectorSrc = readFileSync(
  new URL("../lib/runtime-bridge/dag/render/inspector.ts", import.meta.url), "utf8");
const pipelineSrc = readFileSync(
  new URL("../lib/runtime-bridge/dag/pipeline.ts", import.meta.url), "utf8");
const edgesSrc = readFileSync(
  new URL("../lib/runtime-bridge/dag/render/edges.ts", import.meta.url), "utf8");
const canvasSrc = readFileSync(
  new URL("../lib/runtime-bridge/dag/canvas.ts", import.meta.url), "utf8");

/* ---- 1. the thread pass ---- */

// The shape the real case had: one turn makes calls and spawns an
// agent; the agent runs its own turns and calls; the agent's return
// triggers a followup reply. Backend lane/tier/depth as the API stamps
// them.
const G = () => [
  { id: "ROOT", display: "root", _lane: 0, _tier: 0, _depth: 0,
    created_at: 0 },
  { id: "u0", role: "user", predecessor: "ROOT",
    _lane: 0, _tier: 1, _depth: 1, created_at: 1 },
  { id: "a0", role: "assistant", predecessor: "u0",
    _lane: 0, _tier: 2, _depth: 2, created_at: 2 },
  { id: "e1", role: "tool", display: "runtime", caller: "a0",
    _lane: 0, _tier: 3, _depth: 3, created_at: 3 },
  { id: "s1", role: "user", source: "agent_spawn", caller: "a0",
    spawned_from: { label: "后端架构" },
    _lane: 4, _tier: 0, _depth: 3, created_at: 4 },
  { id: "s1_reply", role: "assistant", predecessor: "s1",
    _lane: 4, _tier: 1, _depth: 4, created_at: 5 },
  { id: "s1e1", role: "tool", display: "runtime", caller: "s1_reply",
    _lane: 4, _tier: 2, _depth: 5, created_at: 6 },
  { id: "s1e2", role: "tool", display: "runtime", caller: "s1_reply",
    _lane: 4, _tier: 2, _depth: 6, created_at: 7 },
  { id: "e2", role: "tool", display: "runtime", caller: "a0",
    _lane: 0, _tier: 3, _depth: 4, created_at: 8 },
  { id: "f1", role: "assistant", source: "job_followup",
    predecessor: "a0", _lane: 0, _tier: 2, _depth: 7, created_at: 9 },
];

setThreadOpen(Object.create(null));

{
  const m = buildThreadModel(G());
  const ids = m.visible.map((n) => n.id);
  assert.deepEqual(
    ids.sort(), ["ROOT", "a0", "u0"],
    "folded is folded: chain only — no execution node, no spawn head, "
    + "and no followup reply anywhere on the chain",
  );
  assert.deepEqual(
    (m.events.a0 || []).map((e) => e.id), ["e1", "s1", "e2"],
    "the turn's thread is every call AND every spawn, one sequence, "
    + "in call order",
  );
  assert.deepEqual(
    (m.events.s1 || []).map((e) => e.id), ["s1_reply", "s1e1", "s1e2"],
    "the spawn's own thread carries the agent's TURNS and calls, one "
    + "sequence in time order — its replies come back as triangles "
    + "when the square opens, not deleted",
  );
  assert.equal(
    m.anchorOf("f1"), "a0",
    "a followup reply anchors to the turn that received the return",
  );
  assert.equal(
    m.anchorOf("s1_reply"), "s1",
    "an agent-internal turn anchors to the agent's head",
  );
}

{
  // Scarred data: an old bug rewound a second followup onto the spawn
  // turn instead of chaining it. Merged, the scar stops rendering — a
  // followup is not a chain node, so its predecessor cannot fork.
  const g = G();
  g.push({ id: "f2", role: "assistant", source: "job_followup",
    predecessor: "a0", _lane: 9, _tier: 0, _depth: 8, created_at: 10 });
  const m = buildThreadModel(g);
  assert.ok(
    !m.visible.some((n) => n.id === "f2"),
    "a scarred followup merges like any other — no orphan branch",
  );
  assert.equal(m.anchorOf("f2"), "a0");
}

toggleThreadOpen("a0");
{
  const m = buildThreadModel(G());
  const ids = m.visible.map((n) => n.id);
  assert.ok(
    ids.includes("e1") && ids.includes("e2") && ids.includes("s1"),
    "open is open: the turn's calls and its spawn head are real nodes",
  );
  assert.ok(
    !ids.includes("s1e1") && !ids.includes("s1_reply"),
    "the agent's own thread stays folded until the agent is clicked — "
    + "recursion, one level at a time",
  );
  assert.equal(m.nameOf.s1, "后端架构",
    "the agent's name rides the model for the tooltip and inspector — "
    + "the canvas draws no captions");
}

toggleThreadOpen("s1");
{
  const m = buildThreadModel(G());
  const ids = m.visible.map((n) => n.id);
  assert.ok(
    ids.includes("s1e1") && ids.includes("s1e2"),
    "the agent's thread opens into its real calls",
  );
}

setThreadOpen({ s1: true });
{
  const m = buildThreadModel(G());
  const ids = m.visible.map((n) => n.id);
  assert.ok(
    !ids.includes("s1") && !ids.includes("s1e1"),
    "an open agent under a folded turn shows nothing: visibility is the "
    + "whole ancestor chain's, not the node's own flag",
  );
}

setThreadOpen(Object.create(null));

assert.equal(
  isSpawnRoot({ id: "x", role: "user", source: "agent_spawn", predecessor: "p" }),
  false,
  "a node with a predecessor is inside a branch, not the root of one",
);
assert.equal(isSpawnRoot({ id: "x", role: "user" }), false);
assert.equal(
  isSpawnRoot({ id: "x", role: "user", source: "agent_spawn" }), true);
assert.equal(
  isChainNode({ id: "x", role: "assistant", function: "merge" }), true,
  "merge stays on the chain — it is a chain operation");
assert.equal(
  isChainNode({ id: "x", role: "tool" }), false);

/* ---- 2. geometry: lattice, thread placement, scene-3 forks ---- */

const layoutOf = (graph) => {
  const m = buildThreadModel(graph);
  const byId = Object.create(null);
  m.visible.forEach((n) => { byId[n.id] = { ...n }; });
  return { m, byId, geom: computeGeometry(byId, m) };
};

{
  setThreadOpen({ a0: true, s1: true });
  const { geom } = layoutOf(G());
  const { pos } = geom;

  // ① Every node on the layout's own 32px lattice — the background
  // dots ARE the coordinate system.
  for (const id of Object.keys(pos)) {
    assert.equal((pos[id].x - PAD_X) % COL_W, 0, `${id} off-grid in x`);
    assert.equal((pos[id].y - PAD_Y) % ROW_H, 0, `${id} off-grid in y`);
  }

  // ② The thread hangs ONE column right of its anchor, one row per
  // event, in call order.
  assert.equal(pos.e1.x, pos.a0.x + COL_W, "thread column = anchor + 1");
  assert.equal(pos.e1.y, pos.a0.y + ROW_H, "first event on the next row");
  assert.equal(pos.s1.x, pos.e1.x, "the spawn head sits ON the thread");
  assert.equal(pos.s1.y, pos.e1.y + ROW_H, "…in its sequence position");

  // ③ The open agent recurses one column further, and its rows push
  // the parent's later items down. Its own REPLY leads the nested
  // thread — a triangle in the agent's colour, back on screen.
  assert.equal(pos.s1_reply.x, pos.s1.x + COL_W, "nested thread = +1 more");
  assert.equal(pos.s1_reply.y, pos.s1.y + ROW_H);
  assert.equal(pos.s1e1.x, pos.s1_reply.x);
  assert.equal(pos.s1e1.y, pos.s1_reply.y + ROW_H);
  assert.equal(pos.s1e2.y, pos.s1e1.y + ROW_H);
  assert.equal(
    pos.e2.y, pos.s1e2.y + ROW_H,
    "expansion is insertion: the parent's next item continues below the "
    + "nested block, never on top of it",
  );

  // ④ Nothing overlaps.
  const taken = new Map();
  for (const id of Object.keys(pos)) {
    const key = `${pos[id].x},${pos[id].y}`;
    assert.ok(!taken.has(key), `${id} lands on ${taken.get(key)}`);
    taken.set(key, id);
  }
  setThreadOpen(Object.create(null));
}

{
  // Scene 3: a fork branch is a parallel version of the turn beside it
  // — sibling's row, one gap column out, so the dashed bridge is a
  // straight horizontal two grid units long.
  const g = [
    { id: "ROOT", display: "root", _lane: 0, _tier: 0, _depth: 0,
      created_at: 0 },
    { id: "u0", role: "user", predecessor: "ROOT",
      _lane: 0, _tier: 1, _depth: 1, created_at: 1 },
    { id: "a0", role: "assistant", predecessor: "u0",
      _lane: 0, _tier: 2, _depth: 2, created_at: 2 },
    { id: "u1", role: "user", predecessor: "a0",
      _lane: 0, _tier: 1, _depth: 3, created_at: 3 },
    { id: "b0", role: "user", predecessor: "a0",
      _lane: 7, _tier: 5, _depth: 4, created_at: 4 },
  ];
  const { geom } = layoutOf(g);
  const { pos, forkSibOf } = geom;
  assert.equal(forkSibOf.b0, "u1", "the fork's parallel sibling is found");
  assert.equal(
    pos.b0.y, pos.u1.y,
    "the branch shares its sibling's row — it extends RIGHT, level with "
    + "the turn it rewrites, never dangling below",
  );
  assert.equal(
    pos.b0.x, pos.a0.x + 3 * COL_W,
    "gap column + the fork lane's empty spine column: the branch root "
    + "lands three grid units out, stubbed one column right of the "
    + "spine the bridge lands on (per-lane tier zeroing eats the "
    + "backend's stale tier offset)",
  );
}

/* ---- 3. glyphs: the spawn square, the count, no captions ---- */

assert.match(
  shapesSrc,
  /agent_spawn[\s\S]{0,400}return "square"/,
  "the sub-agent glyph is chosen by the same field the thread pass "
  + "reads — and it is a SQUARE: dispatching an agent is a function "
  + "call, the call vocabulary",
);
assert.ok(
  !/agentCaption|AGENT_CAPTION|subagent-label/.test(shapesSrc + nodesSrc),
  "no caption machinery anywhere: the canvas carries the glyph and its "
  + "count, names live in the tooltip and inspector",
);
assert.match(
  nodesSrc,
  /threadCount && !threadOpen[\s\S]{0,300}history-thread-count/,
  "the fold marker is a COUNT on the node's shoulder — digits glued to "
  + "the glyph, no shape that could be read as a node",
);
assert.match(
  nodesSrc,
  /"data-thread": threadCount \? String\(threadCount\) : ""/,
  "the node publishes its thread size for the click handler and inspector",
);

/* ---- 4. interactions ---- */

assert.match(
  interactionSrc,
  /data-thread"\)\) \{\s*\n\s*toggleThreadOpen/,
  "clicking a node with a thread toggles it — chain turn or spawn head, "
  + "one vocabulary",
);
assert.match(
  interactionSrc,
  /toggleThreadOpen\(id\);[\s\S]{0,240}setLastSignature\(null\)/,
  "the fold toggle must bust the render signature or the repaint no-ops",
);
assert.match(
  inspectorSrc,
  /agent_spawn[\s\S]{0,300}子 agent · \$\{nm\}/,
  "the inspector titles a spawn root as the sub-agent it heads",
);
assert.match(
  pipelineSrc,
  /_foldSummaries\(graph, headId\)[\s\S]{0,900}buildThreadModel\(graph\)/,
  "threads fold after summaries so the two passes compose",
);
assert.match(
  pipelineSrc,
  /threadModel\.anchorOf\(headId\)/,
  "HEAD re-seats on its anchor when it points at a merged reply",
);

/* ---- 5. edges: centre-to-centre, the dotted thread, scene-3 bridge ---- */

assert.match(
  edgesSrc,
  /x2: c\.x, y2: c\.y/,
  "chain edges run centre to centre — the glyph's background fill covers "
  + "the line inside its outline, so no fixed stand-off can ever gap "
  + "against a sloped triangle side",
);
assert.match(
  edgesSrc,
  /x1: ap\.x, y1: ap\.y, x2: ap\.x, y2: lastY[\s\S]{0,220}thread-edge/,
  "the thread line is the trunk pattern one level down: a solid grey "
  + "vertical in the anchor's column plus a stub per item — down "
  + "first, then right, like every chain edge (§12)",
);
assert.match(
  edgesSrc,
  /forkSibOf\[id\][\s\S]{0,3000}fork-edge/,
  "a fork with a level sibling gets the straight dashed bridge",
);
assert.ok(
  !/spawn-edge(?!-)/.test(edgesSrc),
  "the S-curve spawn edge is gone — the spawn head sits ON its owner's "
  + "thread, and the thread line is its connection",
);

/* ---- 6. canvas: the lattice breathes, the wheel zooms ---- */

assert.match(
  canvasSrc,
  /1\.2 \* _viewScale/,
  "the lattice dot radius rides the zoom — fixed at 1.2px it vanishes "
  + "into a zoomed-in tile",
);

// The wheel triage (rendering.md gesture table): pinch and ⌘/ctrl+wheel
// zoom at pinch rate; a MOUSE wheel (legacy wheelDeltaY in notch
// multiples, or a non-pixel deltaMode) zooms about the cursor; every
// other wheel is a trackpad two-finger swipe and pans, both axes.
assert.match(
  canvasSrc,
  /e\.ctrlKey \|\| e\.metaKey[\s\S]{0,200}PINCH_ZOOM_RATE/,
  "a pinch (ctrlKey wheel) or ⌘+wheel zooms at the pinch rate",
);
assert.match(
  canvasSrc,
  /wheelDeltaY[\s\S]{0,200}% 120 === 0/,
  "mouse wheels are told from trackpads by the legacy wheelDeltaY notch "
  + "multiple — macOS scroll acceleration defeats every deltaY heuristic",
);
assert.match(
  canvasSrc,
  /setView\(_viewTx - e\.deltaX, _viewTy - e\.deltaY, _viewScale\)/,
  "trackpad two-finger swipe: pan, both axes",
);
assert.match(
  canvasSrc,
  /ZOOM_STEP = Math\.exp\(100 \* WHEEL_ZOOM_RATE\)/,
  "a HUD −/+ press is exactly one wheel notch of zoom, so button and "
  + "wheel stay one control",
);

/* ---- 6b. HUD: shared chrome, not a private lookalike ---- */

const dagViewSrc = readFileSync(
  new URL("../components/chat/dag-view.tsx", import.meta.url), "utf8");
const composerCss = readFileSync(
  new URL("../components/chat/composer/composer.module.css",
    import.meta.url), "utf8");
assert.match(
  dagViewSrc,
  /zoomStep\(-1\)[\s\S]*dag-hud-zoom[\s\S]*resetZoom\(\)[\s\S]*zoomStep\(1\)/,
  "the HUD zoom cluster is − · readout · + — steps zoom, and the "
  + "readout itself resets to 100%",
);
assert.match(
  dagViewSrc,
  /dag-legend-body \$\{MENU_PANEL\}/,
  "the legend popover wears MENU_PANEL — the one panel frame every "
  + "menu in the app shares",
);
assert.match(
  composerCss,
  /\.envChips :global\(\.dag-hud-chip\)/,
  "HUD chips are the env-pill rule itself (composer.module.css), "
  + "not a hand-rolled lookalike in the dag styles",
);

/* ---- 7. HEAD is a breathing glow on its own hollow glyph ---- */

assert.match(
  nodesSrc,
  /el\.setAttribute\("data-head", "1"\)/,
  "HEAD is said by the glow stamp on the glyph, not by a fill of its own",
);
assert.match(
  shapesSrc,
  /fill: "var\(--bg-primary/,
  "every glyph is hollow with the CANVAS colour, never transparent "
  + "— edges are drawn centre to centre and the fill is what buries the "
  + "line ends inside the outline",
);
const _cssSrc = readFileSync(new URL("../app/styles/dag/nodes.css",
  import.meta.url), "utf8");
assert.match(
  _cssSrc,
  /\[data-head="1"\][\s\S]*?dag-head-glow/,
  "the HEAD glyph carries the breathing glow animation",
);
assert.match(
  _cssSrc,
  /dag-head-glow[\s\S]*?drop-shadow\(0 0 [\d.]+px currentColor\)/,
  "the glow is a drop-shadow riding the glyph outline in the branch "
  + "colour — not a drawn halo ring, which reads as a second node",
);

console.log("dag-subagent checks passed");
