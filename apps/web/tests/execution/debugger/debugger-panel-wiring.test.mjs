import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const panel = readFileSync(new URL("../../../components/right-sidebar/debugger/debugger-panel.tsx", import.meta.url), "utf8");

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

test("debugger never narrates loading, reconnecting or a failed read", () => {
  assert.doesNotMatch(panel, /Loading executions|Retrying automatically|Could not load executions|Reconnecting|Event gap|"Stale"|Synced/);
  // The empty state is shown only once the executions have been read.
  assert.match(panel, /fetchedAt \? <SidebarNotice>No executions in this conversation\.<\/SidebarNotice>/);
});

test("a conflict on the user's own optimistic action is still shown", () => {
  assert.match(panel, /connection\.state === "conflict" && <div className=\{styles\.connectionLine\}/);
  assert.match(panel, /The server rejected an optimistic version/);
  // Only the conflict draws the line; no other connection state does.
  assert.doesNotMatch(panel, /connection\.state !== "connected" && <div className=\{styles\.connectionLine\}/);
});
