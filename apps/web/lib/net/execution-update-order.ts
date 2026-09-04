export interface ExecutionUpdateOrder {
  sequence: number;
  terminal: boolean;
  sessionId?: string;
  messageIds: string[];
}

const TERMINAL_EXECUTION_STATUSES = new Set([
  "cancelled",
  "completed",
  "failed",
  "interrupted",
  "error",
  "done",
]);

export function decideExecutionUpdateOrder(
  previous: ExecutionUpdateOrder | undefined,
  eventSequence: unknown,
  status: unknown,
  sessionId?: unknown,
  messageIds?: Iterable<unknown>,
): { accepted: boolean; next?: ExecutionUpdateOrder } {
  const sequence = Number(eventSequence);
  // Legacy senders do not participate in canonical ordering. Their path is
  // removed with the remaining legacy execution entry points.
  if (!Number.isSafeInteger(sequence) || sequence < 0) return { accepted: true };
  if (previous?.terminal || (previous !== undefined && sequence <= previous.sequence)) {
    return { accepted: false };
  }
  return {
    accepted: true,
    next: {
      sequence,
      terminal: TERMINAL_EXECUTION_STATUSES.has(String(status)),
      ...(typeof sessionId === "string" && sessionId ? { sessionId } : {}),
      messageIds: Array.from(messageIds ?? [])
        .filter((messageId): messageId is string => typeof messageId === "string" && Boolean(messageId))
        .filter((messageId, index, values) => values.indexOf(messageId) === index),
    },
  };
}

export function removeExecutionUpdateOrders(
  orders: Record<string, ExecutionUpdateOrder>,
  removedMessageIds: Iterable<string>,
): Record<string, ExecutionUpdateOrder> {
  const removed = new Set(removedMessageIds);
  const next: Record<string, ExecutionUpdateOrder> = {};
  for (const [executionId, order] of Object.entries(orders)) {
    if (
      removed.has(executionId)
      || order.messageIds.some((messageId) => removed.has(messageId))
    ) continue;
    next[executionId] = order;
  }
  return next;
}
