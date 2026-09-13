/**
 * Pending user text / first-ACK reservations, keyed by chat key.
 *
 * The composer stashes the text of a turn here right before writing it
 * to the socket; `lib/net/chat-stream.ts` reads it back on `chat_ack`
 * to build the user bubble (the server does not echo web-originated
 * user turns), and `lib/runtime-bridge/chat-handlers.ts` reads it for
 * the sidebar preview and then clears both maps.
 *
 * The first-ACK map is only used for provisional (`local_*`) keys: it
 * marks "a turn for this draft is in flight", so a second UI submit
 * before the ACK is swallowed instead of writing a second turn.
 *
 * Previously `window.__pendingUserTextBySession` /
 * `window.__pendingFirstAckBySession`.
 */

const pendingText: Record<string, string> = Object.create(null);
const pendingTimestamp: Record<string, number> = Object.create(null);
const pendingFirstAck: Record<string, true> = Object.create(null);
const pendingAckCallbacks: Record<string, (() => void) | undefined> = Object.create(null);
const pendingRejectCallbacks: Record<string, (() => void) | undefined> = Object.create(null);
const pendingHasAttachments: Record<string, boolean> = Object.create(null);
const pendingMessageIds: Record<string, string> = Object.create(null);

export interface PendingUserTextOptions {
  onAck?: () => void;
  onReject?: () => void;
  hasAttachments?: boolean;
  messageId?: string;
}

export function getPendingUserText(sessionId: string): string | undefined {
  return pendingText[sessionId];
}

export function hasPendingUserText(sessionId: string): boolean {
  return sessionId in pendingText;
}

export function getPendingUserTimestamp(sessionId: string): number | undefined {
  return pendingTimestamp[sessionId];
}

export function setPendingUserText(
  sessionId: string,
  text: string,
  timestamp = Date.now(),
  options?: PendingUserTextOptions,
): void {
  pendingText[sessionId] = text;
  pendingTimestamp[sessionId] = timestamp;
  if (options) {
    pendingAckCallbacks[sessionId] = options.onAck;
    pendingRejectCallbacks[sessionId] = options.onReject;
    pendingHasAttachments[sessionId] = options.hasAttachments === true;
    if (options.messageId) pendingMessageIds[sessionId] = options.messageId;
  }
}

export function clearPendingUserText(sessionId: string): void {
  delete pendingText[sessionId];
  delete pendingTimestamp[sessionId];
  delete pendingAckCallbacks[sessionId];
  delete pendingRejectCallbacks[sessionId];
  delete pendingHasAttachments[sessionId];
  delete pendingMessageIds[sessionId];
}

export function getPendingUserMessageId(sessionId: string): string | undefined {
  return pendingMessageIds[sessionId];
}

export function getPendingUserAck(sessionId: string): (() => void) | undefined {
  return pendingAckCallbacks[sessionId];
}

export function getPendingUserReject(sessionId: string): (() => void) | undefined {
  return pendingRejectCallbacks[sessionId];
}

export function pendingUserHasAttachments(sessionId: string): boolean {
  return pendingHasAttachments[sessionId] === true;
}

/** Run the cleanup registered for the exact turn after chat_ack. */
export function acknowledgePendingUserText(sessionId: string): void {
  const callback = pendingAckCallbacks[sessionId];
  delete pendingAckCallbacks[sessionId];
  delete pendingHasAttachments[sessionId];
  if (!callback) return;
  try {
    callback();
  } catch (error) {
    console.error("[pending-user-text] ACK cleanup failed:", error);
  }
}

export function rejectPendingUserText(sessionId: string): void {
  const callback = pendingRejectCallbacks[sessionId];
  delete pendingRejectCallbacks[sessionId];
  delete pendingAckCallbacks[sessionId];
  delete pendingHasAttachments[sessionId];
  delete pendingText[sessionId];
  delete pendingTimestamp[sessionId];
  delete pendingMessageIds[sessionId];
  if (!callback) return;
  try { callback(); } catch (error) {
    console.error("[pending-user-text] rejection cleanup failed:", error);
  }
}

export function hasPendingFirstAck(sessionId: string): boolean {
  return pendingFirstAck[sessionId] === true;
}

export function setPendingFirstAck(sessionId: string): void {
  pendingFirstAck[sessionId] = true;
}

export function clearPendingFirstAck(sessionId: string): void {
  delete pendingFirstAck[sessionId];
}
