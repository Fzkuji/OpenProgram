/**
 * Send an `execution.cancel`.
 *
 * A stop names its turn: the execution, or, before the ack names the
 * execution, the send that starts it (its session and `client_request_id`).
 * The worker announces on connect what it does (`worker_capabilities`):
 *
 * - `cancel_ignores_version`: it takes a stop at the execution's current
 *   version whatever version it names, so it never refuses one as stale and
 *   the cancel goes out once. An older worker, or a remote server not yet
 *   updated, refuses a cancel whose `expected_version` is not current
 *   (`stale_version`) with the execution's latest snapshot; for those the
 *   cancel goes out once more, with the same command id and the version the
 *   refusal names, as `openprogram --print` (`interfaces/cli/worker_turn.py`)
 *   does.
 * - `cancel_by_request`: a stop may name the send before its execution is
 *   known; the send's admission then ends as stopped.
 */
import type { BackendClient, WsEnvelope } from '../../ws/client.js';

type CancelClient = Pick<BackendClient, 'send' | 'on'> & {
  hasCapability?: (name: string) => boolean;
};

/** The send a turn started from, before its ack names the execution. */
export type PendingSend = { sessionId: string; requestId: string };

// Statuses in which a cancel still has something to stop.
const CANCELLABLE = new Set(['queued', 'running', 'pausing', 'paused']);
// How long the listener waits for the worker's answer before it lets go.
const ANSWER_WAIT_MS = 60_000;

type CommandFrame = {
  command_id?: unknown;
  execution_id?: unknown;
  status?: unknown;
  rejection_code?: unknown;
  latest_snapshot?: { execution_id?: unknown; status?: unknown; status_version?: unknown };
};

function commandOf(ev: WsEnvelope): CommandFrame | undefined {
  if (ev.type !== 'execution.command.updated') return undefined;
  return (ev.command ?? ev.data?.command) as CommandFrame | undefined;
}

function announces(client: CancelClient, name: string): boolean {
  return client.hasCapability?.(name) === true;
}

/**
 * Cancel `executionId` at `expectedVersion`. For a worker that does not
 * announce `cancel_ignores_version`, a `stale_version` refusal of this
 * command is answered with one more cancel at the refusal's version.
 * Returns the command id.
 */
export function cancelExecution(
  client: CancelClient,
  executionId: string,
  expectedVersion: number,
  commandId: string,
): string {
  const frame = (version: number) => ({
    type: 'execution.command' as const,
    action: 'execution.cancel' as const,
    command_id: commandId,
    execution_id: executionId,
    expected_version: version,
    payload: {},
  });
  client.send(frame(expectedVersion));
  if (announces(client, 'cancel_ignores_version')) return commandId;
  let resent = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const off = client.on((ev) => {
    const command = commandOf(ev);
    if (!command || command.command_id !== commandId) return;
    const status = String(command.status ?? '');
    if (status !== 'rejected') {
      if (status === 'applied') done();
      return;
    }
    const snapshot = command.latest_snapshot;
    const version = snapshot?.status_version;
    if (
      !resent
      && command.rejection_code === 'stale_version'
      && snapshot?.execution_id === executionId
      && typeof version === 'number'
      && CANCELLABLE.has(String(snapshot?.status ?? ''))
    ) {
      resent = true;
      client.send(frame(version));
      return;
    }
    done();
  });
  function done() {
    off();
    if (timer) clearTimeout(timer);
  }
  timer = setTimeout(done, ANSWER_WAIT_MS);
  (timer as { unref?: () => void }).unref?.();
  return commandId;
}

/**
 * Cancel the turn `send` starts before its ack names the execution: the
 * worker stops the execution the send admitted, or keeps the stop for the
 * send's admission. Returns the command id.
 */
export function cancelSend(
  client: CancelClient,
  send: PendingSend,
  commandId: string,
): string {
  client.send({
    type: 'execution.command',
    action: 'execution.cancel',
    command_id: commandId,
    session_id: send.sessionId,
    client_request_id: send.requestId,
    payload: {},
  });
  return commandId;
}

/**
 * The REPL's stop (Ctrl-C while a turn streams, or the stop control):
 * cancel the current execution, the send it starts from while the ack has
 * not named it yet, or say why there is nothing to cancel. A stop names its
 * execution; the version is only what the REPL last saw (the worker takes
 * the stop at the execution's current one). Returns whether a cancel was
 * sent.
 */
export function cancelCurrentExecution(
  client: CancelClient,
  executionId: string | null | undefined,
  expectedVersion: number | undefined,
  pushSystem: (text: string) => void,
  newCommandId: () => string,
  pendingSend?: PendingSend | null,
): boolean {
  if (executionId) {
    cancelExecution(client, executionId, expectedVersion ?? 0, newCommandId());
    return true;
  }
  if (pendingSend && announces(client, 'cancel_by_request')) {
    cancelSend(client, pendingSend, newCommandId());
    return true;
  }
  pushSystem('Cancel unavailable: no current execution version');
  return false;
}

/**
 * Stop a foreground operation that has no execution, such as a /compact,
 * by the `op_id` its `compaction_started` frame named. The server ends the
 * summary request and the branch's slot; the operation's own frame says it
 * stopped.
 */
export function cancelOperation(
  client: Pick<BackendClient, 'send'>,
  sessionId: string,
  opId: string,
): void {
  client.send({ action: 'operation.cancel', session_id: sessionId, op_id: opId });
}

/** The stop of the turn in progress: one command id for every press. */
export type StopState = { commandId: string | null };

/**
 * The stop key (Ctrl-C or Esc): stop the branch's foreground operation,
 * whatever it is (Codex: one key stops the current task).
 *
 * - A turn in progress (streaming, or sent and not yet acknowledged) is
 *   stopped on the first press. Later presses while it winds down send the
 *   same stop again, with the same command id, so a first signal that was
 *   lost still arrives; only the first press leaves a line in the
 *   transcript (Claude Code shows the interruption once).
 * - Otherwise a /compact in progress is stopped by its `op_id`.
 *
 * Returns what was stopped, or null when nothing is running (the caller's
 * idle behaviour applies: double Ctrl-C exits).
 */
export function stopForeground(args: {
  client: CancelClient;
  running: boolean;
  executionId: string | null | undefined;
  expectedVersion: number | undefined;
  pendingSend: PendingSend | null | undefined;
  sessionId: string | undefined;
  operationId: string | undefined;
  state: StopState;
  pushSystem: (text: string) => void;
  newCommandId: () => string;
}): 'turn' | 'operation' | null {
  const { client, state } = args;
  if (args.running) {
    const first = state.commandId === null;
    const commandId = state.commandId ?? args.newCommandId();
    const sent = cancelCurrentExecution(
      client, args.executionId, args.expectedVersion,
      first ? args.pushSystem : () => {}, () => commandId, args.pendingSend,
    );
    if (sent) {
      state.commandId = commandId;
      if (first) args.pushSystem('Cancel execution');
    }
    return 'turn';
  }
  if (args.operationId && args.sessionId) {
    cancelOperation(client, args.sessionId, args.operationId);
    return 'operation';
  }
  return null;
}
