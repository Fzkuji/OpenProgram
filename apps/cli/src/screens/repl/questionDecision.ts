/**
 * question.asked frame → PendingDecision, and the answer/reject WS
 * payloads. Pure functions so the contract (kept in lock-step with the
 * web composer, apps/web/lib/use-ws.ts + question-mode.tsx) is unit-testable
 * without an Ink render. useWsEvents and QuestionPicker both route
 * through these so there's one definition of the wire shape.
 */
import type { QuestionAskedEnvelope, WsRequest } from '../../ws/client.js';
import type { PendingDecision } from './types.js';

/** Map a question.asked frame's `data` to a PendingDecision, or null if
 *  it carries no id (malformed). Mirrors use-ws.ts's enqueue mapping. */
export function decisionFromFrame(
  data: QuestionAskedEnvelope['data'] | undefined,
): PendingDecision | null {
  if (!data?.id) return null;
  return {
    id: String(data.id),
    executionId: String(data.execution_id ?? ''),
    waitGeneration: Number(data.wait_generation ?? 0),
    expectedVersion: Number(data.expected_version ?? 0),
    kind: (data.kind as 'ask' | 'confirm' | 'approval' | 'form' | 'ask_many') || 'ask',
    prompt: String(data.prompt ?? ''),
    options: Array.isArray(data.options) ? data.options.map(String) : [],
    multi: Boolean(data.multi),
    allow_custom: data.allow_custom !== false,
    detail: data.detail ? String(data.detail) : undefined,
    tool: data.tool ? String(data.tool) : undefined,
    args: (data.args as Record<string, unknown>) || undefined,
    schema:
      data.schema && typeof data.schema === 'object' && Object.keys(data.schema).length
        ? (data.schema as PendingDecision['schema'])
        : undefined,
    questions: Array.isArray(data.questions)
      ? (data.questions as PendingDecision['questions'])
      : undefined,
  };
}

/** Enqueue with id-dedupe (a reconnect replay re-sends question.asked). */
export function enqueueDecision(
  queue: PendingDecision[],
  decision: PendingDecision,
): PendingDecision[] {
  return queue.some((p) => p.id === decision.id) ? queue : [...queue, decision];
}

/** The WS reply for an answered question. For approvals, scope='always'
 *  also persists a project-level allow rule for the tool server-side
 *  (mirrors the web's 总是允许; permission-model.md §6.3). */
export function replyAction(
  decision: PendingDecision, answer: unknown,
): WsRequest {
  return {
    type: 'execution.command', action: 'execution.wait.answer',
    command_id: `tui-wait-${Math.random().toString(36).slice(2)}`,
    execution_id: decision.executionId, expected_version: decision.expectedVersion,
    payload: { wait_id: decision.id, generation: decision.waitGeneration, answer },
  };
}

/** The WS reject for a declined question. */
export function rejectAction(decision: PendingDecision, reason?: string): WsRequest {
  return {
    type: 'execution.command', action: 'execution.wait.decline',
    command_id: `tui-wait-${Math.random().toString(36).slice(2)}`,
    execution_id: decision.executionId, expected_version: decision.expectedVersion,
    payload: { wait_id: decision.id, generation: decision.waitGeneration, ...(reason ? { reason } : {}) },
  };
}
