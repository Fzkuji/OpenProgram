"use client";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { animateJumpToLatest, isChatAtBottom, readBottomPadding, readComposerHeight, readChatScroll, resolveChatScrollTop, writeChatScroll } from "@/lib/chat/chat-scroll";
import { renderMathInChat } from "@/lib/runtime-bridge/markdown-render";
import { useSessionHistory } from "@/lib/chat/session-history";
import { loadSessionHistoryWindow } from "@/lib/runtime-bridge/session-history-loader";
import { useSessionStore } from "@/lib/session-store";
import { saveHistoryAnchor } from "@/lib/chat/history-viewport";

export function useChatAreaStick(
  chatKey: string | null,
  newTurnSeed: string | null,
  ownTurn: boolean,
  paintRows: boolean,
  options?: { sessionId: string | null; areaRef: RefObject<HTMLElement>; columnRef: RefObject<HTMLElement> },
) {
  const focusedId = useSessionStore(s => s.currentSessionId);
  const sessionId = options ? options.sessionId : focusedId;
  const areaRef = options?.areaRef;
  const columnRef = options?.columnRef;
  const hasNewer = useSessionHistory(s => !!(sessionId && s.pages[sessionId]?.after));
  const interactionRef = useRef(0);
  const pendingJumpRef = useRef(false);
  const activeKeyRef = useRef<string | null>(chatKey);
  const previousKeyRef = useRef<string | null>(null);
  const previousSeedRef = useRef(newTurnSeed);
  const historyWindowRef = useRef("");
  const previousPaintRef = useRef(paintRows);
  const stuckRef = useRef(true);
  const jumpingRef = useRef(false);
  const cancelJumpRef = useRef<(() => void) | null>(null);
  const lastPointerRef = useRef(0);
  const scrollTopRef = useRef(0);
  // The ref drives the scroll math on every event; this mirrors it into
  // render state so the "jump to latest" affordance can appear. Set only
  // on transitions, so ordinary scrolling doesn't re-render per frame.
  const [detached, setDetached] = useState(false);

  useEffect(() => {
    if (!paintRows) return;
    const area = areaRef?.current ?? document.getElementById("chatArea");
    const msgs = columnRef?.current ?? document.getElementById("chatMessages");
    if (!area || !msgs) return;
    // A click that expands/collapses something (execution strip, thinking
    // row) resizes the container; pinning then yanks the clicked element
    // upward. Suppress the pin briefly after any pointer interaction so
    // user-initiated growth expands downward in place.
    const syncDetached = () => {
      const atBottom = !(sessionId && useSessionHistory.getState().pages[sessionId]?.after) && isChatAtBottom(
        area,
        readBottomPadding(msgs),
        areaRef ? 0 : readComposerHeight(),
      );
      if (jumpingRef.current) {
        // Stay visible until the ease-in-out ride finishes.
        stuckRef.current = true;
        return atBottom;
      }
      stuckRef.current = atBottom;
      setDetached((was) => (was === !atBottom ? was : !atBottom));
      return atBottom;
    };
    const onScroll = () => {
      if (area.clientHeight <= 0) return;
      syncDetached();
      scrollTopRef.current = area.scrollTop;
      const key = activeKeyRef.current;
      if (key && !area.hasAttribute("data-self-update-verification")) writeChatScroll(window.sessionStorage, key, area.scrollTop);
    };
    const pin = () => {
      renderMathInChat();
      // A Jump-to-latest click is already smoothing down; snapping
      // scrollTop here fights that and flashes the transcript.
      if (area.clientHeight <= 0) return;
      if (
        stuckRef.current
        && !(sessionId && useSessionHistory.getState().pages[sessionId]?.after)
        && !jumpingRef.current
        && performance.now() - lastPointerRef.current > 600
      ) {
        area.scrollTop = area.scrollHeight;
        scrollTopRef.current = area.scrollTop;
        const key = activeKeyRef.current;
        if (key && !area.hasAttribute("data-self-update-verification")) writeChatScroll(window.sessionStorage, key, area.scrollTop);
      }
      // Composer / pad growth must re-evaluate "at latest" even when
      // we do not pin — otherwise the button stays up after the last
      // bubble is already above the input.
      syncDetached();
    };
    const onPointer = () => {
      interactionRef.current++;
      pendingJumpRef.current = false;
      lastPointerRef.current = performance.now();
      if (jumpingRef.current) {
        cancelJumpRef.current?.();
        cancelJumpRef.current = null;
        jumpingRef.current = false;
      }
    };
    area.addEventListener("scroll", onScroll, { passive: true });
    area.addEventListener("pointerdown", onPointer, { passive: true });
    area.addEventListener("wheel", onPointer, { passive: true });
    area.addEventListener("keydown", onPointer);
    const ro = new ResizeObserver(pin);
    ro.observe(msgs);
    return () => {
      interactionRef.current++;
      pendingJumpRef.current = false;
      cancelJumpRef.current?.();
      cancelJumpRef.current = null;
      jumpingRef.current = false;
      area.removeEventListener("scroll", onScroll);
      area.removeEventListener("pointerdown", onPointer);
      area.removeEventListener("wheel", onPointer);
      area.removeEventListener("keydown", onPointer);
      ro.disconnect();
    };
  }, [paintRows, chatKey, sessionId, areaRef, columnRef]);

  // Save the outgoing position and restore the incoming one before paint.
  // `chatKey` is part of the dependency so equal-length conversations still
  // switch correctly. Whether a new turn in the same chat returns to the
  // bottom depends on where the reader was — see `resolveChatScrollTop`.
  useLayoutEffect(() => {
    const area = areaRef?.current ?? document.getElementById("chatArea");
    if (!area) return;
    if (!paintRows) {
      previousPaintRef.current = false;
      return;
    }
    const becameVisible = previousPaintRef.current === false;
    previousPaintRef.current = true;
    const keyChanged = previousKeyRef.current !== chatKey;
    const sid = sessionId;
    const history = sid ? useSessionHistory.getState().pages[sid] : undefined;
    const windowKey = `${history?.snapshot}:${history?.start}:${history?.end}`;
    const windowChanged = historyWindowRef.current !== windowKey;
    historyWindowRef.current = windowKey;
    const seedChanged = previousSeedRef.current !== newTurnSeed && !windowChanged;
    if (seedChanged && ownTurn && sid && history?.after) {
      void loadSessionHistoryWindow(sid, "latest");
    }
    if (previousKeyRef.current && keyChanged) {
      writeChatScroll(
        window.sessionStorage,
        previousKeyRef.current,
        scrollTopRef.current,
      );
    }
    activeKeyRef.current = chatKey;
    previousKeyRef.current = chatKey;
    previousSeedRef.current = newTurnSeed;

    const saved = (keyChanged || becameVisible) && chatKey
      ? readChatScroll(window.sessionStorage, chatKey)
      : null;
    // Reveal after a hide must use the same follow/stay rule as a new
    // turn. Preferring `saved` here left a following reader on a stale
    // pixel after the transcript grew in DAG / another pane.
    area.scrollTop = resolveChatScrollTop({
      keyChanged,
      seedChanged: seedChanged || becameVisible,
      saved,
      scrollHeight: area.scrollHeight,
      currentTop: becameVisible
        ? (saved ?? scrollTopRef.current)
        : area.scrollTop,
      atBottom: stuckRef.current,
      ownTurn,
    });
    scrollTopRef.current = area.scrollTop;
    // Recompute rather than assume: after a follow we are at the bottom,
    // and after a deliberate stay-put we are not — and it is this flag
    // that decides whether the streaming deltas keep pinning.
    if (!jumpingRef.current) {
      stuckRef.current = !hasNewer && isChatAtBottom(
        area,
        readBottomPadding(columnRef?.current ?? document.getElementById("chatMessages")),
        areaRef ? 0 : readComposerHeight(),
      );
      setDetached(!stuckRef.current);
    }
  }, [chatKey, newTurnSeed, ownTurn, paintRows, hasNewer, sessionId, areaRef, columnRef]);

  const jumpToLatest = useCallback(async () => {
    if (pendingJumpRef.current) return;
    const sid = sessionId;
    const interaction = interactionRef.current;
    const isCurrent = () => interaction === interactionRef.current;
    pendingJumpRef.current = true;
    try {
      if (sid) {
        const history = useSessionHistory.getState().pages[sid];
        if (history?.after || history?.loading) {
          const loaded = await loadSessionHistoryWindow(sid, "latest", undefined, { isCurrent });
          if (!loaded) return;
        }
        if (!isCurrent()) return;
        saveHistoryAnchor(sid, null);
      }
      const area = areaRef?.current ?? document.getElementById("chatArea");
      if (!area || !isCurrent()) return;
      cancelJumpRef.current?.();
      jumpingRef.current = true;
      stuckRef.current = true;
      cancelJumpRef.current = animateJumpToLatest(area, () => {
        cancelJumpRef.current = null;
        jumpingRef.current = false;
        stuckRef.current = true;
        setDetached(false);
      });
    } finally {
      if (isCurrent()) pendingJumpRef.current = false;
    }
  }, [sessionId, areaRef]);

  return { detached, jumpToLatest };
}
