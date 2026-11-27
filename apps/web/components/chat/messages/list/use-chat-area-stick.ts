"use client";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import {
  animateJumpToLatest,
  followAfterGrowth,
  followAfterScroll,
  chatPaneOf,
  isProvisionalSessionBind,
  jumpVisible,
  lastSettledTakeLatest,
  latestScrollTop,
  noteTakeLatest,
  peekTakeLatest,
  relocateTakeLatest,
  remainingScroll,
  retainTakeLatestViewport,
  settleTakeLatest,
  snapToLatest,
  subscribeTakeLatest,
  scrolledAwayFromLatest,
  CHAT_READER_SEEK_EVENT,
  COMPOSER_RESERVE_SLIDE_EVENT,
  COMPOSER_SLIDING_ATTR,
  type ReserveSlide,
} from "@/lib/chat/history/chat-scroll";
import { typesetMath } from "@/lib/runtime-bridge/dom/markdown-render";
import { useSessionHistory } from "@/lib/chat/history/session-history";
import { loadSessionHistoryWindow } from "@/lib/runtime-bridge/session/session-history-loader";
import { useSessionStore } from "@/lib/session-store";
import { setFollowLock } from "@/lib/chat/history/history-viewport";

/** How long after the reader's own input growth is left unpinned. */
const READER_INPUT_MS = 600;
const SCROLL_KEYS = new Set([
  "ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " ", "Spacebar",
]);
const SCROLL_UP_KEYS = new Set(["ArrowUp", "PageUp", "Home"]);

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
  },
) {
  const focusedId = useSessionStore((s) => s.currentSessionId);
  const sessionId = (options ? options.sessionId : focusedId) ?? chatKey;
  const areaRef = options?.areaRef;
  const columnRef = options?.columnRef;
  const hasNewer = useSessionHistory((s) => !!(sessionId && s.pages[sessionId]?.after));
  const historyLoading = useSessionHistory((s) => !!(sessionId && s.pages[sessionId]?.loading));
  const welcomeVisible = useSessionStore((s) => s.welcomeVisible);
  const interactionRef = useRef(0);
  const pendingJumpRef = useRef(false);
  const activeKeyRef = useRef<string | null>(chatKey);
  const previousKeyRef = useRef<string | null>(null);
  const previousSidRef = useRef<string | null>(null);
  const previousPaintRef = useRef(paintRows);
  const stuckRef = useRef(true);
  const jumpingRef = useRef(false);
  const cancelJumpRef = useRef<(() => void) | null>(null);
  const lastPointerRef = useRef(-Infinity);
  const scrollTopRef = useRef(0);
  const programmaticRef = useRef(false);
  const pointerArmedRef = useRef(false);
  const lastAppliedByKeyRef = useRef<Record<string, number>>({});
  const effectGenRef = useRef(0);
  const opRef = useRef<FollowOp | null>(null);
  const [noteTick, setNoteTick] = useState(0);
  const [detached, setDetached] = useState(false);
  const [showJump, setShowJump] = useState(false);


  /** The reader's own input is recent or still held: growth is theirs. */
  const readerActive = useCallback(() =>
    pointerArmedRef.current
    || performance.now() - lastPointerRef.current <= READER_INPUT_MS, []);

  const appliedOf = useCallback((key: string | null) =>
    (key && lastAppliedByKeyRef.current[key]) || 0, []);

  const setApplied = useCallback((key: string | null, generation: number) => {
    if (!key) return;
    lastAppliedByKeyRef.current[key] = Math.max(appliedOf(key), generation);
  }, [appliedOf]);

  const opCurrent = useCallback((op: FollowOp | null) =>
    !!op
    && op.epoch === interactionRef.current
    && op.scrollerKey === activeKeyRef.current
    && (sessionId == null || op.sessionId === sessionId), [sessionId]);

  // The window holds the newest page and no send or Jump is waiting for a
  // verified one. A page loading at either end does not change that: older
  // rows arriving above a reader at the latest are not the reader leaving.
  const atLatestWindow = useCallback(() => {
    if (pendingJumpRef.current) return false;
    const op = opRef.current;
    if (op?.awaitingLatest && opCurrent(op)) return false;
    if (!sessionId) return true;
    return !useSessionHistory.getState().pages[sessionId]?.after;
  }, [sessionId, opCurrent]);

  const markProgrammatic = useCallback(() => {
    programmaticRef.current = true;
    queueMicrotask(() => {
      programmaticRef.current = false;
    });
  }, []);

  const applySnap = useCallback((area: HTMLElement) => {
    markProgrammatic();
    snapToLatest(area);
    scrollTopRef.current = area.scrollTop;
  }, [markProgrammatic]);

  /** Publish the follow state and the button from it. */
  const publish = useCallback((area: HTMLElement) => {
    if (jumpingRef.current) {
      // The button stays until the ride finishes.
      stuckRef.current = true;
      return;
    }
    const op = opRef.current;
    if (op?.awaitingLatest && opCurrent(op)) {
      // Waiting for the newest page: not following yet, button as it was.
      stuckRef.current = false;
      setDetached(true);
      return;
    }
    const following = stuckRef.current;
    const hasNewer = !atLatestWindow();
    const remaining = remainingScroll(area);
    setDetached(!following);
    setShowJump((was) => jumpVisible(was, { following, hasNewer, remaining }));
  }, [atLatestWindow, opCurrent]);

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

    // A tuck toggle (use-tuck-backdrop.ts) moves the composer's covered
    // height only once its slide has ended. Meanwhile a column following
    // the latest rides the distance its reserve will move, as a transform
    // the compositor runs; when the reserve lands, the scroll that
    // follows it and the end of the ride share one frame.
    let ride: { animation: Animation; composer: Element } | null = null;
    const endRide = () => {
      ride?.animation.cancel();
      ride = null;
    };
    const cancelPending = () => {
      endRide();
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
      const programmatic = programmaticRef.current;
      if (pointerArmedRef.current && !programmatic) {
        cancelPending();
      }
      const movedUp = !programmatic
        && scrolledAwayFromLatest(scrollTopRef.current, area.scrollTop);
      // A view taken up to a message (rail, search hit, parent return) is
      // not pulled back by the late snap of an earlier placement.
      if (movedUp && opRef.current) opRef.current.nextResizeArmed = false;
      if (!jumpingRef.current) {
        stuckRef.current = followAfterScroll({
          following: stuckRef.current,
          latestWindow: atLatestWindow(),
          remaining: remainingScroll(area),
          movedUp,
        });
        if (stuckRef.current && !movedUp && !readerActive()) applySnap(area);
      }
      publish(area);
      scrollTopRef.current = area.scrollTop;
    };

    const onReserveSlide = (event: Event) => {
      const composer = event.target as Element;
      const pane = chatPaneOf(area);
      if (!pane || chatPaneOf(composer) !== pane) return;
      if (!stuckRef.current || jumpingRef.current || !atLatestWindow() || area.clientHeight <= 0) return;
      const { by, duration, easing } = (event as CustomEvent<ReserveSlide>).detail;
      endRide();
      ride = {
        animation: msgs.animate(
          [{ transform: "none" }, { transform: `translateY(${by}px)` }],
          { duration, easing, fill: "forwards" },
        ),
        composer,
      };
    };
    const pin = () => {
      follow();
      if (ride && !ride.composer.hasAttribute(COMPOSER_SLIDING_ATTR)) endRide();
    };
    const follow = () => {
      typesetMath(msgs);
      if (area.clientHeight <= 0) return;
      const op = opRef.current;
      if (jumpingRef.current) {
        publish(area);
        return;
      }
      if (op && opCurrent(op) && op.nextResizeArmed && atLatestWindow()) {
        applySnap(area);
        op.nextResizeArmed = false;
        if (op.kind === "send") {
          settleTakeLatest(op.sessionId, op.scrollerKey, op.generation);
          setApplied(op.scrollerKey, op.generation);
          stuckRef.current = true;
          opRef.current = { ...op, nextResizeArmed: false, welcomeArmed: op.welcomeArmed };
        }
        publish(area);
        return;
      }
      const active = readerActive();
      stuckRef.current = followAfterGrowth({
        following: stuckRef.current,
        latestWindow: atLatestWindow(),
        remaining: remainingScroll(area),
        readerActive: active,
      });
      if (stuckRef.current && !active) applySnap(area);
      publish(area);
    };

    const onPointerDown = () => {
      pointerArmedRef.current = true;
      lastPointerRef.current = performance.now();
      if (jumpingRef.current || pendingJumpRef.current) {
        cancelPending();
        stuckRef.current = false;
        publish(area);
      }
    };
    const onPointerUp = () => {
      pointerArmedRef.current = false;
    };
    // Only input that moves the view up is the reader leaving the latest.
    // A finger resting on the trackpad, or inertia after reaching the end,
    // sends wheel events that are neither.
    const onWheel = (event: Event) => {
      const delta = (event as WheelEvent).deltaY;
      if (!delta) return;
      if (delta < 0) lastPointerRef.current = performance.now();
      cancelPending();
    };
    const onSeek = () => {
      lastPointerRef.current = performance.now();
      cancelPending();
    };
    const onKey = (event: KeyboardEvent) => {
      if (!SCROLL_KEYS.has(event.key)) return;
      if (SCROLL_UP_KEYS.has(event.key)) lastPointerRef.current = performance.now();
      cancelPending();
    };

    area.addEventListener("scroll", onScroll, { passive: true });
    area.addEventListener("pointerdown", onPointerDown, { passive: true });
    area.addEventListener("pointerup", onPointerUp, { passive: true });
    area.addEventListener("pointercancel", onPointerUp, { passive: true });
    area.addEventListener("wheel", onWheel, { passive: true });
    // A seek started outside the area is the reader moving, like a wheel up.
    area.addEventListener(CHAT_READER_SEEK_EVENT, onSeek);
    area.addEventListener("keydown", onKey);
    document.addEventListener(COMPOSER_RESERVE_SLIDE_EVENT, onReserveSlide);
    // The border box: a taller composer grows only the column's bottom
    // padding (its reserve), which leaves the content box as it was.
    const ro = new ResizeObserver(pin);
    ro.observe(msgs, { box: "border-box" });
    // The area too: a window made only shorter changes its clientHeight
    // but neither the column's box nor scrollTop, so nothing else would
    // run pin and the latest would slide under the composer.
    ro.observe(area);
    return () => {
      // A new key's layout effect may already own a follow operation. Old
      // listener cleanup must not cancel that incoming viewport operation.
      if (activeKeyRef.current === chatKey) {
        effectGenRef.current += 1;
        pendingJumpRef.current = false;
        interactionRef.current += 1;
        if (chatKey) setFollowLock(chatKey, false);
        cancelJumpRef.current?.();
        cancelJumpRef.current = null;
        jumpingRef.current = false;
      }
      area.removeEventListener("scroll", onScroll);
      area.removeEventListener("pointerdown", onPointerDown);
      area.removeEventListener("pointerup", onPointerUp);
      area.removeEventListener("pointercancel", onPointerUp);
      area.removeEventListener("wheel", onWheel);
      area.removeEventListener(CHAT_READER_SEEK_EVENT, onSeek);
      area.removeEventListener("keydown", onKey);
      document.removeEventListener(COMPOSER_RESERVE_SLIDE_EVENT, onReserveSlide);
      endRide();
      ro.disconnect();
    };
  }, [paintRows, chatKey, sessionId, areaRef, columnRef, applySnap, atLatestWindow, opCurrent, publish, readerActive, setApplied]);

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

    if (previousKeyRef.current && keyChanged) {
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
    activeKeyRef.current = chatKey;
    previousKeyRef.current = chatKey;
    previousSidRef.current = sid ?? null;

    // Opening, switching to or showing a conversation again lands on the
    // latest and follows it through delayed layout. A reader taken to one
    // message (link, search hit, rail, parent return) moves up from there,
    // which stops the following like any other move up.
    const arriving = keyChanged || becameVisible;
    let note = sid && chatKey ? peekTakeLatest(sid, chatKey) : null;
    if (arriving && sid && chatKey) {
      note = noteTakeLatest({ sessionId: sid, scrollerKey: chatKey, turnSeed: newTurnSeed ?? "" });
      lastPointerRef.current = -Infinity;
      pointerArmedRef.current = false;
    }
    if (arriving) {
      stuckRef.current = true;
      setDetached(false);
      setShowJump(false);
    }
    const settled = sid && chatKey ? lastSettledTakeLatest(sid, chatKey) : 0;
    if (chatKey) setApplied(chatKey, settled);
    const takeLatest = !!(
      note
      && chatKey
      && note.scrollerKey === chatKey
      && note.generation > appliedOf(chatKey)
    );

    if (jumpingRef.current && history?.after) {
      pendingJumpRef.current = false;
      interactionRef.current += 1;
      cancelJumpRef.current?.();
      cancelJumpRef.current = null;
      jumpingRef.current = false;
      if (chatKey) setFollowLock(chatKey, false);
      stuckRef.current = false;
      publish(area);
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
            const shown = areaRef ? areaRef.current : document.getElementById("chatArea");
            if (shown) publish(shown);
            else setDetached(true);
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
          setShowJump(false);
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
        setShowJump(false);
      }
    } else if (!jumpingRef.current && !keyChanged) {
      // Rows commit before ResizeObserver runs: a new row, or a page of
      // older ones above. That is growth, never the reader leaving.
      const active = readerActive();
      stuckRef.current = followAfterGrowth({
        following: stuckRef.current,
        latestWindow: !hasNewer && atLatestWindow(),
        remaining: remainingScroll(area),
        readerActive: active,
      });
      if (stuckRef.current && !active) applySnap(area);
      publish(area);
    }
  }, [chatKey, newTurnSeed, paintRows, hasNewer, historyLoading, sessionId, areaRef, noteTick, applySnap, appliedOf, atLatestWindow, opCurrent, publish, readerActive, setApplied]);

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
      setShowJump(false);
    };
    tryWelcome();
    const mount = document.getElementById("welcome-mount");
    if (!mount || typeof MutationObserver !== "function") return;
    const observer = new MutationObserver(tryWelcome);
    observer.observe(mount, { childList: true });
    return () => observer.disconnect();
  }, [welcomeVisible, areaRef, applySnap, atLatestWindow, opCurrent]);

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
          setShowJump(false);
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
  }, [sessionId, areaRef, setApplied]);

  // `detached`: the view no longer follows the latest. `showJump`: the
  // reader is far enough above it for the button.
  return { detached, showJump, jumpToLatest };
}
