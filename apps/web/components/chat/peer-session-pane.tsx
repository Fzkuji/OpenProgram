"use client";

/**
 * One session pane in a split view.
 *
 * In a split view BOTH panes render this — they are symmetric. Neither is
 * the legacy `#chatView` shell: that shell is a singleton (hardcoded
 * `#chatArea` / `#chatMessages` ids read by ~10 modules under
 * `lib/runtime-bridge/`), so it can't be mounted twice. AppShell hides it
 * entirely while split, and each pane renders pure React instead: the same
 * `MessageRow`s off `useSessionStore` plus a full `<Composer sessionId=… />`.
 *
 * Both panes are always live. Each composer owns its session's draft,
 * settings and run state, and sends with `background: true`, so typing in
 * one never disturbs the other. There is no click-to-activate and no
 * position swapping.
 *
 * "Focus" here is only bookkeeping — which session the URL, tab highlight,
 * right rail and DAG follow. Interacting with a pane sets it silently
 * (`setActive`), which changes no layout and interrupts no input.
 */
import { useCallback, useEffect, useRef } from "react";

import { useMessageIds, useSessionStore } from "@/lib/session-store";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import { readChatScroll, writeChatScroll } from "@/lib/state/chat-scroll";
import { getSocket } from "@/lib/runtime-bridge/state";
import { wsSend } from "@/components/sidebar/sessions-list/helpers";
import { Composer } from "./composer";
import { SessionScopeProvider } from "@/lib/session-store/session-scope";
import { useTranslation } from "@/lib/i18n";

import { MessageRow } from "./messages/message-list";

export function PeerSessionPane({
  tabId,
  sessionId,
  title,
}: {
  tabId: string;
  sessionId: string | null;
  title: string;
}) {
  const { text } = useTranslation();
  const setActive = useCenterTabs((s) => s.setActive);
  const activeId = useCenterTabs((s) => s.activeId);
  const ids = useMessageIds(sessionId);
  const areaRef = useRef<HTMLDivElement | null>(null);
  const scrollKey = sessionId ? `peer:${sessionId}` : null;

  // Interacting with a pane makes it the focused one for bookkeeping
  // purposes (URL, tab highlight, right rail, DAG). Silent: no layout
  // change, no swap, and the other pane's composer keeps its state and
  // focus. Skipped when already focused so typing doesn't churn the store.
  const claimFocus = useCallback(() => {
    if (activeId !== tabId) setActive(tabId);
  }, [activeId, tabId, setActive]);

  // Nothing else loads a session that isn't the focused one, so after a
  // page refresh a pane's store entry is empty and renders blank. Ask for
  // the transcript ourselves. `loadSessionData` feeds the store for
  // non-focused sessions, so the reply lands without disturbing the other.
  //
  // On a hard refresh the socket usually isn't open yet at mount, so retry
  // on a short interval until it is.
  // ponytail: poll for the socket instead of subscribing to an onopen
  // event the WS layer doesn't expose. Swap to an event if one appears.
  const requestedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!sessionId || ids.length > 0) return;
    if (requestedRef.current === sessionId) return;
    const send = () => {
      const sock = getSocket();
      if (!sock || sock.readyState !== WebSocket.OPEN) return false;
      requestedRef.current = sessionId;
      wsSend({ action: "load_session", session_id: sessionId });
      return true;
    };
    if (send()) return;
    const timer = setInterval(() => {
      if (send()) clearInterval(timer);
    }, 400);
    return () => clearInterval(timer);
  }, [sessionId, ids.length]);

  // Reserve the composer's real height as scroller padding. The composer is
  // `position:absolute; bottom:0` and floats over the transcript (same as
  // the main shell, which reserves a flat 25vh). Ours varies with the input
  // row / attachment chips / fn-form, so measure it and publish the value
  // as a CSS var the .chat-messages padding reads.
  const composerHostRef = useRef<HTMLDivElement | null>(null);
  const paneRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const host = composerHostRef.current;
    const pane = paneRef.current;
    if (!host || !pane) return;
    // Measure the composer ROOT, not the host. The host is
    // `position:absolute` and its only child (`.inputArea`) is absolutely
    // positioned too, so the host's own box collapses to 0 height —
    // measuring it yielded a useless 24px. `.inputArea` is the Composer's
    // root element and the provider around it renders no DOM, so the
    // host's first element child IS that root.
    const target = host.firstElementChild as HTMLElement | null;
    if (!target) return;
    const apply = () => {
      const h = target.offsetHeight;
      if (h > 0) {
        pane.style.setProperty("--peer-composer-h", `${Math.round(h) + 24}px`);
      }
    };
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(target);
    return () => ro.disconnect();
  }, [sessionId]);

  // Stick-to-bottom. Same rule the main shell's useChatAreaStick uses: track
  // whether the user is parked near the bottom, and re-pin as content grows.
  // A ResizeObserver on the message column (rather than a message-count
  // effect) is what catches streamed text deltas too, not just new bubbles.
  const stuckRef = useRef(true);
  const columnRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const area = areaRef.current;
    const column = columnRef.current;
    if (!area || !column) return;
    const pin = () => {
      if (!stuckRef.current) return;
      area.scrollTop = area.scrollHeight;
      if (scrollKey) writeChatScroll(window.sessionStorage, scrollKey, area.scrollTop);
    };
    const ro = new ResizeObserver(pin);
    ro.observe(column);
    return () => ro.disconnect();
  }, [scrollKey]);

  // Own scroll state, keyed per session — the panes never share a position.
  useEffect(() => {
    const area = areaRef.current;
    if (!area || !scrollKey) return;
    const saved = readChatScroll(window.sessionStorage, scrollKey);
    area.scrollTop = saved ?? area.scrollHeight;
    stuckRef.current =
      area.scrollHeight - area.scrollTop - area.clientHeight < 80;
    const onScroll = () => {
      stuckRef.current =
        area.scrollHeight - area.scrollTop - area.clientHeight < 80;
      writeChatScroll(window.sessionStorage, scrollKey, area.scrollTop);
    };
    area.addEventListener("scroll", onScroll, { passive: true });
    return () => area.removeEventListener("scroll", onScroll);
  }, [scrollKey]);

  const streaming = useSessionStore((s) =>
    sessionId ? Boolean(s.runningTasks[sessionId]) : false,
  );

  // `flex: 1` (not just height) — the .center-split-* wrapper is a flex row
  // with no explicit height, so the pane fills its slot by flexing.
  return (
    <div
      ref={paneRef}
      className="peer-session-pane"
      data-pane-focused={activeId === tabId ? "true" : "false"}
      onFocusCapture={claimFocus}
      onPointerDownCapture={claimFocus}
      style={{
        display: "flex",
        flexDirection: "column",
        flex: 1,
        minWidth: 0,
        minHeight: 0,
        overflow: "hidden",
        position: "relative",
      }}
    >
      <div
        className="peer-session-header"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "6px 12px",
          fontSize: 12,
          color: "var(--text-secondary, #888)",
          borderBottom: "1px solid var(--border, rgba(128,128,128,.2))",
          flex: "0 0 auto",
        }}
      >
        <span
          style={{
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {title}
        </span>
        {streaming ? <span className="thinking-spinner" aria-hidden="true" /> : null}
      </div>
      {/* `minWidth: 0` on both the scroller and the column: without it a
          flex child refuses to shrink below its content's intrinsic
          width, and the bubbles collapse instead of wrapping. */}
      <div
        ref={areaRef}
        className="chat-area peer-session-area"
        style={{ flex: 1, minHeight: 0, minWidth: 0, overflowY: "auto" }}
      >
        <div
          ref={columnRef}
          className="chat-messages"
          style={{ minWidth: 0, minHeight: "100%" }}
        >
          {ids.length === 0 ? (
            <div
              style={{
                margin: "auto",
                fontSize: 13,
                opacity: 0.55,
                textAlign: "center",
              }}
            >
              {text("Loading conversation…", "加载会话中…")}
            </div>
          ) : (
            ids.map((id) => <MessageRow key={id} id={id} isLatest={false} />)
          )}
        </div>
      </div>
      {/* Full composer, scoped to this pane's session. The scope is what the
          composer and its control hooks (draft, run state, thinking effort,
          tools, permission mode, /context panel) read, so everything here
          targets this session rather than the focused one. */}
      {sessionId ? (
        <div ref={composerHostRef} className="peer-session-composer-host">
          <SessionScopeProvider sid={sessionId}>
            <Composer sessionId={sessionId} />
          </SessionScopeProvider>
        </div>
      ) : null}
    </div>
  );
}
