import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";

const WEB_ROOT = new URL("../..", import.meta.url);
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("@/")) {
      const base = new URL(specifier.slice(2), WEB_ROOT).href;
      const file = `${base}.ts`;
      const url = existsSync(fileURLToPath(file)) ? file : `${base}/index.ts`;
      return { url, shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});

const { tooltipRows } = await import("../../lib/runtime-bridge/dag/interaction/node/tooltip-content.ts");
const keys = (rows) => rows.map((r) => r.key);

test("A turn cancelled before it said anything shows its state, not a 1-token guess", () => {
  const rows = tooltipRows({ id: "r1", role: "assistant", status: "cancelled", preview: "" }, null, false);
  assert.ok(!keys(rows).some((k) => k.startsWith("tokens")));
  assert.deepEqual(rows.find((r) => r.key === "status"), { kind: "kv", key: "status", value: "cancelled" });
});

test("A finished node carries no status row", () => {
  const rows = tooltipRows({ id: "r1", role: "assistant", status: "completed", preview: "hi" }, null, false);
  assert.ok(!keys(rows).includes("status"));
});

test("The detail card shows the whole id", () => {
  const id = "2d3eea30_reply_t_call_00_OIw2W4iJXNBX9lbQlVR65676";
  const rows = tooltipRows({ id, role: "tool", name: "bash" }, null, true);
  assert.equal(rows.find((r) => r.key === "id").value, id);
});

test("A fold count takes the hand over its whole box", () => {
  // A fold count is part of its node: its whole box hits the node (the
  // hand, and a click folds / unfolds), not just the strokes of the digits.
  const nodes = readFileSync(new URL("../../lib/runtime-bridge/dag/render/nodes.ts", import.meta.url), "utf8");
  const counts = nodes.match(/class: "history-thread-count",[\s\S]*?"pointer-events": "[^"]+"/g) || [];
  assert.equal(counts.length, 2);
  for (const c of counts) assert.match(c, /"pointer-events": "bounding-box"/);
});
