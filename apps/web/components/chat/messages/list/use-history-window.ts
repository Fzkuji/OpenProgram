'use client';
import { useEffect, useRef, type RefObject } from 'react';
import { holdsHistoryWindow, useSessionHistory } from '@/lib/chat/history/session-history';
import { startHistoryAutoload } from '@/lib/chat/history/history-autoload';
import { captureHistoryAnchor, restoreHistoryAnchor, type HistoryAnchor } from '@/lib/chat/history/history-viewport';
import { loadSessionHistoryWindow, registerHistoryViewport } from '@/lib/runtime-bridge/session/session-history-loader';
import { useSessionStore } from '@/lib/session-store';

/** Owns the visible pane's history lifecycle, not message rendering: the
 *  pane is registered as a viewport of its session and pages load as the
 *  reader nears an edge.
 *
 *  Opening a conversation lands on the latest (the stick hook). The row a
 *  reader in history is on is remembered only while this pane shows that
 *  conversation, so a fresh read of it (a reconnect, a renewed snapshot)
 *  puts them back on that row. It is not stored and does not outlive the
 *  view. */
export function useHistoryWindow(sessionId: string | null, enabled: boolean, areaRef?: RefObject<HTMLElement>, chatKey?: string | null): void {
  const generation=useSessionHistory(s=>sessionId?s.pages[sessionId]?.generation:undefined);
  const reading=useRef<{sessionId:string;anchor:HistoryAnchor}|null>(null);
  useEffect(()=>{
    if (!sessionId || !enabled) { reading.current=null; return; }
    const area=areaRef?.current ?? document.getElementById('chatArea');
    if (!area) return;
    const release=registerHistoryViewport(sessionId,area,chatKey??sessionId);
    let stopped=false, frame=0, interacted=false;
    let dispose: (()=>void) | undefined;
    if (reading.current?.sessionId!==sessionId) reading.current=null;
    const saved=reading.current?.anchor ?? null;
    const onInteract=()=>{interacted=true;};
    const note=()=>{
      const page=useSessionHistory.getState().pages[sessionId];
      const atLatest=!page?.after && area.scrollHeight-area.scrollTop-area.clientHeight<100;
      const anchor=atLatest ? null : captureHistoryAnchor(area);
      reading.current=anchor ? {sessionId,anchor} : null;
    };
    const onScroll=()=>{if(!frame)frame=requestAnimationFrame(()=>{frame=0;note();});};
    area.addEventListener('wheel',onInteract,{passive:true});
    area.addEventListener('pointerdown',onInteract,{passive:true});
    area.addEventListener('keydown',onInteract);
    void (async()=>{
      const page=useSessionHistory.getState().pages[sessionId];
      if (saved && holdsHistoryWindow(page)) {
        const found=useSessionStore.getState().messageOrder[sessionId]?.includes(saved.id);
        // A row the branch no longer has is forgotten, or every read would ask for it again.
        if (!found && !await loadSessionHistoryWindow(sessionId,'around',saved.id,{isCurrent:()=>!stopped&&!interacted})) {
          if (reading.current?.anchor===saved) reading.current=null;
        }
        if (!stopped && !interacted) restoreHistoryAnchor(area,saved);
      }
      if (stopped) return;
      dispose=startHistoryAutoload(area,{
        read:()=>useSessionHistory.getState().pages[sessionId],
        subscribe:useSessionHistory.subscribe,
        load:direction=>loadSessionHistoryWindow(sessionId,direction),
      });
      area.addEventListener('scroll',onScroll,{passive:true});
    })();
    return ()=>{
      if(frame)cancelAnimationFrame(frame);
      stopped=true;
      dispose?.();
      release();
      area.removeEventListener('scroll',onScroll);
      area.removeEventListener('wheel',onInteract);
      area.removeEventListener('pointerdown',onInteract);
      area.removeEventListener('keydown',onInteract);
    };
  },[sessionId,enabled,generation,areaRef,chatKey]);
}
