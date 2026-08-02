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

registerHooks({
  resolve(specifier, context, nextResolve) {
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
  assert.match(nodesSrc, /spawnName \|\| "sub-agent"/,
    "an unnamed capsule still says what kind of thing it is");
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
  /shape === "spawn_capsule"[\s\S]{0,900}stroke-opacity/,
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

console.log("dag-subagent checks passed");
