'use client';
import { useEffect, useLayoutEffect, useRef, type RefObject } from 'react';
import { useSessionHistory } from '@/lib/chat/session-history';
import { startHistoryAutoload } from '@/lib/chat/history-autoload';
import { captureHistoryAnchor, readHistoryAnchor, restoreHistoryAnchor, saveHistoryAnchor } from '@/lib/chat/history-viewport';
import { loadSessionHistoryWindow, registerHistoryViewport } from '@/lib/runtime-bridge/session-history-loader';
import { useSessionStore } from '@/lib/session-store';

/** Owns the visible pane's history lifecycle, not message rendering. */
export function useHistoryWindow(sessionId: string | null, enabled: boolean, areaRef?: RefObject<HTMLElement>, chatKey?: string | null): void {
  const generation=useSessionHistory(s=>sessionId?s.pages[sessionId]?.generation:undefined);
  const saveOnDetach = useRef<(() => void) | undefined>(undefined);
  useLayoutEffect(() => () => saveOnDetach.current?.(), [sessionId, enabled, generation, areaRef, chatKey]);
  useEffect(()=>{
    if (!sessionId || !enabled) return;
    const area=areaRef?.current ?? document.getElementById('chatArea');
    if (!area) return;
    const release=registerHistoryViewport(sessionId,area,chatKey??sessionId);
    let stopped=false, frame=0, interacted=false, ready=false;
    let dispose: (()=>void) | undefined;
    const saved=readHistoryAnchor(sessionId);
    const onInteract=()=>{interacted=true;};
    const save=()=>{
      if (area.hasAttribute("data-self-update-verification")) return;
      const page=useSessionHistory.getState().pages[sessionId];
      const atLatest=!page?.after && area.scrollHeight-area.scrollTop-area.clientHeight<100;
      const anchor=captureHistoryAnchor(area);
      saveHistoryAnchor(sessionId,atLatest || !anchor ? null : {...anchor,head:page?.head_id});
    };
    saveOnDetach.current=()=>{
      if ((ready || interacted) && useSessionHistory.getState().pages[sessionId]?.generation === generation) save();
    };
    const onScroll=()=>{if(!frame)frame=requestAnimationFrame(()=>{frame=0;save();});};
    area.addEventListener('wheel',onInteract,{passive:true});
    area.addEventListener('pointerdown',onInteract,{passive:true});
    area.addEventListener('keydown',onInteract);
    void (async()=>{
      const page=useSessionHistory.getState().pages[sessionId];
      if (saved && page?.snapshot) {
        const found=useSessionStore.getState().messageOrder[sessionId]?.includes(saved.id);
        if (!found) await loadSessionHistoryWindow(sessionId,'around',saved.id,{isCurrent:()=>!stopped&&!interacted});
        if (!stopped && !interacted) restoreHistoryAnchor(area,saved);
      }
      if (stopped) return;
      ready=true;
      dispose=startHistoryAutoload(area,{
        read:()=>useSessionHistory.getState().pages[sessionId],
        subscribe:useSessionHistory.subscribe,
        load:direction=>loadSessionHistoryWindow(sessionId,direction),
      });
      area.addEventListener('scroll',onScroll,{passive:true});
    })();
    return ()=>{
      if(frame)cancelAnimationFrame(frame);
      saveOnDetach.current=undefined;
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
