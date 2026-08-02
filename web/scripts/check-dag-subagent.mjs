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
// The capsule's own size arithmetic and the layout that reserves room for
// it are both pure — no DOM — so both run for real here rather than being
// asserted at as source text.
const { spawnCapsuleLabel, spawnCapsuleHW, spawnCapsuleText, spawnCapsuleDX,
  CAPSULE_HW } = await import("../lib/runtime-bridge/dag/shapes.ts");
const { computeGeometry } =
  await import("../lib/runtime-bridge/dag/layout/geometry.ts");

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
    spawnCapsuleLabel("", 3, false), "▸ sub-agent (3)",
    "an unnamed capsule still says what kind of thing it is",
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
  /agent_spawn[\s\S]{0,120}return "spawn_capsule"/,
  "the sub-agent capsule shape is chosen by the same field the fold reads",
);
assert.match(
  shapesSrc,
  /shape === "spawn_capsule"[\s\S]{0,1400}stroke-opacity/,
  "the sub-agent capsule draws a second, inset outline — that is what "
  + "distinguishes it from a compaction capsule at a glance",
);
assert.match(
  nodesSrc,
  /"data-spawn": isSpawnCapsule \? String\(spawnBranch\.length\) : ""/,
  "the node publishes its branch size for the click handler and inspector",
);
assert.match(
  nodesSrc,
  /"data-spawn-name": spawnName/,
  "the node publishes the sub-agent's name so the inspector can title itself",
);
assert.match(
  nodesSrc,
  /isSpawnCapsule[\s\S]{0,400}history-subagent-label/,
  "the capsule is labelled by NAME — the fold's job is to say whose branch this is",
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

/* ---- 4. the capsule carries its name, and owns its row ---- */

// The pill is sized from the text it draws. Assert the arithmetic, not a
// pixel count: without a canvas ``_textWidth`` falls back to 8px/char, so
// the numbers below are the fallback's, and what matters is the relation.
{
  assert.equal(
    spawnCapsuleLabel("后端架构", 14, false), "▸ 后端架构 (14)",
    "folded: arrow, name, and the count it is hiding",
  );
  assert.equal(
    spawnCapsuleLabel("后端架构", 14, true), "▾ 后端架构 (14)",
    "expanded: the same label, the arrow turned down — the count is a "
    + "branch size, not a hidden-node count, so it stays either way",
  );
  assert.equal(
    spawnCapsuleLabel("x", 0, false), "▸ x",
    "a branch with nothing behind the root has no count to show",
  );

  const short = spawnCapsuleHW(spawnCapsuleLabel("ab", 1, false));
  const long = spawnCapsuleHW(spawnCapsuleLabel("a".repeat(20), 1, false));
  assert.ok(
    long > short,
    "a longer name makes a wider pill — the label lives inside it",
  );
  assert.ok(
    short >= CAPSULE_HW,
    "a capsule never shrinks below the plain compaction pill: it has to "
    + "read as the same silhouette",
  );

  // The pill grows to the RIGHT of its anchor, never around it: centred,
  // its extra width reaches back over the turn that spawned it, and a
  // capsule covering its own host reads as the host being inside the
  // sub-agent — backwards.
  assert.equal(
    spawnCapsuleDX(CAPSULE_HW), 0,
    "a pill no wider than a plain capsule sits exactly where one does",
  );
  assert.equal(
    spawnCapsuleDX(long) - (long - CAPSULE_HW), 0,
    "every pixel a capsule gains goes to its right: left edge = -CAPSULE_HW "
    + "for any name length, so it never reaches back over its host",
  );

  const huge = "名字".repeat(80);
  const cut = spawnCapsuleText(spawnCapsuleLabel(huge, 3, false));
  assert.ok(cut.endsWith("…"), "an over-long name is ellipsised");
  assert.ok(cut.length < huge.length, "…and is actually shorter");
  assert.ok(
    spawnCapsuleHW(cut) <= spawnCapsuleHW(spawnCapsuleText(
      spawnCapsuleLabel("名字".repeat(200), 3, false))) + 1,
    "past the cap the pill stops growing — a capsule is a glyph, not a "
    + "paragraph the lane has to make room for",
  );
}

// Two capsules spawned by the same turn: the backend gives both the same
// ``_depth`` (a spawn root sits on its call node's row) and different
// lanes. Drawn that way their pills, each a hundred-odd pixels wide and
// one lane apart, print straight through each other. Layout has to break
// the tie.
{
  const hw = spawnCapsuleHW(spawnCapsuleLabel("OpenProgram 后端调用链梳理", 1, false));
  const byId = {
    ROOT: { id: "ROOT", display: "root", _lane: 0, _tier: 0, _depth: 0 },
    a0: { id: "a0", role: "assistant", predecessor: "ROOT",
      _lane: 0, _tier: 2, _depth: 2 },
    cap1: { id: "cap1", role: "user", source: "agent_spawn", caller: "a0",
      _lane: 4, _tier: 1, _depth: 2, _spawnHW: hw, created_at: 1 },
    cap2: { id: "cap2", role: "user", source: "agent_spawn", caller: "a0",
      _lane: 8, _tier: 1, _depth: 2, _spawnHW: hw, created_at: 2 },
  };
  const { pos, maxX } = computeGeometry(byId);

  assert.notEqual(
    pos.cap1.y, pos.cap2.y,
    "two sub-agent capsules never share a row — side by side their names "
    + "overlap and neither is readable",
  );
  assert.ok(
    pos.cap1.y < pos.cap2.y,
    "they stack in the order they were spawned",
  );
  // Pill span, from the anchor: [-CAPSULE_HW, dx + hw].
  const dx = spawnCapsuleDX(hw);
  const span = (p) => [p.x - CAPSULE_HW, p.x + dx + hw];
  const [l1, r1] = span(pos.cap1);
  const [l2, r2] = span(pos.cap2);
  assert.ok(
    !(l1 < r2 && r1 > l2) || pos.cap1.y !== pos.cap2.y,
    "no two pills share both a row and an x range — the whole failure "
    + "this section exists for",
  );
  // The layout also has to reserve the columns the pill covers, or an
  // ordinary node in the next lane over lands inside the name.
  assert.ok(
    r1 <= l2 || pos.cap1.y !== pos.cap2.y,
    "a capsule's lane is widened to the columns its ink occupies, so the "
    + "lane packed next to it starts past the pill rather than inside it",
  );
  assert.ok(
    maxX >= r2,
    "the canvas is sized to the pill's right edge, not to the point it "
    + "is anchored at — otherwise the name is cut off by the viewport",
  );
  // The capsules push each other, never the lane they were spawned from:
  // dropping one to a spare row must not slide the whole conversation.
  const { pos: solo } = computeGeometry({
    ROOT: byId.ROOT, a0: byId.a0,
    cap1: { ...byId.cap1, _spawnHW: undefined },
  });
  assert.equal(
    pos.a0.y, solo.a0.y,
    "the main lane is not pushed around by the capsules' row claims",
  );
  assert.equal(
    pos.cap1.y, solo.a0.y,
    "the first capsule keeps the row the backend gave it (scene 10: a "
    + "spawn root sits on its call node's row) — only the second moves",
  );
}

// The pill runs several columns to the right of the point the layout
// placed it. A lane sized to the capsule's tier alone lets it spill over
// whatever the next lane put on that row — in the real session, the
// following turn's reply landed inside the pill, on top of the name.
{
  const hw = spawnCapsuleHW(spawnCapsuleLabel("OpenProgram WebUI 架构梳理", 1, false));
  const byId = {
    a0: { id: "a0", role: "assistant", _lane: 0, _tier: 2, _depth: 2 },
    cap: { id: "cap", role: "user", source: "agent_spawn", caller: "a0",
      _lane: 8, _tier: 1, _depth: 2, _spawnHW: hw, created_at: 1 },
    // The next lane's own node, on the capsule's row.
    next: { id: "next", role: "assistant", _lane: 13, _tier: 2, _depth: 2,
      created_at: 2 },
  };
  const { pos } = computeGeometry(byId);
  assert.ok(
    pos.next.x > pos.cap.x + spawnCapsuleDX(hw) + hw,
    "a node in the lane packed after a capsule starts past the pill's "
    + "right edge — not inside the name it is carrying",
  );
}

// One capsule has no one to collide with, so it keeps the row the backend
// put it on (scene 10: a spawn root sits on its call node's row).
{
  const byId = {
    a0: { id: "a0", role: "assistant", _lane: 0, _tier: 2, _depth: 2 },
    cap: { id: "cap", role: "user", source: "agent_spawn", caller: "a0",
      _lane: 4, _tier: 1, _depth: 2, _spawnHW: 90, created_at: 1 },
  };
  const { pos } = computeGeometry(byId);
  assert.equal(pos.cap.y, pos.a0.y, "a lone capsule stays on its call row");
}

// Expanded, the branch hangs off the capsule — pushing the capsule down a
// row has to take its lane with it, or the head detaches from its body.
{
  const byId = {
    a0: { id: "a0", role: "assistant", _lane: 0, _tier: 2, _depth: 2 },
    cap1: { id: "cap1", role: "user", source: "agent_spawn", caller: "a0",
      _lane: 4, _tier: 1, _depth: 2, _spawnHW: 90, created_at: 1 },
    cap2: { id: "cap2", role: "user", source: "agent_spawn", caller: "a0",
      _lane: 8, _tier: 1, _depth: 2, _spawnHW: 90, created_at: 2 },
    kid2: { id: "kid2", role: "assistant", predecessor: "cap2",
      _lane: 8, _tier: 2, _depth: 3, created_at: 3 },
  };
  const { pos } = computeGeometry(byId);
  assert.equal(
    pos.kid2.y, pos.cap2.y + 32,
    "an expanded branch moves with the capsule it hangs off",
  );
}

/* ---- 5. the name is drawn inside the pill, and edges stop at its edge ---- */

assert.match(
  nodesSrc,
  /isSpawnCapsule[\s\S]{0,900}"text-anchor": "middle"/,
  "the sub-agent capsule's name is centred INSIDE the pill — hung off the "
  + "right edge the way a compaction count is, two capsules' labels "
  + "overlap each other and each other's bodies",
);
assert.match(
  nodesSrc,
  /_buildShapeEl\(\s*\n?\s*_shapeFor\(node\), color, r, isSpawnCapsule \? spawnHW/,
  "the pill is built at the width its own label measured",
);
assert.match(
  edgesSrc,
  /function _anchorReach[\s\S]{0,400}_spawnHW/,
  "edges read the capsule's own width so they can land on its edge",
);
assert.match(
  edgesSrc,
  /fromLeft \? CAPSULE_HW : spawnCapsuleDX\(hw\) \+ hw/,
  "the reach is asymmetric because the pill is: its left cap sits where "
  + "any capsule's does, its right edge is a whole name away",
);
assert.ok(
  (edgesSrc.match(/_clipToX\(/g) || []).length >= 5,
  "every edge that can terminate on a capsule clips to its edge — a line "
  + "aimed at the centre of a wide pill runs through its whole name",
);

console.log("dag-subagent checks passed");
