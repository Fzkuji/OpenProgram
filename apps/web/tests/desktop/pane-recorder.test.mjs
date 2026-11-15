import assert from "node:assert/strict";
import test from "node:test";

const timers = [];
globalThis.window = {
  innerWidth: 1512, innerHeight: 848, devicePixelRatio: 2,
  setTimeout: (fn, ms) => { timers.push([fn, ms]); return timers.length; },
  clearTimeout() {}, setInterval() { return 0; }, clearInterval() {},
};

const { choosePane, handleRecordCommand, visiblePanes } = await import("../../lib/desktop/recording/pane-recorder.ts");

const element = (dataset, width = 800, height = 600) => ({ dataset, offsetWidth: width, offsetHeight: height });
const tabs = [
  { id: "t-chat", kind: "session", title: "Plan", sessionId: "s1" },
  { id: "t-file", kind: "file", title: "notes.md" },
  { id: "t-web", kind: "web", title: "example.com" },
  { id: "t-hidden", kind: "builtin", title: "Settings" },
];
const root = {
  querySelectorAll: () => [
    element({ centerTabId: "t-chat" }),
    element({ filePaneId: "t-file" }, 400, 600),
    element({ centerTabId: "t-web" }),
    element({ centerTabId: "t-hidden" }, 0, 0),
    element({ centerTabId: "t-unknown" }),
    element({ centerTabId: "t-chat" }),
  ],
};
const size = { width: 1512, height: 848 };

test("the panes on screen are the tabs shown, once each", () => {
  const panes = visiblePanes(tabs, root);
  assert.deepEqual(panes.map(({ id, kind, title, width, height }) => ({ id, kind, title, width, height })), [
    { id: "t-chat", kind: "conversation", title: "Plan", width: 800, height: 600 },
    { id: "t-file", kind: "file", title: "notes.md", width: 400, height: 600 },
    { id: "t-web", kind: "web", title: "example.com", width: 800, height: 600 },
  ]);
});

test("a pane is named by id, as this conversation's own, or as the whole window", () => {
  const panes = visiblePanes(tabs, root);
  assert.equal(choosePane("t-file", "s1", panes, size).pane.id, "t-file");
  assert.equal(choosePane("conversation", "s1", panes, size).pane.id, "t-chat");
  assert.deepEqual(choosePane("window", "s9", panes, size).pane, { id: "window", kind: "window", title: "", width: 1512, height: 848, element: null });
  assert.equal(choosePane("conversation", "s9", panes, size).reason_code, "conversation_not_shown");
  assert.equal(choosePane("nope", "s1", panes, size).reason_code, "unknown_pane");
  assert.equal(choosePane(undefined, "s1", panes, size).reason_code, "unknown_pane");
  assert.equal(choosePane("t-web", "s1", panes, size).reason_code, "web_tab_not_recordable");
});

test("the window that shows the conversation answers at once, the main window after it, others never", async () => {
  globalThis.document = root;
  const replies = [];
  timers.length = 0;
  handleRecordCommand({ windowId: "w2" }, { op: "record_panes", session_id: "s9" }, tabs, r => replies.push(r));
  assert.equal(timers.length, 0, "a window that neither shows it nor is main stays silent");

  handleRecordCommand({ windowId: "w2" }, { op: "record_panes", session_id: "s1" }, tabs, r => replies.push(r));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(replies.length, 1);
  assert.equal(replies[0].ok, true);
  assert.equal(replies[0].panes.length, 3);

  handleRecordCommand({ windowId: "main" }, { op: "record_stop", session_id: "s9" }, tabs, r => replies.push(r));
  assert.equal(timers.length, 1);
  assert.equal(timers[0][1], 400);
  timers[0][0]();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(replies[1].reason_code, "not_recording");
});

test("a shell without the recording bridge refuses to start", async () => {
  globalThis.document = root;
  const replies = [];
  handleRecordCommand({ windowId: "main" }, { op: "record_start", session_id: "s1", pane: "conversation", path: "/x.mp4", max_duration: 5 }, tabs, r => replies.push(r));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(replies[0].reason_code, "recording_unavailable");
});
