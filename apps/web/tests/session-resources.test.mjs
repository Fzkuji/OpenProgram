import test from "node:test";
import assert from "node:assert/strict";
import { resourceSessionIds, sessionResourceRows, backendResourceRows } from "../lib/state/session-resources.ts";

test("session resource scope uses recorded provenance without assigning manual views", () => {
  const tabs = [
    { id: "s:a", kind: "session", title: "A", sessionId: "a" },
    { id: "s:d", kind: "session", title: "Draft", sessionId: "draft", draft: true },
    { id: "w:a", kind: "web", title: "Web", agentSessionId: "a", url: "https://example.test" },
    { id: "f:a", kind: "file", title: "Code", diffSessionId: "b", path: "app.py" },
    { id: "b:terminal", kind: "builtin", page: "terminal", title: "" },
    { id: "w:m", kind: "web", title: "Manual", url: "https://manual.test" },
  ];
  assert.deepEqual(resourceSessionIds(tabs, "a"), ["a", "b"]);
  const rows = sessionResourceRows(tabs, []);
  assert.deepEqual(rows.map(row => [row.kind, row.sessionId]), [["web", "a"], ["file", "b"], ["terminal", null], ["web", null]]);
});

test("heterogeneous integrations stay typed and shared scoped resources appear once", () => {
  const items = ["docker", "vm", "ssh", "device"].map(kind => ({
    id: `usage:${kind}`, sourceId: kind, source: "usage", sessionId: "a", kind, title: kind, target: "target", status: "in_use",
  }));
  const rows = sessionResourceRows([], [...items, items[0]]);
  assert.equal(rows.length, 4);
  assert.equal(rows[3].kind, "device");
});

test("caller scope grants inspection without rewriting the resource's actual session", () => {
  const item = { id: "r1", session_id: "a-child", source: "process", kind: "docker", title: "Docker", target: "image", status: "running" };
  const child = backendResourceRows([item], "a-child");
  const parent = backendResourceRows([item], "z-parent");
  for (const rows of [parent, [...parent, ...child], [...child, ...parent]]) {
    const result = sessionResourceRows([], rows);
    assert.equal(result.length, 1);
    assert.equal(result[0].sessionId, "a-child");
    assert.ok(["z-parent", "a-child"].includes(result[0].scopeSessionId));
  }
  assert.equal(parent[0].scopeSessionId, "z-parent");
});
