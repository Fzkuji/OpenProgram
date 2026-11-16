// Every surface says the same thing about a conversation whose turn runs:
// the sidebar row counts a turn on any branch, the composer's Stop follows
// the branch selected now, and a client that connects mid-turn learns which
// conversations run.
import assert from "node:assert/strict";
import { test } from "node:test";

import { installLoader } from "../../_support/load.mjs";
import { installDom } from "../../_support/dom.mjs";

installLoader();
installDom({ pathname: "/chat" });
globalThis.WebSocket = { OPEN: 1 };

const { applyRunState, listedRunStates, unlistedRunning } = await import("../../../lib/chat/run/running-sessions.ts");
const { sessionsWithBranchRun, useOtherBranchRuns } = await import("../../../lib/chat/run/other-branch-runs.ts");
const { useSessionStore } = await import("../../../lib/session-store/index.ts");
const { onSelectedBranch, restoreForegroundExecutionTask } = await import("../../../lib/runtime-bridge/session/chat-handlers.ts");
await import("../../../lib/chat/send/send-queue.ts");

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

test("a conversation with a turn in progress on another branch counts as running", () => {
  const runs = {
    goal: { branch_goal: { branchId: "branch_goal", name: null, state: "running" } },
    waiting: { branch_w: { branchId: "branch_w", name: null, state: "approval" } },
    failed: { branch_f: { branchId: "branch_f", name: null, state: "failed" } },
    idle: {},
  };
  assert.deepEqual(sessionsWithBranchRun(runs), { goal: true });
});

test("a Goal turn on a branch that is not selected lights the row and leaves the composer free", async () => {
  applyRunState({
    session_id: "goal", run_active: false, run_task: null, run_seq: 10,
    running_branches: [{ branch_id: "branch_goal", execution_id: "e-goal", msg_id: "m-goal", selected: false }],
  });
  await settle();
  assert.deepEqual(sessionsWithBranchRun(useOtherBranchRuns.getState().runs), { goal: true });
  assert.equal(useSessionStore.getState().runningTasks.goal, undefined);

  // Its end, as the server announces it for that branch.
  useOtherBranchRuns.getState().applyFrame("branch_running_task_clear",
    { session_id: "goal", branch_id: "branch_goal", execution_id: "e-goal" });
  assert.deepEqual(sessionsWithBranchRun(useOtherBranchRuns.getState().runs), {});
});

test("a client that connects mid-turn takes the running turn with the ids its Stop needs", async () => {
  const listed = listedRunStates({ sessions: [
    { session_id: "typed", run_active: true, run_seq: 20, running_branches: [],
      run_task: { msg_id: "m1", execution_id: "e1", status_version: 4, op_kind: "reply" } },
    { run_active: true },
    null,
  ] });
  assert.equal(listed.length, 1);
  for (const state of listed) applyRunState(state);
  await settle();
  const task = useSessionStore.getState().runningTasks.typed;
  assert.equal(task?.execution_id, "e1");
  assert.equal(task?.status_version, 4);
});

test("a conversation still shown running that the server no longer lists is asked about and cleared", async () => {
  useSessionStore.getState().setRunningTaskFor("stale", { session_id: "stale", msg_id: "old", execution_id: "e-old" }, "never");
  const listed = listedRunStates({ sessions: [{ session_id: "typed", run_active: true }] });
  assert.deepEqual(unlistedRunning(listed, ["typed", "stale", "open", "stale"], "open"), ["stale"]);
  // The answer to its own `get_run_state`.
  applyRunState({ session_id: "stale", run_active: false, run_task: null, running_branches: [], run_seq: 30 });
  await settle();
  assert.equal(useSessionStore.getState().runningTasks.stale, undefined);
});

test("a run state older than a frame already applied changes nothing", async () => {
  applyRunState({ session_id: "ordered", run_active: true, run_seq: 50,
    run_task: { msg_id: "m", execution_id: "e-new", status_version: 1 }, running_branches: [] });
  applyRunState({ session_id: "ordered", run_active: false, run_task: null, run_seq: 40, running_branches: [] });
  await settle();
  assert.equal(useSessionStore.getState().runningTasks.ordered?.execution_id, "e-new");
});

test("a durable turn is the page's running turn only on the branch selected now", () => {
  const store = useSessionStore.getState();
  // The page does not know the selection: the flag frozen at admission answers.
  assert.equal(onSelectedBranch({ session_id: "unknown", branch_id: "branch_a", side_branch: false }), true);
  assert.equal(onSelectedBranch({ session_id: "unknown", branch_id: "branch_a", side_branch: true }), false);

  store.setComposerSettings({ active_branch_id: "branch_b" }, "follow");
  assert.equal(onSelectedBranch({ session_id: "follow", branch_id: "branch_a", side_branch: false }), false);
  assert.equal(onSelectedBranch({ session_id: "follow", branch_id: "branch_b", side_branch: true }), true);

  // A Goal's turn on the branch left does not take the composer of the branch shown ...
  restoreForegroundExecutionTask(
    { session_id: "follow", execution_id: "e-a", msg_id: "m-a", status_version: 1, branch_id: "branch_a", side_branch: false },
    { session_id: "follow", execution_id: "e-a", status: "running" });
  assert.equal(useSessionStore.getState().runningTasks.follow, undefined);
  // ... and one on the branch shown does: Stop names it.
  restoreForegroundExecutionTask(
    { session_id: "follow", execution_id: "e-b", msg_id: "m-b", status_version: 2, branch_id: "branch_b", side_branch: false },
    { session_id: "follow", execution_id: "e-b", status: "running" });
  assert.equal(useSessionStore.getState().runningTasks.follow?.execution_id, "e-b");
});

test("a branch run that ended while the list was on its way stays ended", async () => {
  // The run is on, as this client heard it start.
  useOtherBranchRuns.getState().applyFrame("branch_running_task",
    { session_id: "late", branch_id: "b", execution_id: "e-b", msg_id: "m-b", run_seq: 100 });
  // It ends; its clear arrives before the answer the server made earlier.
  useOtherBranchRuns.getState().applyFrame("branch_running_task_clear",
    { session_id: "late", branch_id: "b", execution_id: "e-b", run_seq: 120 });
  applyRunState({ session_id: "late", run_active: false, run_task: null, run_seq: 110,
    running_branches: [{ branch_id: "b", execution_id: "e-b", msg_id: "m-b", selected: false }] });
  await settle();
  assert.deepEqual(sessionsWithBranchRun(useOtherBranchRuns.getState().runs), {});
});

test("a branch run that started while the list was on its way stays running", async () => {
  useOtherBranchRuns.getState().applyFrame("branch_running_task",
    { session_id: "early", branch_id: "b", execution_id: "e-b", msg_id: "m-b", run_seq: 220 });
  applyRunState({ session_id: "early", run_active: false, run_task: null, run_seq: 210, running_branches: [] });
  await settle();
  assert.deepEqual(sessionsWithBranchRun(useOtherBranchRuns.getState().runs), { early: true });
  // A frame made before that list is older than what the list said.
  useOtherBranchRuns.getState().applyFrame("branch_running_task_clear",
    { session_id: "early", branch_id: "b", execution_id: "e-b", run_seq: 205 });
  assert.deepEqual(sessionsWithBranchRun(useOtherBranchRuns.getState().runs), { early: true });
});
