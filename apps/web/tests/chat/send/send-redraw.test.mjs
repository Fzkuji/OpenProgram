// Sending a message redraws what changed. The run's status frames and the
// rows being written replace store objects many times a second; a reader
// that takes one of those objects whole redraws with each of them.
import assert from "node:assert/strict";
import { test } from "node:test";

import { installLoader } from "../../_support/load.mjs";
import { installDom, render } from "../../_support/dom.mjs";

installLoader();
installDom();

const { createElement: h } = await import("react");
const { useSessionStore } = await import("../../../lib/session-store/index.ts");
const { useTaskStore } = await import("../../../lib/chat/transcript/task-blocks.ts");
const { useRowTasks } = await import("../../../lib/chat/transcript/use-row-tasks.ts");

test("the same worker badge keeps its object, a different one replaces it", () => {
  const store = useSessionStore.getState();
  store.setStatusBadge({ label: "Local", tone: "ok", title: "Connected" });
  const held = useSessionStore.getState().statusBadge;
  // top-bar-sync derives the badge again on every run frame.
  store.setStatusBadge({ label: "Local", tone: "ok", title: "Connected" });
  assert.equal(useSessionStore.getState().statusBadge, held);
  store.setStatusBadge({ label: "Local", tone: "ok", title: "Connected", paused: true });
  assert.notEqual(useSessionStore.getState().statusBadge, held);
  assert.equal(useSessionStore.getState().statusBadge.paused, true);
});

test("a reply is not redrawn when another row of its conversation is written", async () => {
  let renders = 0;
  let tasks;
  function Reply() { tasks = useRowTasks("s1", "s1", "a1"); renders++; return null; }
  const rows = { u1: { id: "u1", role: "user", content: "q" }, a1: { id: "a1", role: "assistant", content: "" } };
  useSessionStore.setState({ rowsByView: { s1: rows }, messageOrder: { s1: ["u1", "a1"] } });
  const view = await render(h(Reply));
  const before = renders;

  // No tasks in the conversation: the row order is not read at all.
  await view.act(async () => { useSessionStore.setState({ messageOrder: { s1: ["u1", "a1", "u2"] } }); });
  assert.equal(renders, before, "a new row does not redraw a reply without tasks");

  // With a task, the reply follows the row sequence, not the array the
  // store writes anew with every streamed token.
  const segment = {
    taskId: "t1", scope: "main", anchorId: "a1", title: "Task", state: "running", steps: [],
    startedAt: 1, updatedAt: 1,
  };
  await view.act(async () => { useTaskStore.setState({ segmentsBySession: { s1: [segment] } }); });
  assert.deepEqual(tasks.segments.map((held) => held.taskId), ["t1"]);
  const withTask = renders;
  await view.act(async () => { useSessionStore.setState({ messageOrder: { s1: ["u1", "a1", "u2"] } }); });
  assert.equal(renders, withTask, "the same sequence in a new array redraws nothing");
  await view.act(async () => { useSessionStore.setState({ messageOrder: { s1: ["u1", "a1", "u2", "a2"] } }); });
  assert.equal(renders, withTask + 1, "a row added to the sequence places the tasks again");
  await view.unmount();
});
