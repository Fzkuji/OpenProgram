const MAX_TRACKED_EXECUTIONS = 1024;
const lastSequenceByExecution = new Map<string, number>();

/** Accept a sequenced execution update only when it advances that execution. */
export function acceptExecutionUpdate(
  executionId: string,
  eventSequence: unknown,
): boolean {
  const sequence = Number(eventSequence);
  if (!Number.isSafeInteger(sequence) || sequence < 0) return true;
  const previous = lastSequenceByExecution.get(executionId);
  if (previous !== undefined && sequence <= previous) return false;
  lastSequenceByExecution.set(executionId, sequence);
  while (lastSequenceByExecution.size > MAX_TRACKED_EXECUTIONS) {
    lastSequenceByExecution.delete(lastSequenceByExecution.keys().next().value!);
  }
  return true;
}

export function resetExecutionUpdateOrderForTests(): void {
  lastSequenceByExecution.clear();
}
