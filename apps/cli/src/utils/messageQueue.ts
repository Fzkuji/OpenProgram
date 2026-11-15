/**
 * Messages typed while a turn runs.
 *
 * Enter on a busy branch puts the message here instead of refusing it;
 * the rows show above the input, and when the turn ends (finished or
 * stopped) the oldest one is sent as the next turn, then the next. ↑ on
 * an empty input selects a row, where it can be edited (taken back into
 * the input, attachments included) or removed — Grok Build's queue
 * pane, over Claude Code's and Codex's "pull the last one back". The web
 * composer's queued bubbles (apps/web/lib/chat/send/send-queue.ts) are
 * the same model: visible, individually editable, individually removable.
 */
import type { PendingAttachment } from './attachments.js';

export interface QueuedMessage {
  id: string;
  /** As typed, placeholders and all. */
  text: string;
  attachments: PendingAttachment[];
}

let counter = 0;

export function enqueue(queue: QueuedMessage[], text: string, attachments: PendingAttachment[] = []): QueuedMessage[] {
  counter += 1;
  return [...queue, { id: `q-${Date.now()}-${counter}`, text, attachments }];
}

export function removeAt(queue: QueuedMessage[], index: number): QueuedMessage[] {
  return queue.filter((_, i) => i !== index);
}

/** Take one row out (to edit it): the row and the queue without it. */
export function takeAt(queue: QueuedMessage[], index: number): [QueuedMessage | undefined, QueuedMessage[]] {
  return [queue[index], removeAt(queue, index)];
}

/** The row to send when the branch is free, and the queue after it. */
export function nextToSend(queue: QueuedMessage[]): [QueuedMessage | undefined, QueuedMessage[]] {
  return takeAt(queue, 0);
}

/** One line of a queued message as the row shows it. */
export function previewLine(text: string): { line: string; more: number } {
  const lines = text.split('\n');
  return { line: lines[0] ?? '', more: lines.length - 1 };
}
