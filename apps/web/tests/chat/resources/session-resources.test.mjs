import test from "node:test";
import assert from "node:assert/strict";
import {
  resourceSessionId,
  sessionResourceRows,
  backendResourceRows,
  groupSessionResources,
  resourceDisplayTitle,
  resourceIsUnavailable,
  ingestBrowserResource,
  applyResourceSnapshot,
  beginResourceSnapshotClock,
  followPreviewBinding,
  listedBrowserResources,
  resetBrowserResources,
  getPreviewPreference,
  selectResourcePreview,
  previewResource,
  followCurrentBranch,
  followNativeWindow,
  hideResourcePreview,
  showResourcePreview,
  togglePreviewExpanded,
  latestFollowTarget,
  requestResourceControl,
  recoverSessionResources,
  sessionResourceView,
  setBrowserConnection,
  optimisticallyCloseBrowserResource,
  requestCloseBrowserResource,
  useBrowserResourceStore,
} from "../../../lib/chat/resources/session-resources.ts";

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
  const groups = groupSessionResources(rows);
  assert.deepEqual(groups.map(group => group.key), ["web", "vm"]);
  assert.ok(groups[0].rows.some(row => row.id === "assoc-c"));
  assert.ok(groups[1].rows.some(row => row.kind === "vm"));
});

test("optimistic close suppresses a late old event but authoritative snapshot restores on failure", () => {
  resetBrowserResources();
  ingestBrowserResource(browserItem({ generation: 4, sequence: 8 }), "a");
  optimisticallyCloseBrowserResource("page-a", 4);
  assert.equal(listedBrowserResources().length, 0);
  ingestBrowserResource(browserItem({ generation: 4, sequence: 9 }), "a");
  assert.equal(listedBrowserResources().length, 0);
  ingestBrowserResource(browserItem({ generation: 4, sequence: 10 }), "a", { origin: "snapshot" });
  assert.equal(listedBrowserResources().length, 1);
});

test("optimistic close timeout accepts live authority when no close receipt arrives", () => {
  resetBrowserResources();
  const now = Date.now;
  let clock = 1000;
  Date.now = () => clock;
  try {
    ingestBrowserResource(browserItem({ generation: 2 }), "a");
    optimisticallyCloseBrowserResource("page-a", 2);
    ingestBrowserResource(browserItem({ id: "stale-close", generation: 2, sequence: 0,
      status: "closed", control_state: "closed" }), "a");
    assert.equal(listedBrowserResources().length, 0);
    clock += 15001;
    ingestBrowserResource(browserItem({ generation: 2, sequence: 2 }), "a");
    assert.equal(listedBrowserResources().length, 1);
  } finally {
    Date.now = now;
  }
});

test("closing by resource id hides the row until the server's closed rows arrive", async () => {
  resetBrowserResources();
  ingestBrowserResource(browserItem({ generation: 2 }), "a");
  optimisticallyCloseBrowserResource("page-a", 2);
  let request;
  const ok = await requestCloseBrowserResource({ conversationSessionId: "a", resourceId: "page-a" }, async (url, init) => {
    request = { url, init };
    return { items: [browserItem({ generation: 2, sequence: 2, status: "closed", control_state: "closed" })] };
  });
  assert.equal(ok, true);
  assert.equal(request.url, "/api/session/a/resources/page-a/close");
  assert.equal(request.init.method, "POST");
  assert.ok(listedBrowserResources().every(row => row.status === "closed"));
  assert.deepEqual(Object.keys(useBrowserResourceStore.getState().optimisticClosed), []);
});

test("a failed close brings the row back at once; an unknown Page stays hidden", async () => {
  resetBrowserResources();
  ingestBrowserResource(browserItem({ generation: 2 }), "a");
  optimisticallyCloseBrowserResource("page-a", 2);
  const failed = await requestCloseBrowserResource({ conversationSessionId: "a", resourceId: "page-a" }, async () => {
    throw Object.assign(new Error("offline"), { status: 503 });
  });
  assert.equal(failed, false);
  assert.equal(listedBrowserResources().filter(row => row.status === "open").length, 1);

  optimisticallyCloseBrowserResource("page-a", 2);
  const gone = await requestCloseBrowserResource({ conversationSessionId: "a", resourceId: "page-a" }, async () => {
    throw Object.assign(new Error("not_found"), { status: 404 });
  });
  assert.equal(gone, true);
  assert.equal(listedBrowserResources().length, 0);
  assert.deepEqual(Object.keys(useBrowserResourceStore.getState().optimisticClosed), []);
});

test("one targeted closed event updates every association for the Page", () => {
  resetBrowserResources();
  ingestBrowserResource(browserItem({ id: "assoc-a", branch_id: "br-a" }), "a");
  ingestBrowserResource(browserItem({ id: "assoc-b", branch_id: "br-b", sequence: 2 }), "a");
  ingestBrowserResource(browserItem({ id: "stale-close", status: "closed", control_state: "closed", sequence: 1 }), "a");
  assert.ok(listedBrowserResources().every(row => row.status === "open"));
  ingestBrowserResource(browserItem({ id: "close", status: "closed", control_state: "closed", sequence: 3 }), "a");
  assert.equal(listedBrowserResources().length, 2);
  assert.ok(listedBrowserResources().every(row => row.status === "closed" && row.controlState === "closed"));
  ingestBrowserResource(browserItem({ id: "successor", generation: 5, sequence: 1 }), "a");
  assert.equal(listedBrowserResources().find(row => row.id === "successor")?.status, "open");
});

test("confirmed close restores hidden associations before marking them closed", () => {
  resetBrowserResources();
  ingestBrowserResource(browserItem({ id: "assoc-a", branch_id: "br-a", sequence: 4 }), "a");
  ingestBrowserResource(browserItem({ id: "assoc-b", branch_id: "br-b", sequence: 5 }), "a");
  optimisticallyCloseBrowserResource("page-a", 4);
  ingestBrowserResource(browserItem({ id: "close", generation: 4, sequence: 6,
    status: "closed", control_state: "closed" }), "a");
  assert.equal(listedBrowserResources().length, 2);
  assert.ok(listedBrowserResources().every(row => row.status === "closed"));
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
  assert.equal(groupSessionResources(listed)[0].key, "web");
});

test("a Page used on several branches is listed once, as the viewed branch's row", () => {
  const page = (branchId, sequence) => ({
    id: `browser:page-a:${branchId || "unassigned"}`, resourceId: "page-a", source: "browser", kind: "web",
    title: "Doc", target: "", status: "restore_failed", branchId, sequence,
  });
  const rows = [page("b1", 9), page("b2", 3), page(null, 1), {
    id: "browser:page-b:b1", resourceId: "page-b", source: "browser", kind: "web",
    title: "Other", target: "", status: "open", branchId: "b1", sequence: 1,
  }];
  const viewed = groupSessionResources(rows, "b2")[0].rows;
  assert.deepEqual(viewed.map(row => row.id), ["browser:page-a:b2", "browser:page-b:b1"]);
  const unviewed = groupSessionResources(rows, "elsewhere")[0].rows;
  assert.deepEqual(unviewed.map(row => row.id), ["browser:page-a:b1", "browser:page-b:b1"]);
});

test("a web row shows a real page title when the captured one is blank or a URL", () => {
  const row = { id: "r", source: "browser", kind: "web", title: "", target: "", status: "open" };
  assert.equal(resourceDisplayTitle(row, { title: "AML @ CityU", url: "https://doc.example/s" }), "AML @ CityU");
  assert.equal(resourceDisplayTitle({ ...row, title: "immi.example.gov.au/visas/600#HowTo" }, { title: "Visitor visa" }), "Visitor visa");
  assert.equal(resourceDisplayTitle({ ...row, title: "\u200e" }, { title: "周报表单" }), "周报表单");
  assert.equal(resourceDisplayTitle({ ...row, title: "\u200b ", target: "https://form.example/s" }), "https://form.example/s");
  assert.equal(resourceDisplayTitle({ ...row, title: "Example Domain" }, { title: "https://example.org" }), "Example Domain");
  assert.equal(resourceDisplayTitle({ ...row, title: "Example Domain" }, { title: "Renamed tab" }), "Example Domain");
  assert.equal(resourceDisplayTitle({ ...row, title: "腾讯文档" }, { title: "AML @ CityU", urlNativeAt: 1 }), "AML @ CityU");
  assert.equal(resourceDisplayTitle({ ...row, title: "a.example/x" }, { title: "https://a.example/x" }), "a.example/x");
  assert.equal(resourceDisplayTitle({ ...row, target: "https://a.example/x" }, null), "https://a.example/x");
  assert.equal(resourceDisplayTitle(row, { url: "https://b.example/" }), "https://b.example/");
});

test("resource type order ignores branch names and keeps unknown kinds in other", () => {
  const rows = ["mystery", "remote", "docker", "application", "terminal", "desktop", "vm", "web"].map((kind, i) => ({
    id: String(i), source: "usage", kind, title: "Open Baidu", branchId: "branch", branchName: "Research", status: "open",
  }));
  const groups = groupSessionResources(rows);
  assert.deepEqual(groups.map(group => group.key), ["web", "vm", "desktop", "terminal", "application", "docker", "remote", "other"]);
  assert.equal(groups.at(-1).rows[0].kind, "mystery");
  assert.deepEqual(groupSessionResources(rows.map(row => ({ ...row, title: "Renamed", branchName: "Renamed branch" }))).map(group => group.key), groups.map(group => group.key));
});

test("closed Pages leave active type groups", () => {
  const rows = backendResourceRows([
    browserItem({ id: "assoc-live", status: "open", control_state: "active" }),
    browserItem({
      id: "assoc-dead", resource_id: "page-dead", title: "Closed",
      status: "closed", control_state: "closed", sequence: 2,
    }),
  ], "a");
  const groups = groupSessionResources(rows);
  assert.deepEqual(groups.map(group => group.key), ["web"]);
  assert.equal(groups[0].rows.length, 1);
  assert.equal(groups[0].rows[0].id, "assoc-live");
  assert.equal(groups.length, 1);
});

test("retained live Pages stay in the type group across closed control and restore statuses", () => {
  const retained = [
    browserItem({ status: "open", control_state: "closed" }),
    browserItem({ id: "assoc-unknown", resource_id: "page-u", status: "unknown", control_state: "closed", sequence: 2 }),
    browserItem({ id: "assoc-restoring", resource_id: "page-r", status: "restoring", control_state: "unknown", sequence: 3 }),
    browserItem({ id: "assoc-failed", resource_id: "page-f", status: "restore_failed", control_state: "unknown", sequence: 4 }),
  ].map(item => backendResourceRows([item], "a")[0]);
  for (const row of retained) {
    assert.equal(resourceIsUnavailable(row), false, row.status);
  }
  const groups = groupSessionResources(retained);
  assert.deepEqual(groups.map(group => group.key), ["web"]);
  assert.equal(groups[0].rows.length, 4);
  assert.equal(resourceIsUnavailable(backendResourceRows([
    browserItem({ status: "closed", control_state: "unknown" }),
  ], "a")[0]), true);
});

test("listed rows do not add a synthetic tab duplicate for an existing Page", () => {
  const backend = backendResourceRows([browserItem({ tab_id: "w:a" })], "a");
  const listed = sessionResourceRows(tabs, backend, "a");
  assert.equal(listed.filter(row => row.kind === "web").length, 1);
  assert.equal(listed[0].id, "assoc-a");
  assert.ok(!listed.some(row => row.id === "tab:w:a"));
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

test("clicking a Page previews it until the Agent operates another one", () => {
  resetBrowserResources();
  const sessionTabs = [
    { id: "s:a", kind: "session", sessionId: "a" },
    { id: "w:a", kind: "web" },
    { id: "w:b", kind: "web" },
  ];
  ingestBrowserResource(browserItem(), "a");
  ingestBrowserResource(browserItem({
    id: "assoc-b", resource_id: "page-b", tab_id: "w:b", title: "Pricing",
    target: "https://b.test", sequence: 2,
  }), "a");
  followCurrentBranch("a", "br-a");
  previewResource("a", "br-a", "assoc-a");
  assert.equal(getPreviewPreference("a", "br-a").mode, "follow");
  assert.deepEqual(followPreviewBinding("a", "br-a", sessionTabs), { tabId: "w:a", ownerTabId: "s:a" });
  ingestBrowserResource(browserItem({
    id: "assoc-b", resource_id: "page-b", tab_id: "w:b", title: "Pricing",
    target: "https://b.test", sequence: 3,
    last_operation: { id: "op-observe", action: "observe", phase: "acknowledged" },
  }), "a");
  assert.deepEqual(followPreviewBinding("a", "br-a", sessionTabs), { tabId: "w:b", ownerTabId: "s:a" });
});

test("a Page listed only from its tab can still be previewed", () => {
  resetBrowserResources();
  const sessionTabs = [
    { id: "s:a", kind: "session", sessionId: "a" },
    { id: "w:popup", kind: "web" },
  ];
  previewResource("a", "br-a", "tab:w:popup");
  assert.deepEqual(followPreviewBinding("a", "br-a", sessionTabs), { tabId: "w:popup", ownerTabId: "s:a" });
  previewResource("a", "br-a", "tab:w:gone");
  assert.equal(followPreviewBinding("a", "br-a", sessionTabs), null);
});

test("a pin is not stored: a reload follows the Agent again", () => {
  const saved = new Map();
  const previous = globalThis.localStorage;
  globalThis.localStorage = { getItem: (key) => saved.get(key) ?? null, setItem: (key, value) => saved.set(key, value), removeItem: (key) => saved.delete(key) };
  try {
    resetBrowserResources();
    selectResourcePreview("pinned", "br", "tab:w:page");
    assert.equal(getPreviewPreference("pinned", "br").mode, "manual", "the pin holds for this page load");
    followNativeWindow("app", 7);
    const stored = [...saved.values()].map((value) => Object.values(JSON.parse(value))).at(-1);
    assert.ok(stored.length >= 2);
    assert.ok(stored.every((pref) => pref.mode === "follow"), "no stored pin");
    assert.ok(stored.some((pref) => pref.targetId === "tab:w:page"), "a Page target is kept");
    assert.ok(!stored.some((pref) => String(pref.targetId).startsWith("native:")), "app window targets are not stored");
  } finally {
    globalThis.localStorage = previous;
    resetBrowserResources();
  }
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

test("readers asking for one conversation at once share a single request", async () => {
  resetBrowserResources();
  let reads = 0;
  let answer;
  const get = () => { reads += 1; return new Promise(resolve => { answer = resolve; }); };
  const first = recoverSessionResources("a", get);
  const second = recoverSessionResources("a", get);
  assert.equal(reads, 1);
  answer({ items: [browserItem()] });
  await Promise.all([first, second]);
  await recoverSessionResources("a", async () => { reads += 1; return { items: [] }; });
  assert.equal(reads, 2, "a later read asks again");
});

test("persistent terminals use the same scoped resource list and closed grouping", async () => {
  const { terminalResourceRows } = await import("../../../lib/chat/resources/session-resources.ts");
  const records = [{ terminal_id: "t", generation: "g", preset: "shell", status: "running",
    start_cwd: "/project", session_ids: ["a"], shared: true, in_use: true }];
  const terminalRows = terminalResourceRows(records, "a");
  const rows = sessionResourceRows(tabs, terminalRows, "a");
  assert.deepEqual(rows.map(row => row.kind), ["web", "terminal"]);
  assert.equal(terminalRows[0].sourceId, "t");
  assert.deepEqual(terminalResourceRows(records, "b"), []);
  assert.deepEqual(terminalResourceRows(records, null), []);
  const exited = terminalResourceRows([{ ...records[0], status: "exited" }], "a");
  assert.deepEqual(groupSessionResources(exited), []);
});

test("the preview follows a desktop app the Agent operates, then the next Page", async () => {
  resetBrowserResources();
  const { ingestNativeWindow, nativeWindowFrameUrl, useNativeWindows } = await import("../../../lib/browser/control/native-window-store.ts");
  const { nativeWindowPreview } = await import("../../../lib/chat/resources/session-resources.ts");
  useNativeWindows.setState({ windows: {} });
  useBrowserResourceStore.setState({ viewedBranch: { a: "br-a" } });
  const sessionTabs = [{ id: "s:a", kind: "session", sessionId: "a" }, { id: "w:a", kind: "web" }];
  ingestBrowserResource(browserItem({
    sequence: 2, last_operation: { id: "op-web-1", action: "click", phase: "dispatched" },
  }), "a");
  assert.deepEqual(followPreviewBinding("a", "br-a", sessionTabs), { tabId: "w:a", ownerTabId: "s:a" });

  const shown = ingestNativeWindow({
    session_id: "a", execution_id: "exec", window_id: 34327, pid: 9, app_name: "WeChat",
    sequence: 1, has_frame: true,
  });
  assert.equal(nativeWindowPreview("a", "br-a"), 34327);
  assert.equal(followPreviewBinding("a", "br-a", sessionTabs), null, "the Page preview steps aside");
  assert.equal(nativeWindowFrameUrl(shown), "/api/session/a/native-window/frame?window_id=34327&v=1");
  assert.equal(ingestNativeWindow({ ...shown, session_id: "a", window_id: 1, app_name: "Old", sequence: 1 }).windowId,
    34327, "a repeated or older event changes nothing");

  ingestBrowserResource(browserItem({
    sequence: 3, last_operation: { id: "op-web-2", action: "click", phase: "dispatched" },
  }), "a");
  assert.equal(nativeWindowPreview("a", "br-a"), null);
  assert.deepEqual(followPreviewBinding("a", "br-a", sessionTabs), { tabId: "w:a", ownerTabId: "s:a" });

  hideResourcePreview("a", "br-a");
  ingestNativeWindow({ session_id: "a", window_id: 34327, pid: 9, app_name: "WeChat", sequence: 2, has_frame: false });
  assert.equal(nativeWindowPreview("a", "br-a"), null, "a closed preview stays closed");
});

test("a pinned Page is not displaced by desktop app steps", async () => {
  resetBrowserResources();
  const { ingestNativeWindow, useNativeWindows } = await import("../../../lib/browser/control/native-window-store.ts");
  const { nativeWindowPreview } = await import("../../../lib/chat/resources/session-resources.ts");
  useNativeWindows.setState({ windows: {} });
  useBrowserResourceStore.setState({ viewedBranch: { a: "br-a" } });
  ingestBrowserResource(browserItem(), "a");
  selectResourcePreview("a", "br-a", "assoc-a");
  ingestNativeWindow({ session_id: "a", window_id: 5, pid: 9, app_name: "Notes", sequence: 1, has_frame: true });
  assert.equal(nativeWindowPreview("a", "br-a"), null);
  assert.equal(getPreviewPreference("a", "br-a").targetId, "assoc-a");
});

test("default resource groups are listed even when they hold nothing", () => {
  const groups = groupSessionResources([], null, ["web", "application", "terminal"]);
  assert.deepEqual(groups.map(group => [group.key, group.rows.length]), [["web", 0], ["terminal", 0], ["application", 0]]);
  assert.deepEqual(groupSessionResources([]), []);
});
