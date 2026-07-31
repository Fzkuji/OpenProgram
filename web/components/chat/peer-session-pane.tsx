"use client";

/**
 * Peer session pane — the SECOND chat in a chat+chat split view.
 *
 * The primary chat surface is a singleton: one legacy `#chatView` shell
 * (hardcoded `#chatArea` / `#chatMessages` ids, the composer portal, the
 * DAG + right-rail bindings) mounted once in `AppShell`. Cloning it would
 * duplicate those ids and break every `getElementById` caller in
 * `lib/runtime-bridge/*`.
 *
 * So the peer pane is a thin read-along view instead: it renders the same
 * `MessageRow`s off `useSessionStore`, which already keys messages by
 * session id and is fed by the single multiplexed WebSocket. Nothing
 * needs a second connection or subscription — a peer session streams live
 * because its rows come out of the same store.
 *
 * Clicking the pane calls `setActive(tabId)`, which swaps which session
 * owns the singleton shell (and the URL, right rail, DAG, composer). The
 * two panes therefore trade places rather than both being interactive.
 *
 * ponytail: read-along + click-to-focus, not a second full chat surface.
 * A per-pane composer needs the singleton shell parameterized first —
 * add it when someone actually needs to type into both panes at once.
 */
import { useEffect, useRef } from "react";

import { useMessageIds, useSessionStore } from "@/lib/session-store";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import { readChatScroll, writeChatScroll } from "@/lib/state/chat-scroll";
import { wsSend } from "@/components/sidebar/sessions-list/helpers";
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
  const ids = useMessageIds(sessionId);
  const areaRef = useRef<HTMLDivElement | null>(null);
  const scrollKey = sessionId ? `peer:${sessionId}` : null;

  // Nothing else loads a session that isn't the focused one, so after a
  // page refresh the peer pane's store entry is empty and the pane renders
  // blank. Ask for the transcript ourselves. `loadSessionData` feeds the
  // store for non-focused sessions, so the reply lands without disturbing
  // the focused chat (we never touch currentSessionId here).
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
      const sock = (window as unknown as { ws?: WebSocket }).ws;
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

  // Own scroll state, keyed per session and stored the same way the
  // primary pane stores its own — the two never share a position.
  useEffect(() => {
    const area = areaRef.current;
    if (!area || !scrollKey) return;
    const saved = readChatScroll(window.sessionStorage, scrollKey);
    area.scrollTop = saved ?? area.scrollHeight;
    const onScroll = () => {
      writeChatScroll(window.sessionStorage, scrollKey, area.scrollTop);
    };
    area.addEventListener("scroll", onScroll, { passive: true });
    return () => area.removeEventListener("scroll", onScroll);
  }, [scrollKey]);

  // Follow the tail while the user is parked at the bottom, so a peer
  // session that is streaming stays readable without interaction.
  useEffect(() => {
    const area = areaRef.current;
    if (!area) return;
    if (area.scrollHeight - area.scrollTop - area.clientHeight < 120) {
      area.scrollTop = area.scrollHeight;
    }
  }, [ids.length]);

  const streaming = useSessionStore((s) =>
    sessionId ? Boolean(s.runningTasks[sessionId]) : false,
  );

  // `flex: 1` (not just height) — the .center-split-* wrapper is a flex row
  // with no explicit height, so the pane fills its slot by flexing.
  return (
    <div
      className="peer-session-pane"
      onPointerDown={() => setActive(tabId)}
      style={{
        display: "flex",
        flexDirection: "column",
        flex: 1,
        minWidth: 0,
        minHeight: 0,
        overflow: "hidden",
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
        <span style={{ marginLeft: "auto", opacity: 0.7 }}>
          {text("click to focus", "点击聚焦")}
        </span>
      </div>
      {/* `minWidth: 0` on both the scroller and the column: without it a
          flex child refuses to shrink below its content's intrinsic
          width, and the bubbles collapse instead of wrapping. */}
      <div
        ref={areaRef}
        className="chat-area peer-session-area"
        style={{ flex: 1, minHeight: 0, minWidth: 0, overflowY: "auto" }}
      >
        {/* `minHeight: 100%` so the column fills the scroller even when
            empty — otherwise a zero-height child leaves the pane's middle
            unclickable, which is exactly what made clicks in the blank
            area do nothing. */}
        <div
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
            ids.map((id) => <MessageRow key={id} id={id} />)
          )}
        </div>
      </div>
    </div>
  );
}
