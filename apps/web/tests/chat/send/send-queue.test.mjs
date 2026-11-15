/**
 * Send queue: messages typed while a turn is running.
 *
 * Drives the real send-chat-message, send-queue, steer and chat-handler
 * modules over a fake socket. The tests share one store and run in order:
 * queued entries drain one at a time, in order, only when the session goes
 * idle; the drain hangs off the running-task clear, with no polling; queues
 * are keyed by session. Also covers removal, ordering, Stop, and canonical
 * steering acknowledgements.
 *
 * OPENPROGRAM_TEST_CANCEL_FRAME: tests/component/webui/execution/
 * test_execution_cancellation.py runs this file to capture the exact cancel
 * frame Stop sends (the last test below), then feeds it to the server.
 */
import assert from "node:assert/strict";
import { writeFileSync } from "node:fs";
import { describe, test } from "node:test";

import { installLoader } from "../../_support/load.mjs";
import { installDom } from "../../_support/dom.mjs";

installLoader();
installDom({ pathname: "/chat" });
globalThis.WebSocket = { OPEN: 1 };

const sent = [];
const { setSocket } = await import("../../../lib/runtime-bridge/state.ts");
const openSocket = () => setSocket({ readyState: 1, send: (payload) => sent.push(JSON.parse(payload)) });
openSocket();

// Importing send-chat-message registers the real chat sender with the
// queue, the same wiring the app relies on.
await import("../../../components/chat/composer/submit/send-chat-message.ts");
const {
  useSendQueue,
  enqueueMessage,
  promoteToHead,
  requeueRejected,
  reconcileAfterSessionLoad,
  releaseStopHold,
} = await import("../../../lib/chat/send/send-queue.ts");
const { useSessionStore } = await import("../../../lib/session-store/index.ts");
const { stopSession } = await import("../../../components/chat/composer/submit/use-chat-submit.ts");
const { handleRunningTaskClear, restoreForegroundExecutionTask } = await import(
  "../../../lib/runtime-bridge/session/chat-handlers.ts"
);
const { steerQueuedMessage, takeBackQueuedSteer } = await import("../../../lib/chat/send/steer-message.ts");

const A = "sess_a";
const B = "sess_b";
const draft = (text) => ({ text, thinking: "medium", webSearchEnabled: false, background: false });
const store = useSessionStore.getState();
const run = (sid) => store.setRunningTaskFor(sid, { session_id: sid, msg_id: "m" });
const idle = (sid) => store.setRunningTaskFor(sid, null);
const queue = (sid) => useSendQueue.getState().queues[sid];
const texts = () => sent.map((m) => m.text);
// The drain hangs off a microtask + dynamic import inside the store
// setter; let both settle before asserting.
const settle = async () => {
  for (let i = 0; i < 5; i++) await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
};
const fresh = () => {
  sent.length = 0;
  useSendQueue.setState({ queues: {} });
};

describe("draining", () => {
  test("messages typed during a turn wait, then the turn ending sends exactly one, in order", async () => {
    run(A);
    enqueueMessage(A, draft("first"));
    enqueueMessage(A, draft("second"));
    assert.equal(queue(A).length, 2, "both messages park in the queue while the turn runs");
    assert.equal(sent.length, 0, "nothing goes out while the session is busy");

    idle(A);
    await settle();
    assert.equal(sent.length, 1, "the turn ending sends exactly ONE queued message");
    assert.equal(sent[0].text, "first", "the queue drains in the order typed");
    assert.equal(queue(A).length, 1, "the rest waits for the next turn to end");
  });

  test("the next queued message waits for its own turn to end", async () => {
    run(A);
    await settle();
    assert.equal(sent.length, 1, "a queued message never jumps a running turn");
    idle(A);
    await settle();
    assert.equal(sent.length, 2, "the next turn ending ships the next message");
    assert.equal(sent[1].text, "second");
    assert.equal(queue(A), undefined, "an emptied queue drops its session entry");
  });

  test("a chat turn never sends its own tool set; the Agent configuration decides it", () => {
    assert.ok(sent.length > 0);
    for (const frame of sent) {
      assert.equal(Object.hasOwn(frame, "tools"), false);
      assert.equal(Object.hasOwn(frame, "tools_profile"), false);
    }
  });

  test("queues are per session: one session's turn ending never ships another's queue", async () => {
    sent.length = 0;
    run(A);
    run(B);
    enqueueMessage(A, draft("for-a"));
    enqueueMessage(B, draft("for-b"));
    idle(B);
    await settle();
    assert.equal(sent.length, 1, "only the idle session drains");
    assert.equal(sent[0].session_id, B, "session B's turn ending sends B's message");
    assert.equal(queue(A).length, 1, "session A's queue is untouched while A is still running");
    idle(A);
    await settle();
    assert.equal(sent[1].text, "for-a");
  });

  test("a removed queued message is never sent", async () => {
    sent.length = 0;
    run(A);
    enqueueMessage(A, draft("keep"));
    const dropId = enqueueMessage(A, draft("drop"));
    useSendQueue.getState().remove(A, dropId);
    idle(A);
    await settle();
    assert.deepEqual(texts(), ["keep"]);
    assert.equal(queue(A), undefined);
  });
});

describe("ordering", () => {
  test("promoting moves an entry to the front and it is the one sent", async () => {
    sent.length = 0;
    run(A);
    enqueueMessage(A, draft("was-first"));
    const jumpId = enqueueMessage(A, draft("jump-me"));
    promoteToHead(A, jumpId);
    assert.deepEqual(queue(A).map((q) => q.text), ["jump-me", "was-first"]);
    idle(A);
    await settle();
    assert.equal(sent[0].text, "jump-me");
  });

  test("a turn rejected by a run_active race re-queues ahead of messages typed after it", async () => {
    sent.length = 0;
    run(A);
    requeueRejected(A, "rejected-by-race");
    assert.deepEqual(queue(A).map((q) => q.text), ["rejected-by-race", "was-first"]);
    idle(A);
    await settle();
    assert.equal(sent[0].text, "rejected-by-race");
  });

  test("duplicate idle notifications before the ACK dispatch only one entry", async () => {
    fresh();
    run(A);
    enqueueMessage(A, draft("clear-first"));
    enqueueMessage(A, draft("clear-second"));
    idle(A);
    idle(A);
    await settle();
    assert.deepEqual(texts(), ["clear-first"]);
    assert.deepEqual(queue(A).map((q) => q.text), ["clear-second"], "the second entry waits for the first queued turn");
  });

  test("setting an already-idle session to idle is not a turn completion and does not drain", async () => {
    fresh();
    idle(A);
    await settle();
    enqueueMessage(A, draft("stale-idle"));
    idle(A);
    await settle();
    assert.equal(sent.length, 0);
    assert.equal(queue(A)[0].text, "stale-idle");
  });
});

describe("reconnect", () => {
  test("a closed socket keeps the entry; the restored session drains only once it is idle", async () => {
    fresh();
    enqueueMessage(A, draft("after-reconnect"));
    setSocket({ readyState: 0, send: () => assert.fail("closed socket wrote") });
    useSendQueue.getState().drain(A);
    assert.equal(queue(A)[0].text, "after-reconnect");
    openSocket();
    reconcileAfterSessionLoad(A, true);
    assert.equal(sent.length, 0, "an active restored session must not drain");
    reconcileAfterSessionLoad(A, false);
    await settle();
    assert.deepEqual(texts(), ["after-reconnect"]);
    assert.equal(queue(A), undefined);
  });

  test("a load that lands as a turn ends: the clear naming that turn idles the slot and drains", async () => {
    sent.length = 0;
    store.setRunningTaskFor(A, null, "never");
    enqueueMessage(A, draft("after-late-load"));
    reconcileAfterSessionLoad(A, true, { msg_id: "late", execution_id: "late_exec" });
    assert.equal(useSessionStore.getState().runningTasks[A]?.execution_id, "late_exec");
    assert.equal(sent.length, 0, "the named turn still holds the session");
    handleRunningTaskClear(A, { msg_id: "late", execution_id: "late_exec" });
    await settle();
    assert.notEqual(useSessionStore.getState().runningTasks[A]?.execution_id, "late_exec");
    assert.deepEqual(texts(), ["after-late-load"]);
    store.setRunningTaskFor(A, null, "never");
  });

  test("after a branch switch the load hands over the selected branch's turn", () => {
    store.setRunningTaskFor(A, { session_id: A, msg_id: "on_a", execution_id: "exec_a", status_version: 3 }, "never");
    reconcileAfterSessionLoad(A, true, { msg_id: "on_b", execution_id: "exec_b", status_version: 5 });
    const task = useSessionStore.getState().runningTasks[A];
    assert.equal(task?.execution_id, "exec_b");
    assert.equal(task?.status_version, 5);
    store.setRunningTaskFor(A, null, "never");
  });

  test("a draft with attachments never enters the text queue", () => {
    const attachedId = enqueueMessage(A, draft("caption with image"), 1);
    assert.equal(attachedId, null);
    assert.equal(queue(A), undefined);
  });
});

describe("Stop", () => {
  test("Stop sends one exact cancel, frees the composer, and the queue waits for the server", async () => {
    fresh();
    store.setRunningTaskFor(A, { session_id: A, msg_id: "m", execution_id: "m_reply", status_version: 7 });
    const stoppedId = enqueueMessage(A, draft("send-after-stop"));
    promoteToHead(A, stoppedId);
    const stopFrames = [];
    stopSession(A, (payload) => { stopFrames.push(payload); return true; });
    await settle();
    assert.equal(stopFrames.length, 1, "cancel is sent immediately");
    assert.equal(stopFrames[0].action, "execution.cancel");
    assert.equal(stopFrames[0].execution_id, "m_reply");
    assert.equal(typeof stopFrames[0].command_id, "string");
    assert.equal(stopFrames[0].expected_version, 7);
    assert.deepEqual(texts(), [], "the queue waits for the server to confirm the stop before draining");
    assert.equal(useSessionStore.getState().runningTasks[A], undefined, "Stop frees the composer at once");

    handleRunningTaskClear(A);
    await settle();
    assert.deepEqual(texts(), ["send-after-stop"], "the confirmed stop releases the queued message once");
    const sentOnce = sent.length;
    assert.ok(useSessionStore.getState().runningTasks[A], "the queued send leaves occupancy for the new turn");

    handleRunningTaskClear(A, { execution_id: "m_reply", msg_id: "m" });
    await settle();
    assert.equal(sent.length, sentOnce, "a late clear for the stopped turn does not send the queued message again");
    assert.ok(useSessionStore.getState().runningTasks[A], "a clear naming the interrupted turn does not idle the new turn");
  });
});

describe("steering", () => {
  test("an in-flight steer blocks the normal drain until it is released", () => {
    fresh();
    idle(A);
    const steerId = enqueueMessage(A, draft("inject-me"));
    useSendQueue.getState().setInjecting(A, steerId, true);
    useSendQueue.getState().drain(A);
    assert.equal(sent.length, 0, "an injecting row must not also send as a normal turn");
    useSendQueue.getState().setInjecting(A, steerId, false);
    useSendQueue.getState().drain(A);
    assert.deepEqual(texts(), ["inject-me"], "not_running releases the row into ordinary queue semantics");
  });

  test("canonical steer receipts retain or release the queued row", async () => {
    const originalFetch = globalThis.fetch;
    const steerCommands = [];
    let loseSteerReceipt = false;
    globalThis.fetch = async (url, init) => {
      if (init?.method === "POST") {
        assert.equal(url, "/api/execution/steer");
        const command = JSON.parse(init.body);
        assert.equal(command.action, "execution.steer");
        assert.equal(command.execution_id, "active-execution");
        steerCommands.push(command);
        if (loseSteerReceipt) throw new Error("receipt lost");
        return Response.json({ command: { command_id: command.command_id, status: "applied" } });
      }
      assert.equal(url, `/api/execution/active-execution?conversation_session_id=${A}`);
      return Response.json({ snapshot: {
        execution_id: "active-execution", session_id: A, status_version: 7,
        status: "running", capabilities: { steer: true },
      } });
    };
    try {
      fresh();
      store.setRunningTaskFor(A, { session_id: A, msg_id: "m", execution_id: "active-execution" });
      const acceptedId = enqueueMessage(A, draft("accepted-steer"));
      const acceptedPromise = steerQueuedMessage(A, acceptedId);
      assert.equal(queue(A)[0].injecting, true, "the retained row renders injecting before ACK");
      assert.equal(await acceptedPromise, true);
      assert.equal(queue(A), undefined, "an applied steer removes the temporary queued row");

      store.setRunningTaskFor(A, null, "always");
      const fallbackId = enqueueMessage(A, draft("fallback-turn"));
      assert.equal(await steerQueuedMessage(A, fallbackId), false);
      await settle();
      assert.deepEqual(texts(), ["fallback-turn"], "not_running sends the row through the normal chat sender");

      // A lost receipt cannot release the same input as a second ordinary turn.
      fresh();
      store.setRunningTaskFor(A, { session_id: A, msg_id: "m", execution_id: "active-execution" });
      loseSteerReceipt = true;
      const uncertainId = enqueueMessage(A, draft("uncertain-steer"));
      assert.equal(await steerQueuedMessage(A, uncertainId), false);
      assert.equal(queue(A)[0].steerError, "unconfirmed");
      const originalCommand = steerCommands.at(-1);
      store.setRunningTaskFor(A, null, "always");
      await settle();
      assert.equal(sent.length, 0, "an unknown receipt blocks the ordinary drain");
      loseSteerReceipt = false;
      assert.equal(await steerQueuedMessage(A, uncertainId), true);
      assert.deepEqual(steerCommands.at(-1), originalCommand, "the retry keeps the immutable command envelope");
      assert.equal(queue(A), undefined);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  test("a message waiting to join the turn can be taken back, edited and removed", async () => {
    const originalFetch = globalThis.fetch;
    const withdrawn = [];
    let steerStatus = "accepted";
    let holdSteer = null;
    let withdrawReply = (commandId) => Response.json({
      withdrawn: true, command: { command_id: commandId, status: "rejected", rejection_code: "withdrawn" },
    });
    globalThis.fetch = async (url, init) => {
      if (init?.method === "POST" && url === "/api/execution/steer") {
        const command = JSON.parse(init.body);
        if (holdSteer) await holdSteer;
        return Response.json({ command: { command_id: command.command_id, status: steerStatus } });
      }
      if (init?.method === "POST") {
        const commandId = /^\/api\/execution\/command\/([^/]+)\/withdraw$/.exec(url)?.[1];
        assert.ok(commandId, `unexpected request ${url}`);
        withdrawn.push(commandId);
        return withdrawReply(commandId);
      }
      return Response.json({ snapshot: {
        execution_id: "active-execution", session_id: A, status_version: 7,
        status: "running", capabilities: { steer: true },
      } });
    };
    const waiting = async (text) => {
      fresh();
      store.setRunningTaskFor(A, { session_id: A, msg_id: "m", execution_id: "active-execution" });
      const id = enqueueMessage(A, draft(text));
      steerStatus = "accepted";
      assert.equal(await steerQueuedMessage(A, id), false);
      assert.ok(queue(A)[0].steerCommand, "the accepted steer keeps the row for this turn");
      return id;
    };
    try {
      // Taken back: the row is an ordinary queued row again.
      const editId = await waiting("take-me-back");
      const commandId = queue(A)[0].steerCommand.command_id;
      assert.equal(useSendQueue.getState().beginEdit(A, editId), false, "a row in the turn's hands cannot be edited directly");
      assert.equal(await takeBackQueuedSteer(A, editId), true);
      assert.deepEqual(withdrawn, [commandId]);
      assert.equal(queue(A)[0].steerCommand, undefined);
      assert.equal(queue(A)[0].steerError, undefined);
      assert.equal(useSendQueue.getState().beginEdit(A, editId), true);
      useSendQueue.getState().endEdit(A, editId);
      assert.equal(useSendQueue.getState().removeDraft(A, editId), true);
      assert.equal(queue(A), undefined);
      assert.equal(sent.length, 0, "a taken-back message is not sent anywhere");

      // A confirmation request in flight is waited for, and its receipt
      // cannot claim the row once the message is taken back.
      const racingId = await waiting("racing");
      let release;
      holdSteer = new Promise((resolve) => { release = resolve; });
      const replay = steerQueuedMessage(A, racingId);
      const takeBack = takeBackQueuedSteer(A, racingId);
      await settle();
      assert.equal(withdrawn.length, 1, "the take-back waits for the request in flight");
      steerStatus = "applied";
      holdSteer = null;
      release();
      assert.equal(await replay, true);
      assert.equal(await takeBack, false, "a message the turn took is not taken back");
      assert.equal(queue(A), undefined, "the delivered row left the queue");

      // The turn already has it: the row leaves as delivered.
      const deliveredId = await waiting("already-in");
      withdrawReply = (id) => Response.json({ withdrawn: false, command: { command_id: id, status: "applied" } });
      assert.equal(await takeBackQueuedSteer(A, deliveredId), false);
      assert.equal(queue(A), undefined);

      // On its way in: the row stays until its delivery is confirmed.
      const applyingId = await waiting("on-its-way");
      withdrawReply = (id) => Response.json({ withdrawn: false, command: { command_id: id, status: "applying" } });
      assert.equal(await takeBackQueuedSteer(A, applyingId), false);
      assert.ok(queue(A)[0].steerCommand);
      steerStatus = "applied";
      assert.equal(await steerQueuedMessage(A, applyingId), true);
      assert.equal(queue(A), undefined);

      // The server never stored the command: nothing can deliver it.
      const lostId = await waiting("never-arrived");
      withdrawReply = () => Response.json({ error: "not_found" }, { status: 404 });
      assert.equal(await takeBackQueuedSteer(A, lostId), true);
      assert.equal(queue(A)[0].steerCommand, undefined);
      assert.equal(useSendQueue.getState().removeDraft(A, lostId), true);
    } finally {
      globalThis.fetch = originalFetch;
      store.setRunningTaskFor(A, null, "always");
    }
  });
});

describe("durable continuation", () => {
  test("a resumed execution projects foreground controls without a chat_ack, and Stop cancels it exactly", () => {
    store.setRunningTaskFor(A, null, "always");
    useSendQueue.setState({ queues: {} });
    const resumedTask = {
      session_id: A, msg_id: "resumed-user", execution_id: "resumed-execution",
      status_version: 11, status: "running", func_name: "agent",
    };
    restoreForegroundExecutionTask(resumedTask);
    assert.equal(useSessionStore.getState().runningTasks[A]?.execution_id, "resumed-execution");
    restoreForegroundExecutionTask(null, { ...resumedTask, status: "paused" });
    assert.equal(useSessionStore.getState().runningTasks[A], undefined, "a paused continuation releases its controls");

    resumedTask.status_version = 12;
    restoreForegroundExecutionTask(resumedTask);
    const resumedCancel = [];
    stopSession(A, (frame) => { resumedCancel.push(frame); return true; });
    assert.equal(resumedCancel[0].execution_id, "resumed-execution");
    assert.equal(resumedCancel[0].expected_version, 12);
    restoreForegroundExecutionTask(resumedTask);
    assert.equal(useSessionStore.getState().runningTasks[A], undefined, "a pending cancel cannot be revived by a late projection");

    store.setRunningTaskFor(A, { session_id: A, msg_id: "new-user", execution_id: "new-owner" });
    restoreForegroundExecutionTask(resumedTask);
    restoreForegroundExecutionTask(null, { ...resumedTask, status: "paused" });
    assert.equal(useSessionStore.getState().runningTasks[A].execution_id, "new-owner", "another foreground owner is kept");
    store.setRunningTaskFor(A, null, "always");
  });
});

// Cross-language acceptance: the Python test submits this exact frontend envelope.
test("writes the Stop cancel frame for the Python cancellation test", { skip: !process.env.OPENPROGRAM_TEST_CANCEL_FRAME }, () => {
  const target = JSON.parse(process.env.OPENPROGRAM_TEST_CANCEL_TARGET);
  store.setRunningTaskFor(target.session_id, target);
  stopSession(target.session_id, (frame) => {
    writeFileSync(process.env.OPENPROGRAM_TEST_CANCEL_FRAME, JSON.stringify(frame));
    return true;
  });
  // The stop hold keeps asking a server that is not there; drop it so the
  // process can exit once the frame is written.
  releaseStopHold(target.session_id, false);
});
