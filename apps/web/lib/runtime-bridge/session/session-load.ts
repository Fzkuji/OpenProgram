import { pendingBranchSettings } from "@/lib/agents/branch-settings";
import { flushTextFrames } from "@/lib/net/ws/text-frames";
import { createSessionLoads, type SessionRead, type SessionReadStatus } from '@/lib/net/ws/session-load';
import { useSessionStore } from '@/lib/session-store';
import { getSocket } from '../state';

function status(id: string, value: SessionReadStatus) {
  useSessionStore.getState().setTranscriptReadStatus(id, value);
}
function createOwner(socket: WebSocket) {
  return createSessionLoads({
    send: request => {
      if (socket.readyState !== WebSocket.OPEN) throw new Error('Disconnected');
      socket.send(JSON.stringify(request));
    },
    status: (id, value) => { if (getSocket() === socket) status(id, value); }, requestId: () => crypto.randomUUID(),
  });
}
const owners = new WeakMap<WebSocket, ReturnType<typeof createOwner>>();
function owner(socket: WebSocket) {
  let value = owners.get(socket);
  if (!value) { value = createOwner(socket); owners.set(socket, value); }
  return value;
}
export function requestSessionLoad(payload: SessionRead, invalidate = false): boolean {
  const pending = pendingBranchSettings(payload.session_id);
  if (pending) {
    void pending.then(() => requestSessionLoad(payload, invalidate));
    return true;
  }
  const socket = getSocket();
  if (!socket || socket.readyState !== WebSocket.OPEN) { status(payload.session_id, 'disconnected'); return false; }
  return owner(socket).request(payload, invalidate);
}
/** Existing command helpers route reads here; all other commands keep their original delivery. */
export function sendRuntimeCommand(payload: unknown): boolean {
  const request = payload as Record<string, unknown> | null;
  if (request?.action === 'load_session' && typeof request.session_id === 'string') {
    return requestSessionLoad(request as SessionRead, true);
  }
  const socket = getSocket();
  if (!socket || socket.readyState !== WebSocket.OPEN) return false;
  if (request?.action === 'checkout_branch' && typeof request.session_id === 'string') {
    const pending = pendingBranchSettings(request.session_id);
    if (pending) {
      void pending.then(() => { if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(payload)); });
      return true;
    }
  }
  socket.send(JSON.stringify(payload));
  return true;
}
export function acceptSessionLoad(socket: WebSocket, data: Record<string, unknown>, apply: (data: never) => void): boolean {
  const accepted = owner(socket).accept(data);
  if (!accepted) return false;
  const id = data.id as string;
  flushTextFrames();
  try { apply(data as never); } catch (error) { status(id, 'error'); throw error; }
  if (accepted.next) owner(socket).request(accepted.next);
  return true;
}
/** A repair or page read of a view, owned by this socket's reads: see createSessionLoads.exclusive. */
export function exclusiveSessionRead<R>(view: string, read: () => Promise<R>, skipped: R): Promise<R> {
  const socket = getSocket();
  return socket ? owner(socket).exclusive(view, read, skipped) : read();
}
/** Whether a repair or page read of the view is in flight on this socket. */
export function sessionReadBusy(view: string): boolean {
  const socket = getSocket();
  return socket ? owner(socket).busy(view) : false;
}
export function failSessionLoad(socket: WebSocket, data: { request_id?: unknown }) { return owner(socket).error(data); }
export function disposeSessionLoads(socket: WebSocket) { owners.get(socket)?.dispose(); }
