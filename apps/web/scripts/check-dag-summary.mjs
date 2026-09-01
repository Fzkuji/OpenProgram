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
const WEB_ROOT = new URL("../", import.meta.url);

// Extensionless relative imports between source modules (Node needs the
// extension; TypeScript and the Next build resolve them on their own).
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("@/")) {
      const base = new URL(specifier.slice(2), WEB_ROOT).href;
      const file = `${base}.ts`;
      const url = existsSync(fileURLToPath(file)) ? file : `${base}/index.ts`;
      return { url, shortCircuit: true };
    }
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

// u0 a0 u1 a1 with a summary standing in for the first turn. The
// kept tail keeps its own edges (u1 <- a0); the summary hangs at the
// segment's start with nothing pointing at it (context/compaction.md).
const graph = () => [
  { id: "sum1", role: "assistant", predecessor: null, covers_ids: ["u0", "a0"] },
  { id: "u0", role: "user", predecessor: null },
  { id: "a0", role: "assistant", predecessor: "u0" },
  { id: "u1", role: "user", predecessor: "a0" },
  { id: "a1", role: "assistant", predecessor: "u1" },
];

setSummaryExpanded(Object.create(null));

{
  const { visible, coversOf } = _foldSummaries(graph(), "a1");
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
  const { visible, coversOf } = _foldSummaries(graph(), "a1");
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
  // covers_ids only ever names real turns (context/compaction.md §2),
  // but two capsules naming the same turn must still never fold each
  // other away.
  const nested = [
    { id: "s1", role: "assistant", covers_ids: ["u0"] },
    { id: "s2", role: "assistant", covers_ids: ["u0"] },
    { id: "u0", role: "user" },
  ];
  const { visible } = _foldSummaries(nested, "u0");
  const ids = visible.map((n) => n.id);
  assert.ok(ids.includes("s1") && ids.includes("s2"), "capsules survive each other");
  assert.ok(!ids.includes("u0"), "the covered turn is still folded");
}

{
  // No summaries → the graph must come through untouched, by identity:
  // every render of every uncompacted session runs this path.
  const plain = [{ id: "u0", role: "user" }];
  const out = _foldSummaries(plain, "u0");
  assert.equal(out.visible, plain, "no summary: the same array, no copy");
  assert.deepEqual(Object.keys(out.coversOf), [], "no summary: nothing covered");
}

{
  // The wire list adds caller subtrees so the fold hides a turn's
  // tools with it. Those ids are not on the predecessor chain; the
  // apply test must still see the carrying branch.
  setSummaryExpanded(Object.create(null));
  const withTools = [
    { id: "sum1", role: "assistant", predecessor: null, covers_ids: ["u0", "a0", "tool1"] },
    { id: "u0", role: "user", predecessor: null },
    { id: "a0", role: "assistant", predecessor: "u0" },
    { id: "tool1", role: "tool", caller: "a0", predecessor: "a0" },
    { id: "u1", role: "user", predecessor: "a0" },
    { id: "a1", role: "assistant", predecessor: "u1" },
  ];
  const { visible } = _foldSummaries(withTools, "a1");
  const ids = visible.map((n) => n.id);
  assert.deepEqual(
    ids, ["sum1", "u1", "a1"],
    "caller subtrees in covers_ids must not make the carrying branch inert",
  );
  assert.ok(!ids.includes("tool1"), "the covered tool folds with its turn");
  assert.ok(!visible.find((n) => n.id === "sum1")?._summaryInert,
    "the capsule on the carrying branch is not inert");
}

assert.equal(coversIds({ id: "x" }), null, "a plain node covers nothing");
assert.equal(coversIds({ id: "x", covers_ids: [] }), null, "an empty range is no range");
assert.deepEqual(coversIds({ id: "x", covers_ids: ["a"] }), ["a"]);

{
  // Per-branch application (dag/rendering.md §9): viewed from a branch
  // that does NOT contain the whole covered segment, nothing folds,
  // the turns keep their colour, and the capsule arrives inert.
  setSummaryExpanded(Object.create(null));
  const forked = [
    { id: "sum1", role: "assistant", predecessor: null, covers_ids: ["u0", "a0"] },
    { id: "u0", role: "user", predecessor: null },
    { id: "a0", role: "assistant", predecessor: "u0" },
    { id: "f0", role: "user", predecessor: "u0" },        // fork from inside
    { id: "f0r", role: "assistant", predecessor: "f0" },
  ];
  const { visible } = _foldSummaries(forked, "f0r");
  const byId = Object.fromEntries(visible.map((n) => [n.id, n]));
  assert.ok(byId.u0 && byId.a0, "other branch: covered turns stay visible");
  assert.ok(!byId.u0._ghost && !byId.a0._ghost, "other branch: no ghost marking");
  assert.equal(byId.sum1._summaryInert, true, "other branch: the capsule is the inert one");

  // Sticky expansion: those turns were just on screen raw, so switching
  // back to the carrying branch finds the range OPEN (ghosts), not
  // snapped shut again. The capsule's click still folds it manually.
  const back = _foldSummaries(forked, "a0");
  const backById = Object.fromEntries(back.visible.map((n) => [n.id, n]));
  assert.equal(backById.u0 && backById.u0._ghost, true,
    "returning to the carrier keeps the seen range open");
  setSummaryExpanded(Object.create(null));
}

{
  // Expanded on the carrying branch: covered turns come back as ghosts.
  setSummaryExpanded({ sum1: true });
  const { visible } = _foldSummaries(graph(), "a1");
  const byId = Object.fromEntries(visible.map((n) => [n.id, n]));
  assert.equal(byId.u0._ghost, true, "expanded: covered turns are ghosts");
  assert.ok(!byId.u1._ghost, "the kept tail is never a ghost");
  assert.ok(!byId.sum1._ghost && !byId.sum1._summaryInert,
    "the applying capsule is neither ghost nor inert");
  setSummaryExpanded(Object.create(null));
}

/* ---- 2. shape / drawing / click all key on the same field ---- */

assert.match(
  shapesSrc,
  /covers_ids[\s\S]{0,120}return "capsule"/,
  "the capsule shape must be chosen by covers_ids, the field the fold reads",
);
assert.match(
  shapesSrc,
  /_svg\("circle", \{ r, "data-shape": "capsule"/,
  "the capsule is a circle on the reference radius — one grid slot, "
  + "same footprint as every other glyph",
);
assert.match(
  nodesSrc,
  /isCapsule && !capsuleOpen[\s\S]{0,500}history-thread-count/,
  "the folded capsule wears the shoulder count, same language as §12",
);
assert.match(
  nodesSrc,
  /"data-summary": isCapsule && !isInert \? String\(covered\.length\) : ""/,
  "the node must publish its covered count for the click handler and inspector",
);
// The capsule carries no text caption: the glyph (double circle) says
// what it is, the shoulder count says how much, and the
// inspector/tooltip carry the details.
assert.ok(
  !/history-summary-label/.test(nodesSrc),
  "the capsule draws no caption text on the canvas",
);
// The inner ring is the "condensed turns" cue, drawn in the branch
// colour like the outer stroke — out-of-context is said by the missing
// white fill and the dashes, never by draining the colour.
assert.match(
  nodesSrc,
  /CAPSULE_RING[\s\S]{0,120}stroke: color/,
  "the capsule's inner ring is drawn by the node drawer in branch colour",
);
assert.match(
  pipelineSrc,
  /_foldSummaries\(graph, headId\)[\s\S]{0,900}buildThreadModel\(graph(?:, headId)?\)/,
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
