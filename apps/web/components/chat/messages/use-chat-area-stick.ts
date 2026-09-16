"use client";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import {
  animateJumpToLatest,
  isChatAtBottom,
  isProvisionalSessionBind,
  lastSettledTakeLatest,
  latestScrollTop,
  peekTakeLatest,
  readBottomPadding,
  readChatScroll,
  readComposerOverlay,
  resolveChatScrollTop,
  relocateTakeLatest,
  retainTakeLatestViewport,
  settleTakeLatest,
  snapToLatest,
  subscribeTakeLatest,
  writeChatScroll,
} from "@/lib/chat/chat-scroll";
import { typesetMath } from "@/lib/runtime-bridge/markdown-render";
import { useSessionHistory } from "@/lib/chat/session-history";
import { loadSessionHistoryWindow } from "@/lib/runtime-bridge/session-history-loader";
import { useSessionStore } from "@/lib/session-store";
import { saveHistoryAnchor, setFollowLock } from "@/lib/chat/history-viewport";

const GROWTH_SUPPRESS_MS = 600;
const SCROLL_KEYS = new Set([
  "ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " ", "Spacebar",
]);

type FollowKind = "send" | "jump";

interface FollowOp {
  sessionId: string;
  scrollerKey: string;
  generation: number;
  epoch: number;
  kind: FollowKind;
  nextResizeArmed: boolean;
  welcomeArmed: boolean;
  awaitingLatest: boolean;
}

export function useChatAreaStick(
  chatKey: string | null,
  newTurnSeed: string | null,
  paintRows: boolean,
  options?: {
    sessionId: string | null;
    areaRef: RefObject<HTMLElement | null>;
    columnRef: RefObject<HTMLElement | null>;
    composerRootRef?: RefObject<HTMLElement | null>;
  },
) {
  const focusedId = useSessionStore((s) => s.currentSessionId);
  const sessionId = (options ? options.sessionId : focusedId) ?? chatKey;
  const areaRef = options?.areaRef;
  const columnRef = options?.columnRef;
  const composerRootRef = options?.composerRootRef;
  const hasNewer = useSessionHistory((s) => !!(sessionId && s.pages[sessionId]?.after));
  const historyLoading = useSessionHistory((s) => !!(sessionId && s.pages[sessionId]?.loading));
  const welcomeVisible = useSessionStore((s) => s.welcomeVisible);
  const interactionRef = useRef(0);
  const pendingJumpRef = useRef(false);
  const activeKeyRef = useRef<string | null>(chatKey);
  const previousKeyRef = useRef<string | null>(null);
  const previousSidRef = useRef<string | null>(null);
  const previousSeedRef = useRef(newTurnSeed);
  const historyWindowRef = useRef("");
  const previousPaintRef = useRef(paintRows);
  const stuckRef = useRef(true);
  const jumpingRef = useRef(false);
  const cancelJumpRef = useRef<(() => void) | null>(null);
  const lastPointerRef = useRef(0);
  const scrollTopRef = useRef(0);
  const programmaticRef = useRef(false);
  const pointerArmedRef = useRef(false);
  const lastAppliedByKeyRef = useRef<Record<string, number>>({});
  const effectGenRef = useRef(0);
  const opRef = useRef<FollowOp | null>(null);
  const [noteTick, setNoteTick] = useState(0);
  const [detached, setDetached] = useState(false);

  const atLatestWindow = () => {
    if (!sessionId) return true;
    const page = useSessionHistory.getState().pages[sessionId];
    return !page?.after && !page?.loading;
  };

  const appliedOf = (key: string | null) =>
    (key && lastAppliedByKeyRef.current[key]) || 0;

  const setApplied = (key: string | null, generation: number) => {
    if (!key) return;
    lastAppliedByKeyRef.current[key] = Math.max(appliedOf(key), generation);
  };

  const opCurrent = (op: FollowOp | null) =>
    !!op
    && op.epoch === interactionRef.current
    && op.scrollerKey === activeKeyRef.current
    && (sessionId == null || op.sessionId === sessionId);

  const markProgrammatic = () => {
    programmaticRef.current = true;
    queueMicrotask(() => {
      programmaticRef.current = false;
    });
  };

  const applySnap = (area: HTMLElement) => {
    markProgrammatic();
    snapToLatest(area);
    scrollTopRef.current = area.scrollTop;
    const key = activeKeyRef.current;
    if (key && !area.hasAttribute("data-self-update-verification")) {
      writeChatScroll(window.sessionStorage, key, area.scrollTop);
    }
  };

  const stopJump = () => {
    const cancel = cancelJumpRef.current;
    cancelJumpRef.current = null;
    jumpingRef.current = false;
    cancel?.();
  };

  useEffect(() => {
    if (!chatKey) return;
    const sid = sessionId ?? chatKey;
    const releaseViewport = retainTakeLatestViewport(sid, chatKey);
    const unsubscribe = subscribeTakeLatest((note) => {
      if (note.scrollerKey === chatKey && note.sessionId === sid) {
        setNoteTick((n) => n + 1);
      }
    });
    return () => {
      unsubscribe();
      releaseViewport();
    };
  }, [chatKey, sessionId]);

  useEffect(() => {
    if (!paintRows) return;
    const area = areaRef ? areaRef.current : document.getElementById("chatArea");
    const msgs = columnRef ? columnRef.current : document.getElementById("chatMessages");
    if (!area || !msgs) return;

    const overlay = () => readComposerOverlay(area, composerRootRef?.current ?? null);

    const syncDetached = () => {
      const latest = atLatestWindow();
      const atBottom = latest && isChatAtBottom(area, readBottomPadding(msgs), overlay());
      if (jumpingRef.current) {
        // Stay visible until the ease-in-out ride finishes.
        stuckRef.current = true;
        return atBottom;
      }
      const pendingSend = opRef.current?.kind === "send"
        && opCurrent(opRef.current)
        && opRef.current.awaitingLatest;
      if (pendingSend) {
        stuckRef.current = false;
        setDetached(true);
        return false;
      }
      stuckRef.current = atBottom;
      setDetached((was) => (was === !atBottom ? was : !atBottom));
      return atBottom;
    };

    const cancelPending = () => {
      const op = opRef.current;
      interactionRef.current += 1;
      pendingJumpRef.current = false;
      if (jumpingRef.current) {
        stopJump();
        stuckRef.current = false;
      }
      if (op && op.generation > 0) {
        settleTakeLatest(op.sessionId, op.scrollerKey, op.generation);
        setApplied(op.scrollerKey, op.generation);
      }
      const key = op?.scrollerKey ?? activeKeyRef.current;
      if (key) setFollowLock(key, false);
      opRef.current = null;
    };

    const onScroll = () => {
      if (area.clientHeight <= 0) return;
      if (pointerArmedRef.current && !programmaticRef.current) {
        cancelPending();
      }
      syncDetached();
      scrollTopRef.current = area.scrollTop;
      const key = activeKeyRef.current;
      if (key && !area.hasAttribute("data-self-update-verification")) {
        writeChatScroll(window.sessionStorage, key, area.scrollTop);
      }
    };

    const pin = () => {
      typesetMath(msgs);
      if (area.clientHeight <= 0) return;
      const op = opRef.current;
      if (jumpingRef.current) {
        syncDetached();
        return;
      }
      if (op && opCurrent(op) && op.nextResizeArmed && atLatestWindow()) {
        applySnap(area);
        op.nextResizeArmed = false;
        if (op.kind === "send") {
          settleTakeLatest(op.sessionId, op.scrollerKey, op.generation);
          setApplied(op.scrollerKey, op.generation);
          stuckRef.current = true;
          setDetached(false);
          opRef.current = { ...op, nextResizeArmed: false, welcomeArmed: op.welcomeArmed };
        }
        syncDetached();
        return;
      }
      if (
        stuckRef.current
        && atLatestWindow()
        && !jumpingRef.current
        && performance.now() - lastPointerRef.current > GROWTH_SUPPRESS_MS
      ) {
        applySnap(area);
      }
      syncDetached();
    };

    const onPointerDown = () => {
      pointerArmedRef.current = true;
      lastPointerRef.current = performance.now();
      if (jumpingRef.current || pendingJumpRef.current) {
        cancelPending();
        stuckRef.current = false;
        setDetached(true);
      }
    };
    const onPointerUp = () => {
      pointerArmedRef.current = false;
    };
    const onWheel = () => {
      lastPointerRef.current = performance.now();
      cancelPending();
    };
    const onKey = (event: KeyboardEvent) => {
      if (!SCROLL_KEYS.has(event.key)) return;
      lastPointerRef.current = performance.now();
      cancelPending();
    };

    area.addEventListener("scroll", onScroll, { passive: true });
    area.addEventListener("pointerdown", onPointerDown, { passive: true });
    area.addEventListener("pointerup", onPointerUp, { passive: true });
    area.addEventListener("pointercancel", onPointerUp, { passive: true });
    area.addEventListener("wheel", onWheel, { passive: true });
    area.addEventListener("keydown", onKey);
    const ro = new ResizeObserver(pin);
    ro.observe(msgs);
    return () => {
      effectGenRef.current += 1;
      pendingJumpRef.current = false;
      interactionRef.current += 1;
      const key = activeKeyRef.current;
      if (key) setFollowLock(key, false);
      cancelJumpRef.current?.();
      cancelJumpRef.current = null;
      jumpingRef.current = false;
      area.removeEventListener("scroll", onScroll);
      area.removeEventListener("pointerdown", onPointerDown);
      area.removeEventListener("pointerup", onPointerUp);
      area.removeEventListener("pointercancel", onPointerUp);
      area.removeEventListener("wheel", onWheel);
      area.removeEventListener("keydown", onKey);
      ro.disconnect();
    };
  }, [paintRows, chatKey, sessionId, areaRef, columnRef, composerRootRef]);

  useLayoutEffect(() => {
    const area = areaRef ? areaRef.current : document.getElementById("chatArea");
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

    if (previousKeyRef.current && keyChanged) {
      writeChatScroll(
        window.sessionStorage,
        previousKeyRef.current,
        scrollTopRef.current,
      );
      const outgoingKey = previousKeyRef.current;
      const outgoingSid = previousSidRef.current;
      if (
        outgoingSid
        && outgoingKey
        && sid
        && chatKey
        && isProvisionalSessionBind(outgoingKey, outgoingSid, chatKey, sid)
      ) {
        const moved = relocateTakeLatest(outgoingSid, outgoingKey, sid, chatKey);
        setFollowLock(outgoingKey, false);
        if (moved && lastSettledTakeLatest(sid, chatKey) < moved.generation) {
          setFollowLock(chatKey, true);
        }
      } else if (outgoingSid && outgoingKey) {
        const outgoing = peekTakeLatest(outgoingSid, outgoingKey);
        if (outgoing) settleTakeLatest(outgoingSid, outgoingKey, outgoing.generation);
        setFollowLock(outgoingKey, false);
        pendingJumpRef.current = false;
        interactionRef.current += 1;
        opRef.current = null;
        jumpingRef.current = false;
        cancelJumpRef.current?.();
        cancelJumpRef.current = null;
      }
    }
    const seedChanged = previousSeedRef.current !== newTurnSeed && !windowChanged;
    activeKeyRef.current = chatKey;
    previousKeyRef.current = chatKey;
    previousSidRef.current = sid ?? null;
    previousSeedRef.current = newTurnSeed;

    const saved = (keyChanged || becameVisible) && chatKey
      ? readChatScroll(window.sessionStorage, chatKey)
      : null;

    const note = sid && chatKey ? peekTakeLatest(sid, chatKey) : null;
    const settled = sid && chatKey ? lastSettledTakeLatest(sid, chatKey) : 0;
    if (chatKey) setApplied(chatKey, settled);
    const takeLatest = !!(
      note
      && chatKey
      && note.scrollerKey === chatKey
      && note.generation > appliedOf(chatKey)
    );

    if (keyChanged && !takeLatest) {
      area.scrollTop = resolveChatScrollTop({
        keyChanged: true,
        seedChanged: false,
        saved,
        scrollHeight: area.scrollHeight,
        currentTop: saved ?? scrollTopRef.current,
        atBottom: stuckRef.current,
        ownTurn: false,
      });
      if (typeof area.scrollTop === "number" && saved == null) {
        markProgrammatic();
        snapToLatest(area);
      } else {
        markProgrammatic();
      }
      scrollTopRef.current = area.scrollTop;
    } else if (!takeLatest && becameVisible && !keyChanged) {
      area.scrollTop = resolveChatScrollTop({
        keyChanged: false,
        seedChanged: seedChanged || becameVisible,
        saved,
        scrollHeight: area.scrollHeight,
        currentTop: saved ?? scrollTopRef.current,
        atBottom: stuckRef.current,
        ownTurn: false,
      });
      scrollTopRef.current = area.scrollTop;
    }

    if (jumpingRef.current && history?.after) {
      pendingJumpRef.current = false;
      interactionRef.current += 1;
      cancelJumpRef.current?.();
      cancelJumpRef.current = null;
      jumpingRef.current = false;
      if (chatKey) setFollowLock(chatKey, false);
      stuckRef.current = false;
      setDetached(true);
    }

    if (takeLatest && note && sid && chatKey) {
      const jumpInFlight = pendingJumpRef.current || jumpingRef.current;
      const jumpOwnsThisNote = !!(
        jumpInFlight
        && opRef.current
        && opRef.current.scrollerKey === chatKey
        && opRef.current.generation === note.generation
      );
      if (jumpInFlight && !jumpOwnsThisNote) {
        pendingJumpRef.current = false;
        interactionRef.current += 1;
        cancelJumpRef.current?.();
        cancelJumpRef.current = null;
        jumpingRef.current = false;
      }
      const epoch = interactionRef.current;
      const needsLatest = !!(history?.after || history?.loading);
      const alreadyAwaiting = !!(
        opRef.current?.awaitingLatest
        && opCurrent(opRef.current)
        && opRef.current.generation === note.generation
        && opRef.current.scrollerKey === chatKey
      );
      if (needsLatest) setFollowLock(chatKey, true);
      if (alreadyAwaiting || (jumpOwnsThisNote && !jumpingRef.current)) {
        stuckRef.current = false;
        setDetached(true);
      } else if (jumpOwnsThisNote) {
        // Jump animation owns this note; hide only after verified settle.
      } else if (needsLatest) {
        const effectGen = ++effectGenRef.current;
        stuckRef.current = false;
        setDetached(true);
        opRef.current = {
          sessionId: sid,
          scrollerKey: chatKey,
          generation: note.generation,
          epoch,
          kind: "send",
          nextResizeArmed: false,
          welcomeArmed: !areaRef,
          awaitingLatest: true,
        };
        void (async () => {
          const isCurrent = () =>
            effectGen === effectGenRef.current
            && epoch === interactionRef.current
            && activeKeyRef.current === chatKey
            && peekTakeLatest(sid, chatKey)?.generation === note.generation
            && lastSettledTakeLatest(sid, chatKey) < note.generation;
          const loaded = await loadSessionHistoryWindow(sid, "latest", undefined, { isCurrent });
          if (!isCurrent()) {
            const jumpOwns = pendingJumpRef.current || jumpingRef.current;
            if (!jumpOwns) setFollowLock(chatKey, false);
            const live = opRef.current;
            if (live && live.epoch === epoch && live.awaitingLatest) {
              opRef.current = { ...live, awaitingLatest: false };
            }
            return;
          }
          if (!loaded) {
            settleTakeLatest(sid, chatKey, note.generation);
            setApplied(chatKey, note.generation);
            setFollowLock(chatKey, false);
            opRef.current = null;
            stuckRef.current = false;
            setDetached(true);
            return;
          }
          const live = areaRef ? areaRef.current : document.getElementById("chatArea");
          if (!live || !isCurrent()) {
            if (!(pendingJumpRef.current || jumpingRef.current)) {
              setFollowLock(chatKey, false);
            }
            return;
          }
          stuckRef.current = true;
          applySnap(live);
          setApplied(chatKey, note.generation);
          settleTakeLatest(sid, chatKey, note.generation);
          setFollowLock(chatKey, false);
          opRef.current = {
            sessionId: sid,
            scrollerKey: chatKey,
            generation: note.generation,
            epoch,
            kind: "send",
            nextResizeArmed: true,
            welcomeArmed: !areaRef,
            awaitingLatest: false,
          };
          setDetached(false);
        })();
      } else {
        stuckRef.current = true;
        applySnap(area);
        setApplied(chatKey, note.generation);
        settleTakeLatest(sid, chatKey, note.generation);
        setFollowLock(chatKey, false);
        opRef.current = {
          sessionId: sid,
          scrollerKey: chatKey,
          generation: note.generation,
          epoch,
          kind: "send",
          nextResizeArmed: true,
          welcomeArmed: !areaRef,
          awaitingLatest: false,
        };
        setDetached(false);
      }
    } else if (!jumpingRef.current && !keyChanged) {
      stuckRef.current = !hasNewer && isChatAtBottom(
        area,
        readBottomPadding(columnRef?.current ?? document.getElementById("chatMessages")),
        readComposerOverlay(area, composerRootRef?.current ?? null),
      );
      setDetached(!stuckRef.current);
    }
  }, [chatKey, newTurnSeed, paintRows, hasNewer, historyLoading, sessionId, areaRef, columnRef, composerRootRef, noteTick]);

  useEffect(() => {
    if (areaRef) return;
    const tryWelcome = () => {
      const op = opRef.current;
      if (!op || !op.welcomeArmed || !opCurrent(op) || op.kind !== "send") return;
      if (welcomeVisible) return;
      const area = document.getElementById("chatArea");
      if (!area) return;
      const mount = document.getElementById("welcome-mount");
      if (mount && mount.childElementCount > 0) return;
      if (!atLatestWindow()) return;
      op.welcomeArmed = false;
      applySnap(area);
      stuckRef.current = true;
      setDetached(false);
    };
    tryWelcome();
    const mount = document.getElementById("welcome-mount");
    if (!mount || typeof MutationObserver !== "function") return;
    const observer = new MutationObserver(tryWelcome);
    observer.observe(mount, { childList: true });
    return () => observer.disconnect();
  }, [welcomeVisible, areaRef]);

  const jumpToLatest = useCallback(async () => {
    if (pendingJumpRef.current) return;
    const sid = sessionId;
    const key = activeKeyRef.current;
    interactionRef.current += 1;
    const epoch = interactionRef.current;
    const isCurrent = () => epoch === interactionRef.current && activeKeyRef.current === key;
    pendingJumpRef.current = true;
    const history = sid ? useSessionHistory.getState().pages[sid] : undefined;
    if (key && (history?.after || history?.loading)) setFollowLock(key, true);
    try {
      if (sid) {
        if (history?.after || history?.loading) {
          const loaded = await loadSessionHistoryWindow(sid, "latest", undefined, { isCurrent });
          if (!loaded) {
            if (isCurrent() && key) {
              setFollowLock(key, false);
              if (sid) {
                const note = peekTakeLatest(sid, key);
                if (note) {
                  settleTakeLatest(sid, key, note.generation);
                  setApplied(key, note.generation);
                }
              }
              opRef.current = null;
            }
            return;
          }
        }
        if (!isCurrent()) return;
        saveHistoryAnchor(sid, null);
      }
      const area = areaRef ? areaRef.current : document.getElementById("chatArea");
      if (!area || !isCurrent()) return;
      cancelJumpRef.current?.();
      jumpingRef.current = true;
      stuckRef.current = true;
      const generation = sid && key ? (peekTakeLatest(sid, key)?.generation ?? 0) : 0;
      opRef.current = key && sid
        ? {
            sessionId: sid,
            scrollerKey: key,
            generation,
            epoch,
            kind: "jump",
            nextResizeArmed: false,
            welcomeArmed: false,
            awaitingLatest: false,
          }
        : null;
      cancelJumpRef.current = animateJumpToLatest(
        area,
        () => {
          if (!isCurrent()) return;
          cancelJumpRef.current = null;
          jumpingRef.current = false;
          stuckRef.current = true;
          setDetached(false);
          if (sid && key) {
            setApplied(key, generation);
            const note = peekTakeLatest(sid, key);
            if (note && lastSettledTakeLatest(sid, key) < note.generation) {
              settleTakeLatest(sid, key, note.generation);
            }
            setFollowLock(key, false);
          }
          opRef.current = null;
        },
        { getTarget: () => latestScrollTop(area) },
      );
    } finally {
      if (isCurrent()) pendingJumpRef.current = false;
    }
  }, [sessionId, areaRef]);

  return { detached, jumpToLatest };
}
