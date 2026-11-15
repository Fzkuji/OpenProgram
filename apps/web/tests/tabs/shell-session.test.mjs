import assert from "node:assert/strict";
import test from "node:test";

import { rowLayout } from "../../lib/tabs/canvas/canvas-layout.ts";
import { sessionForShellReturn, shellTabId } from "../../lib/tabs/navigation/shell-session.ts";

const tabs = [
  { id: "s:a", kind: "session", title: "A", sessionId: "a" },
  { id: "s:b", kind: "session", title: "B", sessionId: "b" },
  { id: "w:x", kind: "web", title: "X", url: "https://example.com" },
];
const split = {
  tabs,
  activeId: "s:b",
  groups: [{ id: "g", memberIds: ["s:a", "s:b"], visibleIds: ["s:a", "s:b"], focusedId: "s:b", canvas: rowLayout(["s:a", "s:b"]) }],
};

test("A split hides the chat shell; a lone session shows it", () => {
  assert.equal(shellTabId(split), null);
  assert.equal(shellTabId({ tabs, activeId: "s:a", groups: [] }), "s:a");
  assert.equal(shellTabId({ tabs, activeId: "w:x", groups: [] }), null);
});

test("A split folding to one session hands the shell that session at once", () => {
  // Yellow on the focused pane: the shell was hidden on B, A stays.
  assert.equal(sessionForShellReturn(split, { tabs, activeId: "s:a", groups: [] }), "a");
});

test("Changes that do not bring the shell back from a split leave it alone", () => {
  const single = { tabs, activeId: "s:a", groups: [] };
  // Plain tab switches go through the route as before.
  assert.equal(sessionForShellReturn(single, { tabs, activeId: "s:b", groups: [] }), null);
  // A web tab left alone keeps the shell hidden.
  assert.equal(sessionForShellReturn(split, { tabs, activeId: "w:x", groups: [] }), null);
  // A draft has no session to switch to yet.
  const draft = tabs.map((tab) => tab.id === "s:a" ? { ...tab, draft: true } : tab);
  assert.equal(sessionForShellReturn({ ...split, tabs: draft }, { tabs: draft, activeId: "s:a", groups: [] }), null);
});
