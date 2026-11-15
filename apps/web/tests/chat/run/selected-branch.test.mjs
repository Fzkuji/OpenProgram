// Frames of a turn on a branch the person switched away from are not drawn
// and change no run state on the branch shown.
import assert from "node:assert/strict";
import { test } from "node:test";

import { installLoader } from "../../_support/load.mjs";
import { installDom } from "../../_support/dom.mjs";

installLoader();
installDom({ pathname: "/chat" });
globalThis.WebSocket = { OPEN: 1 };

const { offSelectedBranch } = await import("../../../lib/chat/run/selected-branch.ts");
const { useSessionStore } = await import("../../../lib/session-store/index.ts");
const { wsHandleChatResponse } = await import("../../../lib/runtime-bridge/session/chat-handlers.ts");

test("a frame is off the selection only when both branches are known and differ", () => {
  assert.equal(offSelectedBranch("branch_a", "branch_b"), true);
  assert.equal(offSelectedBranch("branch_b", "branch_b"), false);
  assert.equal(offSelectedBranch(undefined, "branch_b"), false);
  assert.equal(offSelectedBranch("branch_a", null), false);
  assert.equal(offSelectedBranch("", "branch_b"), false);
});

test("the end of a turn on the branch left does not clear the selected branch's run", () => {
  const store = useSessionStore.getState();
  store.setComposerSettings({ active_branch_id: "branch_b" }, "s");
  store.setRunningTaskFor("s", { session_id: "s", msg_id: "on_b", execution_id: "exec_b" }, "never");
  wsHandleChatResponse({ type: "result", session_id: "s", msg_id: "on_a", execution_id: "exec_a",
    branch_id: "branch_a", content: "done" });
  assert.equal(useSessionStore.getState().runningTasks.s?.execution_id, "exec_b");
  wsHandleChatResponse({ type: "result", session_id: "s", msg_id: "on_b", execution_id: "exec_b",
    branch_id: "branch_b", content: "done" });
  assert.notEqual(useSessionStore.getState().runningTasks.s?.execution_id, "exec_b");
});
