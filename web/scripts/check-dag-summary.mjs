// Guards the compaction capsule (dag/rendering.md §9): the fold pass
// itself is executed, not pattern-matched, because it is the piece that
// decides what the graph does and does not draw. Getting it wrong hides
// turns with no way back to them.
//
// The drawing side stays a source assertion — SVG geometry needs a
// browser to mean anything, and what matters here is that the shape,
// the pleats, and the click all key on the SAME field the fold does.
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";

// Extensionless relative imports between source modules (Node needs the
// extension; TypeScript and the Next build resolve them on their own).
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith(".") && !/\.[a-z]+$/.test(specifier)) {
      // Append to href, not to pathname: pathname is percent-encoded and
      // re-parsing it double-encodes any space in the repo path.
      const base = new URL(specifier, context.parentURL).href;
      const file = `${base}.ts`;
      const url = existsSync(fileURLToPath(file)) ? file : `${base}/index.ts`;
      return { url, shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});

const { _foldSummaries, coversIds } =
  await import("../lib/runtime-bridge/dag/passes/fold-summaries.ts");
// Namespace import, not destructuring: ``_summaryExpanded`` is a live
// ``let`` binding that ``setSummaryExpanded`` REPLACES, so a destructured
// copy would go stale the first time the test resets it.
const globals =
  await import("../lib/runtime-bridge/dag/store/globals.ts");
const { setSummaryExpanded, toggleSummaryExpanded } = globals;

const nodesPath = new URL("../lib/runtime-bridge/dag/render/nodes.ts", import.meta.url);
const shapesPath = new URL("../lib/runtime-bridge/dag/shapes.ts", import.meta.url);
const interactionPath = new URL("../lib/runtime-bridge/dag/render/interaction.ts", import.meta.url);
const inspectorPath = new URL("../lib/runtime-bridge/dag/render/inspector.ts", import.meta.url);
const pipelinePath = new URL("../lib/runtime-bridge/dag/pipeline.ts", import.meta.url);

const nodesSrc = readFileSync(nodesPath, "utf8");
const shapesSrc = readFileSync(shapesPath, "utf8");
const interactionSrc = readFileSync(interactionPath, "utf8");
const inspectorSrc = readFileSync(inspectorPath, "utf8");
const pipelineSrc = readFileSync(pipelinePath, "utf8");

/* ---- 1. the fold pass ---- */

// u0 a0 u1 a1 with a summary standing in for the first turn.
const graph = () => [
  { id: "sum1", role: "assistant", covers_ids: ["u0", "a0"] },
  { id: "u0", role: "user", predecessor: null },
  { id: "a0", role: "assistant", predecessor: "u0" },
  { id: "u1", role: "user", predecessor: "sum1" },
  { id: "a1", role: "assistant", predecessor: "u1" },
];

setSummaryExpanded(Object.create(null));

{
  const { visible, coversOf } = _foldSummaries(graph());
  const ids = visible.map((n) => n.id);
  assert.deepEqual(
    ids, ["sum1", "u1", "a1"],
    "folded by default: the covered range is elided and the capsule stays",
  );
  // Object.entries, not deepEqual: coversOf is a null-prototype map
  // (Object.create(null)), which strict deepEqual will not match against
  // an object literal.
  assert.deepEqual(
    Object.entries(coversOf), [["sum1", ["u0", "a0"]]],
    "coversOf is reported whether folded or not — the drawer needs it either way",
  );
}

toggleSummaryExpanded("sum1");
assert.equal(globals._summaryExpanded.sum1, true, "toggle opens the capsule");

{
  const { visible, coversOf } = _foldSummaries(graph());
  assert.deepEqual(
    visible.map((n) => n.id), ["sum1", "u0", "a0", "u1", "a1"],
    "expanded: every covered node comes back",
  );
  assert.deepEqual(
    Object.entries(coversOf), [["sum1", ["u0", "a0"]]],
    "coversOf is unchanged by the toggle",
  );
}

toggleSummaryExpanded("sum1");
assert.equal(globals._summaryExpanded.sum1, undefined, "toggling again folds it back");

{
  // A capsule must never be folded away by another capsule's range: it
  // is the only handle back to the turns it covers.
  setSummaryExpanded(Object.create(null));
  const nested = [
    { id: "s1", role: "assistant", covers_ids: ["s2", "u0"] },
    { id: "s2", role: "assistant", covers_ids: ["u0"] },
    { id: "u0", role: "user" },
  ];
  const { visible } = _foldSummaries(nested);
  const ids = visible.map((n) => n.id);
  assert.ok(ids.includes("s1") && ids.includes("s2"), "capsules survive each other");
  assert.ok(!ids.includes("u0"), "the covered turn is still folded");
}

{
  // No summaries → the graph must come through untouched, by identity:
  // every render of every uncompacted session runs this path.
  const plain = [{ id: "u0", role: "user" }];
  const out = _foldSummaries(plain);
  assert.equal(out.visible, plain, "no summary: the same array, no copy");
  assert.deepEqual(Object.keys(out.coversOf), [], "no summary: nothing covered");
}

assert.equal(coversIds({ id: "x" }), null, "a plain node covers nothing");
assert.equal(coversIds({ id: "x", covers_ids: [] }), null, "an empty range is no range");
assert.deepEqual(coversIds({ id: "x", covers_ids: ["a"] }), ["a"]);

/* ---- 2. shape / drawing / click all key on the same field ---- */

assert.match(
  shapesSrc,
  /covers_ids[\s\S]{0,120}return "capsule"/,
  "the capsule shape must be chosen by covers_ids, the field the fold reads",
);
assert.match(
  shapesSrc,
  /data-shape[\s\S]{0,80}capsule[\s\S]{0,200}return/,
  "the capsule rect must be tagged so _applyShapeSize leaves its geometry alone",
);
assert.match(
  shapesSrc,
  /ds === "capsule" \|\| ds === "diamond"\) return;/,
  "_applyShapeSize must skip the capsule (and the ROOT diamond) or a "
  + "coverage flip re-squares them",
);
assert.match(
  nodesSrc,
  /isCapsule && !capsuleOpen[\s\S]{0,700}stroke-opacity/,
  "the pleats are drawn only while the capsule is folded",
);
assert.match(
  nodesSrc,
  /"data-summary": isCapsule \? String\(covered\.length\) : ""/,
  "the node must publish its covered count for the click handler and inspector",
);
// The capsule is a wordless pill; the note beside it is what says how
// much it stands for. Without it the shape is just an odd-looking turn.
assert.match(
  nodesSrc,
  /isCapsule\) \{[\s\S]{0,400}已压缩 · \$\{covered\.length\} 轮/,
  "a folded capsule is annotated with the number of turns it replaced",
);
assert.match(
  nodesSrc,
  /class: "history-summary-label"/,
  "…as a caption in the canvas's annotation grey, not inside the pill",
);
// The compaction capsule keeps its own geometry: it is the ONE shape
// wider than the reference circle, and the layout treats it as one cell
// all the same (grid coordinates, ../layout/geometry.ts).
assert.match(
  shapesSrc,
  /x: -CAPSULE_HW, y: -CAPSULE_HH/,
  "the pill is centred on its grid point like every other glyph",
);
assert.match(
  pipelineSrc,
  /_foldSummaries\(graph\)[\s\S]{0,900}buildThreadModel\(graph\)/,
  "summaries fold BEFORE the thread pass so the two compose: a covered "
  + "turn is gone before threads attribute events to anchors",
);

/* ---- 3. interactions ---- */

assert.match(
  interactionSrc,
  /data-summary"\)\) \{\s*\n\s*toggleSummaryExpanded/,
  "clicking a capsule toggles its fold",
);
assert.match(
  interactionSrc,
  /toggleSummaryExpanded\(id\);[\s\S]{0,240}setLastSignature\(null\)/,
  "the fold toggle must bust the render signature or the repaint no-ops",
);
assert.match(
  interactionSrc,
  /addEventListener\("contextmenu"/,
  "right-click opens the node menu",
);
assert.match(
  interactionSrc,
  /gn\.role === "user"[\s\S]{0,160}forkAndEditNode\(gn\)/,
  "double-clicking a user turn is fork & edit",
);

// Every action must be an existing route. A new verb here is a protocol
// change hiding in a UI change.
assert.match(
  inspectorSrc,
  /fetch\("\/api\/chat\/checkout"/,
  "checkout / fork go through the existing checkout route",
);
assert.ok(
  // Calls only — the module's comments name /api/chat/edit to explain
  // which shape this path reproduces, which is the point, not a slip.
  !/fetch\(\s*"\/api\/chat\/(edit|retry)"/.test(inspectorSrc),
  "fork & edit is checkout + composer prefill, not a second edit endpoint",
);
assert.match(
  inspectorSrc,
  /setComposerInput\([\s\S]{0,40}\)[\s\S]{0,80}focusComposer\(\)/,
  "fork & edit must land the text in the composer and focus it",
);
assert.match(
  inspectorSrc,
  /const pivot = node\.predecessor;/,
  "fork & edit checks out the PREDECESSOR — the edit has to be a sibling",
);
assert.ok(
  // A `|| node.caller` fallback would send a spawn branch root
  // (predecessor=None, caller=<spawning node in the PARENT branch>)
  // checking out into a different branch entirely.
  !/const pivot = node\.predecessor \|\| node\.caller/.test(inspectorSrc),
  "fork & edit must not fall back to the caller (sub-call) edge",
);

console.log("dag-summary checks passed");
