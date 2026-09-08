import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  resourceSessionId,
  sessionResourceRows,
  backendResourceRows,
  groupSessionResources,
  ingestBrowserResource,
  applyResourceSnapshot,
  beginResourceSnapshotClock,
  followPreviewBinding,
  listedBrowserResources,
  resetBrowserResources,
  getPreviewPreference,
  selectResourcePreview,
  followCurrentBranch,
  hideResourcePreview,
  showResourcePreview,
  togglePreviewExpanded,
  latestFollowTarget,
  requestResourceControl,
  recoverSessionResources,
  sessionResourceView,
  setBrowserConnection,
} from "../lib/state/session-resources.ts";

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

function browserItem(overrides = {}) {
  return {
    id: "assoc-a",
    resource_id: "page-a",
    session_id: "a",
    conversation_session_id: "a",
    execution_id: "exec-a",
    branch_id: "br-a",
    branch_name: "Research",
    agent_name: "Research Agent",
    tab_id: "w:a",
    window_id: "main",
    kind: "web",
    title: "Plans overview",
    target: "https://a.test",
    status: "open",
    source: "browser",
    control_state: "active",
    generation: 1,
    sequence: 1,
    ...overrides,
  };
}

test("browser associations keep exact page identity and group by branch not session", () => {
  const backend = backendResourceRows([
    browserItem(),
    browserItem({
      id: "assoc-b", resource_id: "page-a", branch_id: "br-b", branch_name: "Build",
      agent_name: "Build Agent", tab_id: "w:a", sequence: 2,
    }),
    browserItem({
      id: "assoc-c", resource_id: "page-c", branch_id: null, branch_name: null,
      title: "Legacy page", tab_id: "w:legacy", target: "https://legacy.test",
    }),
    { id: "vm-a", session_id: "a", source: "usage", kind: "vm", title: "VM", target: "http://vm.test", status: "in_use" },
  ], "a");
  assert.equal(backend.find(row => row.id === "assoc-a").resourceId, "page-a");
  assert.equal(backend.find(row => row.id === "assoc-b").resourceId, "page-a");
  assert.equal(backend.filter(row => row.resourceId === "page-a").length, 2);
  const rows = sessionResourceRows(tabs, backend, "a");
  assert.equal(rows.filter(row => row.sourceId === "w:a" && row.source === "web").length, 0);
  const groups = groupSessionResources(rows, "br-a");
  assert.deepEqual(groups.map(group => group.key), ["br-a", "br-b", "unassigned"]);
  assert.equal(groups[0].current, true);
  assert.equal(groups[0].title, "Research");
  assert.equal(groups[2].title, "Unassigned");
  assert.ok(groups[2].rows.some(row => row.id === "assoc-c"));
  assert.ok(groups[2].rows.some(row => row.kind === "vm"));
});

test("parent Resources keep authorized child-owned browser Pages", () => {
  resetBrowserResources();
  ingestBrowserResource({
    id: "assoc-child",
    resource_id: "page-child",
    session_id: "child",
    conversation_session_id: "parent",
    execution_id: "exec-child",
    branch_id: "br-parent",
    branch_name: "Research",
    agent_name: "Child Agent",
    tab_id: "w:child",
    kind: "web",
    title: "Child page",
    target: "https://child.test",
    status: "open",
    source: "browser",
    control_state: "active",
    generation: 1,
    sequence: 1,
  }, "parent");
  const listed = sessionResourceRows([], listedBrowserResources(), "parent");
  assert.equal(listed.length, 1);
  assert.equal(listed[0].sessionId, "child");
  assert.equal(listed[0].conversationSessionId, "parent");
  assert.equal(groupSessionResources(listed, "br-parent")[0].key, "br-parent");
});

test("unnamed branch groups use short origin, not the raw branch id", () => {
  const branchId = "local_139d9ce9-b16e-4fb6-ac85-712ef0e5b03a:6bf13196";
  const unnamed = backendResourceRows([
    browserItem({ branch_id: branchId, branch_name: null }),
  ], "a");
  const unnamedGroups = groupSessionResources(unnamed, branchId);
  assert.equal(unnamedGroups[0].key, branchId);
  assert.equal(unnamedGroups[0].title, "6bf13196");
  assert.equal(unnamedGroups[0].title.includes("local_"), false);

  const promoted = backendResourceRows([
    browserItem({ branch_id: branchId, branch_name: null }),
    browserItem({
      id: "assoc-named", branch_id: branchId, branch_name: "五页计数器发布验收", sequence: 2,
    }),
  ], "a");
  assert.equal(groupSessionResources(promoted, branchId)[0].title, "五页计数器发布验收");
  assert.equal(groupSessionResources(promoted, branchId)[0].key, branchId);

  const firstNamed = backendResourceRows([
    browserItem({ branch_id: branchId, branch_name: "Research" }),
    browserItem({
      id: "assoc-later", branch_id: branchId, branch_name: "Build", sequence: 2,
    }),
  ], "a");
  assert.equal(groupSessionResources(firstNamed, branchId)[0].title, "Research");
});

test("closed Pages move to a collapsed unavailable group and leave live counts", () => {
  const rows = backendResourceRows([
    browserItem({ id: "assoc-live", status: "open", control_state: "active" }),
    browserItem({
      id: "assoc-dead", resource_id: "page-dead", title: "Closed",
      status: "closed", control_state: "closed", sequence: 2,
    }),
  ], "a");
  const groups = groupSessionResources(rows, "br-a");
  assert.deepEqual(groups.map(group => group.key), ["br-a", "unavailable"]);
  assert.equal(groups[0].rows.length, 1);
  assert.equal(groups[0].rows[0].id, "assoc-live");
  assert.equal(groups[1].title, "Unavailable");
  assert.equal(groups[1].rows[0].id, "assoc-dead");
});

test("ingest ignores stale generation or sequence and resets on session change", () => {
  resetBrowserResources();
  const first = ingestBrowserResource(browserItem({ sequence: 4, title: "Live" }), "a");
  assert.equal(first.title, "Live");
  assert.equal(ingestBrowserResource(browserItem({ sequence: 3, title: "Old seq" }), "a")?.title, "Live");
  assert.equal(ingestBrowserResource(browserItem({ generation: 0, sequence: 9, title: "Old gen" }), "a")?.title, "Live");
  assert.equal(ingestBrowserResource(browserItem({ session_id: "other", conversation_session_id: "other" }), "a"), null);
  resetBrowserResources("a");
  assert.equal(ingestBrowserResource(browserItem({ sequence: 1, title: "Still live" }), "a")?.title, "Live");
  resetBrowserResources();
  assert.equal(ingestBrowserResource(browserItem({ sequence: 1, title: "Reopened" }), "a")?.title, "Reopened");
});

test("snapshot recovery does not overwrite newer events or another session", () => {
  resetBrowserResources();
  ingestBrowserResource(browserItem({ conversation_session_id: "a", sequence: 1, title: "A1" }), "a");
  ingestBrowserResource(browserItem({
    id: "assoc-b", resource_id: "page-b", session_id: "b", conversation_session_id: "b",
    tab_id: "w:b", branch_id: "br-b", title: "B1", sequence: 1,
  }), "b");
  const begun = beginResourceSnapshotClock();
  ingestBrowserResource(browserItem({ sequence: 4, title: "A-live" }), "a", { origin: "event" });
  const merged = applyResourceSnapshot([browserItem({ sequence: 1, title: "A-stale" })], "a", { begunClock: begun });
  assert.equal(merged.find(row => row.id === "assoc-a").title, "A-live");
  assert.ok(listedBrowserResources().some(row => row.id === "assoc-b"));
});

test("authentic dispatch binds follow preview; snapshot operations do not", () => {
  resetBrowserResources();
  const sessionTabs = [
    { id: "s:a", kind: "session", sessionId: "a" },
    { id: "w:a", kind: "web" },
    { id: "w:b", kind: "web" },
  ];
  ingestBrowserResource(browserItem(), "a", { origin: "snapshot" });
  ingestBrowserResource(browserItem({
    id: "assoc-b", resource_id: "page-b", tab_id: "w:b", title: "Pricing",
    sequence: 2, last_operation: { id: "op-old", action: "click", phase: "dispatched" },
  }), "a", { origin: "snapshot" });
  followCurrentBranch("a", "br-a");
  assert.equal(followPreviewBinding("a", "br-a", sessionTabs), null);
  ingestBrowserResource(browserItem({
    id: "assoc-b", resource_id: "page-b", tab_id: "w:b", title: "Pricing",
    sequence: 3, last_operation: { id: "op-new", action: "click", phase: "dispatched" },
  }), "a", { origin: "event" });
  assert.deepEqual(followPreviewBinding("a", "br-a", sessionTabs), { tabId: "w:b", ownerTabId: "s:a" });
  hideResourcePreview("a", "br-a");
  assert.equal(followPreviewBinding("a", "br-a", sessionTabs), null);
});

test("follow tracks admitted operations only and hide does not reopen from later events", () => {
  resetBrowserResources();
  ingestBrowserResource(browserItem(), "a");
  ingestBrowserResource(browserItem({
    id: "assoc-b", resource_id: "page-b", tab_id: "w:b", title: "Pricing",
    target: "https://b.test", sequence: 2,
  }), "a");
  selectResourcePreview("a", "br-a", "assoc-a");
  let pref = getPreviewPreference("a", "br-a");
  assert.equal(pref.mode, "manual");
  assert.equal(pref.targetId, "assoc-a");
  ingestBrowserResource(browserItem({
    id: "assoc-b", resource_id: "page-b", tab_id: "w:b", title: "Pricing",
    target: "https://b.test", sequence: 3,
    last_operation: { id: "op-1", action: "click", phase: "dispatched" },
  }), "a");
  pref = getPreviewPreference("a", "br-a");
  assert.equal(pref.mode, "manual");
  assert.equal(pref.targetId, "assoc-a");
  assert.equal(latestFollowTarget("a", "br-a"), "assoc-b");
  followCurrentBranch("a", "br-a");
  pref = getPreviewPreference("a", "br-a");
  assert.equal(pref.mode, "follow");
  assert.equal(pref.targetId, "assoc-b");
  hideResourcePreview("a", "br-a");
  ingestBrowserResource(browserItem({
    sequence: 5,
    last_operation: { id: "op-2", action: "click", phase: "acknowledged" },
  }), "a");
  pref = getPreviewPreference("a", "br-a");
  assert.equal(pref.hidden, true);
  assert.equal(pref.mode, "follow");
  assert.equal(pref.targetId, "assoc-a");
  assert.equal(latestFollowTarget("a", "br-a"), "assoc-a");
  showResourcePreview("a", "br-a");
  pref = getPreviewPreference("a", "br-a");
  assert.equal(pref.hidden, false);
  assert.equal(pref.targetId, "assoc-a");
});

test("expand preserves follow or fixed mode; pin and unpin keep expanded", () => {
  resetBrowserResources();
  ingestBrowserResource(browserItem(), "a");
  ingestBrowserResource(browserItem({
    id: "assoc-b", resource_id: "page-b", tab_id: "w:b", title: "Pricing",
    target: "https://b.test", sequence: 2,
  }), "a");
  followCurrentBranch("a", "br-a");
  let pref = togglePreviewExpanded("a", "br-a");
  assert.equal(pref.mode, "follow");
  assert.equal(pref.expanded, true);
  pref = togglePreviewExpanded("a", "br-a");
  assert.equal(pref.mode, "follow");
  assert.equal(pref.expanded, false);
  togglePreviewExpanded("a", "br-a");
  pref = selectResourcePreview("a", "br-a", "assoc-a");
  assert.equal(pref.mode, "manual");
  assert.equal(pref.targetId, "assoc-a");
  assert.equal(pref.expanded, true);
  ingestBrowserResource(browserItem({
    id: "assoc-b", resource_id: "page-b", tab_id: "w:b", title: "Pricing",
    target: "https://b.test", sequence: 3,
    last_operation: { id: "op-pin", action: "click", phase: "dispatched" },
  }), "a");
  pref = getPreviewPreference("a", "br-a");
  assert.equal(pref.mode, "manual");
  assert.equal(pref.targetId, "assoc-a");
  assert.equal(latestFollowTarget("a", "br-a"), "assoc-b");
  pref = followCurrentBranch("a", "br-a");
  assert.equal(pref.mode, "follow");
  assert.equal(pref.targetId, "assoc-b");
  assert.equal(pref.expanded, true);
  ingestBrowserResource(browserItem({
    sequence: 4,
    last_operation: { id: "op-follow", action: "click", phase: "dispatched" },
  }), "a");
  pref = getPreviewPreference("a", "br-a");
  assert.equal(pref.mode, "follow");
  assert.equal(pref.targetId, "assoc-a");
  assert.equal(pref.expanded, true);
});

test("admitted operations follow per session and branch; pinned targets stay", () => {
  resetBrowserResources();
  const sessionTabs = [
    { id: "s:a", kind: "session", sessionId: "a" },
    { id: "s:b", kind: "session", sessionId: "b" },
    { id: "w:a1", kind: "web" },
    { id: "w:a2", kind: "web" },
    { id: "w:b1", kind: "web" },
    { id: "w:b2", kind: "web" },
    { id: "w:x", kind: "web" },
  ];
  ingestBrowserResource(browserItem({
    id: "a-br-a-1", resource_id: "page-a1", tab_id: "w:a1", title: "A1",
  }), "a");
  ingestBrowserResource(browserItem({
    id: "a-br-a-2", resource_id: "page-a2", tab_id: "w:a2", title: "A2", sequence: 2,
  }), "a");
  ingestBrowserResource(browserItem({
    id: "a-br-b-1", resource_id: "page-ab1", tab_id: "w:b1", branch_id: "br-b",
    branch_name: "Build", title: "AB1",
  }), "a");
  ingestBrowserResource(browserItem({
    id: "b-br-a-1", resource_id: "page-b1", session_id: "b", conversation_session_id: "b",
    tab_id: "w:x", title: "B1",
  }), "b");
  followCurrentBranch("a", "br-a");
  selectResourcePreview("a", "br-b", "a-br-b-1");
  followCurrentBranch("b", "br-a");
  ingestBrowserResource(browserItem({
    id: "a-br-a-2", resource_id: "page-a2", tab_id: "w:a2", title: "A2", sequence: 3,
    last_operation: { id: "op-a2", action: "click", phase: "dispatched" },
  }), "a");
  ingestBrowserResource(browserItem({
    id: "a-br-b-2", resource_id: "page-ab2", tab_id: "w:b2", branch_id: "br-b",
    branch_name: "Build", title: "AB2", sequence: 2,
    last_operation: { id: "op-ab2", action: "click", phase: "dispatched" },
  }), "a");
  ingestBrowserResource(browserItem({
    id: "b-br-a-1", resource_id: "page-b1", session_id: "b", conversation_session_id: "b",
    tab_id: "w:x", title: "B1", sequence: 2,
    last_operation: { id: "op-b1", action: "click", phase: "dispatched" },
  }), "b");
  let pref = getPreviewPreference("a", "br-a");
  assert.equal(pref.mode, "follow");
  assert.equal(pref.targetId, "a-br-a-2");
  assert.deepEqual(followPreviewBinding("a", "br-a", sessionTabs), { tabId: "w:a2", ownerTabId: "s:a" });
  pref = getPreviewPreference("a", "br-b");
  assert.equal(pref.mode, "manual");
  assert.equal(pref.targetId, "a-br-b-1");
  assert.equal(latestFollowTarget("a", "br-b"), "a-br-b-2");
  assert.deepEqual(followPreviewBinding("a", "br-b", sessionTabs), { tabId: "w:b1", ownerTabId: "s:a" });
  pref = getPreviewPreference("b", "br-a");
  assert.equal(pref.mode, "follow");
  assert.equal(pref.targetId, "b-br-a-1");
  assert.equal(getPreviewPreference("a", "br-a").targetId, "a-br-a-2");
  assert.equal(latestFollowTarget("a", "br-a"), "a-br-a-2");
  assert.equal(latestFollowTarget("b", "br-a"), "b-br-a-1");
});

test("resource control posts pause or resume and failed rows are not paused", async () => {
  const calls = [];
  const row = await requestResourceControl({
    conversationSessionId: "a",
    resourceId: "page-a",
    action: "pause",
    commandId: "cmd-1",
    generation: 1,
  }, async (url, init) => {
    calls.push({ url, init });
    return { id: "assoc-a", resource_id: "page-a", control_state: "unknown", generation: 1, sequence: 8 };
  });
  assert.equal(calls[0].url, "/api/session/a/resources/page-a/control");
  assert.equal(JSON.parse(calls[0].init.body).action, "pause");
  assert.equal(JSON.parse(calls[0].init.body).command_id, "cmd-1");
  assert.equal(row.control_state, "unknown");
  assert.notEqual(row.control_state, "paused");
});

test("HTTP control envelope is ingested into the shared row", async () => {
  resetBrowserResources();
  ingestBrowserResource(browserItem({ control_state: "active", sequence: 1 }), "a");
  await requestResourceControl({
    conversationSessionId: "a",
    resourceId: "page-a",
    action: "pause",
    commandId: "cmd-2",
    generation: 1,
  }, async () => ({
    item: browserItem({ control_state: "paused", sequence: 9 }),
    now: 1,
  }));
  assert.equal(listedBrowserResources().find(row => row.id === "assoc-a").controlState, "paused");
});

test("first resource snapshot is pending until this conversation's GET completes", () => {
  resetBrowserResources();
  const pending = sessionResourceView("a");
  assert.equal(pending.loaded, false);
  assert.equal(pending.unavailable, false);
  assert.deepEqual(pending.rows, []);
  ingestBrowserResource(browserItem({ title: "From event" }), "a", { origin: "event" });
  const afterEvent = sessionResourceView("a");
  assert.equal(afterEvent.loaded, false);
  assert.equal(afterEvent.rows.length, 1);
  applyResourceSnapshot([browserItem({
    id: "assoc-b", resource_id: "page-b", session_id: "b", conversation_session_id: "b",
    tab_id: "w:b", title: "Other", sequence: 1,
  })], "b");
  assert.equal(sessionResourceView("a").loaded, false);
  assert.equal(sessionResourceView("b").loaded, true);
});

test("a successful empty snapshot shows empty and a nonempty snapshot lists Pages", () => {
  resetBrowserResources();
  applyResourceSnapshot([], "a");
  const empty = sessionResourceView("a");
  assert.equal(empty.loaded, true);
  assert.equal(empty.unavailable, false);
  assert.deepEqual(empty.rows, []);
  applyResourceSnapshot([browserItem({ title: "Plans" })], "a");
  const filled = sessionResourceView("a");
  assert.equal(filled.loaded, true);
  assert.equal(filled.rows[0].title, "Plans");
  applyResourceSnapshot([browserItem({ title: "Plans", sequence: 2 })], "a");
  assert.equal(sessionResourceView("a").loaded, true);
  assert.equal(sessionResourceView("a").rows[0].title, "Plans");
});

test("a failed snapshot keeps retained rows and uses unavailable without unloading", async () => {
  resetBrowserResources();
  ingestBrowserResource(browserItem({ title: "Kept" }), "a", { origin: "event" });
  await assert.rejects(() => recoverSessionResources("a", async () => {
    throw new Error("snapshot failed");
  }));
  const failed = sessionResourceView("a");
  assert.equal(failed.loaded, true);
  assert.equal(failed.unavailable, true);
  assert.equal(failed.rows[0].title, "Kept");
  await recoverSessionResources("a", async () => ({ items: [browserItem({ title: "Recovered", sequence: 2 })] }));
  const recovered = sessionResourceView("a");
  assert.equal(recovered.loaded, true);
  assert.equal(recovered.unavailable, false);
  assert.equal(recovered.rows[0].title, "Recovered");
});

test("conversation switch isolates first-load completion", async () => {
  resetBrowserResources();
  await recoverSessionResources("a", async () => ({ items: [browserItem()] }));
  assert.equal(sessionResourceView("a").loaded, true);
  assert.equal(sessionResourceView("b").loaded, false);
  await recoverSessionResources("b", async () => ({ items: [] }));
  assert.equal(sessionResourceView("a").loaded, true);
  assert.equal(sessionResourceView("a").rows.length, 1);
  assert.equal(sessionResourceView("b").loaded, true);
  assert.deepEqual(sessionResourceView("b").rows, []);
  setBrowserConnection(false);
  assert.equal(sessionResourceView("a").unavailable, true);
  setBrowserConnection(true);
  assert.equal(sessionResourceView("a").unavailable, false);
});

test("the Resources hook and projection use per-conversation snapshot completion", () => {
  const hook = readFileSync(new URL("../lib/use-session-resources.ts", import.meta.url), "utf8");
  const projection = readFileSync(new URL("../lib/state/browser-resource-projection.ts", import.meta.url), "utf8");
  const store = readFileSync(new URL("../lib/state/session-resources.ts", import.meta.url), "utf8");
  assert.doesNotMatch(hook, /rowClock\)\.length\s*>=\s*0/);
  assert.match(hook, /sessionResourceView\(sessionId\)/);
  assert.match(projection, /recoverSessionResources\(sessionId/);
  assert.match(store, /completeResourceSnapshot\(sessionId, \{ ok: false \}\)/);
});
