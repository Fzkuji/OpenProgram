export interface ExecutionUpdateOrder {
  sequence: number;
  terminal: boolean;
  sessionId?: string;
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
    },
  };
}

export function removeExecutionUpdateOrders(
  orders: Record<string, ExecutionUpdateOrder>,
  executionIds: Iterable<string>,
): Record<string, ExecutionUpdateOrder> {
  const next = { ...orders };
  for (const executionId of executionIds) delete next[executionId];
  return next;
}
