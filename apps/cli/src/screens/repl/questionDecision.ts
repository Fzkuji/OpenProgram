/**
 * question.asked frame → PendingDecision, and the answer/reject WS
 * payloads. Pure functions so the contract (kept in lock-step with the
 * web composer, web/lib/use-ws.ts + question-mode.tsx) is unit-testable
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
  id: string, answer: string | string[], scope?: 'always',
): WsRequest {
  return scope
    ? { action: 'question_reply', id, answer, scope }
    : { action: 'question_reply', id, answer };
}

/** The WS reject for a declined question. */
export function rejectAction(id: string, reason?: string): WsRequest {
  return reason
    ? { action: 'question_reject', id, reason }
    : { action: 'question_reject', id };
}
