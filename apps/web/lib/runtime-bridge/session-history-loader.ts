import { flushSync } from 'react-dom';
import { convToChatMsgs } from '@/lib/chat/conv-mapper';
import { useSessionHistory, updateSessionHistory, type HistoryPage } from '@/lib/chat/session-history';
import { HistoryWindow, type HistoryDirection, type HistoryRow } from '@/lib/chat/history-window';
import { captureAreaRestoreState, historyAreaStillOwned, restoreAreaWindow } from '@/lib/chat/history-viewport';
import { clearHeights, retainRowHeights } from '@/lib/chat/message-window';
import { useSessionStore } from '@/lib/session-store';
import { wsRequest } from '@/lib/net/ws-request';
import { getSocket, runtimeState } from './state';

const windows = new Map<string, HistoryWindow>();
const viewports = new Map<string, Map<HTMLElement, string>>();
const MAX_CACHED_SESSIONS = 4;

export function registerHistoryViewport(id: string, area: HTMLElement, chatKey: string): () => void {
  const areas = viewports.get(id) ?? new Map<HTMLElement, string>();
  areas.set(area,chatKey); viewports.set(id,areas);
  return () => { areas.delete(area); if (!areas.size) viewports.delete(id); trimHistoryWindows(); };
}

function trimHistoryWindows(protectedId?: string): void {
  for (const id of windows.keys()) {
    if (windows.size <= MAX_CACHED_SESSIONS) break;
    if (id===protectedId || viewports.has(id) || id===useSessionStore.getState().currentSessionId
        || useSessionHistory.getState().pages[id]?.loading) continue;
    windows.delete(id);
    const conv=runtimeState.conversations[id];
    if(conv){delete conv.messages;delete conv.graph;}
    const state=useSessionStore.getState();
    const live=(state.messageOrder[id]??[]).map(mid=>state.messagesById[mid]).filter(m=>m && ['running','streaming','pending','cancelling'].includes(m.status??''));
    state.setMessages(id,live);
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
export function seedHistoryWindow(id: string, messages: HistoryRow[], history?: HistoryPage): void {
  windows.delete(id);
  if (!history?.snapshot) return;
  const historyWindow = new HistoryWindow();
  historyWindow.add(messages, history, 'latest');
  windows.set(id, historyWindow);
  trimHistoryWindows(id);
}
export async function loadOlderSessionHistory(id: string): Promise<void> {
  await loadSessionHistoryWindow(id, 'older');
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
  updateSessionHistory(id, expected.generation, { loading: true, error: false });
  const field = direction === 'older' ? {history_before: expected.before}
    : direction === 'newer' ? {history_after: expected.after}
    : direction === 'around' ? {history_around: around} : {history_latest: true};
  const page = await wsRequest<{id: string; messages: HistoryRow[]; history: HistoryPage}>(
    'load_session', {session_id:id, history_head:expected.head_id, history_snapshot:expected.snapshot, ...field},
    'session_history_page', {requestId:true}, 15000,
  );
  if (useSessionHistory.getState().pages[id]?.generation !== expected.generation) return false;
  if (getSocket() !== socket || !page || page.id !== id || !page.history || !Array.isArray(page.messages)
      || (direction !== 'latest' && page.history.head_id !== expected.head_id)) {
    updateSessionHistory(id, expected.generation, {loading:false,error:true});
    return false;
  }
  if (options && !options.isCurrent()) {
    updateSessionHistory(id, expected.generation, { loading: false });
    return false;
  }
  const conv = runtimeState.conversations[id] as {messages?: HistoryRow[]} | undefined;
  if (!conv) { updateSessionHistory(id, expected.generation, {loading:false}); return false; }
  const registered=viewports.get(id);
  const captures = registered
    ? [...registered.entries()].map(([area, chatKey]) => captureAreaRestoreState(area, chatKey, true))
    : [];
  if (!captures.length && runtimeState.currentSessionId === id) {
    const area = document.getElementById('chatArea');
    if (area) captures.push(captureAreaRestoreState(area, useSessionStore.getState().activeChatKey ?? id, false));
  }
  const store = useSessionStore.getState();
  const current = (store.messageOrder[id] ?? []).map(mid=>store.messagesById[mid]).filter(Boolean);
  const previous = windows.get(id);
  const known = new Set(previous?.messages.map(m=>m.id) ?? (conv.messages ?? []).map(m=>m.id));
  let messages: HistoryRow[], history: HistoryPage;
  if (page.history.snapshot) {
    const historyWindow = previous ?? new HistoryWindow();
    historyWindow.add(page.messages,page.history,direction, direction === 'around' ? around : captures[0]?.anchor?.id);
    windows.delete(id); windows.set(id,historyWindow);
    messages=historyWindow.messages; history=historyWindow.history!;
  } else {
    // Compatibility with an older server: its before-only protocol has no eviction cursors.
    const incomingIds=new Set(page.messages.map(m=>m.id));
    messages=[...page.messages,...(conv.messages ?? []).filter(m=>!incomingIds.has(m.id))];
    history=page.history;
  }
  const mapped=convToChatMsgs(messages as never[]);
  const selected=new Set(mapped.map(m=>m.id));
  const live = current.filter(m=>!selected.has(m.id) && (!known.has(m.id)
    || ['running','streaming','pending','cancelling'].includes(m.status ?? ''))).slice(-50);
  const currentById=new Map(current.map(m=>[m.id,m]));
  const merged=[...mapped.map(m=>currentById.get(m.id) ?? m),...live];
  conv.messages=messages as typeof conv.messages;
  flushSync(()=>{
    store.setMessages(id,merged);
    updateSessionHistory(id,expected.generation,{...history,loading:false,error:false});
  });
  const liveIds = new Set(merged.map(m=>m.id));
  const liveRegistered = viewports.get(id);
  for (const cap of captures) {
    retainRowHeights(cap.chatKey, liveIds);
    if (direction === 'latest') continue;
    if (historyAreaStillOwned(cap, liveRegistered, {
      sessionId: id,
      focusedSessionId: runtimeState.currentSessionId,
      focusedChatKey: useSessionStore.getState().activeChatKey,
    })) {
      restoreAreaWindow(cap, direction, around);
    }
  }
  trimHistoryWindows(id);
  return true;
}
