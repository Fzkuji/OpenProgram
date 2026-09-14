'use client';
import { useEffect, type RefObject } from 'react';
import { useSessionHistory } from '@/lib/chat/session-history';
import { startHistoryAutoload } from '@/lib/chat/history-autoload';
import { captureHistoryAnchor, readHistoryAnchor, restoreHistoryAnchor, saveHistoryAnchor } from '@/lib/chat/history-viewport';
import { loadSessionHistoryWindow, registerHistoryViewport } from '@/lib/runtime-bridge/session-history-loader';
import { useSessionStore } from '@/lib/session-store';

/** Owns the visible pane's history lifecycle, not message rendering. */
export function useHistoryWindow(sessionId: string | null, enabled: boolean, areaRef?: RefObject<HTMLElement>, chatKey?: string | null): void {
  const generation=useSessionHistory(s=>sessionId?s.pages[sessionId]?.generation:undefined);
  useEffect(()=>{
    if (!sessionId || !enabled) return;
    const area=areaRef?.current ?? document.getElementById('chatArea');
    if (!area) return;
    const release=registerHistoryViewport(sessionId,area,chatKey??sessionId);
    let stopped=false, frame=0, interacted=false;
    let dispose: (()=>void) | undefined;
    const saved=readHistoryAnchor(sessionId);
    const onInteract=()=>{interacted=true;};
    const save=()=>{
      frame=0;
      if (area.hasAttribute("data-self-update-verification")) return;
      const page=useSessionHistory.getState().pages[sessionId];
      const atLatest=!page?.after && area.scrollHeight-area.scrollTop-area.clientHeight<100;
      const anchor=captureHistoryAnchor(area);
      saveHistoryAnchor(sessionId,atLatest || !anchor ? null : {...anchor,head:page?.head_id});
    };
    const onScroll=()=>{if(!frame)frame=requestAnimationFrame(save);};
    area.addEventListener('wheel',onInteract,{passive:true});
    area.addEventListener('pointerdown',onInteract,{passive:true});
    void (async()=>{
      const page=useSessionHistory.getState().pages[sessionId];
      if (saved && page?.snapshot) {
        const found=useSessionStore.getState().messageOrder[sessionId]?.includes(saved.id);
        if (!found) await loadSessionHistoryWindow(sessionId,'around',saved.id);
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
      stopped=true;
      dispose?.();
      release();
      if(frame)cancelAnimationFrame(frame);
      area.removeEventListener('scroll',onScroll);
      area.removeEventListener('wheel',onInteract);
      area.removeEventListener('pointerdown',onInteract);
    };
  },[sessionId,enabled,generation,areaRef,chatKey]);
}
