/**
 * The composer's function dispatch (use-function-dispatch), rendered as a
 * hook over the real session store, center tabs and desktop bridge. HTTP
 * and the WebSocket send are recorded by stubs.
 */
import assert from "node:assert/strict";
import { beforeEach, describe, test } from "node:test";

import { installLoader } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

installLoader({
  stubs: {
    "@/components/ui/confirm-dialog": "export const confirmAction = async () => true;"
      + " export const ConfirmDialog = () => null; export const ConfirmDialogHost = () => null;",
  },
});
installDom({ pathname: "/chat" });
globalThis.WebSocket = { OPEN: 1 };

const { createElement } = await import("react");
const { useFunctionDispatch } = await import("../../components/chat/composer/modes/fn-form/use-function-dispatch.ts");
const { selectRow, useSessionStore } = await import("../../lib/session-store/index.ts");
const { useCenterTabs, sessionTabId } = await import("../../lib/tabs/center-tabs-store.ts");
const { runtimeState } = await import("../../lib/runtime-bridge/state.ts");

const fn = { name: "summarize", params: [] };
let requests;
let respond;
let replaced;
let sentFrames;
let conversations;

globalThis.fetch = async (url, init) => {
  requests.push({ url, body: JSON.parse(init.body) });
  return respond();
};
window.history.replaceState = (_state, _title, path) => {
  replaced.push(path);
  window.location.pathname = path;
};

beforeEach(() => {
  requests = [];
  replaced = [];
  sentFrames = [];
  conversations = [];
  respond = () => Response.json({ session_id: "real-1" });
  window.location.pathname = "/chat";
  window.openprogramDesktop = { windowId: "win-1" };
  runtimeState.currentSessionId = null;
  runtimeState.isRunning = false;
  useCenterTabs.setState({
    tabs: [{ id: sessionTabId("local_fn"), kind: "session", title: "", sessionId: "local_fn", draft: true }],
    activeId: sessionTabId("local_fn"),
    groups: [],
  });
  useSessionStore.getState().setCurrentDraft("local_fn");
  useSessionStore.getState().setPendingProject("local_fn", "proj-1");
});

async function dispatch({ background = false, isRunning = false } = {}) {
  let dispatcher;
  function Probe() {
    dispatcher = useFunctionDispatch({
      currentSessionId: null,
      activeChatKey: "local_fn",
      background,
      isRunning,
      noEnabledModels: false,
      promptNeedModel() {},
      send: (frame) => { sentFrames.push(frame); return true; },
      setCurrentConv: (sid) => conversations.push(sid),
    });
    return null;
  }
  const view = await render(createElement(Probe));
  let accepted;
  await view.act(async () => {
    accepted = dispatcher(fn, { text: "hi" });
    for (let i = 0; i < 5; i++) await new Promise((resolve) => setTimeout(resolve, 0));
  });
  await view.unmount();
  return accepted;
}

describe("function dispatch", () => {
  test("the request names the draft session, its pending project, the window and the chat surface", async () => {
    assert.equal(await dispatch(), true);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, "/api/function/summarize");
    const body = requests[0].body;
    assert.deepEqual(body.kwargs, { text: "hi" });
    assert.equal(body.session_id, "local_fn");
    assert.equal(body.project_id, "proj-1");
    assert.equal(body.window_id, "win-1");
    assert.equal(body.surface_ref.window_id, "win-1");
    assert.equal(typeof body.client_request_id, "string");
  });

  test("the placeholder is a typed local row at the tail, named by the request's id", async () => {
    let placeholder;
    respond = () => {
      const store = useSessionStore.getState();
      placeholder = selectRow("local_fn", store.runningTasks.local_fn.msg_id)(store);
      return Response.json({ session_id: "real-1" });
    };
    await dispatch();
    assert.equal(placeholder.rowKind, "function_run");
    assert.equal(placeholder.localAnchor, "tail");
    assert.equal(placeholder.clientRequestId, requests[0].body.client_request_id);
  });

  test("the placeholder and the running task share one start time", async () => {
    let placeholder;
    let task;
    respond = () => {
      const store = useSessionStore.getState();
      task = store.runningTasks.local_fn;
      placeholder = selectRow("local_fn", task.msg_id)(store);
      return Response.json({ session_id: "real-1" });
    };
    await dispatch();
    assert.equal(placeholder.function, "summarize");
    assert.equal(task.started_at, placeholder.timestamp / 1000);
  });

  test("on the ACK a foreground dispatch moves to the new session and /chat becomes /s/<id>", async () => {
    await dispatch();
    assert.deepEqual(conversations, ["real-1"]);
    assert.deepEqual(replaced, ["/s/real-1"], "the URL is replaced, not pushed");
    assert.equal(runtimeState.currentSessionId, "real-1");
    assert.ok(sentFrames.some((frame) => frame.action === "load_session" && frame.session_id === "real-1"));
  });

  test("the pending project is attached to the new session and consumed", async () => {
    await dispatch();
    assert.ok(sentFrames.some((frame) =>
      frame.action === "set_session_project" && frame.session_id === "real-1" && frame.project_id === "proj-1"));
    assert.equal(useSessionStore.getState().pendingProjectsByChat.local_fn, undefined);
  });

  test("a background dispatch never navigates or flips the focused shell's running state", async () => {
    await dispatch({ background: true });
    assert.equal(requests.length, 1);
    assert.deepEqual(conversations, []);
    assert.deepEqual(replaced, []);
    assert.equal(sentFrames.some((frame) => frame.action === "load_session"), false);
    assert.equal(runtimeState.isRunning, false);
  });

  test("a page shown from the tab keeps its own URL", async () => {
    window.location.pathname = "/settings";
    await dispatch();
    assert.deepEqual(replaced, []);
  });

  test("a failed call removes the placeholder and frees the draft", async () => {
    let placeholderId;
    respond = () => {
      placeholderId = useSessionStore.getState().runningTasks.local_fn.msg_id;
      return Response.json({ error: "boom" }, { status: 500 });
    };
    const original = console.error;
    console.error = () => {};
    try {
      await dispatch();
    } finally {
      console.error = original;
    }
    const store = useSessionStore.getState();
    assert.equal(store.runningTasks.local_fn, undefined);
    assert.ok(placeholderId);
    assert.equal((store.messageOrder.local_fn ?? []).includes(placeholderId), false);
    assert.equal(runtimeState.isRunning, false);
  });

  test("a dispatch while a run is active is refused without a request", async () => {
    assert.equal(await dispatch({ isRunning: true }), false);
    assert.equal(requests.length, 0);
  });
});
