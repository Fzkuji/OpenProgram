/**
 * Every `execution.cancel` the page sends, from one place.
 *
 * A stop names its execution. The Worker takes it at the execution's
 * current version whatever version the page last saw (the version is only
 * recorded), so a cancel goes out once: a running turn starts stopping, a
 * second stop of a turn already stopping is answered with the stop in force
 * and reaches the turn again, and a stop of a turn that has ended answers
 * `already_terminal`. The page says "Stopped." when the execution's own
 * frame reports it cancelled, not on a command's answer.
 *
 * WebSocket senders call `sendExecutionCancel`; the REST path (Activity's
 * controls) posts `executionCancelCommand(...)` as any other command.
 */
import type { ExecutionCommand } from "./execution-debugger";

type Send = (payload: unknown) => boolean;

export function executionCancelCommand(
  executionId: string,
  expectedVersion: number,
  commandId: string,
): ExecutionCommand {
  return {
    type: "execution.command",
    action: "execution.cancel",
    command_id: commandId,
    execution_id: executionId,
    expected_version: expectedVersion,
    payload: {},
  };
}

/**
 * Send a stop of the turn a send starts, before its ack names the
 * execution: the Worker stops the execution that send admitted, or keeps
 * the stop for the send's admission, which then ends as stopped. Returns
 * whether the frame went out.
 */
export function sendRequestCancel(
  send: Send,
  sessionId: string,
  clientRequestId: string,
  commandId: string,
): boolean {
  try {
    return Boolean(send({
      type: "execution.command",
      action: "execution.cancel",
      command_id: commandId,
      session_id: sessionId,
      client_request_id: clientRequestId,
      payload: {},
    }));
  } catch {
    return false;
  }
}

/**
 * Send a cancel of `executionId` through `send`; `expectedVersion` is the
 * version the page last saw. Returns whether the frame went out.
 */
export function sendExecutionCancel(
  send: Send,
  executionId: string,
  expectedVersion: number,
  commandId: string = crypto.randomUUID(),
): boolean {
  try {
    return Boolean(send(executionCancelCommand(executionId, expectedVersion, commandId)));
  } catch {
    // A transport exception does not establish that anything was sent.
    return false;
  }
}
