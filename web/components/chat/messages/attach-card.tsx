"use client";

/**
 * AttachCard — rendered for assistant rows whose ``function === "attach"``.
 *
 * An attach row records that this turn spawned another agent. The
 * spawned agent's reply is a branch in the SAME session, so opening
 * the card checks out that branch in this chat view (no
 * cross-session navigation). Legacy rows (from before the same-
 * session refactor) carry a foreign ``session_id`` and fall back to
 * navigating to that session.
 */
import type { ChatMsg } from "@/lib/session-store";

import { useSessionStore } from "@/lib/session-store";
import { renderMarkdown, useMarkdownReady } from "./markdown";

function wsSend(payload: unknown): void {
  const w = window as unknown as { ws?: WebSocket };
  if (w.ws && w.ws.readyState === WebSocket.OPEN) {
    w.ws.send(JSON.stringify(payload));
  }
}

export function AttachCard({ msg }: { msg: ChatMsg }) {
  useMarkdownReady();
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const attach = msg.attach || {};
  const targetSessionId = attach.session_id || "";
  const targetHead = attach.head_id || "";
  const label = (attach.label || "").trim();
  const preview = msg.content || "(no output)";
  const headTag = targetHead ? targetHead.slice(0, 8) : "";
  const sameSession =
    !!targetSessionId && targetSessionId === currentSessionId;

  function open() {
    if (sameSession && targetHead) {
      // Same-session: checkout the spawned branch in place.
      wsSend({
        action: "checkout_branch",
        session_id: targetSessionId,
        head_msg_id: targetHead,
      });
      wsSend({ action: "load_session", session_id: targetSessionId });
      return;
    }
    if (!targetSessionId) return;
    const nav = (window as unknown as { __navigate?: (p: string) => void })
      .__navigate;
    if (nav) nav("/s/" + targetSessionId);
    else window.location.href = "/s/" + targetSessionId;
  }

  // Label intro: "spawn" for typical /spawn or task() rows. Legacy
  // cross-session attaches read "agent" so the foreign session_id
  // line reads naturally.
  const labelKind = sameSession ? "spawn" : "agent";
  const subtitle = sameSession
    ? (targetHead || "(no head id)")
    : targetSessionId;

  return (
    <div
      className="attach-card"
      data-peer-session-id={targetSessionId}
      data-head-id={targetHead}
    >
      <div className="attach-card-header">
        <div className="attach-card-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
               strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="4 17 10 11 4 5" />
            <line x1="12" y1="19" x2="20" y2="19" />
          </svg>
        </div>
        <div className="attach-card-meta">
          <div className="attach-card-label">
            {labelKind}{label ? ` · ${label}` : ""}
          </div>
          <div className="attach-card-sub" title={subtitle}>
            {subtitle || "(no session id)"}
            {!sameSession && headTag ? (
              <span className="attach-card-head">@{headTag}</span>
            ) : null}
          </div>
        </div>
        {(sameSession ? !!targetHead : !!targetSessionId) ? (
          <button
            type="button"
            className="attach-card-open"
            onClick={open}
            aria-label={sameSession ? "Switch to this branch" : "Open peer session"}
            title={sameSession ? "Switch to this branch" : "Open peer session"}
          >
            {sameSession ? "Switch" : "Open"}
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                 aria-hidden="true">
              <line x1="7" y1="17" x2="17" y2="7" />
              <polyline points="7 7 17 7 17 17" />
            </svg>
          </button>
        ) : null}
      </div>
      <div
        className="attach-card-preview chat-text"
        dangerouslySetInnerHTML={{ __html: renderMarkdown(preview) }}
      />
    </div>
  );
}
