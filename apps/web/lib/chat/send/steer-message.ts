"use client";

import { ExecutionApiError, getExecutionSnapshot, postExecutionCommand, withdrawExecutionCommand } from "@/lib/net/execution/execution-client";
import { useSessionStore } from "@/lib/session-store";
import { queuedMessagePayload } from "@/lib/chat/send/queued-attachments";
import { queueFor, useSendQueue } from "@/lib/chat/send/send-queue";
import type { CommandResult, ExecutionCommand } from "@/lib/execution/execution-debugger";

type Confirmation = { timer?: ReturnType<typeof setTimeout>; delay: number };
const confirmations = new Map<string, Confirmation>();
const confirmationKey = (sessionId: string, messageId: string) => `${sessionId}\0${messageId}`;

function clearConfirmation(key: string): void {
  const pending = confirmations.get(key);
  if (pending?.timer !== undefined) clearTimeout(pending.timer);
  confirmations.delete(key);
}

// Steers typed before the turn had an execution id, waiting for one.
const awaitingExecution = new Set<string>();
let unsubscribeExecutionWatch: (() => void) | null = null;

function retryWhenExecutionKnown(sessionId: string, messageId: string): void {
  awaitingExecution.add(confirmationKey(sessionId, messageId));
  if (unsubscribeExecutionWatch || typeof useSessionStore.subscribe !== "function") return;
  unsubscribeExecutionWatch = useSessionStore.subscribe((state, previous) => {
    if (state.runningTasks === previous.runningTasks) return;
    for (const key of Array.from(awaitingExecution)) {
      const [sid, id] = key.split("\0");
      const task = state.runningTasks[sid];
      if (task && !task.execution_id) continue;
      awaitingExecution.delete(key);
      // With the id the steer goes to that execution; without a task the
      // turn ended and the row drains as the next turn.
      if (task) void steerQueuedMessage(sid, id);
    }
    if (!awaitingExecution.size) {
      unsubscribeExecutionWatch?.();
      unsubscribeExecutionWatch = null;
    }
  });
}

/** The turn this steer was meant for is gone. A different execution now
 *  running means the instruction belonged to a turn that ended: hold it
 *  for the user. No execution at all: it goes out as the next turn. */
function targetGone(sessionId: string): "ended" | "unavailable" {
  return useSessionStore.getState().runningTasks[sessionId]?.execution_id ? "ended" : "unavailable";
}

function confirmLater(sessionId: string, messageId: string, transportFailed: boolean): void {
  const key = confirmationKey(sessionId, messageId);
  const prior = confirmations.get(key);
  if (prior?.timer !== undefined) clearTimeout(prior.timer);
  const delay = transportFailed ? Math.min((prior?.delay ?? 750) * 2, 30000) : 1500;
  const pending: Confirmation = { delay };
  pending.timer = setTimeout(() => {
    pending.timer = undefined;
    void steerQueuedMessage(sessionId, messageId);
  }, delay);
  confirmations.set(key, pending);
}

function holdsCommand(sessionId: string, messageId: string, commandId: string): boolean {
  return queueFor(sessionId).find(row => row.id === messageId)?.steerCommand?.command_id === commandId;
}

/** Resolves once no steer request for the row is in flight. */
function requestSettled(sessionId: string, messageId: string): Promise<void> {
  const busy = () => queueFor(sessionId).some(row => row.id === messageId && row.injecting);
  if (!busy()) return Promise.resolve();
  return new Promise((resolve) => {
    const stop = useSendQueue.subscribe(() => {
      if (busy()) return;
      stop();
      resolve();
    });
  });
}

/** Take a message back from the current turn while its steer still waits
 *  for a safe point, so it can be edited or removed. True when the row is an
 *  ordinary queued row again; false when the turn already has the message
 *  (the row then leaves the queue as delivered) or the answer is unknown. */
export async function takeBackQueuedSteer(sessionId: string, messageId: string): Promise<boolean> {
  await requestSettled(sessionId, messageId);
  const command = queueFor(sessionId).find(row => row.id === messageId)?.steerCommand;
  if (!command) return queueFor(sessionId).some(row => row.id === messageId);
  const key = confirmationKey(sessionId, messageId);
  clearConfirmation(key);
  const queue = useSendQueue.getState();
  const release = () => queue.setSteering(sessionId, messageId, { steerCommand: undefined, steerError: undefined });
  try {
    const result = await withdrawExecutionCommand(command.command_id, AbortSignal.timeout(15000));
    if (!holdsCommand(sessionId, messageId, command.command_id)) return false;
    if (result.withdrawn) { release(); return true; }
    if (result.command.status === "applied") { queue.remove(sessionId, messageId); return false; }
  } catch (error) {
    if (!holdsCommand(sessionId, messageId, command.command_id)) return false;
    // The server never stored this command, so there is nothing to deliver.
    if (error instanceof ExecutionApiError && error.status === 404) { release(); return true; }
  }
  // Still on its way into the turn, or unknown: keep confirming it.
  confirmLater(sessionId, messageId, false);
  return false;
}

/** The queue is the fallback; this path never cancels an execution. */
export async function steerQueuedMessage(sessionId: string, messageId: string): Promise<boolean> {
  const entry = queueFor(sessionId).find(item => item.id === messageId);
  const key = confirmationKey(sessionId, messageId);
  if (!entry) { clearConfirmation(key); return false; }
  if (entry.deliveryError || entry.editing || entry.injecting) return false;
  const pending = confirmations.get(key);
  if (pending?.timer !== undefined) {
    clearTimeout(pending.timer);
    pending.timer = undefined;
  }
  const queue = useSendQueue.getState();
  queue.setSteering(sessionId, messageId, { injecting: true,
    ...(entry.steerCommand ? {} : { steerError: undefined }) });
  // Identity belongs to this user intent, not to a later version retry.
  const targetId = entry.steerCommand?.execution_id ?? useSessionStore.getState().runningTasks[sessionId]?.execution_id;
  const stopped = () => queueFor(sessionId).find(row => row.id === messageId)?.steerError === "cancelled";
  let command: ExecutionCommand | undefined = entry.steerCommand;
  try {
    // Attachment markers count toward the limit, as the server checks them.
    const { text, attachments } = queuedMessagePayload(entry);
    if (Array.from(text).length > 4096) {
      queue.setSteering(sessionId, messageId, { steerError: "too_long" });
      return false;
    }
    // One retry is allowed only after a definitive version-conflict receipt.
    for (let attempt = 0; attempt < 2; attempt++) {
      if (!command) {
        if (stopped()) return false;
        const task = useSessionStore.getState().runningTasks[sessionId];
        if (targetId && task?.execution_id !== targetId) {
          queue.setSteering(sessionId, messageId, {steerError:targetGone(sessionId)});
          return false;
        }
        if (!task) return false;
        if (!task.execution_id) {
          queue.setSteering(sessionId, messageId, { steerError: "retry" });
          retryWhenExecutionKnown(sessionId, messageId);
          return false;
        }
        const snapshot = await getExecutionSnapshot(task.execution_id, AbortSignal.timeout(15000), sessionId);
        if (stopped()) return false;
        if (useSessionStore.getState().runningTasks[sessionId]?.execution_id !== targetId) {
          queue.setSteering(sessionId, messageId, {steerError:targetGone(sessionId)});
          return false;
        }
        if (!queueFor(sessionId).some(item => item.id === messageId)
          || snapshot.session_id !== sessionId || snapshot.execution_id !== task.execution_id
          || !snapshot.capabilities?.steer || !["running", "paused", "pausing"].includes(snapshot.status)) {
          queue.setSteering(sessionId, messageId, { steerError: snapshot.status === "cancelled" || snapshot.status === "cancelling" ? "cancelled" : "unavailable" });
          return false;
        }
        command = {
          type: "execution.command", action: "execution.steer", command_id: crypto.randomUUID(),
          execution_id: snapshot.execution_id, expected_version: snapshot.status_version,
          payload: attachments.length ? { message: text, attachments } : { message: text },
        };
        queue.setSteering(sessionId, messageId, { steerCommand: command });
      }
      let result: CommandResult;
      try { result = await postExecutionCommand(command, AbortSignal.timeout(15000)); }
      catch (error) {
        if (error instanceof ExecutionApiError && error.command?.command_id === command.command_id
          && error.command.status === "rejected") result = error.command;
        else if (!entry.steerCommand && error instanceof ExecutionApiError && error.status >= 400 && error.status < 500
          && ![408, 409].includes(error.status)) {
          queue.setSteering(sessionId, messageId, { steerCommand: undefined, steerError: stopped() ? "cancelled" : "unavailable" });
          return false;
        } else throw error;
      }
      if (result.command_id !== command.command_id) throw new Error("Unconfirmed command acknowledgement");
      // Taken back while this request was out: the row is no longer this
      // command's, whatever the receipt says.
      if (!holdsCommand(sessionId, messageId, command.command_id)) return false;
      if (result.status === "applied") {
        clearConfirmation(key);
        queue.remove(sessionId, messageId);
        return true;
      }
      if (["accepted", "applying"].includes(result.status)) {
        if (!stopped()) queue.setSteering(sessionId, messageId, {steerError: undefined});
        // The server owns this command, but delivery is not complete until the
        // instruction has been persisted in the conversation. Replay is idempotent.
        confirmLater(sessionId, messageId, false);
        return false;
      }
      if (result.status !== "rejected") throw new Error("Unconfirmed command outcome");
      clearConfirmation(key);
      queue.setSteering(sessionId, messageId, { steerCommand: undefined });
      command = undefined;
      if (stopped() || result.rejection_code === "superseded_by_cancel") {
        queue.setSteering(sessionId, messageId, { steerError: "cancelled" });
        return false;
      }
      if (result.rejection_code === "stale_version" && attempt === 0) continue;
      queue.setSteering(sessionId, messageId, { steerError: "unavailable" });
      return false;
    }
    return false;
  } catch {
    if (command && !holdsCommand(sessionId, messageId, command.command_id)) return false;
    if (!stopped()) queue.setSteering(sessionId, messageId, { steerError: command ? "unconfirmed" : "retry" });
    if (command) confirmLater(sessionId, messageId, true);
    return false;
  } finally {
    queue.setInjecting(sessionId, messageId, false);
    queue.drain(sessionId);
  }
}
