import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const panel = readFileSync(new URL("../../components/right-sidebar/debugger/debugger-panel.tsx", import.meta.url), "utf8");

test("debugger renders its empty state before reading execution fields", () => {
  const emptyGuard = panel.indexOf("if (!snapshot)");
  const actionPayloads = panel.indexOf("const actionPayloads");
  assert.ok(emptyGuard >= 0, "debugger must guard an empty execution selection");
  assert.ok(actionPayloads >= 0, "debugger must build canonical action payloads");
  assert.ok(
    emptyGuard < actionPayloads,
    "empty execution state must return before action payloads read snapshot fields",
  );
});
