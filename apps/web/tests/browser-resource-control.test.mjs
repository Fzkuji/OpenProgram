import test from "node:test";
import assert from "node:assert/strict";
import {
  displayedControlState,
  isHumanYieldEvent,
  markScopeYielding,
  recordOperationCue,
  resetBrowserControl,
  signalHumanBrowserInput,
  toggleShowActions,
  operationHistory,
  liveOperationMarker,
  requestCloseBrowserPage,
  requestExplicitPause,
  requestResumeAgent,
  selectTabsReadyForHumanClose,
  pendingCloseRequest,
  settlePendingClose,
  useBrowserControlStore,
} from "../lib/state/browser-control.ts";
import { ingestBrowserResource, listedBrowserResources, resetBrowserResources, setBrowserConnection } from "../lib/state/session-resources.ts";

function ingestPage(overrides = {}) {
  const raw = {
    id: "assoc-a",
    resource_id: "page-a",
    session_id: "a",
    conversation_session_id: "a",
    tab_id: "w:a",
    kind: "web",
    title: "Plans",
    target: "https://a.test",
    status: "open",
    source: "browser",
    control_state: "active",
    generation: 1,
    sequence: 1,
    execution_id: "exec-a",
    ...overrides,
  };
  ingestBrowserResource(raw, raw.conversation_session_id || raw.session_id);
  return listedBrowserResources().find(item => item.id === raw.id);
}

function resource(overrides = {}) {
  return {
    id: "assoc-a",
    resourceId: "page-a",
    tabId: "w:a",
    conversationSessionId: "a",
    generation: 1,
    controlState: "active",
    ...overrides,
  };
}

test("focus and tab navigation do not yield; pointer scroll key and nav do", () => {
  assert.equal(isHumanYieldEvent({ type: "focus" }), false);
  assert.equal(isHumanYieldEvent({ type: "keydown", key: "Tab" }), false);
  assert.equal(isHumanYieldEvent({ type: "keydown", key: "Shift" }), false);
  assert.equal(isHumanYieldEvent({ type: "keydown", key: "Meta" }), false);
  assert.equal(isHumanYieldEvent({ type: "pointermove" }), false);
  assert.equal(isHumanYieldEvent({ type: "pointerdown" }), true);
  assert.equal(isHumanYieldEvent({ type: "wheel" }), true);
  assert.equal(isHumanYieldEvent({ type: "keydown", key: "a" }), true);
  assert.equal(isHumanYieldEvent({ type: "key" }), true);
  assert.equal(isHumanYieldEvent({ type: "keydown" }), true);
  assert.equal(isHumanYieldEvent({ type: "navigate" }), true);
});

test("native input marks yielding without posting and shares a generation pending pause", async () => {
  resetBrowserControl();
  const posted = [];
  assert.equal(markScopeYielding(resource()), "yielding");
  await signalHumanBrowserInput(resource(), {
    postControl: async (input) => { posted.push(input); return { control_state: "yielding" }; },
  });
  const again = await requestExplicitPause(resource(), {
    postControl: async (input) => { posted.push(input); return { control_state: "yielding" }; },
  });
  assert.equal(posted.length, 1);
  assert.equal(again, "yielding");
});

test("yielding still becomes stop_unconfirmed after five seconds", () => {
  resetBrowserControl();
  markScopeYielding(resource(), { now: () => 1000 });
  assert.equal(displayedControlState(resource({ controlState: "yielding" }), { now: 1000 }), "yielding");
  assert.equal(displayedControlState(resource({ controlState: "yielding" }), { now: 7000 }), "stop_unconfirmed");
});

test("rejected resume stays paused and disconnect disables it", async () => {
  resetBrowserControl();
  resetBrowserResources();
  setBrowserConnection(true);
  const resume = await requestResumeAgent(resource({ controlState: "paused" }), {
    postControl: async () => { throw new Error("not authorized"); },
  });
  assert.equal(resume, "paused");
  setBrowserConnection(false);
  assert.equal(displayedControlState(resource({ controlState: "paused" })), "unknown");
  assert.equal(await requestResumeAgent(resource({ controlState: "paused" })), null);
  setBrowserConnection(true);
});

test("human input requests pause and stays yielding until backend pause", async () => {
  resetBrowserControl();
  const posted = [];
  const state = await signalHumanBrowserInput(resource(), {
    postControl: async (input) => {
      posted.push(input);
      return { ...resource(), controlState: "yielding" };
    },
  });
  assert.equal(posted[0].action, "pause");
  assert.equal(state, "yielding");
  assert.equal(displayedControlState(resource({ controlState: "active" })), "yielding");
  assert.equal(displayedControlState(resource({ controlState: "unknown" })), "yielding");
  assert.notEqual(displayedControlState(resource({ controlState: "unknown" })), "paused");
  assert.equal(displayedControlState(resource({ controlState: "paused" })), "paused");
});

test("failed control is not paused and resume stays disabled until pause", async () => {
  resetBrowserControl();
  await signalHumanBrowserInput(resource(), {
    postControl: async () => ({ ...resource(), controlState: "unknown" }),
  });
  assert.equal(displayedControlState(resource({ controlState: "unknown" })), "yielding");
  const resume = await requestResumeAgent(resource({ controlState: "yielding" }), {
    postControl: async () => ({ ...resource(), controlState: "paused" }),
  });
  assert.equal(resume, null);
  assert.equal(displayedControlState(resource({ controlState: "paused" })), "paused");
  const ok = await requestResumeAgent(resource({ controlState: "paused" }), {
    postControl: async (input) => {
      assert.equal(input.action, "resume");
      return { ...resource(), controlState: "active" };
    },
  });
  assert.equal(ok, "active");
});

test("show actions and history stay independent from pause and follow", () => {
  resetBrowserControl();
  const validPoint = { x: 10, y: 20, width: 100, height: 80 };
  recordOperationCue({
    resourceId: "page-a",
    generation: 1,
    operation: { id: "op-1", action: "click", phase: "acknowledged", frame_id: "frame-1", geometry_revision: 3, point: validPoint },
  });
  assert.equal(toggleShowActions(), false);
  assert.equal(liveOperationMarker("page-a"), null);
  assert.equal(toggleShowActions(), true);
  assert.equal(liveOperationMarker("page-a"), null);
  recordOperationCue({
    resourceId: "page-a",
    generation: 1,
    operation: { id: "op-2", action: "click", phase: "acknowledged", frame_id: "frame-1", geometry_revision: 3, point: validPoint },
    now: 10,
    ttlMs: 100,
  });
  assert.equal(liveOperationMarker("page-a", { generation: 1, now: 50 })?.id, "op-2");
  assert.equal(liveOperationMarker("page-a", { generation: 2, now: 50 }), null);
  assert.equal(liveOperationMarker("page-a", { generation: 1, now: 200 }), null);
  assert.deepEqual(operationHistory("page-a").map(item => item.id), ["op-1", "op-2"]);
  signalHumanBrowserInput(resource(), { postControl: async () => resource({ controlState: "yielding" }) });
  assert.equal(liveOperationMarker("page-a"), null);
  assert.equal(operationHistory("page-a")[0].id, "op-1");
});

test("live markers require frame viewport and geometry and do not remint the same operation", () => {
  resetBrowserControl();
  const operation = {
    id: "op-replay",
    action: "click",
    phase: "acknowledged",
    frame_id: "frame-1",
    geometry_revision: 4,
    point: { x: 400, y: 300, width: 800, height: 600 },
  };
  recordOperationCue({ resourceId: "page-a", generation: 1, operation: { ...operation, frame_id: undefined, point: { x: 400, y: 300, width: 800, height: 600 } }, now: 0, ttlMs: 50 });
  assert.equal(liveOperationMarker("page-a", { generation: 1, now: 10 }), null);
  resetBrowserControl();
  recordOperationCue({ resourceId: "page-a", generation: 1, operation, geometryRevision: 9, now: 0, ttlMs: 50 });
  assert.equal(liveOperationMarker("page-a", { generation: 1, now: 10 }), null);
  resetBrowserControl();
  recordOperationCue({ resourceId: "page-a", generation: 1, operation, geometryRevision: 4, now: 0, ttlMs: 50 });
  assert.equal(liveOperationMarker("page-a", { generation: 1, now: 10 })?.id, "op-replay");
  assert.equal(liveOperationMarker("page-a", { generation: 1, now: 60 }), null);
  recordOperationCue({ resourceId: "page-a", generation: 1, operation, geometryRevision: 4, now: 70, ttlMs: 50 });
  assert.equal(liveOperationMarker("page-a", { generation: 1, now: 80 }), null);
  assert.equal(operationHistory("page-a")[0].phase, "acknowledged");
});

test("closing an active page waits for pause and idle pages close immediately", () => {
  resetBrowserControl();
  resetBrowserResources();
  ingestBrowserResource({
    id: "assoc-a", resource_id: "page-a", session_id: "a", conversation_session_id: "a",
    tab_id: "w:a", kind: "web", title: "Plans", target: "https://a.test",
    status: "open", source: "browser", control_state: "active", generation: 1, sequence: 1,
    execution_id: "exec-a",
  }, "a");
  const row = listedBrowserResources().find(item => item.id === "assoc-a");
  const closed = [];
  assert.equal(requestCloseBrowserPage(row, [{ id: "w:a" }]), "pending");
  assert.equal(settlePendingClose(id => closed.push(id)), false);
  ingestBrowserResource({
    id: "assoc-a", resource_id: "page-a", session_id: "a", conversation_session_id: "a",
    tab_id: "w:a", kind: "web", title: "Plans", target: "https://a.test",
    status: "open", source: "browser", control_state: "paused", generation: 1, sequence: 2,
    execution_id: "exec-a",
  }, "a");
  assert.equal(settlePendingClose(id => closed.push(id)), true);
  assert.deepEqual(closed, ["w:a"]);
  ingestBrowserResource({
    id: "assoc-idle", resource_id: "page-idle", session_id: "a", conversation_session_id: "a",
    tab_id: "w:idle", kind: "web", title: "Idle", target: "https://idle.test",
    status: "idle", source: "browser", control_state: "idle", generation: 1, sequence: 1,
  }, "a");
  const idle = listedBrowserResources().find(item => item.id === "assoc-idle");
  assert.equal(requestCloseBrowserPage(idle, [{ id: "w:idle" }]), "closed");
  assert.equal(requestCloseBrowserPage(idle, []), "unavailable");
});

test("human close defers an active Page and does not pause an unrelated tab", () => {
  resetBrowserControl();
  resetBrowserResources();
  ingestBrowserResource({
    id: "assoc-a", resource_id: "page-a", session_id: "a", conversation_session_id: "a",
    tab_id: "w:a", kind: "web", title: "Plans", target: "https://a.test",
    status: "open", source: "browser", control_state: "active", generation: 1, sequence: 1,
    execution_id: "exec-a",
  }, "a");
  const file = { id: "f:notes", kind: "file" };
  const session = { id: "s:a", kind: "session" };
  const page = { id: "w:a", kind: "web" };
  const other = { id: "w:other", kind: "web" };
  const ready = selectTabsReadyForHumanClose(
    [file, session, page, other],
    [file, session, page, other],
  );
  assert.deepEqual(ready.map(tab => tab.id), ["f:notes", "s:a", "w:other"]);
  const closed = [];
  assert.equal(settlePendingClose(id => closed.push(id)), false);
  assert.deepEqual(closed, []);
  ingestBrowserResource({
    id: "assoc-a", resource_id: "page-a", session_id: "a", conversation_session_id: "a",
    tab_id: "w:a", kind: "web", title: "Plans", target: "https://a.test",
    status: "open", source: "browser", control_state: "paused", generation: 1, sequence: 2,
    execution_id: "exec-a",
  }, "a");
  assert.equal(settlePendingClose(id => closed.push(id)), true);
  assert.deepEqual(closed, ["w:a"]);
});

test("unknown in-use Page is not removed and keeps the existing error", () => {
  resetBrowserControl();
  resetBrowserResources();
  ingestPage({ control_state: "unknown", execution_id: undefined });
  const page = { id: "w:a", kind: "web" };
  assert.deepEqual(selectTabsReadyForHumanClose([page], [page]), []);
  const closed = [];
  assert.equal(settlePendingClose(id => closed.push(id)), false);
  assert.equal(closed.length, 0);
  assert.equal(pendingCloseRequest()?.error, "Unknown");
});

test("stop_unconfirmed Page stays with the existing error and is not removed", () => {
  resetBrowserControl();
  resetBrowserResources();
  ingestPage({ control_state: "stop_unconfirmed", execution_id: undefined });
  const page = { id: "w:a", kind: "web" };
  assert.deepEqual(selectTabsReadyForHumanClose([page], [page]), []);
  const closed = [];
  assert.equal(settlePendingClose(id => closed.push(id)), false);
  assert.equal(closed.length, 0);
  assert.equal(pendingCloseRequest()?.error, "Stop unconfirmed");
});

test("closing an unrelated tab does not pause a browser Page", () => {
  resetBrowserControl();
  resetBrowserResources();
  ingestPage();
  const file = { id: "f:notes", kind: "file" };
  const session = { id: "s:a", kind: "session" };
  const other = { id: "w:other", kind: "web" };
  const page = { id: "w:a", kind: "web" };
  const ready = selectTabsReadyForHumanClose(
    [file, session, other],
    [file, session, other, page],
  );
  assert.deepEqual(ready.map(tab => tab.id), ["f:notes", "s:a", "w:other"]);
  assert.equal(useBrowserControlStore.getState().pendingCloses.length, 0);
  assert.deepEqual(useBrowserControlStore.getState().pending, {});
  const closed = [];
  assert.equal(settlePendingClose(id => closed.push(id)), false);
  assert.deepEqual(closed, []);
});

test("idle human close proceeds immediately and does not enqueue coordinator close", () => {
  resetBrowserControl();
  resetBrowserResources();
  ingestPage({
    id: "assoc-idle", resource_id: "page-idle", tab_id: "w:idle",
    title: "Idle", target: "https://idle.test", status: "idle", control_state: "idle",
    execution_id: undefined,
  });
  const idle = { id: "w:idle", kind: "web" };
  assert.deepEqual(selectTabsReadyForHumanClose([idle], [idle]).map(tab => tab.id), ["w:idle"]);
  assert.equal(pendingCloseRequest(), null);
  const closed = [];
  assert.equal(settlePendingClose(id => closed.push(id)), false);
  assert.deepEqual(closed, []);
});

test("yielding Page stays until pause acknowledgement then coordinator closes once", () => {
  resetBrowserControl();
  resetBrowserResources();
  ingestPage({ control_state: "yielding" });
  const page = { id: "w:a", kind: "web" };
  assert.deepEqual(selectTabsReadyForHumanClose([page], [page]), []);
  assert.equal(pendingCloseRequest()?.resourceId, "page-a");
  assert.equal(pendingCloseRequest()?.generation, 1);
  const closed = [];
  assert.equal(settlePendingClose(id => closed.push(id)), false);
  ingestPage({ control_state: "paused", sequence: 2 });
  assert.equal(settlePendingClose(id => closed.push(id)), true);
  assert.deepEqual(closed, ["w:a"]);
  assert.equal(settlePendingClose(id => closed.push(id)), false);
  assert.deepEqual(closed, ["w:a"]);
});

test("same-Page idle association does not release an in-use Page", () => {
  resetBrowserControl();
  resetBrowserResources();
  ingestPage({
    id: "assoc-idle", branch_id: "main", control_state: "idle", status: "idle",
    execution_id: "exec-idle", sequence: 1,
  });
  ingestPage({
    id: "assoc-live", branch_id: "child", control_state: "active", status: "open",
    execution_id: "exec-live", sequence: 2,
  });
  const page = { id: "w:a", kind: "web" };
  assert.deepEqual(selectTabsReadyForHumanClose([page], [page]), []);
  const pending = pendingCloseRequest();
  assert.equal(pending?.resourceId, "page-a");
  assert.equal(pending?.generation, 1);
  assert.equal(pending?.tabId, "w:a");
  assert.ok(pending?.associationIds.includes("assoc-idle"));
  assert.ok(pending?.associationIds.includes("assoc-live"));
  const closed = [];
  assert.equal(settlePendingClose(id => closed.push(id)), false);
  ingestPage({
    id: "assoc-idle", branch_id: "main", control_state: "idle", status: "idle",
    execution_id: "exec-idle", sequence: 3,
  });
  ingestPage({
    id: "assoc-live", branch_id: "child", control_state: "paused", status: "open",
    execution_id: "exec-live", sequence: 4,
  });
  assert.equal(settlePendingClose(id => closed.push(id)), true);
  assert.deepEqual(closed, ["w:a"]);
});

test("a later generation does not satisfy an earlier pending close", () => {
  resetBrowserControl();
  resetBrowserResources();
  ingestPage({ generation: 1, control_state: "active" });
  const page = { id: "w:a", kind: "web" };
  assert.deepEqual(selectTabsReadyForHumanClose([page], [page]), []);
  assert.equal(pendingCloseRequest()?.generation, 1);
  ingestPage({ generation: 2, control_state: "paused", sequence: 2 });
  const closed = [];
  assert.equal(settlePendingClose(id => closed.push(id)), false);
  assert.deepEqual(closed, []);
  assert.equal(useBrowserControlStore.getState().pendingCloses.some(item => item.generation === 1), false);
});

test("group human close pauses each in-use Page and leaves unrelated members immediate", () => {
  resetBrowserControl();
  resetBrowserResources();
  ingestPage({ id: "assoc-a", resource_id: "page-a", tab_id: "w:a", execution_id: "exec-a" });
  ingestPage({
    id: "assoc-b", resource_id: "page-b", tab_id: "w:b", title: "Other",
    target: "https://b.test", execution_id: "exec-b",
  });
  const a = { id: "w:a", kind: "web" };
  const b = { id: "w:b", kind: "web" };
  const file = { id: "f:notes", kind: "file" };
  const ready = selectTabsReadyForHumanClose([a, b, file], [a, b, file]);
  assert.deepEqual(ready.map(tab => tab.id), ["f:notes"]);
  const pendingIds = useBrowserControlStore.getState().pendingCloses.map(item => item.resourceId).sort();
  assert.deepEqual(pendingIds, ["page-a", "page-b"]);
  const closed = [];
  ingestPage({ id: "assoc-a", resource_id: "page-a", tab_id: "w:a", control_state: "paused", sequence: 2, execution_id: "exec-a" });
  assert.equal(settlePendingClose(id => closed.push(id)), true);
  assert.deepEqual(closed, ["w:a"]);
  ingestPage({
    id: "assoc-b", resource_id: "page-b", tab_id: "w:b", title: "Other",
    target: "https://b.test", control_state: "paused", sequence: 2, execution_id: "exec-b",
  });
  assert.equal(settlePendingClose(id => closed.push(id)), true);
  assert.deepEqual(closed, ["w:a", "w:b"]);
  assert.equal(settlePendingClose(id => closed.push(id)), false);
  assert.deepEqual(closed, ["w:a", "w:b"]);
});

test("stale incarnation on the same tab does not close a live Page", () => {
  resetBrowserControl();
  resetBrowserResources();
  ingestPage({
    id: "assoc-old", resource_id: "page-old", control_state: "idle", status: "idle",
    execution_id: undefined,
  });
  ingestPage({
    id: "assoc-new", resource_id: "page-new", control_state: "active", status: "open",
    execution_id: "exec-new", sequence: 2,
  });
  const page = { id: "w:a", kind: "web" };
  assert.deepEqual(selectTabsReadyForHumanClose([page], [page]), []);
  const pending = useBrowserControlStore.getState().pendingCloses;
  assert.deepEqual(pending.map(item => item.resourceId), ["page-new"]);
  assert.equal(pending[0].generation, 1);
});
