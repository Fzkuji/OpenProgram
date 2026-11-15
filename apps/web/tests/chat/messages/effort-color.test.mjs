// The compact composer shows only the fire glyph, so each thinking level must
// be told apart by colour alone.
import assert from "node:assert/strict";
import { test } from "node:test";

import { effortLevelColor } from "../../../lib/effort/effort-color.ts";

const options = ["off", "low", "medium", "high", "max"].map((value) => ({ value }));

test("every non-max effort level gets a distinct colour", () => {
  const colors = ["low", "medium", "high"].map((value) => effortLevelColor(options, value));
  assert.equal(new Set(colors).size, colors.length);
  for (const color of colors) assert.match(color, /^hsl\(/);
});

test("off and unknown levels follow the text colour", () => {
  assert.equal(effortLevelColor(options, "off"), "var(--text-bright)");
  assert.equal(effortLevelColor(options, "turbo"), "var(--text-bright)");
});

test("the hottest level is the last option and a lone level is fully hot", () => {
  assert.equal(effortLevelColor(options, "max"), "hsl(0, 100%, 68%)");
  assert.equal(effortLevelColor([{ value: "on" }], "on"), "hsl(0, 100%, 68%)");
  assert.equal(effortLevelColor(options, "low"), "hsl(48, 96%, 56%)");
});
