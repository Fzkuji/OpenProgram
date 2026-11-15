import { applyReadToView } from "@/lib/chat/transcript/apply-read";
import { flushSync } from 'react-dom';
import { rowsOfTurns } from '@/lib/chat/transcript/turn-rows';
import { useSessionHistory, updateSessionHistory } from '@/lib/chat/history/session-history';
import { HistoryWindow, type HistoryDirection, type TurnWindow, type WireTurn } from '@/lib/chat/history/history-window';
import { captureHistoryAnchor, captureAreaRestoreState, historyAreaStillOwned, isFollowLocked, readHistoryAnchor, restoreAreaWindow, saveHistoryAnchor } from '@/lib/chat/history/history-viewport';
import { noteTakeLatest } from '@/lib/chat/history/chat-scroll';
import { clearHeights, retainRowHeights } from '@/lib/chat/history/message-window';
import { useSessionStore, type ChatMsg } from '@/lib/session-store';
import { flushTextFrames } from '@/lib/net/ws/text-frames';
import { exclusiveSessionRead, requestSessionLoad } from './session-load';
import { wsRequest } from '@/lib/net/ws/ws-request';
import { getSocket, runtimeState } from '../state';
import { isLive } from "../../chat/transcript/row-status.ts";

const windows = new Map<string, HistoryWindow>();
const viewports = new Map<string, Map<HTMLElement, string>>();
// Fewer than the views the server keeps (MAX_VIEWS, transcript/turn_index.py),
// so every retained window can keep paging.
const MAX_CACHED_SESSIONS = 4;

export function registerHistoryViewport(id: string, area: HTMLElement, chatKey: string): () => void {
  const areas = viewports.get(id) ?? new Map<HTMLElement, string>();
  areas.set(area,chatKey); viewports.set(id,areas);
  return () => { areas.delete(area); if (!areas.size) viewports.delete(id); trimHistoryWindows(); };
}

/** A split-view pane shows this session through its own windowed read. */
export function peerPresentsSession(id: string): boolean {
  for (const chatKey of viewports.get(id)?.values() ?? []) {
    if (chatKey.startsWith('peer:')) return true;
  }
  return false;
}

function trimHistoryWindows(protectedId?: string): void {
  for (const id of windows.keys()) {
    if (windows.size <= MAX_CACHED_SESSIONS) break;
    if (id===protectedId || viewports.has(id) || id===useSessionStore.getState().currentSessionId
        || useSessionHistory.getState().pages[id]?.loading) continue;
    windows.delete(id);
    sendCloseView(id);
    // No window means the next open of this chat reads it again.
    useSessionHistory.setState(s=>{const pages={...s.pages};delete pages[id];return {pages};});
    applyReadToView(id,storeRows(id).filter(m=>isLive(m.status)),false);
    clearHeights(id);clearHeights(`peer:${id}`);
  }
}

// Removing a conversation or clearing the store releases every retained page.
useSessionStore.subscribe((state,previous)=>{
  for(const id of windows.keys()){
    if(previous.messageOrder[id] && !state.messageOrder[id]){
      windows.delete(id);clearHeights(id);clearHeights(`peer:${id}`);
      useSessionHistory.setState(s=>{const pages={...s.pages};delete pages[id];return {pages};});
    }
  }
});
function sendCloseView(id: string): void {
  const socket = getSocket();
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ action: 'close_view', session_id: id, view: id }));
  }
}

export function historyWindowOf(id: string): HistoryWindow | undefined {
  return windows.get(id);
}

/** Whether a viewport is showing this session, so losing its window needs a read. */
export function isViewShown(id: string): boolean {
  return viewports.has(id) || useSessionStore.getState().currentSessionId === id;
}

/** The server stopped keeping this view: forget the window, keep the rows. */
export function dropHistoryWindow(id: string): void {
  windows.delete(id);
  useSessionHistory.setState(s=>{const pages={...s.pages};delete pages[id];return {pages};});
}

export function seedTurnWindow(id: string, turns: WireTurn[], window: TurnWindow): void {
  windows.delete(id);
  const historyWindow = new HistoryWindow();
  historyWindow.addTurns(turns, window, 'latest');
  windows.set(id, historyWindow);
  trimHistoryWindows(id);
}
export async function loadOlderSessionHistory(id: string): Promise<void> {
  await loadSessionHistoryWindow(id, 'older');
}

function readingAnchor(id: string): string | undefined {
  const area = viewports.get(id)?.keys().next().value
    ?? (runtimeState.currentSessionId === id ? document.getElementById('chatArea') : null);
  return area ? captureHistoryAnchor(area)?.id : undefined;
}
/** Re-read around what the reader sees, or the newest page when no transcript is on screen. */
export async function retrySessionHistory(id: string): Promise<boolean> {
  const anchor = readingAnchor(id);
  return anchor ? loadSessionHistoryWindow(id, 'around', anchor) : loadSessionHistoryWindow(id, 'latest');
}

/** Network and data coordination. All viewport operations live in history-viewport. */
export async function loadSessionHistoryWindow(id: string, direction: HistoryDirection, around?: string, options?: { isCurrent: () => boolean }): Promise<boolean> {
  if (options && !options.isCurrent()) return false;
  const expected = useSessionHistory.getState().pages[id];
  if (expected?.loading && (direction === 'latest' || direction === 'around')) {
    await new Promise<void>(resolve => {
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        unsubscribe();
        queueMicrotask(resolve);
      };
      const unsubscribe = useSessionHistory.subscribe((state) => {
        if (!state.pages[id]?.loading || state.pages[id]?.generation !== expected.generation) {
          finish();
        }
      });
      const current = useSessionHistory.getState().pages[id];
      if (!current?.loading || current.generation !== expected.generation) finish();
    });
    return loadSessionHistoryWindow(id, direction, around, options);
  }
  if (!expected || expected.loading || (direction === 'older' && !expected.before)
      || (direction === 'newer' && !expected.after)) return false;
  const socket = getSocket();
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    updateSessionHistory(id, expected.generation, {error: true});
    return false;
  }
  return exclusiveSessionRead(id, () => loadTurnPage(id, direction, around, options, expected, socket), false);
}

function restoreViewports(id: string, direction: HistoryDirection, around: string | undefined, captures: ReturnType<typeof captureAreaRestoreState>[], liveIds: Set<string>): void {
  const liveRegistered = viewports.get(id);
  for (const cap of captures) {
    retainRowHeights(cap.chatKey, liveIds);
    if (direction === 'latest') {
      // The newest page replaced this viewport's window. A Jump or a send
      // that asked for it holds the follow lock and places the view itself;
      // a renewal or a retry did not, so hand the placement to the stick
      // hook as a take-latest note (the loader never pins the view), or the
      // reader is left wherever the browser clamped the old offset.
      if (!isFollowLocked(cap.chatKey)) noteTakeLatest({ sessionId: id, scrollerKey: cap.chatKey, turnSeed: '' });
      continue;
    }
    if (historyAreaStillOwned(cap, liveRegistered, {
      sessionId: id,
      focusedSessionId: runtimeState.currentSessionId,
      focusedChatKey: useSessionStore.getState().activeChatKey,
    })) {
      restoreAreaWindow(cap, direction, around);
    }
  }
}

function storeRows(id: string): ChatMsg[] {
  const store = useSessionStore.getState();
  const rows = store.rowsByView[id] ?? {};
  return (store.messageOrder[id] ?? []).map(mid => rows[mid]).filter(Boolean);
}

function restoreCaptures(id: string) {
  const registered = viewports.get(id);
  const captures = registered
    ? [...registered.entries()].map(([area, chatKey]) => captureAreaRestoreState(area, chatKey, true))
    : [];
  if (!captures.length && runtimeState.currentSessionId === id) {
    const area = document.getElementById('chatArea');
    if (area) captures.push(captureAreaRestoreState(area, useSessionStore.getState().activeChatKey ?? id, false));
  }
  return captures;
}

/** One page of turns on protocol 3. The server parks a session's updates while
 *  the reply is sent, so the page's rows are current; rows outside it stay. */
async function loadTurnPage(
  id: string, direction: HistoryDirection, around: string | undefined, options: { isCurrent: () => boolean } | undefined,
  expected: ReturnType<typeof useSessionHistory.getState>['pages'][string], socket: WebSocket,
): Promise<boolean> {
  updateSessionHistory(id, expected.generation, { loading: true, error: false });
  const field = direction === 'older' ? {history_before: expected.before}
    : direction === 'newer' ? {history_after: expected.after}
    : direction === 'around' ? {history_around: around} : {history_latest: true};
  type TurnReply = {id: string; turns?: WireTurn[]; window?: TurnWindow; error_code?: string};
  const page = await wsRequest<TurnReply>('load_session', {session_id:id, ...field}, 'session_history_page', {requestId:true}, 15000);
  if (useSessionHistory.getState().pages[id]?.generation !== expected.generation) return false;
  if (page?.error_code === 'cursor_not_on_branch' && getSocket() === socket) {
    // The branch moved away from this cursor: read the chat again, once.
    // A saved reading position on that row would ask for it again after
    // every read, so it is forgotten with the cursor.
    if (direction === 'around' && around && readHistoryAnchor(id)?.id === around) saveHistoryAnchor(id, null);
    updateSessionHistory(id, expected.generation, {loading:false});
    requestSessionLoad({action:'load_session', session_id:id}, true);
    return false;
  }
  if (getSocket() !== socket || !page || page.id !== id || !page.window || !Array.isArray(page.turns)) {
    updateSessionHistory(id, expected.generation, {loading:false,error:true});
    return false;
  }
  if (options && !options.isCurrent()) {
    updateSessionHistory(id, expected.generation, {loading:false});
    return false;
  }
  const captures = restoreCaptures(id);
  const previous = windows.get(id);
  const known = new Set(previous?.rowIds ?? []);
  const historyWindow = previous ?? new HistoryWindow();
  if (!historyWindow.addTurns(page.turns, page.window, direction, direction === 'around' ? around : captures[0]?.anchor?.id)) {
    updateSessionHistory(id, expected.generation, {loading:false});
    return false;
  }
  windows.delete(id); windows.set(id, historyWindow);
  flushTextFrames();
  const current = storeRows(id);
  const incoming = new Map(rowsOfTurns(page.turns).map(row => [row.id, row]));
  const currentById = new Map(current.map(row => [row.id, row]));
  const selected = new Set(historyWindow.rowIds);
  const shown = historyWindow.rowIds.flatMap(rowId => {
    const row = incoming.get(rowId) ?? currentById.get(rowId);
    return row ? [row] : [];
  });
  const live = current.filter(row => !selected.has(row.id) && (!known.has(row.id) || isLive(row.status))).slice(-50);
  const merged = [...shown, ...live];
  const { revision: pageRevision, ...history } = historyWindow.history!;
  // Only a full window replacement moves the view's cursor.
  const cursor = direction === 'around' || direction === 'latest' ? { revision: pageRevision } : {};
  flushSync(() => {
    applyReadToView(id, merged, direction === 'around' || direction === 'latest');
    updateSessionHistory(id, expected.generation, {...history, ...cursor, loading:false, error:false});
  });
  restoreViewports(id, direction, around, captures, new Set(merged.map(row => row.id)));
  trimHistoryWindows(id);
  return true;
}
