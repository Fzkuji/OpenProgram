import test from "node:test";
import assert from "node:assert/strict";
import { resourceSessionId, sessionResourceRows, backendResourceRows } from "../lib/state/session-resources.ts";

const tabs = [
  { id: "s:a", kind: "session", title: "A", sessionId: "a" },
  { id: "s:b", kind: "session", title: "B", sessionId: "b" },
  { id: "s:d", kind: "session", title: "Draft", sessionId: "draft", draft: true },
  { id: "w:a", kind: "web", title: "A page", agentSessionId: "a", url: "https://a.test" },
  { id: "w:b", kind: "web", title: "B page", agentSessionId: "b", url: "https://b.test" },
  { id: "f:b", kind: "file", title: "Code", diffSessionId: "b", path: "app.py" },
  { id: "b:terminal", kind: "builtin", page: "terminal", title: "" },
  { id: "w:manual", kind: "web", title: "Manual", url: "https://manual.test" },
];

test("active session or owned resource determines scope without stale global session fallback", () => {
  assert.deepEqual(tabs.map(resourceSessionId), ["a", "b", null, "a", "b", "b", null, null]);
  assert.equal(resourceSessionId(undefined), null);
});

test("switching sessions isolates web, file and heterogeneous backend resources", () => {
  const backend = backendResourceRows(["docker", "vm", "device"].flatMap(kind => ["a", "b"].map(session_id => ({
    id: `${kind}:${session_id}`, source: "usage", session_id, kind, title: kind, target: "target", status: "in_use",
  }))), "a");
  const a = sessionResourceRows(tabs, backend, "a");
  const b = sessionResourceRows(tabs, backend, "b");
  assert.deepEqual(a.map(r => r.kind), ["web", "docker", "vm", "device"]);
  assert.deepEqual(b.map(r => r.kind), ["web", "docker", "vm", "device"]);
  assert.ok(a.every(r => r.sessionId === "a"));
  assert.ok(b.every(r => r.sessionId === "b"));
  assert.deepEqual(sessionResourceRows(tabs, backend, null), []);
  assert.deepEqual(sessionResourceRows(tabs, backend, "empty-session"), []);
});

test("authorized descendant scope does not relabel or include another session's resource", () => {
  const item = { id: "r1", session_id: "child", source: "usage", kind: "vm", title: "Docker", target: "image", status: "running" };
  const rows = backendResourceRows([item], "parent");
  assert.equal(rows[0].sessionId, "child");
  assert.equal(rows[0].scopeSessionId, "parent");
  assert.deepEqual(sessionResourceRows([], rows, "parent"), []);
  assert.equal(sessionResourceRows([], [...rows, ...rows], "child").length, 1);
});

test("code views and process records never become software resources", () => {
  const legacy = backendResourceRows([{ id: "p", session_id: "b", source: "process", kind: "docker", title: "Code", target: "image", status: "running" }], "b");
  const rows = sessionResourceRows(tabs, legacy, "b");
  assert.deepEqual(rows.map(r => r.kind), ["web"]);
});
