"use client";
import { useEffect, useSyncExternalStore } from 'react';
import { useSessionStore } from '@/lib/session-store';
import { useSessionHistory } from '@/lib/chat/history/session-history';
import { retrySessionHistory } from '@/lib/runtime-bridge/session/session-history-loader';
import { requestSessionLoad } from '@/lib/runtime-bridge/session/session-load';
import { getSocket } from '@/lib/runtime-bridge/state';

/** How long to wait before asking again after the n-th failed read. */
export function transcriptRetryDelay(attempt: number): number {
  return Math.min(30_000, 1000 * 2 ** attempt);
}

/** Failed reads in a row, per conversation: a retry that fails again waits
 *  longer; a read that succeeds starts the count over. */
const failures = new Map<string, number>();

/** Answers that asking again cannot change: the conversation does not exist
 *  (deleted elsewhere, a stale link, a draft the server has not seen yet),
 *  or the server refused the request itself. */
const PERMANENT_CODES = new Set(['session_not_found', 'invalid_request', 'unknown_action']);

/** The conversation a failed load names, when the failure is permanent. */
export function permanentLoadFailure(frame: unknown): string | null {
  if (!frame || typeof frame !== 'object') return null;
  const { type, data } = frame as { type?: unknown; data?: unknown };
  if (type !== 'operation_error' && type !== 'action_error') return null;
  if (!data || typeof data !== 'object') return null;
  const { action, code, session_id: id } = data as Record<string, unknown>;
  if (action !== 'load_session' || typeof id !== 'string' || !id) return null;
  return typeof code === 'string' && PERMANENT_CODES.has(code) ? id : null;
}

/** Conversations whose read got a permanent answer: no retry, no skeleton.
 *  A missing conversation is closed by the socket's own handler (a draft
 *  stays and is simply empty); a refused read shows what is already there. */
const settled = new Set<string>();
const listeners = new Set<() => void>();
function notify() { for (const listener of listeners) listener(); }
export function settleTranscriptRead(sessionId: string) {
  if (settled.has(sessionId)) return;
  settled.add(sessionId);
  failures.delete(sessionId);
  notify();
}
export function transcriptReadSettled(sessionId: string): boolean {
  return settled.has(sessionId);
}
function unsettle(sessionId: string) {
  if (settled.delete(sessionId)) notify();
}
function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
export function useTranscriptReadSettled(sessionId: string | null): boolean {
  return useSyncExternalStore(subscribe, () => !!sessionId && settled.has(sessionId), () => false);
}

/** Each connection is watched once for permanent load failures. */
const watched = new WeakSet<WebSocket>();
function watchSocket(socket: WebSocket | undefined) {
  if (!socket || watched.has(socket) || typeof socket.addEventListener !== 'function') return;
  watched.add(socket);
  socket.addEventListener('message', (event: MessageEvent) => {
    const raw = event.data;
    if (typeof raw !== 'string' || !raw.includes('load_session') || !raw.includes('_error')) return;
    let frame: unknown;
    try { frame = JSON.parse(raw); } catch { return; }
    const id = permanentLoadFailure(frame);
    if (id) settleTranscriptRead(id);
  });
}

/** A failed read of the transcript is asked again, quietly, with backoff,
 *  until it succeeds or gets a permanent answer. Rows already on screen stay;
 *  nothing is said about the failure. */
export function TranscriptReadStatus({ sessionId }: { sessionId: string | null }) {
  const status = useSessionStore(s => sessionId ? s.transcriptReadStatus[sessionId] : undefined);
  const pageError = useSessionHistory(s => sessionId ? !!s.pages[sessionId]?.error : false);
  const done = useTranscriptReadSettled(sessionId);
  const failed = status === 'error' || status === 'disconnected';
  const retrying = sessionId && !done ? (failed ? 'load' : pageError ? 'page' : null) : null;
  useEffect(() => {
    watchSocket(getSocket());
    if (!sessionId) return;
    if (status === 'ready') unsettle(sessionId);
    if (!retrying) {
      if (status === 'ready' || status === undefined) failures.delete(sessionId);
      return;
    }
    let timer: ReturnType<typeof setTimeout> | undefined;
    // Chained: a read refused at once (socket still down) changes nothing
    // this effect watches, so the next attempt is scheduled here.
    const schedule = () => {
      const attempt = failures.get(sessionId) ?? 0;
      timer = setTimeout(() => {
        if (settled.has(sessionId)) return;
        failures.set(sessionId, attempt + 1);
        console.warn('transcript read failed; reading again', { sessionId, kind: retrying, attempt });
        watchSocket(getSocket());
        if (retrying === 'load') requestSessionLoad({ action: 'load_session', session_id: sessionId });
        else void retrySessionHistory(sessionId);
        schedule();
      }, transcriptRetryDelay(attempt));
    };
    schedule();
    return () => { if (timer) clearTimeout(timer); };
  }, [sessionId, retrying, status]);
  return null;
}
