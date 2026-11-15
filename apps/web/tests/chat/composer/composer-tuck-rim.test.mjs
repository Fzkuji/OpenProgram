import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

// The rows tucked on any click in the box's padding, including the 12px
// above and below the text and the gaps beside the mic and send, so the
// switch sat well inside the box. Only a band along the edge tucks now,
// 6px inside and 6px outside the box; the inner padding belongs to the
// text field.
const root = join(dirname(fileURLToPath(import.meta.url)), "../../..");
const read = (path) => readFileSync(join(root, path), "utf8");

test("only a band along the input box's edge tucks the rows", () => {
  const hook = read("components/chat/composer/controls/use-tucked-controls.ts");
  assert.match(hook, /const RIM_IN = 6;/);
  assert.match(hook, /<= RIM_IN \? "band" : "inner";/);
});
