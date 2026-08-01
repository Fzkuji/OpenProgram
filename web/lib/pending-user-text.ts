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
const pendingFirstAck: Record<string, true> = Object.create(null);

export function getPendingUserText(sessionId: string): string | undefined {
  return pendingText[sessionId];
}

export function hasPendingUserText(sessionId: string): boolean {
  return sessionId in pendingText;
}

export function setPendingUserText(sessionId: string, text: string): void {
  pendingText[sessionId] = text;
}

export function clearPendingUserText(sessionId: string): void {
  delete pendingText[sessionId];
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
