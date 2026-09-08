type MessageAnchors = { user_message_id?: unknown; assistant_message_id?: unknown };

/** Prefer the canonical snapshot; input is retained for older event envelopes. */
export function executionMessageIds(execution: { display?: MessageAnchors }, input?: MessageAnchors): string[] {
  const anchors = execution.display ?? input;
  return [anchors?.user_message_id, anchors?.assistant_message_id]
    .filter((id): id is string => typeof id === "string" && Boolean(id));
}

/** Pending waits may be restored before any execution cursor exists. */
export function pendingExecutionReplayRequests(waits: Array<{ execution_id?: unknown }>) {
  return [...new Set(waits.map(wait => wait.execution_id)
    .filter((id): id is string => typeof id === "string" && Boolean(id)))]
    .map(execution_id => ({ action: "execution.replay", execution_id, after_sequence: 0 }));
}
