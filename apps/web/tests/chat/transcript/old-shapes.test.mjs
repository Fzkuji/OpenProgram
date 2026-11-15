/** Every old record shape loads through the production mapper and row
 *  components, and shows the facts its case states and draws what they imply. */
import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { openStored } from "../../_support/transcript-replay.mjs";

const cases = JSON.parse(readFileSync(fileURLToPath(new URL("./old-shapes.json", import.meta.url)), "utf8"));

function read(row, path) {
  return path.split(".").reduce((value, key) => (value == null ? value : value[key]), row);
}

function check(view, { facts = [], drawn = [], steps = [] }) {
  for (const [id, path, value] of facts) assert.deepEqual(read(view.rows[id], path), value, `${id}.${path}`);
  for (const [id, text] of drawn) assert.ok(view.dom[id]?.includes(text), `${id} does not draw ${text}: ${view.dom[id]}`);
  for (const [id, expected] of steps) assert.deepEqual(view.steps[id], expected, `${id} draws these steps`);
}

const open = (frames, expected) => openStored(frames, { steps: Boolean(expected.steps) });

for (const shape of cases) {
  test(`old shape ${shape.name} draws its facts`, async () => {
    const view = await open(shape.frames, shape);
    for (const id of shape.rows) assert.ok(view.rows[id], `row ${id} is missing from ${view.ids.join(", ")}`);
    if (shape.order) assert.deepEqual(view.ids, shape.order);
    check(view, shape);
  });
  // What the design requires of this shape and current code does not do yet.
  for (const entry of shape.pending ?? []) {
    test(`old shape ${shape.name}: ${entry.finding} ${entry.rule}`, { todo: `${entry.finding} is not fixed yet` }, async () => {
      check(await open(shape.frames, entry), entry);
    });
  }
}

test("every old-shape todo still fails, so a fixed one loses its mark", async () => {
  const stale = [];
  for (const shape of cases) {
    for (const entry of shape.pending ?? []) {
      const view = await open(shape.frames, entry);
      try {
        check(view, entry);
        stale.push(`${shape.name}: ${entry.finding} ${entry.rule}`);
      } catch (error) {
        if (!(error instanceof assert.AssertionError)) throw error;
      }
    }
  }
  assert.deepEqual(stale, [], "these now hold, so move them out of pending in tests/support/transcript/old_shapes.py");
});
