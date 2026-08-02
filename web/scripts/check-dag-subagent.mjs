// Guards the sub-agent capsule (dag/rendering.md §12). Same split as
// check-dag-summary: the fold pass is EXECUTED, because it decides which
// nodes the graph draws at all, and the drawing side stays a source
// assertion, because SVG geometry needs a browser to mean anything.
//
// What it protects: a spawned agent's whole conversation collapses to one
// named capsule by default. Break the fold and 280 sub-agent nodes bury
// the four main-lane turns they belong to.
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

const { _foldSpawnBranches, isSpawnRoot } =
  await import("../lib/runtime-bridge/dag/passes/fold-spawn-branches.ts");
// Namespace import: ``_spawnExpanded`` is a live ``let`` that the setter
// REPLACES, so a destructured copy goes stale on the first reset.
const globals = await import("../lib/runtime-bridge/dag/store/globals.ts");
const { setSpawnExpanded, toggleSpawnExpanded } = globals;
// The caption arithmetic and the layout are both pure — no DOM — so both
// run for real here rather than being asserted at as source text.
const { agentCaption, agentCaptionText, AGENT_DOT_R } =
  await import("../lib/runtime-bridge/dag/shapes.ts");
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

/* ---- 1. the fold pass ---- */

// The shape the real case had: a main-lane turn spawns two sub-agents,
// each an ``agent_spawn`` root with its own chain, each pointed at by an
// attach node that carries the sub-agent's name.
const graph = () => [
  { id: "u0", role: "user", predecessor: null },
  { id: "a0", role: "assistant", predecessor: "u0" },
  { id: "atc1", role: "assistant", function: "attach", predecessor: "a0",
    attach_ref: "s1_reply", attach_label: "后端架构" },
  { id: "s1", role: "user", source: "agent_spawn", predecessor: null,
    caller: "a0" },
  { id: "s1_tool", role: "tool", predecessor: "s1" },
  { id: "s1_reply", role: "assistant", predecessor: "s1_tool" },
  { id: "atc2", role: "assistant", function: "attach", predecessor: "a0",
    attach_ref: "s2_reply", attach_label: "前端测试" },
  { id: "s2", role: "user", source: "agent_spawn", predecessor: null,
    caller: "a0" },
  { id: "s2_reply", role: "assistant", predecessor: "s2" },
  { id: "u1", role: "user", predecessor: "a0" },
];

setSpawnExpanded(Object.create(null));

{
  const { visible, branchOf, nameOf } = _foldSpawnBranches(graph());
  const ids = visible.map((n) => n.id);
  assert.ok(
    !ids.includes("s1_tool") && !ids.includes("s1_reply")
      && !ids.includes("s2_reply"),
    "folded by default: the sub-agents' own turns are elided",
  );
  assert.ok(
    ids.includes("s1") && ids.includes("s2"),
    "the spawn roots stay — they are the capsules, and the only way back",
  );
  assert.ok(
    ids.includes("u0") && ids.includes("a0") && ids.includes("u1"),
    "the main lane is untouched by the sub-agent fold",
  );
  assert.deepEqual(
    branchOf.s1.sort(), ["s1_reply", "s1_tool"],
    "branchOf reports the whole sub-branch, folded or not",
  );
  assert.deepEqual(branchOf.s2, ["s2_reply"]);
  assert.equal(
    nameOf.s1, "后端架构",
    "the capsule is labelled from the attach pointer that names the branch",
  );
  assert.equal(nameOf.s2, "前端测试");
}

{
  // Sessions that ran before the runner stamped labels have no
  // attach_label at all — the branch name the store recorded is what
  // keeps those capsules from reading as anonymous hex.
  const legacy = [
    { id: "a0", role: "assistant", predecessor: null },
    { id: "atc", role: "assistant", function: "attach", predecessor: "a0",
      attach_ref: "s1_reply" },
    { id: "s1", role: "user", source: "agent_spawn", predecessor: null,
      caller: "a0" },
    { id: "s1_reply", role: "assistant", predecessor: "s1",
      branch_name: "OpenProgram 后端调用链梳理" },
  ];
  const { nameOf } = _foldSpawnBranches(legacy);
  assert.equal(
    nameOf.s1, "OpenProgram 后端调用链梳理",
    "with no attach label, the capsule falls back to the recorded branch name",
  );
}

{
  // Nothing names it → the drawer's own "sub-agent" wording takes over,
  // which is still better than a hex id.
  const anon = [
    { id: "s1", role: "user", source: "agent_spawn", predecessor: null },
    { id: "s1_reply", role: "assistant", predecessor: "s1" },
  ];
  assert.equal(_foldSpawnBranches(anon).nameOf.s1, "");
  assert.equal(
    agentCaption("", 3, false), "sub-agent (3)",
    "an unnamed head still says what kind of thing it is",
  );
}

toggleSpawnExpanded("s1");
assert.equal(globals._spawnExpanded.s1, true, "toggle opens the capsule");

{
  const { visible } = _foldSpawnBranches(graph());
  const ids = visible.map((n) => n.id);
  assert.ok(
    ids.includes("s1_tool") && ids.includes("s1_reply"),
    "expanded: the sub-agent's lane comes back in full",
  );
  assert.ok(
    !ids.includes("s2_reply"),
    "each capsule folds on its own — opening one must not open the other",
  );
}

toggleSpawnExpanded("s1");
assert.equal(globals._spawnExpanded.s1, undefined, "toggling again folds it back");

{
  // HEAD inside a folded branch keeps that branch drawn: the graph may
  // never hide where the user is currently standing.
  const { visible } = _foldSpawnBranches(graph(), "s1_reply");
  const ids = visible.map((n) => n.id);
  assert.ok(
    ids.includes("s1_reply") && ids.includes("s1_tool"),
    "the branch holding HEAD stays visible even while folded elsewhere",
  );
  assert.ok(!ids.includes("s2_reply"), "the other branch still folds");
}

{
  // A nested spawn keeps its own capsule instead of vanishing into its
  // parent's fold — otherwise there is no handle to open it with.
  setSpawnExpanded(Object.create(null));
  const nested = [
    { id: "outer", role: "user", source: "agent_spawn", predecessor: null },
    { id: "outer_a", role: "assistant", predecessor: "outer" },
    { id: "inner", role: "user", source: "agent_spawn", predecessor: null,
      caller: "outer_a" },
    { id: "inner_a", role: "assistant", predecessor: "inner" },
  ];
  const { visible, branchOf } = _foldSpawnBranches(nested);
  const ids = visible.map((n) => n.id);
  assert.ok(ids.includes("outer") && ids.includes("inner"),
    "spawn roots survive each other's folds");
  assert.ok(!branchOf.outer.includes("inner_a"),
    "the outer branch does not swallow the inner agent's turns");
}

{
  // No spawns → the graph comes through by identity: this path runs on
  // every render of every ordinary session.
  const plain = [{ id: "u0", role: "user" }];
  const out = _foldSpawnBranches(plain);
  assert.equal(out.visible, plain, "no spawn: the same array, no copy");
  assert.deepEqual(Object.keys(out.branchOf), [], "no spawn: nothing folded");
}

assert.equal(
  isSpawnRoot({ id: "x", role: "user", source: "agent_spawn", predecessor: "p" }),
  false,
  "a node with a predecessor is inside a branch, not the root of one",
);
assert.equal(isSpawnRoot({ id: "x", role: "user" }), false);
assert.equal(
  isSpawnRoot({ id: "x", role: "user", source: "agent_spawn" }), true);

/* ---- 2. shape / drawing / click all key on the same field ---- */

assert.match(
  shapesSrc,
  /agent_spawn[\s\S]{0,200}return "agent_dot"/,
  "the sub-agent glyph is chosen by the same field the fold reads",
);
assert.match(
  shapesSrc,
  /shape === "agent_dot"[\s\S]{0,900}AGENT_DOT_INNER_R/,
  "the sub-agent dot draws a second, inner ring — that is what "
  + "distinguishes it from an ordinary node at a glance",
);
assert.ok(
  !/spawn_capsule|spawnCapsule|SPAWN_CAPSULE/.test(shapesSrc + nodesSrc
    + edgesSrc + pipelineSrc),
  "the sub-agent pill is gone from every module that drew or measured it: "
  + "a glyph sized to its own name is a glyph the layout has to negotiate "
  + "with, and that negotiation was the whole §12 failure",
);
assert.match(
  nodesSrc,
  /"data-spawn": isAgentDot \? String\(spawnBranch\.length\) : ""/,
  "the node publishes its branch size for the click handler and inspector",
);
assert.match(
  nodesSrc,
  /"data-spawn-name": spawnName/,
  "the node publishes the sub-agent's name so the inspector can title itself",
);
assert.match(
  nodesSrc,
  /isAgentDot[\s\S]{0,400}history-subagent-label/,
  "the dot is captioned by NAME — the fold's job is to say whose branch "
  + "this is",
);
assert.match(
  pipelineSrc,
  /_foldSummaries\(graph\)[\s\S]{0,600}_foldSpawnBranches\(graph, headId\)/,
  "spawn branches fold after summaries so the two passes compose",
);

/* ---- 3. interactions ---- */

assert.match(
  interactionSrc,
  /data-spawn"\)\) \{\s*\n\s*toggleSpawnExpanded/,
  "clicking a sub-agent capsule toggles its fold",
);
assert.match(
  interactionSrc,
  /toggleSpawnExpanded\(id\);[\s\S]{0,240}setLastSignature\(null\)/,
  "the fold toggle must bust the render signature or the repaint no-ops",
);
assert.match(
  inspectorSrc,
  /agent_spawn[\s\S]{0,300}子 agent · \$\{nm\}/,
  "the inspector titles a spawn root as the sub-agent it heads, not as a user turn",
);

/* ---- 4. the caption, and the grid the layout puts the dot on ---- */

// The caption is a label, and only a label: nothing in the layout is
// sized from it, which is the point of replacing the pill with a dot.
{
  assert.equal(
    agentCaption("后端架构", 14, false), "后端架构 (14)",
    "folded: the name, and the size of the chain behind it",
  );
  assert.equal(
    agentCaption("后端架构", 14, true), "后端架构",
    "expanded: just the name — the turns are on screen and countable, so "
    + "a count beside the head is noise",
  );
  assert.equal(
    agentCaption("x", 0, false), "x",
    "a branch with nothing behind the head has no count to show",
  );

  const huge = "名字".repeat(80);
  const cut = agentCaptionText(agentCaption(huge, 3, false));
  assert.ok(cut.endsWith("…"), "an over-long name is ellipsised");
  assert.ok(cut.length < huge.length, "…and is actually shorter");
}

/* ---- 5. layout: the lattice, the row claim, the reserved ink ---- */

// Two sub-agents spawned by the same turn, as the backend hands them
// over: same ``_depth`` (a spawn root sits on its call node's row),
// different lanes. ``pipeline.ts`` stamps ``_spawnHW`` — the caption's
// right reach in pixels — on each head before layout; 150 stands in for
// a measured name here.
const CAP_REACH = 150;
const twoAgents = () => ({
  ROOT: { id: "ROOT", display: "root", _lane: 0, _tier: 0, _depth: 0,
    created_at: 0 },
  u0: { id: "u0", role: "user", predecessor: "ROOT",
    _lane: 0, _tier: 1, _depth: 1, created_at: 1 },
  a0: { id: "a0", role: "assistant", predecessor: "u0",
    _lane: 0, _tier: 2, _depth: 2, created_at: 2 },
  s1: { id: "s1", role: "user", source: "agent_spawn", caller: "a0",
    _lane: 4, _tier: 1, _depth: 2, created_at: 3, _spawnHW: CAP_REACH },
  s1a: { id: "s1a", role: "assistant", predecessor: "s1",
    _lane: 4, _tier: 2, _depth: 3, created_at: 4 },
  s2: { id: "s2", role: "user", source: "agent_spawn", caller: "a0",
    _lane: 8, _tier: 1, _depth: 2, created_at: 5, _spawnHW: CAP_REACH },
  s2a: { id: "s2a", role: "assistant", predecessor: "s2",
    _lane: 8, _tier: 2, _depth: 3, created_at: 6 },
});

{
  const byId = twoAgents();
  const { pos, maxX } = computeGeometry(byId);

  // ① Every node on the layout's own 32px lattice. The canvas paints
  // its dot background at the same pitch and offset (dag/canvas.ts), so
  // a violation here is a node visibly between dots on screen.
  for (const id of Object.keys(pos)) {
    assert.equal(
      (pos[id].x - PAD_X) % COL_W, 0,
      `${id} is off the grid horizontally — the background lattice is the `
      + "coordinate system, and a node between dots reads as a mistake",
    );
    assert.equal(
      (pos[id].y - PAD_Y) % ROW_H, 0,
      `${id} is off the grid vertically`,
    );
  }

  // ② The chain reads downwards in time.
  assert.ok(pos.u0.y > pos.ROOT.y, "the first turn hangs below ROOT");
  assert.ok(pos.a0.y > pos.u0.y, "the reply hangs below its user turn");

  // ③ Two sub-agent heads never share a row, and read in call order —
  // side by side, two captions print through each other; that failure is
  // what the row claim exists to prevent.
  assert.notEqual(pos.s1.y, pos.s2.y, "two sub-agent heads never share a row");
  assert.ok(
    pos.s2.y > pos.s1.y,
    "the later spawn sits below the earlier one, so the two read "
    + "top-to-bottom in the order they were spawned",
  );

  // ④ Caption ink reserves no columns — lanes pack by tier, one gap
  // column apart, and the name flies over the next lane's EMPTY cells
  // (emptiness is what the row claims guarantee). Reserving columns for
  // text spread four branches across a whole page.
  assert.ok(
    pos.s2.x - pos.s1.x <= 5 * COL_W,
    "the second agent's lane packs tight against the first — no columns "
    + "are reserved for the caption's text",
  );

  // ⑤ The bounding box covers the last caption, so a fit never crops a
  // name off the right edge of the view.
  assert.ok(
    maxX >= pos.s2.x + CAP_REACH,
    "maxX reaches the rightmost caption's ink",
  );

  // ⑥ Nothing overlaps. The rule the whole pass is for.
  const taken = new Map();
  for (const id of Object.keys(pos)) {
    const key = `${pos[id].x},${pos[id].y}`;
    assert.ok(
      !taken.has(key),
      `${id} lands on ${taken.get(key)} at (${key}) — two nodes on one grid `
      + "point means one of them can never be clicked",
    );
    taken.set(key, id);
  }
}

// A lone sub-agent claims no extra row: with nothing to collide with,
// its head keeps the row the call tree gave it.
{
  const byId = twoAgents();
  delete byId.s2;
  delete byId.s2a;
  const one = computeGeometry(byId);
  const both = computeGeometry(twoAgents());
  assert.equal(
    one.pos.s1.y, both.pos.s1.y,
    "the first head's row is the same with or without a second agent — "
    + "the claim pass only ever pushes LATER heads down",
  );
}

// A fork root dodges caption rows: its dashed bridge is a horizontal
// line at its row, and a caption on that row would be struck through.
// With no caption in the way it keeps the row the tree gave it —
// scene 3's "fork shares the rewritten turn's row" stays intact.
{
  const byId = twoAgents();
  byId.f0 = { id: "f0", role: "user", predecessor: "a0",
    _lane: 20, _tier: 1, _depth: 2, created_at: 9 };
  const { pos } = computeGeometry(byId);
  assert.ok(
    pos.f0.y !== pos.s1.y && pos.f0.y !== pos.s2.y,
    "a fork root never sits on a sub-agent caption's row — its bridge "
    + "would strike the name through",
  );

  const clear = twoAgents();
  delete clear.s1; delete clear.s1a; delete clear.s2; delete clear.s2a;
  clear.f0 = { id: "f0", role: "user", predecessor: "a0",
    _lane: 20, _tier: 1, _depth: 2, created_at: 9 };
  const free = computeGeometry(clear);
  assert.equal(
    free.pos.f0.y, free.pos.a0.y,
    "with no caption in the way, a fork keeps its anchor's row (scene 3)",
  );
}

/* ---- 6. the dot's edges and its click target ---- */

assert.match(
  edgesSrc,
  /function _anchorReach[\s\S]{0,400}isSpawnRoot\(n\) \) *return AGENT_DOT_R|isSpawnRoot\(n\)\) return AGENT_DOT_R/,
  "an edge stops at the sub-agent dot's own radius — every glyph is now "
  + "centred on its grid point and reaches the same distance in every "
  + "direction, so the clip is symmetric",
);
assert.ok(
  !/fromLeft/.test(edgesSrc),
  "the asymmetric clip is gone with the pill it existed for",
);
assert.match(
  edgesSrc,
  /"stroke-dasharray": "5 4"[\s\S]{0,200}history-edge spawn-edge/,
  "the spawn edge is a dashed curve (§12)",
);
assert.match(
  edgesSrc,
  /const from = \{ x: srcRaw\.x \+ NODE_R \+ 2, y: srcRaw\.y \+ NODE_R \+ 3 \}/,
  "it leaves the caller's lower edge, clear of the ×N execution count "
  + "that sits to the node's right",
);
assert.match(
  nodesSrc,
  /r: isAgentDot \? String\(AGENT_DOT_R \+ 2\)/,
  "the dot's whole job is to be clicked, so its hit target covers it",
);
assert.ok(AGENT_DOT_R > 0, "the dot has a radius to be clipped and hit at");

/* ---- 7. HEAD is solid, with no halo ---- */

assert.match(
  nodesSrc,
  /_buildShapeEl\(_shapeFor\(node\), color, r, isHead\)/,
  "HEAD is drawn solid — where you are standing, said with weight",
);
assert.match(
  shapesSrc,
  /fill: solid \? color : "transparent"/,
  "the solid fill is the branch colour, not a separate accent",
);
assert.ok(
  !/\.history-node\.is-head\s*\{[^}]*(drop-shadow|filter|stroke)/.test(
    readFileSync(new URL("../app/styles/right-dock.css", import.meta.url),
      "utf8")),
  "no halo orbits the HEAD glyph: at this size a glow reads as a second, "
  + "blurrier node beside the first, and the solid fill already says it",
);
assert.match(
  readFileSync(new URL("../lib/runtime-bridge/dag/render/visibility.ts",
    import.meta.url), "utf8"),
  /data-solid"\) === "1"\) return;/,
  "the coverage fill leaves a solid HEAD alone — flipping it would "
  + "hollow out the one node that can never leave the context window",
);
assert.match(
  nodesSrc,
  /isHead && contextSet && contextSet\[id\]/,
  "…and HEAD's own coverage is punched out of the solid fill instead",
);

console.log("dag-subagent checks passed");
