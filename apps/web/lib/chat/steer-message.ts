"use client";

import { ExecutionApiError, getExecutionSnapshot, postExecutionCommand } from "@/lib/net/execution-client";
import { useSessionStore } from "@/lib/session-store";
import { queueFor, useSendQueue } from "@/lib/chat/send-queue";
import type { CommandResult, ExecutionCommand } from "@/lib/execution/execution-debugger";

/** The queue is the fallback; this path never cancels an execution. */
export async function steerQueuedMessage(sessionId: string, messageId: string): Promise<boolean> {
  const entry = queueFor(sessionId).find(item => item.id === messageId);
  if (!entry || entry.injecting) return false;
  const queue = useSendQueue.getState();
  queue.setSteering(sessionId, messageId, { injecting: true, steerError: undefined });
  let command: ExecutionCommand | undefined = entry.steerCommand;
  try {
    if (entry.text.length > 4096) {
      queue.setSteering(sessionId, messageId, { steerError: "too_long" });
      return false;
    }
    // One retry is allowed only after a definitive version-conflict receipt.
    for (let attempt = 0; attempt < 2; attempt++) {
      if (!command) {
        const task = useSessionStore.getState().runningTasks[sessionId];
        if (!task) return false; // finally releases the row for ordinary drain.
        if (!task.execution_id) {
          queue.setSteering(sessionId, messageId, { steerError: "retry" });
          return false;
        }
        const snapshot = await getExecutionSnapshot(task.execution_id, AbortSignal.timeout(15000), sessionId);
        if (useSessionStore.getState().runningTasks[sessionId]?.execution_id !== task.execution_id
          || !queueFor(sessionId).some(item => item.id === messageId)
          || snapshot.session_id !== sessionId || snapshot.execution_id !== task.execution_id
          || !snapshot.capabilities?.steer || !["running", "paused", "pausing"].includes(snapshot.status)) {
          queue.setSteering(sessionId, messageId, { steerError: "unavailable" });
          return false;
        }
        command = {
          type: "execution.command", action: "execution.steer", command_id: crypto.randomUUID(),
          execution_id: snapshot.execution_id, expected_version: snapshot.status_version,
          payload: { message: entry.text },
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
          queue.setSteering(sessionId, messageId, { steerCommand: undefined, steerError: "unavailable" });
          return false;
        } else throw error;
      }
      if (result.command_id !== command.command_id) throw new Error("Unconfirmed command acknowledgement");
      if (result.status === "applied") {
        queue.remove(sessionId, messageId);
        return true;
      }
      if (["accepted", "applying"].includes(result.status)) {
        // The server owns this command, but delivery is not complete until the
        // instruction has been persisted in the conversation. Replay is idempotent.
        setTimeout(() => { void steerQueuedMessage(sessionId, messageId); }, 1500);
        return false;
      }
      if (result.status !== "rejected") throw new Error("Unconfirmed command outcome");
      queue.setSteering(sessionId, messageId, { steerCommand: undefined });
      command = undefined;
      if (result.rejection_code === "stale_version" && attempt === 0) continue;
      queue.setSteering(sessionId, messageId, { steerError: "unavailable" });
      return false;
    }
    return false;
  } catch {
    queue.setSteering(sessionId, messageId, { steerError: command ? "unconfirmed" : "retry" });
    return false;
  } finally {
    queue.setInjecting(sessionId, messageId, false);
    queue.drain(sessionId);
  }
}
