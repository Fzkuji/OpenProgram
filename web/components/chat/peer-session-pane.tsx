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
 * The pane has its own lightweight composer, so BOTH sessions can be
 * typed into without swapping focus first. Sends reuse
 * `sendChatMessage({ sessionId, background: true })` — the same WS
 * payload the main composer writes; `background` only skips the two
 * focused-shell side effects (`setWelcomeVisible` / `setRunning`), which
 * are singletons owned by the focused chat.
 *
 * Clicking the pane background still calls `setActive(tabId)` to swap
 * which session owns the full shell (right rail, DAG, URL, the rich
 * composer with model picker / attachments / slash commands).
 *
 * ponytail: plain textarea + send button, no model picker / attachments /
 * slash menu / steer handling. Those live in the full composer — focus
 * the pane to get them. Promote if peer-side attachments are actually
 * needed.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { useMessageIds, useSessionStore } from "@/lib/session-store";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import { readChatScroll, writeChatScroll } from "@/lib/state/chat-scroll";
import { wsSend } from "@/components/sidebar/sessions-list/helpers";
import { sendChatMessage } from "./composer/legacy-send";
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

  // Per-session run state, straight from the store — no new global. This is
  // the same slice `chat_ack` writes via `setRunningTaskFor`, so the peer's
  // pending state is independent of the focused shell's `isRunning`.
  const streaming = useSessionStore((s) =>
    sessionId ? Boolean(s.runningTasks[sessionId]) : false,
  );

  /* ---- Peer composer ------------------------------------------------- */

  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  const submit = useCallback(() => {
    const trimmed = draft.trim();
    if (!trimmed || !sessionId || streaming) return;
    // Same per-session settings the full composer would use for THIS
    // session — not the focused one's. `composerSettings` on the store is
    // the focused session's live slice, so read the by-session map instead.
    const store = useSessionStore.getState();
    const settings = store.composerSettingsBySession[sessionId];
    const handled = sendChatMessage({
      text: trimmed,
      sessionId,
      thinking: settings?.thinking ?? "",
      toolsEnabled: settings?.tools ?? false,
      webSearchEnabled: settings?.webSearch ?? false,
      background: true,
    });
    // Keep the text on a failed write so a reconnect can retry it.
    if (!handled) return;
    setDraft("");
    // Optimistic bubble: `sendChatMessage` stashes the text on
    // `__pendingUserTextBySession`, and `chat_ack` turns it into the user
    // turn + reply placeholder keyed to THIS session. That round-trip is
    // fast, and going through it keeps ids consistent with the server.
  }, [draft, sessionId, streaming]);

  // Auto-grow the textarea up to the max-height, then let it scroll.
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
  }, [draft]);

  // Enter sends, Shift+Enter newlines — same as the main composer.
  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
        e.preventDefault();
        submit();
      }
    },
    [submit],
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
          {text("click to expand", "点击展开")}
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
      {/* Lightweight composer. `stopPropagation` on pointerdown so typing
          here doesn't trigger the pane's focus-swap. */}
      <div
        className="peer-session-composer"
        onPointerDown={(e) => e.stopPropagation()}
        style={{
          flex: "0 0 auto",
          display: "flex",
          alignItems: "flex-end",
          gap: 8,
          padding: 10,
          borderTop: "1px solid var(--border, rgba(128,128,128,.2))",
        }}
      >
        <textarea
          ref={inputRef}
          value={draft}
          rows={1}
          disabled={!sessionId}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={
            streaming
              ? text("Running…", "运行中…")
              : text("Send a message…", "发送消息…")
          }
          style={{
            flex: 1,
            minWidth: 0,
            resize: "none",
            maxHeight: 120,
            padding: "8px 10px",
            borderRadius: 10,
            border: "1px solid var(--border, rgba(128,128,128,.25))",
            background: "var(--bg-tertiary, transparent)",
            color: "inherit",
            font: "inherit",
            fontSize: 14,
            lineHeight: 1.5,
            outline: "none",
          }}
        />
        <button
          type="button"
          onClick={submit}
          disabled={!draft.trim() || !sessionId || streaming}
          title={text("Send", "发送")}
          style={{
            flex: "0 0 auto",
            padding: "8px 14px",
            borderRadius: 10,
            border: "none",
            cursor: draft.trim() && !streaming ? "pointer" : "default",
            opacity: draft.trim() && !streaming ? 1 : 0.45,
            background: "var(--accent-fill, #4a7)",
            color: "var(--primary-foreground, #fff)",
            fontSize: 13,
          }}
        >
          {text("Send", "发送")}
        </button>
      </div>
    </div>
  );
}
